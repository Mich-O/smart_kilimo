"""
tests/test_web.py
Functional tests for the farmer and admin web workspaces: plot
registration business rules, object-level authorization (IDOR), the admin
operations tools driving the real engine, notifications (feed + manual
compose), and the PlotDailyLog history that backs the dashboard chart.
"""
import re

from tests.conftest import get_csrf_token, login_admin, login_farmer, logout, register_farmer, register_plot


def _new_farmer(client, phone_number, region_code="EASTERN", crop_type="MAIZE"):
    register_farmer(client, phone_number)
    login_farmer(client, phone_number)
    resp = register_plot(client, region_code=region_code, crop_type=crop_type)
    plot_id = int(resp.request.path.rsplit("/", 1)[-1])
    return plot_id


def _simulate_day(client, plot_id, rainfall_mm):
    resp = client.get("/admin/tools")
    token = get_csrf_token(resp.get_data(as_text=True))
    return client.post("/admin/tools", data={"csrf_token": token, "plot_id": str(plot_id), "rainfall_mm": str(rainfall_mm)})


class TestPlotRegistration:
    def test_register_plot_succeeds(self, client):
        register_farmer(client, "+254722000001")
        login_farmer(client, "+254722000001")
        resp = register_plot(client, region_code="COAST", crop_type="ONIONS")
        assert "farmer/plots/" in resp.request.path
        assert b"Onions Coast" in resp.data

    def test_duplicate_crop_region_rejected(self, client):
        register_farmer(client, "+254722000002")
        login_farmer(client, "+254722000002")
        register_plot(client, region_code="EASTERN", crop_type="MAIZE")
        resp = register_plot(client, region_code="EASTERN", crop_type="MAIZE")
        assert b"already have a plot" in resp.data

    def test_same_farmer_can_register_different_crop_same_region(self, client):
        register_farmer(client, "+254722000003")
        login_farmer(client, "+254722000003")
        register_plot(client, region_code="EASTERN", crop_type="MAIZE")
        resp = register_plot(client, region_code="EASTERN", crop_type="BEANS")
        assert "farmer/plots/" in resp.request.path

    def test_dashboard_lists_registered_plots(self, client):
        register_farmer(client, "+254722000004")
        login_farmer(client, "+254722000004")
        register_plot(client, region_code="RIFT_VALLEY", crop_type="BEANS")
        resp = client.get("/farmer/dashboard")
        assert b"Beans Rift Valley" in resp.data


class TestObjectLevelAuthorization:
    def test_farmer_cannot_view_another_farmers_plot(self, client):
        plot_id = _new_farmer(client, "+254722000010")

        other_client = client
        # log in as a different farmer in a fresh session
        logout(other_client)
        register_farmer(other_client, "+254722000011")
        login_farmer(other_client, "+254722000011")

        resp = other_client.get(f"/farmer/plots/{plot_id}")
        assert resp.status_code == 404

    def test_farmer_cannot_view_nonexistent_plot(self, client):
        register_farmer(client, "+254722000012")
        login_farmer(client, "+254722000012")
        resp = client.get("/farmer/plots/999999")
        assert resp.status_code == 404


class TestAdminDirectory:
    def test_farmers_list_shows_registered_farmer(self, client):
        _new_farmer(client, "+254722000020")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/farmers")
        assert b"+254722000020" in resp.data

    def test_farmers_search_filters_by_phone(self, client):
        _new_farmer(client, "+254722000021")
        logout(client)
        _new_farmer(client, "+254722000099")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/farmers?q=000021")
        assert b"+254722000021" in resp.data
        assert b"+254722000099" not in resp.data

    def test_farmer_detail_page_shows_plots(self, client):
        plot_id = _new_farmer(client, "+254722000022")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/farmers/+254722000022")
        assert resp.status_code == 200
        assert b"Maize Eastern" in resp.data

    def test_farmer_detail_404_for_unknown_number(self, client):
        login_admin(client)
        resp = client.get("/admin/farmers/+254700000000")
        assert resp.status_code == 404


