"""
api/app.py
Flask application factory -- the composition root. This is the only place
where concrete classes (AfricasTalkingClient, the scheduler) are
instantiated; everything downstream receives them via dependency injection
through blueprint factory functions or app.config.
"""
from __future__ import annotations

import logging
import os

from flask import Flask, render_template
from flask_wtf import CSRFProtect

from config import settings
from db.database import get_session, init_db
from db.seed import ensure_default_admin, run_seed

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app() -> Flask:
    logging.basicConfig(
        level=logging.INFO if settings.is_production else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Flask(
        __name__,
        template_folder=os.path.join(PROJECT_ROOT, "templates"),
        static_folder=os.path.join(PROJECT_ROOT, "static"),
    )
    app.secret_key = settings.secret_key
    app.config["ENV"] = settings.flask_env

    # -- Session cookie hardening --------------------------------------
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = settings.is_production
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12  # 12 hours

    @app.after_request
    def _security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    # 1. Init DB + seed reference data (regions, crops, stages, default admin)
    init_db()
    with get_session() as session:
        run_seed(session)
        ensure_default_admin(session)

    # 2. Wire up dependencies
    from services.sms import AfricasTalkingClient

    sms_client = AfricasTalkingClient()
    app.config["SMS_CLIENT"] = sms_client  # admin_web routes read this back out

    # 3. Auth: Flask-Login + CSRF protection on every form-submitting route.
    # Exempted below: /ussd and /sms/callback are server-to-server webhooks
    # called by Africa's Talking, which can't supply a CSRF token (there's
    # no browser session to hold one) -- and /internal is already gated by
    # its own bearer token. Every farmer/admin/auth form keeps CSRF.
    from services.auth import login_manager

    login_manager.init_app(app)
    csrf = CSRFProtect(app)

    # 4. Register blueprints
    from api.routes.admin_web import admin_bp
    from api.routes.auth import auth_bp
    from api.routes.farmer_web import farmer_bp
    from api.routes.health import health_bp
    from api.routes.internal import make_internal_blueprint
    from api.routes.sms import make_sms_blueprint
    from api.routes.ussd import make_ussd_blueprint

    # Real telco-facing webhooks (Demo B: Africa's Talking simulator hits these)
    ussd_bp = make_ussd_blueprint(sms_client)
    sms_bp = make_sms_blueprint(sms_client)
    internal_bp = make_internal_blueprint(sms_client)
    csrf.exempt(ussd_bp)
    csrf.exempt(sms_bp)
    csrf.exempt(internal_bp)

    app.register_blueprint(ussd_bp)
    app.register_blueprint(sms_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(health_bp)

    # Web app (Demo A): auth + farmer workspace + admin dashboard
    app.register_blueprint(auth_bp)
    app.register_blueprint(farmer_bp)
    app.register_blueprint(admin_bp)

    @app.route("/")
    def landing():
        return render_template("landing.html")

    # 5. Start the background scheduler (daily 06:00 EAT cycle). Skipped
    # under FLASK_ENV=testing so the test suite doesn't spin up a real
    # APScheduler thread per app instance created across many test cases.
    if settings.flask_env != "testing":
        from scheduler.jobs import start_scheduler

        start_scheduler(sms_client)

    return app
