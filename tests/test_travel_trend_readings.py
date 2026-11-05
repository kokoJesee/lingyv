"""Travel fixtures must remain readable without stitching distinct source timezones."""
from uuid import uuid4

from app.wearables.metric_registry import METRIC_REGISTRY


def test_travel_readings_are_readable_in_the_selected_source_timezone(client):
    as_of = "2026-11-05T12:00:00+08:00"
    sid = client.post("/sessions").json()["session_id"]
    base = f"/sessions/{sid}"
    imported = client.post(base + "/wearables/simulate", json={
        "operation_id": str(uuid4()), "expected_revision": 0,
        "as_of": as_of, "scenario_id": "S04", "seed": 42, "days": 21,
    })
    assert imported.status_code == 200
    before = client.get(base + "/support-state").json()
    assert before["wearables"]["available_timezones"] == []
    allowed = client.post(base + "/wearables/consent", json={
        "expected_revision": 1, "provider": "mock",
        "allowed_metrics": list(METRIC_REGISTRY), "scopes": ["local_analysis"],
    })
    assert allowed.status_code == 200
    state = client.get(base + "/support-state").json()
    assert state["wearables"]["available_timezones"] == ["Asia/Tokyo", "Asia/Shanghai"]
    response = client.get(base + "/wearables/trends", params={
        "as_of": as_of, "days": 30, "timezone": "Asia/Tokyo",
    })
    assert response.status_code == 200
    sleep = next(item for item in response.json()["series"] if item["metric_id"] == "sleep_duration_minutes")
    assert sleep["status"] == "ready", sleep
    assert all(point["value"] is None for point in sleep["points"][:-3])
    assert all(point["value"] is not None for point in sleep["points"][-3:])
    shanghai_response = client.get(base + "/wearables/trends", params={
        "as_of": as_of, "days": 30, "timezone": "Asia/Shanghai",
    })
    assert shanghai_response.status_code == 200
    shanghai = next(item for item in shanghai_response.json()["series"] if item["metric_id"] == "sleep_duration_minutes")
    assert shanghai["status"] == "ready"
    assert sum(point["value"] is not None for point in shanghai["points"]) == 18
    assert all(point["value"] is None for point in shanghai["points"][-3:])
    selected_ids = {point["record_id"] for point in sleep["points"] if point["record_id"]}
    assert not selected_ids.intersection(point["record_id"] for point in shanghai["points"] if point["record_id"])

    revoked = client.post(base + "/wearables/consent", json={
        "expected_revision": 2, "provider": "mock", "allowed_metrics": [], "scopes": [],
    })
    assert revoked.status_code == 200
    assert client.get(base + "/support-state").json()["wearables"]["available_timezones"] == []


def test_simulation_endpoint_replaces_travel_sample_without_carrying_model_permission(client):
    as_of = "2026-11-05T12:00:00+08:00"
    sid = client.post("/sessions").json()["session_id"]
    base = f"/sessions/{sid}"
    body = {"operation_id": str(uuid4()), "expected_revision": 0,
            "as_of": as_of, "scenario_id": "S04", "seed": 42, "days": 21}
    assert client.post(base + "/wearables/simulate", json=body).status_code == 200
    assert client.post(base + "/wearables/consent", json={
        "expected_revision": 1, "provider": "mock", "allowed_metrics": list(METRIC_REGISTRY),
        "scopes": ["local_analysis", "model_context"],
    }).status_code == 200
    replacement = {**body, "operation_id": str(uuid4()), "expected_revision": 2,
                   "scenario_id": "S02", "replace_existing": True}
    response = client.post(base + "/wearables/simulate", json=replacement)
    assert response.status_code == 200, response.text
    assert response.json()["replaced"] == 84
    assert client.post(base + "/wearables/simulate", json=replacement).json() == response.json()
    state = client.get(base + "/support-state").json()
    assert state["wearables"]["record_count"] == 84
    assert state["wearables"]["available_timezones"] == ["Asia/Shanghai"]
    assert state["wearables"]["providers"][0]["scopes"] == ["local_analysis"]
    readings = client.get(base + "/wearables/trends", params={
        "as_of": as_of, "days": 7, "timezone": "Asia/Shanghai",
    }).json()
    sleep = next(item for item in readings["series"] if item["metric_id"] == "sleep_duration_minutes")
    assert sleep["status"] == "ready"
    assert all(point["value"] < 400 for point in sleep["points"][-3:])
