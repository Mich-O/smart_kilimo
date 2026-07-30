"""
tests/conftest.py
Sets required environment variables before any module that reads
config.settings gets imported by a test file. Also points DATABASE_URL at
an isolated on-disk SQLite file per test session so tests never touch a
developer's real smart_kilimo.db, and provides shared fixtures for the
web/auth test suite (test_auth.py, test_web.py).
"""
import os
import re
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)

os.environ.setdefault("FLASK_ENV", "testing")  # skips starting a real scheduler thread
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
os.environ.setdefault("INTERNAL_API_TOKEN", "test-internal-token")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB.name}")
os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test-api-key")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "TestAdminPass123!")

import pytest  # noqa: E402  (import after env vars are set, matches existing pattern)
from unittest.mock import patch  # noqa: E402


class FakeSMSClient:
    """Records every send() call instead of hitting Africa's Talking."""

    def __init__(self):
        self.sent = []

    def send(self, phone_number, message):
        self.sent.append((phone_number, message))
        return {"success": True, "message_id": f"fake-{len(self.sent)}"}


@pytest.fixture(scope="session")
def app():
    """
    One Flask app for the whole test session -- create_app() does real
    work (init_db, seeding, migrations-equivalent), so building it fresh
    per test would be wasteful and isn't necessary since each test cleans
    up the rows it created via the autouse fixture below.
    """
    with patch("services.sms.AfricasTalkingClient", FakeSMSClient):
        from api.app import create_app

        flask_app = create_app()
        flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_transient_tables(app):
    """
    Runs after every test. Wipes everything a test could have created
    (farmers, plots, alerts, messages, sessions) but keeps the seeded
    reference data (regions/crops/stages) and the one seeded admin
    account, resetting its lockout state so tests don't bleed into each
    other via a locked-out default admin.

    Depends on the `app` fixture (even though it doesn't use it directly)
    so create_app()'s init_db() has definitely run before cleanup tries
    to touch tables -- without this, running a DB-free test file (like
    test_irrigation_engine.py) in isolation would hit "no such table" on
    a completely fresh temp database, since nothing else would have
    triggered table creation first.
    """
    yield
    from core.models import (
        AlertRecord,
        Administrator,
        Farmer,
        FarmPlot,
        IrrigationLog,
        PlotDailyLog,
        SMSLog,
        USSDSession,
        WeatherRecord,
    )
    from db.database import get_session

    with get_session() as session:
        for model in (IrrigationLog, AlertRecord, SMSLog, USSDSession, PlotDailyLog, FarmPlot, Farmer, WeatherRecord):
            session.query(model).delete()
        for admin in session.query(Administrator).all():
            admin.failed_login_attempts = 0
            admin.locked_until = None


def get_csrf_token(html: str) -> str:
    """Pulls the CSRF hidden-field value out of a rendered Flask-WTF form."""
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, "no csrf_token field found in response HTML"
    return match.group(1)


def register_farmer(client, phone_number, password="pass12345", full_name="Test Farmer"):
    resp = client.get("/farmer/register")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post(
        "/farmer/register",
        data={
            "csrf_token": token,
            "full_name": full_name,
            "phone_number": phone_number,
            "password": password,
            "confirm_password": password,
        },
    )


def login_farmer(client, phone_number, password="pass12345"):
    resp = client.get("/farmer/login")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post(
        "/farmer/login",
        data={"csrf_token": token, "phone_number": phone_number, "password": password},
        follow_redirects=True,
    )


def login_admin(client, username="admin", password="TestAdminPass123!"):
    resp = client.get("/admin/login")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    """Logs the current session out, fetching a fresh CSRF token first --
    the logout form on the navbar only appears while authenticated, so
    any page render while logged in carries a valid token for it."""
    resp = client.get("/")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post("/logout", data={"csrf_token": token}, follow_redirects=True)


def register_plot(client, region_code="EASTERN", crop_type="MAIZE", plot_size_acres="0.25"):
    resp = client.get("/farmer/plots/new")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post(
        "/farmer/plots/new",
        data={
            "csrf_token": token,
            "region_code": region_code,
            "crop_type": crop_type,
            "plot_size_acres": plot_size_acres,
        },
        follow_redirects=True,
    )
