"""Pure adapter boundary for reproducible wearable fixtures."""
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, StrictBool, field_validator

from app.schemas.wearable import ContractModel, WearableImportBatch

Profile = Literal['mock_basic_band', 'mock_advanced_wearable', 'mock_research_combined']


class MockQuery(ContractModel):
    scenario_id: Literal['S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10'] = 'S01'
    seed: int = Field(default=42, strict=True, ge=0, le=2**32-1)
    as_of: AwareDatetime
    days: int = Field(default=30, strict=True, ge=1, le=60)
    timezone: str = 'Asia/Shanghai'
    capability_profile: Profile = 'mock_research_combined'
    variant: Literal['normal', 'missing', 'source_conflict'] = 'normal'
    include_intervals: StrictBool = True

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError('expected IANA timezone') from None
        return value


class WearableAdapter(Protocol):
    def fetch(self, query: MockQuery) -> WearableImportBatch: ...
