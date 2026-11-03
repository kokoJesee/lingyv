"""Declarative, bounded engineering cases; no executable expressions."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Expected(StrictModel):
    messages: int | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    phase: Literal["IDLE", "RUNNING", "WAITING_CONFIRMATION", "COMPLETED", "CANCELLED", "FAILED"] | None = None
    stop_reason: Identifier | None = None
    status: Literal["ACTIVE", "ENDED"] | None = None
    drafts: int | None = Field(default=None, ge=0)
    draft_revision: int | None = Field(default=None, ge=1)
    draft_status: Literal["PROPOSED", "CONFIRMED", "REJECTED"] | None = None
    context_drafts: int | None = Field(default=None, ge=0)
    excluded_replies: int | None = Field(default=None, ge=0)
    has_context: bool | None = None

    @model_validator(mode="after")
    def no_explicit_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("omit unused expectations instead of null")
        return self


class StepBase(StrictModel):
    session: Identifier = "main"
    expected_error: Identifier | None = None
    expect: Expected = Field(default_factory=Expected)


class SendStep(StepBase):
    action: Literal["send"]
    content: Text
    request: Identifier | None = None
    model: Literal["normal", "timeout", "invalid"] = "normal"
    input_contains: list[Text] = Field(default_factory=list, max_length=10)
    input_absent: list[Text] = Field(default_factory=list, max_length=10)


class DraftStep(StepBase):
    action: Literal["create_draft", "edit_draft", "confirm_draft", "reject_draft"]
    draft: Identifier = "current"
    save_as: Identifier | None = None
    title: Text | None = None
    items: list[Text] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def content_matches_action(self):
        needs_content = self.action in ("create_draft", "edit_draft")
        if needs_content != (self.title is not None and self.items is not None):
            raise ValueError("create/edit require title and items")
        if not needs_content and (self.title is not None or self.items is not None):
            raise ValueError("confirmation/rejection do not accept content")
        return self


class LifecycleStep(StepBase):
    action: Literal["new_session", "clear", "end", "demo", "confirm_demo", "cancel"]


Step = Annotated[SendStep | DraftStep | LifecycleStep, Field(discriminator="action")]


class Budgets(StrictModel):
    context_chars: int = Field(default=48000, ge=1, le=100000)
    steps: int = Field(default=8, ge=1, le=100)
    requests: int = Field(default=500, ge=1, le=1000)


class Case(StrictModel):
    id: Identifier
    critical: bool = True
    budgets: Budgets = Field(default_factory=Budgets)
    steps: list[Step] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def references(self):
        sessions = {"main"}
        drafts = set()
        for step in self.steps:
            if step.action == "new_session":
                if step.session in sessions or step.expected_error is not None:
                    raise ValueError("new session alias must be unused")
                sessions.add(step.session)
            elif step.session not in sessions:
                raise ValueError("unknown session alias")
            if isinstance(step, DraftStep):
                key = (step.session, step.draft)
                if step.action != "create_draft" and key not in drafts:
                    raise ValueError("unknown draft snapshot alias")
                if step.expected_error is None:
                    drafts.add((step.session, step.save_as or step.draft))
        return self


class Suite(StrictModel):
    version: Literal["engineering_v1"]
    synthetic: Literal[True]
    id: Identifier
    cases: list[Case] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case ids")
        return self
