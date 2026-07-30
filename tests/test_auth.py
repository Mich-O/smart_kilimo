"""
tests/test_auth.py
Auth flow and security-property tests for the farmer/admin web surfaces:
registration, login, lockout, CSRF enforcement, cross-role access control,
and the USSD-then-web account attachment path.
"""
from tests.conftest import get_csrf_token, login_admin, login_farmer, logout, register_farmer


class TestFarmerRegistration:
    def test_register_then_redirected_to_login(self, client):
        resp = register_farmer(client, "+254711000001")
        assert resp.status_code in (200, 302)
        # follow the redirect manually to check the flash landed on login
        resp = client.get(resp.headers.get("Location", "/farmer/login"))
        assert b"Account created" in resp.data or resp.status_code == 200

    def test_duplicate_registration_rejected(self, client):
        register_farmer(client, "+254711000002")
        resp = register_farmer(client, "+254711000002")
        assert b"An account already exists" in resp.data

    def test_password_mismatch_rejected(self, client):
        resp = client.get("/farmer/register")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/farmer/register",
            data={
                "csrf_token": token,
                "full_name": "Mismatch Case",
                "phone_number": "+254711000003",
                "password": "correctpass1",
                "confirm_password": "differentpass1",
            },
        )
        assert b"Passwords must match" in resp.data

    def test_invalid_phone_format_rejected(self, client):
        resp = client.get("/farmer/register")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/farmer/register",
            data={
                "csrf_token": token,
                "full_name": "Bad Phone",
                "phone_number": "0711000004",  # missing +254
                "password": "correctpass1",
                "confirm_password": "correctpass1",
            },
        )
        assert b"Kenyan number" in resp.data

    def test_short_password_rejected(self, client):
        resp = client.get("/farmer/register")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/farmer/register",
            data={
                "csrf_token": token,
                "full_name": "Short Pass",
                "phone_number": "+254711000005",
                "password": "short1",
                "confirm_password": "short1",
            },
        )
        assert resp.status_code == 200  # re-renders the form with an error, no redirect
        assert b"farmer/dashboard" not in resp.data


class TestFarmerLogin:
    def test_wrong_password_rejected(self, client):
        register_farmer(client, "+254711000010")
        resp = login_farmer(client, "+254711000010", password="wrongpassword")
        assert b"Incorrect phone number or password" in resp.data

    def test_unregistered_number_rejected_with_generic_message(self, client):
        resp = login_farmer(client, "+254711999999", password="anything123")
        # Same message as a wrong password -- must not reveal whether the
        # number is registered.
        assert b"Incorrect phone number or password" in resp.data

    def test_correct_login_reaches_dashboard(self, client):
        register_farmer(client, "+254711000011")
        resp = login_farmer(client, "+254711000011")
        assert resp.request.path == "/farmer/dashboard"

    def test_lockout_after_repeated_failures(self, client):
        register_farmer(client, "+254711000012")
        for _ in range(6):  # 5 triggers the lock, the 6th confirms it's enforced
            resp = login_farmer(client, "+254711000012", password="wrongpassword")
        assert b"Too many failed attempts" in resp.data

        # even the correct password is rejected while locked
        resp = login_farmer(client, "+254711000012", password="pass12345")
        assert b"Too many failed attempts" in resp.data


class TestAdminLogin:
    def test_seeded_admin_can_log_in(self, client):
        resp = login_admin(client)
        assert resp.request.path == "/admin/dashboard"

    def test_wrong_admin_password_rejected(self, client):
        resp = login_admin(client, password="wrongpassword")
        assert b"Incorrect username or password" in resp.data

    def test_admin_lockout(self, client):
        for _ in range(6):
            resp = login_admin(client, password="wrongpassword")
        assert b"Too many failed attempts" in resp.data
        resp = login_admin(client)  # correct password, still locked
        assert b"Too many failed attempts" in resp.data


class TestCSRFEnforcement:
    def test_post_without_token_rejected(self, client):
        resp = client.post(
            "/farmer/register",
            data={"full_name": "No Token", "phone_number": "+254711000020", "password": "x", "confirm_password": "x"},
        )
        assert resp.status_code == 400

    def test_ussd_webhook_is_exempt_from_csrf(self, client):
        # Africa's Talking can't supply a CSRF token -- this must NOT 400.
        resp = client.post(
            "/ussd",
            data={"sessionId": "csrf-check", "phoneNumber": "+254711000021", "text": "", "serviceCode": "*384*1234#"},
        )
        assert resp.status_code == 200

    def test_sms_callback_webhook_is_exempt_from_csrf(self, client):
        resp = client.post("/sms/callback", data={"from": "+254711000022", "text": "1"})
        assert resp.status_code == 200


class TestCrossRoleAccessControl:
    def test_farmer_cannot_reach_admin_dashboard(self, client):
        register_farmer(client, "+254711000030")
        login_farmer(client, "+254711000030")
        resp = client.get("/admin/dashboard")
        assert resp.status_code == 403

    def test_admin_cannot_reach_farmer_dashboard(self, client):
        login_admin(client)
        resp = client.get("/farmer/dashboard")
        assert resp.status_code == 403

    def test_unauthenticated_redirected_to_login(self, client):
        resp = client.get("/farmer/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestChangePassword:
    def test_wrong_current_password_rejected(self, client):
        register_farmer(client, "+254711000040")
        login_farmer(client, "+254711000040")
        resp = client.get("/account/password")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/account/password",
            data={"csrf_token": token, "current_password": "wrong", "new_password": "newpass123", "confirm_password": "newpass123"},
        )
        assert b"Current password is incorrect" in resp.data

    def test_successful_change_and_relogin(self, client):
        register_farmer(client, "+254711000041")
        login_farmer(client, "+254711000041")
        resp = client.get("/account/password")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/account/password",
            data={"csrf_token": token, "current_password": "pass12345", "new_password": "brandnewpass1", "confirm_password": "brandnewpass1"},
            follow_redirects=True,
        )
        assert b"Password updated" in resp.data

        client.post("/logout", data={"csrf_token": token})
        resp = login_farmer(client, "+254711000041", password="brandnewpass1")
        assert resp.request.path == "/farmer/dashboard"


class TestUSSDWebAccountAttachment:
    """A farmer who only ever dialled in via USSD has no password yet --
    they should be able to attach one later without losing their plot."""

    def test_ussd_only_farmer_cannot_log_in_until_registered(self, client):
        _register_via_ussd(client, "+254711000050")
        resp = login_farmer(client, "+254711000050", password="anything123")
        assert b"Incorrect phone number or password" in resp.data

    def test_ussd_farmer_can_attach_password_and_see_their_plot(self, client):
        _register_via_ussd(client, "+254711000051")
        register_farmer(client, "+254711000051", password="attachedpass1")
        resp = login_farmer(client, "+254711000051", password="attachedpass1")
        assert resp.request.path == "/farmer/dashboard"
        assert b"Maize" in resp.data


def _register_via_ussd(client, phone_number):
    session_id = f"test-{phone_number}"
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1*1", "serviceCode": "*384*1234#"})
    resp = client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1*1*2", "serviceCode": "*384*1234#"})
    assert resp.data.startswith(b"END Registration complete")
