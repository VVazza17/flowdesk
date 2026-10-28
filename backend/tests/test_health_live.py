import subprocess
import sys

from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_live_returns_200_and_ok_body() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_live_is_get_only() -> None:
    assert client.post("/health/live").status_code == 405


def test_live_does_not_pull_in_db_or_redis_clients() -> None:
    # Liveness must stay independent of dependencies (slice 6 documents degraded states).
    # A fresh interpreter: the test process itself may have imported these for other tests.
    code = (
        "import sys; import app.main; app.main.create_app(); "
        "bad = [m for m in ('sqlalchemy','redis','asyncpg','psycopg') if m in sys.modules]; "
        "sys.exit(','.join(bad) or 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"imported by the live path: {result.stderr.strip()}"
