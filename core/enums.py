"""
core/enums.py
Central location for every enum used across Smart Kilimo. Keeping them here
(rather than scattered per-module) avoids circular imports between
core/models.py, core/irrigation_engine.py and core/ussd_session.py.
"""
from __future__ import annotations

import enum


class CropType(str, enum.Enum):
    MAIZE = "MAIZE"
    BEANS = "BEANS"
    ONIONS = "ONIONS"


class RegionCode(str, enum.Enum):
    EASTERN = "EASTERN"
    RIFT_VALLEY = "RIFT_VALLEY"
    COAST = "COAST"


class ApiStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    HTTP_ERROR = "HTTP_ERROR"
    PARSE_ERROR = "PARSE_ERROR"


class IrrigationAction(str, enum.Enum):
    RESET = "RESET"
    INCREMENT = "INCREMENT"
    ALERT = "ALERT"
    SUPPRESS = "SUPPRESS"


class AlertStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class SMSDirection(str, enum.Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class FarmerReply(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    DEFERRED = "DEFERRED"
    INVALID = "INVALID"


class USSDStep(str, enum.Enum):
    WELCOME = "WELCOME"
    CONFIRM_REGISTER = "CONFIRM_REGISTER"
    SELECT_REGION = "SELECT_REGION"
    SELECT_CROP = "SELECT_CROP"
    SELECT_PLOT_SIZE = "SELECT_PLOT_SIZE"
    REGISTRATION_COMPLETE = "REGISTRATION_COMPLETE"
    STATUS_CHECK = "STATUS_CHECK"
    SELECT_PLOT = "SELECT_PLOT"
    UPDATE_PLOT_SIZE = "UPDATE_PLOT_SIZE"
    UNKNOWN = "UNKNOWN"
