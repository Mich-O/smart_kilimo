"""
api/routes/internal.py
POST /internal/run-cycle — token-gated manual trigger for the daily cycle.
Used by tests and the demo simulator UI to avoid waiting for the 06:00 EAT
scheduled job.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from config import settings
from services.daily_cycle import run_daily_cycle


def make_internal_blueprint(sms_client) -> Blueprint:
    bp = Blueprint("internal", __name__)

    @bp.route("/internal/run-cycle", methods=["POST"])
    def run_cycle():
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "", 1)
        if token != settings.internal_api_token:
            return jsonify({"error": "Unauthorized"}), 401

        summary = run_daily_cycle(sms_client)
        return jsonify(
            {
                "plots": summary.total_plots,
                "alerts": summary.total_alerts,
                "resets": summary.total_resets,
                "suppressed": summary.total_suppressed,
                "reminders": summary.total_reminders,
                "errors": summary.total_errors,
                "duration": summary.duration_seconds,
            }
        )

    return bp
