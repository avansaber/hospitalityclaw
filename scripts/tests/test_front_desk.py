"""L1 tests for HospitalityClaw front_desk domain.

Covers:
  - Check-in / Check-out workflow
  - Room assignment
  - Guest requests: add, list, complete
  - Late checkout
  - Room move
  - Folio charges: add, list
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_reservation, seed_room,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ---------------------------------------------------------------------------
# Helper: set reservation to confirmed (needed for check-in)
# ---------------------------------------------------------------------------
def _confirm(conn, reservation_id):
    return call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=reservation_id),
    )


def _check_in(conn, reservation_id, room_id):
    return call_action(
        ACTIONS["hospitality-check-in"], conn,
        ns(reservation_id=reservation_id, room_id=room_id),
    )


# ── Check-In Tests ───────────────────────────────────────────────────────


class TestCheckIn:
    """hospitality-check-in"""

    def test_check_in_confirmed(self, conn, env):
        _confirm(conn, env["reservation_id"])
        result = _check_in(conn, env["reservation_id"], env["room_101_id"])
        assert is_ok(result), result
        assert result["reservation_status"] == "checked_in"
        assert result["room_number"] == "101"

    def test_check_in_pending_also_works(self, conn, env):
        # Create a separate pending reservation
        res_id = seed_reservation(
            conn, env["guest_id"], env["std_room_type_id"],
            env["company_id"],
            check_in="2026-05-10", check_out="2026-05-12",
            rate_amount="150.00", reservation_status="pending",
        )
        result = _check_in(conn, res_id, env["room_102_id"])
        assert is_ok(result), result
        assert result["reservation_status"] == "checked_in"

    def test_check_in_room_not_available(self, conn, env):
        # Occupy room_101 first
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])

        # Create new reservation and try same room
        res_id = seed_reservation(
            conn, env["guest_id"], env["std_room_type_id"],
            env["company_id"],
            check_in="2026-05-15", check_out="2026-05-17",
            rate_amount="150.00", reservation_status="confirmed",
        )
        result = _check_in(conn, res_id, env["room_101_id"])
        assert is_error(result)

    def test_check_in_missing_room(self, conn, env):
        _confirm(conn, env["reservation_id"])
        result = call_action(
            ACTIONS["hospitality-check-in"], conn,
            ns(reservation_id=env["reservation_id"], room_id=None),
        )
        assert is_error(result)


# ── Check-Out Tests ──────────────────────────────────────────────────────


class TestCheckOut:
    """hospitality-check-out"""

    def test_check_out_ok(self, conn, env):
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])
        result = call_action(
            ACTIONS["hospitality-check-out"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_ok(result), result
        assert result["reservation_status"] == "checked_out"
        assert "folio_total" in result

    def test_check_out_not_checked_in(self, conn, env):
        # Reservation is still pending
        result = call_action(
            ACTIONS["hospitality-check-out"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_error(result)


# ── Assign Room Tests ────────────────────────────────────────────────────


class TestAssignRoom:
    """hospitality-assign-room"""

    def test_assign_room_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-assign-room"], conn,
            ns(reservation_id=env["reservation_id"],
               room_id=env["room_102_id"]),
        )
        assert is_ok(result), result
        assert result["room_number"] == "102"

    def test_assign_room_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-assign-room"], conn,
            ns(reservation_id=env["reservation_id"],
               room_id="nonexistent"),
        )
        assert is_error(result)


# ── Guest Request Tests ──────────────────────────────────────────────────


class TestAddGuestRequest:
    """hospitality-add-guest-request"""

    def test_add_request_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="amenity",
               description="Extra towels please",
               priority="normal"),
        )
        assert is_ok(result), result
        assert result["request_type"] == "amenity"
        assert result["request_status"] == "open"

    def test_add_request_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="invalid",
               description="Something"),
        )
        assert is_error(result)

    def test_add_request_missing_description(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="food",
               description=None),
        )
        assert is_error(result)


class TestListGuestRequests:
    """hospitality-list-guest-requests"""

    def test_list_requests(self, conn, env):
        # Create one
        call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="housekeeping",
               description="Room cleanup"),
        )
        result = call_action(
            ACTIONS["hospitality-list-guest-requests"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestCompleteGuestRequest:
    """hospitality-complete-guest-request"""

    def test_complete_ok(self, conn, env):
        # Create a request
        req = call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="maintenance",
               description="Fix AC"),
        )
        assert is_ok(req), req
        req_id = req["id"]

        result = call_action(
            ACTIONS["hospitality-complete-guest-request"], conn,
            ns(request_id=req_id),
        )
        assert is_ok(result), result
        assert result["request_status"] == "completed"

    def test_complete_already_done(self, conn, env):
        req = call_action(
            ACTIONS["hospitality-add-guest-request"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               request_type="other",
               description="Something"),
        )
        req_id = req["id"]
        call_action(
            ACTIONS["hospitality-complete-guest-request"], conn,
            ns(request_id=req_id),
        )
        result = call_action(
            ACTIONS["hospitality-complete-guest-request"], conn,
            ns(request_id=req_id),
        )
        assert is_error(result)


# ── Late Checkout Tests ──────────────────────────────────────────────────


class TestLateCheckout:
    """hospitality-late-checkout"""

    def test_late_checkout_ok(self, conn, env):
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])
        result = call_action(
            ACTIONS["hospitality-late-checkout"], conn,
            ns(reservation_id=env["reservation_id"],
               new_checkout_date="2026-04-05"),
        )
        assert is_ok(result), result
        assert result["new_checkout_date"] == "2026-04-05"
        assert result["nights"] == 4  # Apr 1 to Apr 5

    def test_late_checkout_not_checked_in(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-late-checkout"], conn,
            ns(reservation_id=env["reservation_id"],
               new_checkout_date="2026-04-05"),
        )
        assert is_error(result)

    def test_late_checkout_earlier_date(self, conn, env):
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])
        # Original checkout is 2026-04-03, try 2026-04-02
        result = call_action(
            ACTIONS["hospitality-late-checkout"], conn,
            ns(reservation_id=env["reservation_id"],
               new_checkout_date="2026-04-02"),
        )
        assert is_error(result)


# ── Room Move Tests ──────────────────────────────────────────────────────


class TestRoomMove:
    """hospitality-room-move"""

    def test_room_move_ok(self, conn, env):
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])
        result = call_action(
            ACTIONS["hospitality-room-move"], conn,
            ns(reservation_id=env["reservation_id"],
               new_room_id=env["room_102_id"],
               reason="Guest preference"),
        )
        assert is_ok(result), result
        assert result["new_room_id"] == env["room_102_id"]
        assert result["old_room_id"] == env["room_101_id"]

    def test_room_move_target_occupied(self, conn, env):
        # Check in to room 101
        _confirm(conn, env["reservation_id"])
        _check_in(conn, env["reservation_id"], env["room_101_id"])

        # Occupy room 102 with another reservation
        res2 = seed_reservation(
            conn, env["guest_id"], env["std_room_type_id"],
            env["company_id"],
            check_in="2026-05-20", check_out="2026-05-22",
            rate_amount="150.00", reservation_status="confirmed",
        )
        _check_in(conn, res2, env["room_102_id"])

        # Try moving to occupied room
        result = call_action(
            ACTIONS["hospitality-room-move"], conn,
            ns(reservation_id=env["reservation_id"],
               new_room_id=env["room_102_id"]),
        )
        assert is_error(result)


# ── Folio Charge Tests ───────────────────────────────────────────────────


class TestAddCharge:
    """hospitality-add-charge"""

    def test_add_charge_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-charge"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               charge_type="room",
               description="Room charge night 1",
               amount="150.00"),
        )
        assert is_ok(result), result
        assert result["charge_type"] == "room"
        assert result["amount"] == "150.00"

    def test_add_charge_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-charge"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               charge_type="invalid",
               description="X", amount="10.00"),
        )
        assert is_error(result)

    def test_add_charge_missing_amount(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-charge"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               charge_type="food",
               description="Dinner", amount=None),
        )
        assert is_error(result)


class TestListFolioCharges:
    """hospitality-list-folio-charges"""

    def test_list_charges(self, conn, env):
        # Add a charge first
        call_action(
            ACTIONS["hospitality-add-charge"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               charge_type="minibar",
               description="Drinks", amount="25.00"),
        )
        result = call_action(
            ACTIONS["hospitality-list-folio-charges"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1
        assert "folio_total" in result
