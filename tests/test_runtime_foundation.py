from fastapi.testclient import TestClient

import townlight_comms
from townlight_comms.main import app

client = TestClient(app)


def test_package_version_is_010() -> None:
    assert townlight_comms.__version__ == "0.2.0"


def test_root_endpoint_states_runtime_boundary() -> None:
    payload = client.get("/").json()

    assert payload["name"] == "Townlight Communications"
    assert payload["version"] == "0.2.0"
    assert payload["status"] == "standalone draft-helper prototype"
    assert "legacy draft-helper prototype" in payload["message"]
    assert "not a secured production" in payload["message"]
    assert "/desktop/v1/communications" in payload["message"]
    assert "installed Windows candidate acceptance remains pending" in payload["message"]
    assert "authenticated Windows Suite" in payload["next_step"]
    assert "/desktop/v1/communications" in payload["next_step"]


def test_health_endpoint_reports_versions() -> None:
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["service"] == "townlight_comms"
    assert payload["version"] == "0.2.0"
    assert payload["townlight_core_version"] == "1.3.0.dev2"
