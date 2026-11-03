"""Run cases through the production runtime with a local model adapter."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from platform import python_version
from importlib.metadata import version
from time import perf_counter
from uuid import uuid4

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import PROJECT_ROOT, Settings
from app.evaluation.schema import Case, DraftStep, SendStep, Suite
from app.llm.errors import LLMError
from app.llm.prompts import PROMPT_VERSION
from app.schemas.context import ContextManifest
from app.schemas.draft import ConfirmDraftRequest, CreateDraftRequest, DraftContent, EditDraftRequest, RejectDraftRequest
from app.schemas.message import SendMessageRequest
from app.services.draft_service import DraftService
from app.services.session_service import SessionService

RUNNER_VERSION = "engineering_runner_v0.1"
CASE_TIMEOUT_SECONDS = 3
SUITE_TIMEOUT_SECONDS = 60
MAX_SUITE_BYTES = 1_000_000


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_suite(path: Path) -> Suite:
    # Bounded read; never surface ValidationError inputs in the CLI.
    with path.open("rb") as handle:
        raw = handle.read(MAX_SUITE_BYTES + 1)
    if len(raw) > MAX_SUITE_BYTES:
        raise ValueError("suite too large")
    data = json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return Suite.model_validate(data)


class OfflineModel:
    def __init__(self):
        self.calls = []
        self.mode = "normal"

    async def generate_reply(self, messages):
        self.calls.append([message.model_copy(deep=True) for message in messages])
        if self.mode == "timeout":
            await asyncio.Event().wait()  # Runtime timeout must cancel this wait.
        if self.mode == "invalid":
            return "   "
        blocks = [m.content for m in messages if "current_user_confirmed_drafts" in m.content]
        # Deliberately derive output from drafts to exercise obsolete-reply exclusion.
        return blocks[-1] if blocks else "离线工程评测回复"


def _check(name, expected, actual):
    return {"name": name, "expected": expected, "actual": actual,
            "passed": type(expected) is type(actual) and expected == actual}


class CaseRunner:
    def __init__(self, case: Case):
        self.case = case
        # Explicit settings bypass both .env and environment overrides.
        self.settings = Settings(
            _env_file=None, deepseek_api_key="", deepseek_model="offline",
            deepseek_base_url="https://api.deepseek.com", llm_timeout_seconds=0.02,
            llm_max_tokens=800, llm_thinking="disabled",
            runtime_max_context_chars=case.budgets.context_chars,
            runtime_max_steps=case.budgets.steps,
            runtime_max_requests_per_session=case.budgets.requests,
        )
        self.sessions = SessionService()
        self.runtime = AgentOrchestrator(self.sessions, self.settings)
        self.drafts = DraftService(self.sessions, self.settings)
        self.ids = {"main": self.sessions.create().session_id}
        self.snapshots = {}
        self.requests = {}
        self.model = OfflineModel()
        self.results = []

    async def execute(self, step):
        sid = self.ids.get(step.session)
        if step.action == "new_session":
            self.ids[step.session] = self.sessions.create().session_id
        elif isinstance(step, SendStep):
            self.model.mode = step.model
            key = (step.session, step.request)
            rid = self.requests.setdefault(key, uuid4()) if step.request else uuid4()
            await self.runtime.handle_message(
                sid, SendMessageRequest(content=step.content, request_id=rid), self.model)
        elif isinstance(step, DraftStep):
            if step.action == "create_draft":
                result = await self.drafts.create(sid, CreateDraftRequest(
                    content=DraftContent(title=step.title, items=step.items)))
            else:
                snapshot = self.snapshots[(step.session, step.draft)]
                if step.action == "edit_draft":
                    result = await self.drafts.edit(sid, snapshot.draft_id, EditDraftRequest(
                        expected_revision=snapshot.revision,
                        content=DraftContent(title=step.title, items=step.items)))
                elif step.action == "confirm_draft":
                    if snapshot.confirmation_token is None:
                        raise ValueError("snapshot has no confirmation token")
                    result = await self.drafts.confirm(sid, snapshot.draft_id, ConfirmDraftRequest(
                        expected_revision=snapshot.revision,
                        confirmation_token=snapshot.confirmation_token))
                else:
                    result = await self.drafts.reject(sid, snapshot.draft_id, RejectDraftRequest(
                        expected_revision=snapshot.revision))
            self.snapshots[(step.session, step.save_as or step.draft)] = result
        elif step.action == "clear":
            await self.runtime.clear_history(sid)
        elif step.action == "end":
            await self.runtime.end_session(sid)
        elif step.action == "demo":
            await self.runtime.start_demo(sid)
        elif step.action == "confirm_demo":
            workflow = self.sessions.require(sid).session.workflow
            await self.runtime.confirm(sid, workflow.run_id, workflow.confirmation_token)
        elif step.action == "cancel":
            workflow = self.sessions.require(sid).session.workflow
            await self.runtime.cancel(sid, workflow.run_id)

    def observe(self, step):
        record = self.sessions.require(self.ids[step.session])
        session, manifest = record.session, record.last_context
        values = {
            "messages": len(session.messages), "model_calls": len(self.model.calls),
            "phase": session.workflow.phase.value,
            "stop_reason": session.workflow.stop_reason.value if session.workflow.stop_reason else None,
            "status": session.status.value, "drafts": len(record.drafts),
            "has_context": manifest is not None,
            "context_drafts": len(manifest.draft_references()) if manifest else 0,
            "excluded_replies": len(manifest.excluded_history_indices) if manifest else 0,
        }
        if isinstance(step, DraftStep):
            snapshot = self.snapshots.get((step.session, step.save_as or step.draft))
            if snapshot is None:
                snapshot = self.snapshots.get((step.session, step.draft))
            current = record.drafts.get(snapshot.draft_id) if snapshot else None
            values.update(draft_revision=current.revision if current else None,
                          draft_status=current.status.value if current else None)
        return values

    async def run(self, deadline):
        for index, step in enumerate(self.case.steps, 1):
            if perf_counter() >= deadline:
                raise TimeoutError
            started, before = perf_counter(), len(self.model.calls)
            error = None
            try:
                await self.execute(step)
            except (RuntimeFailure, LLMError) as failure:
                error = failure.code
            # Case authoring errors / unexpected exceptions propagate to safe case failure.
            checks = [_check("error_code", step.expected_error, error)]
            observed = self.observe(step)
            for key, expected in step.expect.model_dump(exclude_unset=True).items():
                checks.append(_check(key, expected, observed.get(key)))
            if isinstance(step, SendStep) and (step.input_contains or step.input_absent):
                fresh = len(self.model.calls) > before
                checks.append(_check("fresh_model_input", True, fresh))
                text = "\n".join(m.content for m in self.model.calls[-1]) if fresh else ""
                for number, marker in enumerate(step.input_contains, 1):
                    checks.append(_check(f"input_contains_{number}", True, fresh and marker in text))
                for number, marker in enumerate(step.input_absent, 1):
                    checks.append(_check(f"input_absent_{number}", True, fresh and marker not in text))
            passed = all(check["passed"] for check in checks)
            self.results.append({
                "index": index, "action": step.action, "passed": passed, "checks": checks,
                "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            })
            if not passed:
                break


def source_hash():
    digest = hashlib.sha256()
    for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def run_suite(suite: Suite, *, progress=None) -> dict:
    # Only report metadata and controlled observations, never inputs or model replies.
    normalized = json.dumps(suite.model_dump(), sort_keys=True, ensure_ascii=False).encode()
    report = {
        "runner_version": RUNNER_VERSION, "suite_id": suite.id, "suite_version": suite.version,
        "suite_sha256": hashlib.sha256(normalized).hexdigest(), "source_sha256": source_hash(),
        "prompt_version": PROMPT_VERSION,
        "context_version": ContextManifest.model_fields["builder_version"].default,
        "started_at": datetime.now(timezone.utc).isoformat(), "mode": "offline",
        "environment": {"python": python_version(), "pydantic": version("pydantic"),
                        "pydantic_settings": version("pydantic-settings")},
        "real_model_calls": 0, "case_timeout_seconds": CASE_TIMEOUT_SECONDS,
        "suite_timeout_seconds": SUITE_TIMEOUT_SECONDS, "model_timeout_seconds": 0.02,
        "cases": [],
    }
    suite_started = perf_counter()
    for index, case in enumerate(suite.cases, 1):
        started = perf_counter()
        runner = CaseRunner(case)
        failure = None
        remaining = SUITE_TIMEOUT_SECONDS - (started - suite_started)
        if remaining <= 0:
            failure = "suite_budget_exceeded"
        else:
            try:
                limit = min(CASE_TIMEOUT_SECONDS, remaining)
                async with asyncio.timeout(limit) as timeout:
                    await runner.run(started + limit)
                # The runtime converts cancellation into a typed failure; still enforce expiry.
                if timeout.expired() or perf_counter() >= started + limit:
                    failure = "case_budget_exceeded"
            except TimeoutError:
                failure = "case_budget_exceeded"
            except Exception:
                failure = "case_execution_error"
        passed = failure is None and len(runner.results) == len(case.steps) and all(
            step["passed"] for step in runner.results)
        result = {
            "id": case.id, "critical": case.critical, "passed": passed,
            "failure": failure or (None if passed else "assertion_failed"),
            "budgets": case.budgets.model_dump(), "steps": runner.results,
            "planned_steps": len(case.steps), "model_calls": len(runner.model.calls),
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        }
        report["cases"].append(result)
        if progress:
            progress(index, len(suite.cases), result)
    report["passed"] = all(case["passed"] for case in report["cases"])
    report["summary"] = {
        "total": len(report["cases"]),
        "passed": sum(case["passed"] for case in report["cases"]),
        "failed": sum(not case["passed"] for case in report["cases"]),
        "critical_failures": [case["id"] for case in report["cases"] if case["critical"] and not case["passed"]],
    }
    report["elapsed_ms"] = round((perf_counter() - suite_started) * 1000, 3)
    return report
