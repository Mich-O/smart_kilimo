"""
api/routes/sms.py
POST /sms/callback — Africa's Talking inbound SMS callback. Parses the
farmer's reply ("1" = CONFIRMED, "2" = DEFERRED, anything else = INVALID)
and delegates the state change to services.sms.handle_farmer_sms_reply.
"""
from __future__ import annotations

from flask import Blueprint, request

from services.sms import handle_farmer_sms_reply


def make_sms_blueprint(sms_client) -> Blueprint:
    bp = Blueprint("sms", __name__)

    @bp.route("/sms/callback", methods=["POST"])
    def sms_callback():
        phone_number = request.form.get("from", "")
        text = request.form.get("text", "").strip()

        handle_farmer_sms_reply(phone_number, text)
        return "OK", 200

    return bp
