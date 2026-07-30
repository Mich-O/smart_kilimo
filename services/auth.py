"""
services/auth.py
Password hashing, Flask-Login integration, and access-control decorators
shared by the farmer and admin web surfaces.

Security notes (so the reasoning is visible, not just asserted):
- Passwords are hashed with bcrypt (adaptive, salted per-hash) — never
  stored or logged in plaintext.
- Login failures return the same generic message whether the account
  doesn't exist or the password is wrong, so the endpoint can't be used to
  enumerate registered phone numbers/usernames.
- A simple failed-attempt lockout (MAX_FAILED_ATTEMPTS within
  LOCKOUT_MINUTES) mitigates credential-stuffing / brute force without an
  external dependency.
- SQL injection isn't "handled" here — it's structurally not possible,
  because every query in this codebase goes through SQLAlchemy's ORM
  (parameterised statements), never raw string-interpolated SQL.
- Object-level authorization (a farmer only ever seeing their own plots)
  is enforced separately in api/routes/farmer_web.py via
  get_owned_plot_or_404 — role checks alone aren't enough to stop one
  farmer from viewing another's data by guessing a plot_id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional, Union

import bcrypt
from flask import abort, flash, redirect, url_for
from flask_login import LoginManager, UserMixin, current_user

from core.models import Administrator, Farmer

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

login_manager = LoginManager()
login_manager.login_view = "auth.choose_login"


def hash_password(raw_password: str) -> str:
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, AttributeError):
        return False


class LoginUser(UserMixin):
    """
    Thin Flask-Login wrapper around either a Farmer or an Administrator row.
    get_id() is prefixed ("farmer:<phone>" / "admin:<id>") so a single
    LoginManager can resolve either table.
    """

    def __init__(self, role: str, raw: Union[Farmer, Administrator]):
        self.role = role
        self.raw = raw

    def get_id(self) -> str:
        if self.role == "farmer":
            return f"farmer:{self.raw.phone_number}"
        return f"admin:{self.raw.admin_id}"

    @property
    def display_name(self) -> str:
        if self.role == "farmer":
            return self.raw.full_name or self.raw.phone_number
        return self.raw.username


@login_manager.user_loader
def load_user(user_id: str) -> Optional[LoginUser]:
    from db.database import get_session  # local import avoids a circular import at module load

    role, _, key = user_id.partition(":")
    with get_session() as session:
        if role == "farmer":
            farmer = session.get(Farmer, key)
            return LoginUser("farmer", farmer) if farmer else None
        if role == "admin":
            try:
                admin = session.get(Administrator, int(key))
            except ValueError:
                return None
            return LoginUser("admin", admin) if admin else None
    return None


def is_locked(account: Union[Farmer, Administrator]) -> bool:
    if account.locked_until is None:
        return False
    locked_until = account.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def register_failed_attempt(session, account: Union[Farmer, Administrator]) -> None:
    account.failed_login_attempts += 1
    if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
        account.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)


def register_successful_login(account: Union[Farmer, Administrator]) -> None:
    account.failed_login_attempts = 0
    account.locked_until = None


def role_required(role: str):
    """Route decorator: requires an authenticated session with the given role."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in to continue.", "error")
                return redirect(url_for("auth.choose_login"))
            if getattr(current_user, "role", None) != role:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
