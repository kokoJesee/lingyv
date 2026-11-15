"""Explicit operations, separate from the model's psychology response."""
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints, model_validator

from app.schemas.understanding import Understanding
from app.schemas.wearable import ContractModel, EvidenceReference, Revision

InterpretationText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class UnderstandingOperation(ContractModel):
    operation_id: UUID = Field(default_factory=uuid4)


class AddUserReport(UnderstandingOperation):
    content: InterpretationText


class ProposeUnderstanding(UnderstandingOperation):
    interpretation: InterpretationText
    origin: Literal['agent_hypothesis', 'user_report']
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def user_source(self):
        if self.origin == 'user_report' and not any(r.kind == 'user_report' for r in self.evidence_refs):
            raise ValueError('user interpretation requires an explicit user report reference')
        return self


class EditUnderstanding(ProposeUnderstanding):
    expected_revision: Revision


class DecideUnderstanding(UnderstandingOperation):
    expected_revision: Revision


class ConfirmUnderstanding(DecideUnderstanding):
    confirmation_token: UUID


class UnderstandingView(ContractModel):
    understanding: Understanding
    confirmation_token: UUID | None
