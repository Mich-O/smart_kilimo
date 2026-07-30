"""
api/routes/health.py
GET /health — simple liveness check, also verifies DB connectivity.
"""
from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import text

from db.database import get_session

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    status_code = 200 if db_status == "ok" else 503
    return jsonify({"status": "ok" if status_code == 200 else "degraded", "database": db_status}), status_code
