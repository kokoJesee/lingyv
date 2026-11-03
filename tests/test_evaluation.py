import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.config.settings import PROJECT_ROOT, Settings
from app.evaluation.__main__ import main
from app.evaluation.report import write_report
from app.evaluation.runner import MAX_SUITE_BYTES, CaseRunner, load_suite, run_suite
from app.evaluation.schema import Suite

CASES = PROJECT_ROOT / "evaluation/cases/runtime_v1.json"


def single(*steps, case_id="example", **case_extra):
    return Suite.model_validate({
        "version": "engineering_v1", "synthetic": True, "id": "test_suite",
        "cases": [{"id": case_id, "steps": list(steps), **case_extra}],
    })


def send(**extra):
    return {"action": "send", "content": "PRIVATE_INPUT_SENTINEL", **extra}


def run(suite):
    return asyncio.run(run_suite(suite))


def test_shipped_suite_runs_through_runtime_without_provider(monkeypatch):
    from app.llm.client import DeepSeekClient

    async def forbidden(*args, **kwargs):
        pytest.fail("provider must never be used")
    monkeypatch.setattr(DeepSeekClient, "generate_reply", forbidden)
    report = run(load_suite(CASES))
    assert report["passed"]
    assert report["summary"] == {
        "total": 14, "passed": 14, "failed": 0, "critical_failures": [],
    }
    assert report["real_model_calls"] == 0
    assert sum(case["model_calls"] for case in report["cases"]) == 19
    assert report["prompt_version"] and report["context_version"]
    assert len(report["source_sha256"]) == 64


def test_expected_failure_passes_and_wrong_expectation_continues_next_case():
    suite = single(send(model="invalid", expected_error="output_validation_failed",
                        expect={"messages": 0, "model_calls": 1, "phase": "FAILED"}))
    assert run(suite)["passed"]
    # Exclude unset nulls to retain strict authoring semantics.
    raw = suite.model_dump(exclude_unset=True)
    raw["cases"][0]["steps"][0]["expect"]["messages"] = 2
    raw["cases"].append({"id": "following_case", "steps": [send(expect={"messages": 2})]})
    report = run(Suite.model_validate(raw))
    assert not report["passed"]
    assert report["summary"]["critical_failures"] == ["example"]
    assert report["cases"][1]["passed"]
    assert report["cases"][0]["failure"] == "assertion_failed"


def test_reports_are_metadata_only_and_old_runs_are_preserved(tmp_path):
    suite = single(
        {"action": "create_draft", "title": "PRIVATE_DRAFT_SENTINEL", "items": ["PRIVATE_ITEM"]},
        {"action": "confirm_draft"},
        send(input_contains=["PRIVATE_DRAFT_SENTINEL"], expect={"messages": 2}),
    )
    report = run(suite)
    assert report["passed"]
    first, second = write_report(report, tmp_path), write_report(report, tmp_path)
    assert first != second
    assert (first / "report.md").exists()
    exported = (first / "report.md").read_text(encoding="utf-8") + (first / "results.json").read_text(encoding="utf-8")
    for marker in ("PRIVATE_INPUT_SENTINEL", "PRIVATE_DRAFT_SENTINEL", "PRIVATE_ITEM", "离线工程评测回复"):
        assert marker not in exported
    assert json.loads((first / "results.json").read_text(encoding="utf-8")) == report
    assert len(list(tmp_path.iterdir())) == 2


def test_failed_content_assertions_do_not_export_markers(tmp_path):
    report = run(single(send(input_contains=["PRIVATE_MISSING_MARKER"])))
    assert not report["passed"]
    folder = write_report(report, tmp_path)
    for path in folder.iterdir():
        assert "PRIVATE_MISSING_MARKER" not in path.read_text(encoding="utf-8")
    assert "input_contains_1" in (folder / "report.md").read_text(encoding="utf-8")


def test_env_file_and_environment_cannot_change_offline_settings(monkeypatch):
    original = Settings.model_config["env_file"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "DO_NOT_EXPORT_THIS_KEY")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("RUNTIME_MAX_STEPS", "not-a-number")
    monkeypatch.setitem(Settings.model_config, "env_file", Path("must-not-open.env"))
    case = single(send()).cases[0]
    runner = CaseRunner(case)
    assert not runner.settings.key_configured
    report = run(single(send()))
    assert report["passed"]
    assert "DO_NOT_EXPORT_THIS_KEY" not in json.dumps(report)
    assert original  # Config was restored by monkeypatch.


def logical_report(report):
    result = copy.deepcopy(report)
    result.pop("started_at")
    result.pop("elapsed_ms")
    for case in result["cases"]:
        case.pop("elapsed_ms")
        for step in case["steps"]:
            step.pop("elapsed_ms")
    return result


