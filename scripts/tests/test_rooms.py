"""L1 tests for HospitalityClaw rooms domain.

Covers:
  - Room types: add, update, list
  - Rooms: add, update, get, list, update-room-status
  - Amenities: add, list, assign
  - Room availability report
"""
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_company, seed_naming_series, seed_room_type, seed_room,
)

# Load ACTIONS dict from db_query.py
_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Room Type Tests ──────────────────────────────────────────────────────


class TestAddRoomType:
    """hospitality-add-room-type"""

    def test_add_room_type_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-type"], conn,
            ns(company_id=env["company_id"], name="Suite",
               base_rate="500.00", max_occupancy="4"),
        )
        assert is_ok(result), result
        assert result["name"] == "Suite"
        assert "id" in result

    def test_add_room_type_missing_name(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-type"], conn,
            ns(company_id=env["company_id"], name=None,
               base_rate="100.00", max_occupancy="2"),
        )
        assert is_error(result)

    def test_add_room_type_missing_rate(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-type"], conn,
            ns(company_id=env["company_id"], name="Suite",
               base_rate=None, max_occupancy="2"),
        )
        assert is_error(result)

    def test_add_room_type_missing_company(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-type"], conn,
            ns(company_id=None, name="Suite",
               base_rate="100.00", max_occupancy="2"),
        )
        assert is_error(result)


class TestUpdateRoomType:
    """hospitality-update-room-type"""

    def test_update_room_type_name(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-type"], conn,
            ns(room_type_id=env["std_room_type_id"], name="Standard Plus"),
        )
        assert is_ok(result), result
        assert "name" in result["updated_fields"]

    def test_update_room_type_rate(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-type"], conn,
            ns(room_type_id=env["std_room_type_id"], base_rate="175.00"),
        )
        assert is_ok(result), result
        assert "base_rate" in result["updated_fields"]

    def test_update_room_type_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-type"], conn,
            ns(room_type_id="nonexistent", name="X"),
        )
        assert is_error(result)

    def test_update_room_type_no_fields(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-type"], conn,
            ns(room_type_id=env["std_room_type_id"]),
        )
        assert is_error(result)


class TestListRoomTypes:
    """hospitality-list-room-types"""

    def test_list_room_types(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-room-types"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 2  # Standard + Deluxe from env


# ── Room Tests ───────────────────────────────────────────────────────────


class TestAddRoom:
    """hospitality-add-room"""

    def test_add_room_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room"], conn,
            ns(company_id=env["company_id"],
               room_number="301",
               room_type_id=env["std_room_type_id"],
               floor="3"),
        )
        assert is_ok(result), result
        assert result["room_number"] == "301"
        assert result["room_status"] == "available"

    def test_add_room_duplicate_number(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room"], conn,
            ns(company_id=env["company_id"],
               room_number="101",  # already exists in env
               room_type_id=env["std_room_type_id"],
               floor="1"),
        )
        assert is_error(result)

    def test_add_room_missing_room_number(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room"], conn,
            ns(company_id=env["company_id"],
               room_number=None,
               room_type_id=env["std_room_type_id"]),
        )
        assert is_error(result)

    def test_add_room_invalid_room_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room"], conn,
            ns(company_id=env["company_id"],
               room_number="999",
               room_type_id="nonexistent"),
        )
        assert is_error(result)


class TestUpdateRoom:
    """hospitality-update-room"""

    def test_update_room_floor(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room"], conn,
            ns(room_id=env["room_101_id"], floor="2"),
        )
        assert is_ok(result), result
        assert "floor" in result["updated_fields"]

    def test_update_room_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room"], conn,
            ns(room_id="nonexistent", floor="2"),
        )
        assert is_error(result)


class TestGetRoom:
    """hospitality-get-room"""

    def test_get_room_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-room"], conn,
            ns(room_id=env["room_101_id"]),
        )
        assert is_ok(result), result
        assert result["room_number"] == "101"
        assert "room_type_name" in result
        assert "amenities" in result

    def test_get_room_not_found(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-get-room"], conn,
            ns(room_id="nonexistent"),
        )
        assert is_error(result)


class TestListRooms:
    """hospitality-list-rooms"""

    def test_list_rooms_all(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-rooms"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 3  # 101, 102, 201

    def test_list_rooms_by_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-rooms"], conn,
            ns(company_id=env["company_id"],
               room_type_id=env["dlx_room_type_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] == 1  # only 201

    def test_list_rooms_by_floor(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-rooms"], conn,
            ns(company_id=env["company_id"], floor="1"),
        )
        assert is_ok(result), result
        assert result["total_count"] == 2  # 101 and 102


class TestUpdateRoomStatus:
    """hospitality-update-room-status"""

    def test_update_room_status_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-status"], conn,
            ns(room_id=env["room_101_id"], room_status="maintenance"),
        )
        assert is_ok(result), result
        assert result["room_status"] == "maintenance"

    def test_update_room_status_invalid(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-update-room-status"], conn,
            ns(room_id=env["room_101_id"], room_status="invalid_status"),
        )
        assert is_error(result)


# ── Amenity Tests ────────────────────────────────────────────────────────


class TestAddAmenity:
    """hospitality-add-amenity"""

    def test_add_amenity_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-amenity"], conn,
            ns(company_id=env["company_id"], name="WiFi",
               amenity_type="room"),
        )
        assert is_ok(result), result
        assert result["name"] == "WiFi"
        assert result["amenity_type"] == "room"

    def test_add_amenity_missing_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-amenity"], conn,
            ns(company_id=env["company_id"], name="WiFi",
               amenity_type=None),
        )
        assert is_error(result)

    def test_add_amenity_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-amenity"], conn,
            ns(company_id=env["company_id"], name="WiFi",
               amenity_type="invalid"),
        )
        assert is_error(result)


class TestListAmenities:
    """hospitality-list-amenities"""

    def test_list_amenities_empty(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-amenities"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] == 0


class TestAssignAmenity:
    """hospitality-assign-amenity"""

    def test_assign_amenity_ok(self, conn, env):
        # First create an amenity
        am_result = call_action(
            ACTIONS["hospitality-add-amenity"], conn,
            ns(company_id=env["company_id"], name="Mini Bar",
               amenity_type="room"),
        )
        assert is_ok(am_result), am_result
        amenity_id = am_result["id"]

        # Then assign it
        result = call_action(
            ACTIONS["hospitality-assign-amenity"], conn,
            ns(company_id=env["company_id"],
               room_id=env["room_101_id"],
               amenity_id=amenity_id),
        )
        assert is_ok(result), result
        assert result["room_id"] == env["room_101_id"]

    def test_assign_amenity_duplicate(self, conn, env):
        # Create and assign
        am_result = call_action(
            ACTIONS["hospitality-add-amenity"], conn,
            ns(company_id=env["company_id"], name="TV",
               amenity_type="room"),
        )
        amenity_id = am_result["id"]
        call_action(
            ACTIONS["hospitality-assign-amenity"], conn,
            ns(company_id=env["company_id"],
               room_id=env["room_101_id"], amenity_id=amenity_id),
        )
        # Try assigning again
        result = call_action(
            ACTIONS["hospitality-assign-amenity"], conn,
            ns(company_id=env["company_id"],
               room_id=env["room_101_id"], amenity_id=amenity_id),
        )
        assert is_error(result)


# ── Room Availability Report ─────────────────────────────────────────────


class TestRoomAvailabilityReport:
    """hospitality-room-availability-report"""

    def test_report_all_available(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-room-availability-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_rooms"] == 3
        assert result["available"] == 3
        assert result["report_type"] == "room_availability"
