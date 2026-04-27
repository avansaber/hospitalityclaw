"""L1 tests for HospitalityClaw guests + reservations domains.

Covers:
  - Guests: update, get, list, add-preference, list-preferences, history, loyalty
  - Reservations: add, update, get, list, confirm, cancel
  - Rate plans: add, list
  - Availability check
  - Group blocks: add, list
  - Reservation forecast report

Note: hospitality-add-guest is NOT tested here because it calls
cross_skill.create_customer (subprocess). Guest rows are seeded directly.
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_customer, seed_guest_ext, seed_naming_series,
    seed_room_type, seed_room, seed_reservation,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Guest Tests ──────────────────────────────────────────────────────────


class TestUpdateGuest:
    """hospitality-update-guest"""

    def test_update_guest_vip(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-guest"], conn,
            ns(guest_id=env["guest_id"], vip_level="gold"),
        )
        assert is_ok(result), result
        assert "vip_level" in result["updated_fields"]

    def test_update_guest_nationality(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-guest"], conn,
            ns(guest_id=env["guest_id"], nationality="US"),
        )
        assert is_ok(result), result
        assert "nationality" in result["updated_fields"]

    def test_update_guest_core_name(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-guest"], conn,
            ns(guest_id=env["guest_id"], customer_name="Alice Updated"),
        )
        assert is_ok(result), result
        assert "customer_name" in result["updated_fields"]

    def test_update_guest_no_fields(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-guest"], conn,
            ns(guest_id=env["guest_id"]),
        )
        assert is_error(result)

    def test_update_guest_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-guest"], conn,
            ns(guest_id="nonexistent", vip_level="gold"),
        )
        assert is_error(result)


class TestGetGuest:
    """hospitality-get-guest"""

    def test_get_guest_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-guest"], conn,
            ns(guest_id=env["guest_id"]),
        )
        assert is_ok(result), result
        assert result["id"] == env["guest_id"]
        assert "customer_name" in result
        assert "preference_count" in result
        assert "reservation_count" in result

    def test_get_guest_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-guest"], conn,
            ns(guest_id="nonexistent"),
        )
        assert is_error(result)


class TestListGuests:
    """hospitality-list-guests"""

    def test_list_guests(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-guests"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1

    def test_list_guests_by_vip(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-guests"], conn,
            ns(company_id=env["company_id"], vip_level="regular"),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestGuestPreferences:
    """hospitality-add-guest-preference / hospitality-list-guest-preferences"""

    def test_add_preference_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-guest-preference"], conn,
            ns(guest_id=env["guest_id"], company_id=env["company_id"],
               preference_type="room", preference_value="High floor"),
        )
        assert is_ok(result), result
        assert result["preference_type"] == "room"
        assert result["preference_value"] == "High floor"

    def test_add_preference_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-guest-preference"], conn,
            ns(guest_id=env["guest_id"], company_id=env["company_id"],
               preference_type="invalid", preference_value="X"),
        )
        assert is_error(result)

    def test_list_preferences(self, conn, env):
        # Seed one
        call_action(
            ACTIONS["hospitality-add-guest-preference"], conn,
            ns(guest_id=env["guest_id"], company_id=env["company_id"],
               preference_type="pillow", preference_value="Firm"),
        )
        result = call_action(
            ACTIONS["hospitality-list-guest-preferences"], conn,
            ns(guest_id=env["guest_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestGuestHistory:
    """hospitality-guest-history"""

    def test_guest_history(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-guest-history"], conn,
            ns(guest_id=env["guest_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1  # reservation from env


class TestLoyaltySummary:
    """hospitality-loyalty-summary"""

    def test_loyalty_summary(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-loyalty-summary"], conn,
            ns(guest_id=env["guest_id"]),
        )
        assert is_ok(result), result
        assert result["vip_level"] == "regular"
        assert result["report_type"] == "loyalty_summary"


# ── Reservation Tests ────────────────────────────────────────────────────


class TestAddReservation:
    """hospitality-add-reservation"""

    def test_add_reservation_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-reservation"], conn,
            ns(company_id=env["company_id"],
               guest_id=env["guest_id"],
               room_type_id=env["std_room_type_id"],
               check_in_date="2026-05-01",
               check_out_date="2026-05-04",
               rate_amount="150.00"),
        )
        assert is_ok(result), result
        assert result["reservation_status"] == "pending"
        assert result["nights"] == 3
        assert result["total_amount"] == "450.00"

    def test_add_reservation_dates_invalid(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-reservation"], conn,
            ns(company_id=env["company_id"],
               guest_id=env["guest_id"],
               room_type_id=env["std_room_type_id"],
               check_in_date="2026-05-04",
               check_out_date="2026-05-01",
               rate_amount="150.00"),
        )
        assert is_error(result)

    def test_add_reservation_missing_guest(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-reservation"], conn,
            ns(company_id=env["company_id"],
               guest_id=None,
               room_type_id=env["std_room_type_id"],
               check_in_date="2026-05-01",
               check_out_date="2026-05-04",
               rate_amount="150.00"),
        )
        assert is_error(result)

    def test_add_reservation_missing_rate(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-reservation"], conn,
            ns(company_id=env["company_id"],
               guest_id=env["guest_id"],
               room_type_id=env["std_room_type_id"],
               check_in_date="2026-05-01",
               check_out_date="2026-05-04",
               rate_amount=None),
        )
        assert is_error(result)


class TestGetReservation:
    """hospitality-get-reservation"""

    def test_get_reservation_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_ok(result), result
        assert result["id"] == env["reservation_id"]
        assert "guest_name" in result
        assert "room_type_name" in result
        assert "folio_total" in result

    def test_get_reservation_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-reservation"], conn,
            ns(reservation_id="nonexistent"),
        )
        assert is_error(result)


class TestListReservations:
    """hospitality-list-reservations"""

    def test_list_reservations(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-reservations"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1

    def test_list_reservations_by_status(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-reservations"], conn,
            ns(company_id=env["company_id"],
               reservation_status="pending"),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestConfirmReservation:
    """hospitality-confirm-reservation"""

    def test_confirm_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-confirm-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_ok(result), result
        assert result["reservation_status"] == "confirmed"

    def test_confirm_already_confirmed(self, conn, env):
        # Confirm first
        call_action(
            ACTIONS["hospitality-confirm-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        # Try again
        result = call_action(
            ACTIONS["hospitality-confirm-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_error(result)


class TestCancelReservation:
    """hospitality-cancel-reservation"""

    def test_cancel_pending(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-cancel-reservation"], conn,
            ns(reservation_id=env["reservation_id"],
               reason="Changed plans"),
        )
        assert is_ok(result), result
        assert result["reservation_status"] == "cancelled"

    def test_cancel_already_cancelled(self, conn, env):
        # Cancel first
        call_action(
            ACTIONS["hospitality-cancel-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        # Try again
        result = call_action(
            ACTIONS["hospitality-cancel-reservation"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_error(result)


class TestUpdateReservation:
    """hospitality-update-reservation"""

    def test_update_special_requests(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-reservation"], conn,
            ns(reservation_id=env["reservation_id"],
               special_requests="Late arrival"),
        )
        assert is_ok(result), result
        assert "special_requests" in result["updated_fields"]

    def test_update_adults(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-reservation"], conn,
            ns(reservation_id=env["reservation_id"], adults="2"),
        )
        assert is_ok(result), result
        assert "adults" in result["updated_fields"]


# ── Rate Plan Tests ──────────────────────────────────────────────────────


class TestAddRatePlan:
    """hospitality-add-rate-plan"""

    def test_add_rate_plan_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-rate-plan"], conn,
            ns(company_id=env["company_id"],
               name="Summer Special",
               room_type_id=env["std_room_type_id"],
               rate_amount="120.00",
               start_date="2026-06-01",
               end_date="2026-08-31",
               rate_type="seasonal"),
        )
        assert is_ok(result), result
        assert result["name"] == "Summer Special"
        assert result["rate_type"] == "seasonal"

    def test_add_rate_plan_missing_dates(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-rate-plan"], conn,
            ns(company_id=env["company_id"],
               name="Plan X",
               room_type_id=env["std_room_type_id"],
               rate_amount="100.00",
               start_date=None, end_date=None),
        )
        assert is_error(result)


class TestListRatePlans:
    """hospitality-list-rate-plans"""

    def test_list_rate_plans(self, conn, env):
        # Create one first
        call_action(
            ACTIONS["hospitality-add-rate-plan"], conn,
            ns(company_id=env["company_id"],
               name="Weekend Rate",
               room_type_id=env["std_room_type_id"],
               rate_amount="130.00",
               start_date="2026-01-01", end_date="2026-12-31"),
        )
        result = call_action(
            ACTIONS["hospitality-list-rate-plans"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


# ── Availability Check ───────────────────────────────────────────────────


class TestCheckAvailability:
    """hospitality-check-availability"""

    def test_check_availability(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-check-availability"], conn,
            ns(company_id=env["company_id"],
               room_type_id=env["std_room_type_id"],
               check_in_date="2026-06-01",
               check_out_date="2026-06-03"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "availability"
        assert "available" in result
        assert "total_rooms" in result


# ── Group Block Tests ────────────────────────────────────────────────────


class TestAddGroupBlock:
    """hospitality-add-group-block"""

    def test_add_group_block_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-group-block"], conn,
            ns(company_id=env["company_id"],
               name="Conference Group",
               room_type_id=env["std_room_type_id"],
               rooms_blocked="10",
               check_in_date="2026-07-01",
               check_out_date="2026-07-05",
               contact_name="Bob Organizer",
               contact_email="bob@conf.com",
               rate_amount="140.00"),
        )
        assert is_ok(result), result
        assert result["block_status"] == "tentative"

    def test_add_group_block_missing_rooms(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-group-block"], conn,
            ns(company_id=env["company_id"],
               name="Group X",
               room_type_id=env["std_room_type_id"],
               rooms_blocked=None,
               check_in_date="2026-07-01",
               check_out_date="2026-07-05"),
        )
        assert is_error(result)


class TestListGroupBlocks:
    """hospitality-list-group-blocks"""

    def test_list_group_blocks(self, conn, env):
        # Create one
        call_action(
            ACTIONS["hospitality-add-group-block"], conn,
            ns(company_id=env["company_id"],
               name="Wedding Block",
               room_type_id=env["dlx_room_type_id"],
               rooms_blocked="5",
               check_in_date="2026-08-01",
               check_out_date="2026-08-03"),
        )
        result = call_action(
            ACTIONS["hospitality-list-group-blocks"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


# ── Reservation Forecast ─────────────────────────────────────────────────


class TestReservationForecastReport:
    """hospitality-reservation-forecast-report"""

    def test_forecast_report(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-reservation-forecast-report"], conn,
            ns(company_id=env["company_id"],
               start_date="2026-04-01",
               end_date="2026-04-30"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "reservation_forecast"
        assert "upcoming_reservations" in result
        assert "forecasted_revenue" in result
