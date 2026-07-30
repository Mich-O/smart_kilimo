"""
api/routes/auth.py
Login, logout, and farmer self-registration. Admin accounts are never
self-service (see db/seed.py ensure_default_admin) — only farmers can
create their own account, matching who's allowed to register a plot in
the real world.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user

from api.forms import AdminLoginForm, ChangePasswordForm, FarmerLoginForm, FarmerRegisterForm
from core.models import Administrator, Farmer
from db.database import get_session
from services.auth import (
    LoginUser,
    hash_password,
    is_locked,
    register_failed_attempt,
    register_successful_login,
    verify_password,
)

auth_bp = Blueprint("auth", __name__)


def _redirect_for_role(role: str):
    return redirect(url_for("farmer.dashboard" if role == "farmer" else "admin.dashboard"))


@auth_bp.route("/login")
def choose_login():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user.role)
    return render_template("auth/choose_login.html")


@auth_bp.route("/farmer/register", methods=["GET", "POST"])
def farmer_register():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user.role)

    form = FarmerRegisterForm()
    if form.validate_on_submit():
        with get_session() as session:
            existing = session.get(Farmer, form.phone_number.data)
            if existing is not None and existing.password_hash is not None:
                flash("An account already exists for that phone number. Log in instead.", "error")
                return render_template("auth/farmer_register.html", form=form)

            if existing is None:
                farmer = Farmer(
                    phone_number=form.phone_number.data,
                    full_name=form.full_name.data,
                    password_hash=hash_password(form.password.data),
                )
                session.add(farmer)
            else:
                # A Farmer row can already exist from a USSD registration
                # with no web account attached yet — attach one now.
                existing.full_name = form.full_name.data
                existing.password_hash = hash_password(form.password.data)
                farmer = existing
            session.flush()

        flash("Account created. Log in to continue.", "success")
        return redirect(url_for("auth.farmer_login"))

    return render_template("auth/farmer_register.html", form=form)


@auth_bp.route("/farmer/login", methods=["GET", "POST"])
def farmer_login():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user.role)

    form = FarmerLoginForm()
    if form.validate_on_submit():
        with get_session() as session:
            farmer = session.get(Farmer, form.phone_number.data)

            if farmer is None or farmer.password_hash is None:
                flash("Incorrect phone number or password.", "error")
                return render_template("auth/farmer_login.html", form=form)

            if is_locked(farmer):
                flash("Too many failed attempts. Try again in a few minutes.", "error")
                return render_template("auth/farmer_login.html", form=form)

            if not verify_password(form.password.data, farmer.password_hash):
                register_failed_attempt(session, farmer)
                flash("Incorrect phone number or password.", "error")
                return render_template("auth/farmer_login.html", form=form)

            register_successful_login(farmer)
            session.flush()
            login_user(LoginUser("farmer", farmer))

        flash("Welcome back.", "success")
        return redirect(url_for("farmer.dashboard"))

    return render_template("auth/farmer_login.html", form=form)


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return _redirect_for_role(current_user.role)

    form = AdminLoginForm()
    if form.validate_on_submit():
        with get_session() as session:
            admin = session.query(Administrator).filter(Administrator.username == form.username.data).first()

            if admin is None:
                flash("Incorrect username or password.", "error")
                return render_template("auth/admin_login.html", form=form)

            if is_locked(admin):
                flash("Too many failed attempts. Try again in a few minutes.", "error")
                return render_template("auth/admin_login.html", form=form)

            if not verify_password(form.password.data, admin.password_hash):
                register_failed_attempt(session, admin)
                flash("Incorrect username or password.", "error")
                return render_template("auth/admin_login.html", form=form)

            register_successful_login(admin)
            session.flush()
            login_user(LoginUser("admin", admin))

        flash("Welcome back.", "success")
        return redirect(url_for("admin.dashboard"))

    return render_template("auth/admin_login.html", form=form)


@auth_bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        with get_session() as session:
            if current_user.role == "farmer":
                account = session.get(Farmer, current_user.raw.phone_number)
            else:
                account = session.get(Administrator, current_user.raw.admin_id)

            if account is None or not verify_password(form.current_password.data, account.password_hash):
                flash("Current password is incorrect.", "error")
                return render_template("auth/change_password.html", form=form)

            account.password_hash = hash_password(form.new_password.data)

        flash("Password updated.", "success")
        return redirect(url_for("farmer.dashboard" if current_user.role == "farmer" else "admin.dashboard"))

    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("landing"))