def test_repeat_suite_has_same_logical_result_and_digest_changes_with_cases():
    suite = load_suite(CASES)
    first, second = run(suite), run(suite)
    assert logical_report(first) == logical_report(second)
    changed = single(send())
    assert run(changed)["suite_sha256"] != first["suite_sha256"]


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(synthetic=False),
    lambda raw: raw.update(unknown="ignored?"),
    lambda raw: raw["cases"].append(raw["cases"][0]),
    lambda raw: raw["cases"][0]["steps"][0].update(action="shell"),
    lambda raw: raw["cases"][0]["steps"][0].update(session="missing"),
    lambda raw: raw["cases"][0]["steps"][0].update(expect={"messages": "2"}),
    lambda raw: raw["cases"][0]["steps"][0].update(expect={"messages": None}),
    lambda raw: raw["cases"][0].update(steps=[send()] * 41),
    lambda raw: raw["cases"][0].update(steps=[{"action": "confirm_draft"}]),
])
def test_invalid_suite_rejected(mutation):
    raw = single(send()).model_dump(exclude_unset=True)
    mutation(raw)
    with pytest.raises(ValidationError):
        Suite.model_validate(raw)


@pytest.mark.parametrize("text", ['{"id":"first","id":"second"}', '{"x": NaN}', "[]", "{bad"])
def test_loader_rejects_ambiguous_or_malformed_json(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_suite(path)


def test_loader_size_budget(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_SUITE_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        load_suite(path)


def test_unexpected_error_is_safe_and_other_cases_continue(monkeypatch):
    from app.evaluation import runner as module
    original = module.CaseRunner.execute

    async def failing(self, step):
        if self.case.id == "broken":
            raise RuntimeError("PRIVATE_EXCEPTION_BODY")
        return await original(self, step)
    monkeypatch.setattr(module.CaseRunner, "execute", failing)
    raw = single(send(), case_id="broken").model_dump(exclude_unset=True)
    raw["cases"].append({"id": "healthy", "steps": [send()]})
    report = run(Suite.model_validate(raw))
    assert report["cases"][0]["failure"] == "case_execution_error"
    assert report["cases"][1]["passed"]
    assert "PRIVATE_EXCEPTION_BODY" not in json.dumps(report)


def test_case_time_budget_produces_failure_report(monkeypatch):
    from app.evaluation import runner as module
    monkeypatch.setattr(module, "CASE_TIMEOUT_SECONDS", 0.01)

    async def blocked(self, step):
        await asyncio.Event().wait()
    monkeypatch.setattr(module.CaseRunner, "execute", blocked)
    report = run(single(send()))
    assert not report["passed"]
    assert report["cases"][0]["failure"] == "case_budget_exceeded"


def test_total_budget_marks_unexecuted_cases_as_failures(monkeypatch):
    from app.evaluation import runner as module
    monkeypatch.setattr(module, "SUITE_TIMEOUT_SECONDS", 0)
    report = run(load_suite(CASES))
    assert report["summary"]["failed"] == 14
    assert all(case["failure"] == "suite_budget_exceeded" and case["model_calls"] == 0
               for case in report["cases"])


def test_cli_returns_failure_and_exports_report(tmp_path, capsys):
    suite = single(send(expect={"messages": 99}))
    path = tmp_path / "wrong.json"
    path.write_text(suite.model_dump_json(exclude_unset=True), encoding="utf-8")
    assert main(["--suite", str(path), "--output", str(tmp_path / "reports")]) == 1
    assert "FAIL" in capsys.readouterr().out
    exported = list((tmp_path / "reports").glob("*/results.json"))
    assert len(exported) == 1
    assert not json.loads(exported[0].read_text(encoding="utf-8"))["passed"]


def test_cli_malformed_suite_does_not_leak_inputs_or_create_reports(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"PRIVATE_INPUT_SENTINEL": 1}', encoding="utf-8")
    output = tmp_path / "reports"
    assert main(["--suite", str(path), "--output", str(output)]) == 2
    assert "PRIVATE_INPUT_SENTINEL" not in capsys.readouterr().out
    assert not output.exists()


def test_module_command_success(tmp_path):
    env = dict(os.environ, PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "app.evaluation", "--output", str(tmp_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[14/14]" in result.stdout and "14/14" in result.stdout
    assert len(list(tmp_path.glob("*/report.md"))) == 1


def test_time_budget_still_fails_when_runtime_consumes_cancellation(monkeypatch):
    from app.evaluation import runner as module
    monkeypatch.setattr(module, "CASE_TIMEOUT_SECONDS", 0.01)

    async def consumes_cancel(self, step):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return  # Mirrors adapters that turn cancellation into a normal return.
    monkeypatch.setattr(module.CaseRunner, "execute", consumes_cancel)
    report = run(single(send()))
    assert report["cases"][0]["steps"][0]["passed"]
    assert report["cases"][0]["failure"] == "case_budget_exceeded"
    assert not report["passed"]
