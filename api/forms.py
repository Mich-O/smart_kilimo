"""
api/forms.py
WTForms form classes for the farmer and admin web surfaces. Using
Flask-WTF gets CSRF protection and server-side validation for free on every
form that inherits from FlaskForm — validation happens here, not just in
the browser, so a request crafted outside the UI still gets checked.
"""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField, ValidationError
from wtforms.validators import DataRequired, EqualTo, Length, Optional as OptionalField, Regexp

PHONE_REGEX = r"^\+254[17]\d{8}$"
PHONE_MESSAGE = "Enter a Kenyan number in the form +2547XXXXXXXX or +2541XXXXXXXX."


class FarmerRegisterForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=80)])
    phone_number = StringField(
        "Phone number", validators=[DataRequired(), Regexp(PHONE_REGEX, message=PHONE_MESSAGE)]
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords must match.")]
    )
    submit = SubmitField("Create account")


class FarmerLoginForm(FlaskForm):
    phone_number = StringField(
        "Phone number", validators=[DataRequired(), Regexp(PHONE_REGEX, message=PHONE_MESSAGE)]
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class AdminLoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=50)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


def _validate_plot_size(form, field):
    try:
        value = float(field.data)
    except (TypeError, ValueError):
        raise ValidationError("Enter a number, e.g. 0.25 for a quarter acre.")
    if value <= 0 or value > 1000:
        raise ValidationError("Enter a plot size between 0 and 1000 acres.")


class PlotRegisterForm(FlaskForm):
    region_code = SelectField("Region", validators=[DataRequired()])
    crop_type = SelectField("Crop", validators=[DataRequired()])
    plot_size_acres = StringField(
        "Plot size (acres)",
        validators=[DataRequired(), _validate_plot_size],
        default="0.25",
    )
    submit = SubmitField("Register plot")


class PlotSizeUpdateForm(FlaskForm):
    plot_size_acres = StringField(
        "Plot size (acres)",
        validators=[DataRequired(), _validate_plot_size],
    )
    submit = SubmitField("Update size")


class ComposeNotificationForm(FlaskForm):
    target_type = SelectField(
        "Send to",
        choices=[("plot", "One farmer (by plot)"), ("region", "Every farmer in a region")],
        validators=[DataRequired()],
    )
    plot_id = SelectField("Plot", coerce=int, validators=[OptionalField()])
    region_code = SelectField("Region", validators=[OptionalField()])
    message = TextAreaField(
        "Message", validators=[DataRequired(), Length(max=320, message="SMS messages are capped at 320 characters.")]
    )
    submit = SubmitField("Send")


class SimulateDayForm(FlaskForm):
    plot_id = SelectField("Plot", coerce=int, validators=[DataRequired()])
    rainfall_mm = StringField("Rainfall (mm)", validators=[DataRequired()])
    submit = SubmitField("Run this day")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")]
    )
    submit = SubmitField("Update password")
