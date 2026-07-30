"""
api/routes/ussd.py
POST /ussd — Africa's Talking USSD callback.
Must respond within 500ms, so this route does no heavy I/O itself; all the
work happens in core.ussd_session.handle_ussd, which uses short, targeted
DB transactions per request.
"""
from __future__ import annotations

from flask import Blueprint, Response, request

from core.ussd_session import handle_ussd


def make_ussd_blueprint(sms_client) -> Blueprint:
    bp = Blueprint("ussd", __name__)

    @bp.route("/ussd", methods=["POST"])
    def ussd():
        session_id = request.form.get("sessionId", "")
        phone_number = request.form.get("phoneNumber", "")
        text = request.form.get("text", "")
        service_code = request.form.get("serviceCode", "")

        response_text = handle_ussd(session_id, phone_number, text, service_code)
        return Response(response_text, mimetype="text/plain")

    return bp