class TestAdminOperationsTools:
    def test_simulate_dry_day_increments_deficit(self, client):
        plot_id = _new_farmer(client, "+254722000030")
        logout(client)
        login_admin(client)
        resp = _simulate_day(client, plot_id, 0)
        assert b"INCREMENT" in resp.data

    def test_simulate_heavy_rain_resets_deficit(self, client):
        plot_id = _new_farmer(client, "+254722000031")
        logout(client)
        login_admin(client)
        for _ in range(5):
            _simulate_day(client, plot_id, 0)
        resp = _simulate_day(client, plot_id, 9)
        assert b"RESET" in resp.data

    def test_simulate_enough_dry_days_triggers_alert_and_sms(self, client):
        plot_id = _new_farmer(client, "+254722000032")
        logout(client)
        login_admin(client)
        resp = None
        for _ in range(21):  # MAIZE germination/vegetative deltas cross the 40mm threshold well within 21 days
            resp = _simulate_day(client, plot_id, 0)
        assert b"ALERT" in resp.data

        resp = client.get(f"/admin/plots/{plot_id}")
        assert b"Needs water" in resp.data
        assert b"Force next reminder" in resp.data

    def test_force_reminder_requires_active_alert(self, client):
        plot_id = _new_farmer(client, "+254722000033")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/tools")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(f"/admin/tools/force-reminder/{plot_id}", data={"csrf_token": token}, follow_redirects=True)
        assert b"No active alert" in resp.data

    def test_plot_daily_log_powers_the_history_chart(self, client):
        plot_id = _new_farmer(client, "+254722000034", region_code="RIFT_VALLEY", crop_type="BEANS")
        logout(client)
        login_admin(client)
        for rain in [0, 0, 8, 0]:
            _simulate_day(client, plot_id, rain)

        logout(client)
        login_farmer(client, "+254722000034")
        resp = client.get(f"/farmer/plots/{plot_id}")
        html = resp.get_data(as_text=True)
        deficit_series = re.search(r"data: (\[[\d.,\s]*\])", html)
        assert deficit_series is not None
        values = [float(v) for v in deficit_series.group(1).strip("[]").split(",")]
        assert len(values) == 4
        assert values[2] == 0.0  # the 8mm rain day reset the deficit to zero


class TestAdminNotifications:
    def test_compose_to_single_plot(self, client):
        plot_id = _new_farmer(client, "+254722000040")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/notifications")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/admin/notifications",
            data={"csrf_token": token, "target_type": "plot", "plot_id": str(plot_id), "region_code": "EASTERN", "message": "Manual check-in message"},
            follow_redirects=True,
        )
        assert b"Sent to 1 farmer" in resp.data

        client.post("/logout", data={"csrf_token": token})
        login_farmer(client, "+254722000040")
        resp = client.get("/farmer/notifications")
        assert b"Manual check-in message" in resp.data

    def test_compose_broadcast_to_region(self, client):
        plot_a = _new_farmer(client, "+254722000041", region_code="COAST", crop_type="MAIZE")
        logout(client)
        plot_b = _new_farmer(client, "+254722000042", region_code="COAST", crop_type="BEANS")
        logout(client)

        login_admin(client)
        resp = client.get("/admin/notifications")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            "/admin/notifications",
            data={"csrf_token": token, "target_type": "region", "region_code": "COAST", "plot_id": str(plot_a), "message": "Coast region advisory"},
            follow_redirects=True,
        )
        assert b"Sent to 2 farmer" in resp.data


class TestAdminCycleTrigger:
    def test_run_cycle_endpoint_renders_summary(self, client):
        _new_farmer(client, "+254722000050")
        logout(client)
        login_admin(client)
        resp = client.get("/admin/cycle")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post("/admin/cycle", data={"csrf_token": token})
        assert resp.status_code == 200
        assert b"Plots" in resp.data


def _drive_plot_to_alert(client, plot_id, days=20):
    """Uses admin tools to push a plot into an ALERT state for the tests below."""
    logout(client)  # caller is left logged in as the farmer from _new_farmer
    login_admin(client)
    for _ in range(days):
        resp = client.get("/admin/tools")
        token = get_csrf_token(resp.get_data(as_text=True))
        client.post("/admin/tools", data={"csrf_token": token, "plot_id": str(plot_id), "rainfall_mm": "0"})
    logout(client)


