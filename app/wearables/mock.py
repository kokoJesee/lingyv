"""Offline adapter: generation never implicitly grants a data-use scope."""
from uuid import UUID

from app.schemas.wearable_service import ImportWearables
from app.wearables.base import MockQuery
from app.wearables.mock_generator import generate


class MockWearableAdapter:
    def fetch(self, query: MockQuery):
        return generate(query).batch

    async def import_into(self, service, session_id, query: MockQuery, *, expected_revision: int, operation_id: UUID):
        batch = self.fetch(query)
        return await service.import_records(session_id, ImportWearables(
            operation_id=operation_id, expected_revision=expected_revision, batch=batch))
