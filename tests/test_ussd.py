"""
tests/test_ussd.py
Coverage for the USSD state machine's interactive status-check menu:
mark as watered, remind me later, and plot-size correction -- the phone
side of the same actions available on the farmer web workspace (see
TestFarmerAlertResponse / TestPlotSizeUpdate in test_web.py). Both paths
share services.plot_evaluation.apply_farmer_reply, so these tests are
really checking the USSD menu wiring, not re-testing that shared logic.
"""
from datetime import date

from core.enums import ApiStatus
from core.models import AlertRecord, FarmPlot
from db.database import get_session
from services.plot_evaluation import evaluate_and_apply
from services.weather import WeatherReading


def _register_plot_via_ussd(client, phone_number, session_id="ussd-test"):
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1", "serviceCode": "*384*1234#"})
    client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1*1", "serviceCode": "*384*1234#"})
    resp = client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone_number, "text": "1*1*1*2", "serviceCode": "*384*1234#"})
    assert resp.data.startswith(b"END Registration complete")

    with get_session() as session:
        plot = session.query(FarmPlot).filter(FarmPlot.phone_number == phone_number).first()
        return plot.plot_id


def _drive_to_alert(plot_id, days=20):
    with get_session() as session:
        region_code = session.get(FarmPlot, plot_id).region_code
    for _ in range(days):
        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            reading = WeatherReading(region_code=region_code, poll_date=date.today(),
                                      rainfall_mm=0.0, api_status=ApiStatus.SUCCESS)
            evaluate_and_apply(session, plot, reading, _NullSMS())


class _NullSMS:
    def send(self, phone_number, message):
        return {"success": True}


class TestStatusCheckMenu:
    def test_no_alert_offers_update_size_and_exit(self, client):
        phone = "+254744000001"
        plot_id = _register_plot_via_ussd(client, phone)
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        body = resp.data.decode()
        assert body.startswith("CON ")
        assert "1. Update plot size" in body
        assert "2. Exit" in body
        assert "Mark as watered" not in body

    def test_active_alert_offers_three_options(self, client):
        phone = "+254744000002"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)

        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        body = resp.data.decode()
        assert body.startswith("CON ")
        assert "1. Mark as watered" in body
        assert "2. Remind me later" in body
        assert "3. Update plot size" in body
        assert len(body) <= 182

    def test_every_status_check_response_fits_ussd_page_limit(self, client):
        phone = "+254744000003"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        assert len(resp.data) <= 182


class TestMarkAsWateredViaUSSD:
    def test_marking_watered_resolves_alert_and_resets_deficit(self, client):
        phone = "+254744000010"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)

        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1", "serviceCode": "*384*1234#"})
        assert resp.data == b"END Thank you. Marked as watered."

        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            assert plot.water_deficit_mm == 0.0
            assert plot.alert_active is False
            active_alerts = (
                session.query(AlertRecord)
                .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == "ACTIVE")
                .count()
            )
            assert active_alerts == 0

    def test_resolving_via_ussd_is_visible_on_the_web_dashboard(self, client):
        """The whole point of sharing apply_farmer_reply: USSD and web must
        agree on the same state, not maintain two versions of the truth."""
        phone = "+254744000011"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)

        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1", "serviceCode": "*384*1234#"})

        from tests.conftest import get_csrf_token

        resp = client.get("/farmer/register")
        token = get_csrf_token(resp.get_data(as_text=True))
        client.post("/farmer/register", data={
            "csrf_token": token, "full_name": "Web Login", "phone_number": phone,
            "password": "webpass123", "confirm_password": "webpass123",
        })
        resp = client.get("/farmer/login")
        token = get_csrf_token(resp.get_data(as_text=True))
        client.post("/farmer/login", data={"csrf_token": token, "phone_number": phone, "password": "webpass123"})

        resp = client.get(f"/farmer/plots/{plot_id}")
        assert b"mark as done" not in resp.data  # already resolved via USSD


class TestRemindMeLaterViaUSSD:
    def test_defer_leaves_alert_active(self, client):
        phone = "+254744000020"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)

        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "2", "serviceCode": "*384*1234#"})
        assert resp.data == b"END Got it, we'll remind you again."

        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            assert plot.alert_active is True


class TestUpdatePlotSizeViaUSSD:
    def test_update_from_no_alert_status_check(self, client):
        phone = "+254744000030"
        plot_id = _register_plot_via_ussd(client, phone)

        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1", "serviceCode": "*384*1234#"})
        assert resp.data.decode().startswith("CON Select plot size")

        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1*3", "serviceCode": "*384*1234#"})
        assert resp.data.decode().startswith("END Plot size updated to 0.5 acre")

        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            assert plot.plot_size_acres == 0.5

    def test_update_from_active_alert_status_check(self, client):
        phone = "+254744000031"
        plot_id = _register_plot_via_ussd(client, phone)
        _drive_to_alert(plot_id)

        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "3", "serviceCode": "*384*1234#"})
        assert resp.data.decode().startswith("CON Select plot size")

        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "3*1", "serviceCode": "*384*1234#"})
        assert resp.data.decode().startswith("END Plot size updated to 0.125 acre")

        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            assert plot.plot_size_acres == 0.125
            # correcting the size mid-alert must not silently clear the alert
            assert plot.alert_active is True

    def test_invalid_size_choice_ends_gracefully(self, client):
        phone = "+254744000032"
        plot_id = _register_plot_via_ussd(client, phone)
        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "", "serviceCode": "*384*1234#"})
        client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1", "serviceCode": "*384*1234#"})
        resp = client.post("/ussd", data={"sessionId": "s2", "phoneNumber": phone, "text": "1*9", "serviceCode": "*384*1234#"})
        assert resp.data == b"END Invalid option. Please dial in again."