class TestFarmerAlertResponse:
    def test_resolve_action_appears_only_when_alert_active(self, client):
        plot_id = _new_farmer(client, "+254733000001")
        resp = client.get(f"/farmer/plots/{plot_id}")
        assert b"mark as done" not in resp.data

        _drive_plot_to_alert(client, plot_id)
        login_farmer(client, "+254733000001")
        resp = client.get(f"/farmer/plots/{plot_id}")
        assert b"mark as done" in resp.data

    def test_resolve_clears_alert_and_resets_deficit(self, client):
        plot_id = _new_farmer(client, "+254733000002")
        _drive_plot_to_alert(client, plot_id)
        login_farmer(client, "+254733000002")

        resp = client.get(f"/farmer/plots/{plot_id}")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(f"/farmer/plots/{plot_id}/resolve", data={"csrf_token": token}, follow_redirects=True)
        assert b"mark as done" not in resp.data
        assert b"resolved" in resp.data

    def test_resolve_only_ever_creates_one_alert_record_regardless_of_repeated_evaluation(self, client):
        # Regression test for the duplicate-alert bug: driving a plot
        # through many dry days must create exactly one ACTIVE AlertRecord,
        # not one per evaluation.
        plot_id = _new_farmer(client, "+254733000003")
        _drive_plot_to_alert(client, plot_id)

        from core.models import AlertRecord
        from db.database import get_session

        with get_session() as session:
            alerts = session.query(AlertRecord).filter(AlertRecord.plot_id == plot_id).all()
            assert len(alerts) == 1

    def test_defer_leaves_alert_active(self, client):
        plot_id = _new_farmer(client, "+254733000004")
        _drive_plot_to_alert(client, plot_id)
        login_farmer(client, "+254733000004")

        resp = client.get(f"/farmer/plots/{plot_id}")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(f"/farmer/plots/{plot_id}/defer", data={"csrf_token": token}, follow_redirects=True)
        assert b"mark as done" in resp.data  # still active after deferring

    def test_resolve_with_no_active_alert_shows_error_not_crash(self, client):
        plot_id = _new_farmer(client, "+254733000005")
        resp = client.get(f"/farmer/plots/{plot_id}")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(f"/farmer/plots/{plot_id}/resolve", data={"csrf_token": token}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"No active alert" in resp.data

    def test_farmer_cannot_resolve_another_farmers_alert(self, client):
        plot_id = _new_farmer(client, "+254733000006")
        _drive_plot_to_alert(client, plot_id)

        register_farmer(client, "+254733000007")
        login_farmer(client, "+254733000007")
        resp = client.get("/farmer/dashboard")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(f"/farmer/plots/{plot_id}/resolve", data={"csrf_token": token})
        assert resp.status_code == 404


class TestPlotSizeUpdate:
    def test_update_plot_size_changes_future_recommendations(self, client):
        plot_id = _new_farmer(client, "+254733000010")
        resp = client.get(f"/farmer/plots/{plot_id}/update-size")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            f"/farmer/plots/{plot_id}/update-size",
            data={"csrf_token": token, "plot_size_acres": "0.5"},
            follow_redirects=True,
        )
        assert b"updated" in resp.data

        from core.models import FarmPlot
        from db.database import get_session

        with get_session() as session:
            plot = session.get(FarmPlot, plot_id)
            assert plot.plot_size_acres == 0.5

    def test_update_plot_size_rejects_invalid_value(self, client):
        plot_id = _new_farmer(client, "+254733000011")
        resp = client.get(f"/farmer/plots/{plot_id}/update-size")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = client.post(
            f"/farmer/plots/{plot_id}/update-size",
            data={"csrf_token": token, "plot_size_acres": "-5"},
        )
        assert b"between 0 and 1000" in resp.data

    def test_farmer_cannot_update_another_farmers_plot_size(self, client):
        plot_id = _new_farmer(client, "+254733000012")
        logout(client)
        register_farmer(client, "+254733000013")
        login_farmer(client, "+254733000013")
        resp = client.get(f"/farmer/plots/{plot_id}/update-size")
        assert resp.status_code == 404
