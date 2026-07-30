"""
tests/fakes/fake_sms.py
Fake SMS client satisfying the same interface as AfricasTalkingClient.send(),
so services.daily_cycle.run_daily_cycle can be tested without any network
calls or real credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class SentMessage:
    phone_number: str
    message: str


@dataclass
class FakeSMSClient:
    sent: List[SentMessage] = field(default_factory=list)
    should_fail: bool = False

    def send(self, phone_number: str, message: str) -> dict:
        if self.should_fail:
            return {"success": False, "message_id": None}
        self.sent.append(SentMessage(phone_number=phone_number, message=message))
        return {"success": True, "message_id": f"fake-{len(self.sent)}"}

    def messages_to(self, phone_number: str) -> List[str]:
        return [m.message for m in self.sent if m.phone_number == phone_number]
