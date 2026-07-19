from __future__ import annotations

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_health_degraded_when_db_and_kafka_uninitialised():
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error", "kafka": "error"}
