"""
services/sms.py
Thin wrapper around the Africa's Talking SMS SDK. Never raises — every
failure is caught and turned into {"success": False, "message_id": None} so
callers (daily_cycle, routes) never need defensive try/except around .send().
"""
from __future__ import annotations

import logging

import africastalking

from config import settings
from core.enums import AlertStatus, FarmerReply, SMSDirection
from core.models import AlertRecord, SMSLog
from db.database import get_session
from services.plot_evaluation import apply_farmer_reply

logger = logging.getLogger(__name__)

STATUS_CODE_SUCCESS = 101


class AfricasTalkingClient:
    def __init__(self) -> None:
        africastalking.initialize(settings.at_username, settings.at_api_key)
        self.sms = africastalking.SMS

    def send(self, phone_number: str, message: str) -> dict:
        try:
            response = self.sms.send(message, [phone_number], settings.at_sender_id)
            recipients = response.get("SMSMessageData", {}).get("Recipients", [])
            if recipients and recipients[0].get("statusCode") == STATUS_CODE_SUCCESS:
                return {"success": True, "message_id": recipients[0].get("messageId")}

            logger.warning(
                "AT SMS not accepted for %s: %s",
                phone_number,
                recipients[0].get("status") if recipients else "no recipients in response",
            )
            return {"success": False, "message_id": None}
        except Exception as exc:  # noqa: BLE001 - deliberately broad, must never raise
            logger.error("AT SMS send failed for %s: %s", phone_number, exc)
            return {"success": False, "message_id": None}


def _parse_reply(text: str) -> FarmerReply:
    stripped = text.strip()
    if stripped == "1":
        return FarmerReply.CONFIRMED
    if stripped == "2":
        return FarmerReply.DEFERRED
    return FarmerReply.INVALID


def handle_farmer_sms_reply(phone_number: str, text: str) -> None:
    """
    Handles an inbound SMS reply to an irrigation alert.
    "1" = CONFIRMED (farmer watered the crop, deficit resets and alert resolves).
    "2" = DEFERRED (farmer will water later, alert stays active for reminders).
    Anything else = INVALID, logged but no state change.

    Always logs the inbound message to SMSLog regardless of outcome. The
    actual state mutation is shared with the farmer web workspace's alert
    response buttons -- see services.plot_evaluation.apply_farmer_reply.
    """
    reply = _parse_reply(text)

    with get_session() as session:
        session.add(
            SMSLog(
                phone_number=phone_number,
                direction=SMSDirection.INBOUND.value,
                message_text=text,
            )
        )

        alert = (
            session.query(AlertRecord)
            .filter(
                AlertRecord.phone_number == phone_number,
                AlertRecord.status == AlertStatus.ACTIVE.value,
            )
            .order_by(AlertRecord.created_at.desc())
            .first()
        )

        if alert is None:
            logger.info("SMS reply from %s but no ACTIVE alert found; reply=%s", phone_number, reply.value)
            return

        if reply == FarmerReply.INVALID:
            logger.info("Invalid SMS reply from %s: %r", phone_number, text)
            return

        apply_farmer_reply(session, alert, reply)
