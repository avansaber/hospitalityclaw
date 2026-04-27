"""L1 tests for HospitalityClaw housekeeping, F&B, revenue, and reports domains.

Covers:
  - Housekeeping tasks: add, list, start, complete
  - Inspections: add, list
  - Housekeeping dashboard, laundry summary
  - F&B outlets: add, list
  - Room service orders: add, list, complete
  - Minibar consumption: add, list
  - F&B revenue report
  - Revenue: add-rate-adjustment, list-rate-adjustments, set-seasonal-rates
  - Revenue reports: occupancy-forecast, revpar, adr, revenue-summary, yield-analysis
  - Reports: occupancy, revenue, housekeeping, guest-satisfaction,
             daily-operations, department-performance
"""
import json
import pytest
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
    seed_reservation,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS


# ── Housekeeping Task Tests ──────────────────────────────────────────────


class TestAddHousekeepingTask:
    """hospitality-add-housekeeping-task"""

    def test_add_task_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="checkout_clean",
               scheduled_date="2026-04-03",
               assigned_to="Maria"),
        )
        assert is_ok(result), result
        assert result["task_type"] == "checkout_clean"
        assert result["task_status"] == "pending"

    def test_add_task_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="invalid_type",
               scheduled_date="2026-04-03"),
        )
        assert is_error(result)

    def test_add_task_missing_date(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="stayover_clean",
               scheduled_date=None),
        )
        assert is_error(result)


class TestListHousekeepingTasks:
    """hospitality-list-housekeeping-tasks"""

    def test_list_tasks(self, conn, env):
        # Create one
        call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="deep_clean",
               scheduled_date="2026-04-05"),
        )
        result = call_action(
            ACTIONS["hospitality-list-housekeeping-tasks"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestStartHousekeepingTask:
    """hospitality-start-housekeeping-task"""

    def test_start_task_ok(self, conn, env):
        t = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="turndown",
               scheduled_date="2026-04-02"),
        )
        assert is_ok(t), t
        result = call_action(
            ACTIONS["hospitality-start-housekeeping-task"], conn,
            ns(task_id=t["id"]),
        )
        assert is_ok(result), result
        assert result["task_status"] == "in_progress"

    def test_start_non_pending(self, conn, env):
        t = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="turndown",
               scheduled_date="2026-04-02"),
        )
        # Start it
        call_action(
            ACTIONS["hospitality-start-housekeeping-task"], conn,
            ns(task_id=t["id"]),
        )
        # Try starting again
        result = call_action(
            ACTIONS["hospitality-start-housekeeping-task"], conn,
            ns(task_id=t["id"]),
        )
        assert is_error(result)


class TestCompleteHousekeepingTask:
    """hospitality-complete-housekeeping-task"""

    def test_complete_task_ok(self, conn, env):
        t = call_action(
            ACTIONS["hospitality-add-housekeeping-task"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               task_type="inspection",
               scheduled_date="2026-04-04"),
        )
        result = call_action(
            ACTIONS["hospitality-complete-housekeeping-task"], conn,
            ns(task_id=t["id"], notes="All done"),
        )
        assert is_ok(result), result
        assert result["task_status"] == "completed"


# ── Inspection Tests ─────────────────────────────────────────────────────


class TestAddInspection:
    """hospitality-add-inspection"""

    def test_add_inspection_pass(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-inspection"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               inspector="John Inspector",
               inspection_date="2026-04-03",
               score="85"),
        )
        assert is_ok(result), result
        assert result["score"] == 85
        assert result["passed"] == 1

    def test_add_inspection_fail(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-inspection"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               inspector="John Inspector",
               inspection_date="2026-04-03",
               score="50"),
        )
        assert is_ok(result), result
        assert result["score"] == 50
        assert result["passed"] == 0

    def test_add_inspection_missing_inspector(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-inspection"], conn,
            ns(room_id=env["room_101_id"],
               company_id=env["company_id"],
               inspector=None,
               inspection_date="2026-04-03",
               score="90"),
        )
        assert is_error(result)


class TestListInspections:
    """hospitality-list-inspections"""

    def test_list_inspections(self, conn, env):
        call_action(
            ACTIONS["hospitality-add-inspection"], conn,
            ns(room_id=env["room_201_id"],
               company_id=env["company_id"],
               inspector="Jane",
               inspection_date="2026-04-04",
               score="92"),
        )
        result = call_action(
            ACTIONS["hospitality-list-inspections"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


# ── Housekeeping Dashboard ───────────────────────────────────────────────


class TestHousekeepingDashboard:
    """hospitality-housekeeping-dashboard"""

    def test_dashboard(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-housekeeping-dashboard"], conn,
            ns(company_id=env["company_id"],
               scheduled_date="2026-04-03"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "housekeeping_dashboard"
        assert "total_tasks" in result
        assert "rooms_in_cleaning" in result


# ── Laundry Summary ──────────────────────────────────────────────────────


class TestLaundrySummary:
    """hospitality-laundry-summary"""

    def test_laundry_summary(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-laundry-summary"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "laundry_summary"
        assert "laundry_revenue" in result


# ── F&B Outlet Tests ─────────────────────────────────────────────────────


class TestAddOutlet:
    """hospitality-add-outlet"""

    def test_add_outlet_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-outlet"], conn,
            ns(company_id=env["company_id"],
               name="Pool Bar",
               outlet_type="pool"),
        )
        assert is_ok(result), result
        assert result["name"] == "Pool Bar"
        assert result["outlet_type"] == "pool"

    def test_add_outlet_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-outlet"], conn,
            ns(company_id=env["company_id"],
               name="Bad Outlet",
               outlet_type="invalid"),
        )
        assert is_error(result)


class TestListOutlets:
    """hospitality-list-outlets"""

    def test_list_outlets(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-list-outlets"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1  # outlet from env


# ── Room Service Order Tests ─────────────────────────────────────────────


class TestAddRoomServiceOrder:
    """hospitality-add-room-service-order"""

    def test_add_order_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=env["outlet_id"],
               items_json=json.dumps([{"name": "Burger", "qty": 2}]),
               total_amount="30.00"),
        )
        assert is_ok(result), result
        assert result["order_status"] == "pending"
        assert result["total_amount"] == "30.00"

    def test_add_order_invalid_json(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=env["outlet_id"],
               items_json="not-json",
               total_amount="10.00"),
        )
        assert is_error(result)

    def test_add_order_missing_outlet(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=None,
               items_json="[]",
               total_amount="10.00"),
        )
        assert is_error(result)


class TestListRoomServiceOrders:
    """hospitality-list-room-service-orders"""

    def test_list_orders(self, conn, env):
        call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=env["outlet_id"],
               items_json=json.dumps([{"name": "Pizza"}]),
               total_amount="15.00"),
        )
        result = call_action(
            ACTIONS["hospitality-list-room-service-orders"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestCompleteRoomServiceOrder:
    """hospitality-complete-room-service-order"""

    def test_complete_order_ok(self, conn, env):
        o = call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=env["outlet_id"],
               items_json=json.dumps([{"name": "Salad"}]),
               total_amount="12.00"),
        )
        assert is_ok(o), o
        result = call_action(
            ACTIONS["hospitality-complete-room-service-order"], conn,
            ns(order_id=o["id"]),
        )
        assert is_ok(result), result
        assert result["order_status"] == "delivered"

    def test_complete_already_delivered(self, conn, env):
        o = call_action(
            ACTIONS["hospitality-add-room-service-order"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               outlet_id=env["outlet_id"],
               items_json=json.dumps([{"name": "Soup"}]),
               total_amount="8.00"),
        )
        call_action(
            ACTIONS["hospitality-complete-room-service-order"], conn,
            ns(order_id=o["id"]),
        )
        result = call_action(
            ACTIONS["hospitality-complete-room-service-order"], conn,
            ns(order_id=o["id"]),
        )
        assert is_error(result)


# ── Minibar Consumption Tests ────────────────────────────────────────────


class TestAddMinibarConsumption:
    """hospitality-add-minibar-consumption"""

    def test_add_minibar_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-minibar-consumption"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               item_name="Soda",
               quantity="2",
               unit_price="5.00",
               consumption_date="2026-04-02"),
        )
        assert is_ok(result), result
        assert result["item_name"] == "Soda"
        assert result["quantity"] == 2
        assert result["total"] == "10.00"

    def test_add_minibar_missing_item(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-minibar-consumption"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               item_name=None, quantity="1",
               unit_price="3.00",
               consumption_date="2026-04-02"),
        )
        assert is_error(result)


class TestListMinibarConsumptions:
    """hospitality-list-minibar-consumptions"""

    def test_list_minibar(self, conn, env):
        call_action(
            ACTIONS["hospitality-add-minibar-consumption"], conn,
            ns(reservation_id=env["reservation_id"],
               company_id=env["company_id"],
               item_name="Water",
               quantity="3",
               unit_price="2.00",
               consumption_date="2026-04-01"),
        )
        result = call_action(
            ACTIONS["hospitality-list-minibar-consumptions"], conn,
            ns(reservation_id=env["reservation_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


# ── F&B Revenue Report ──────────────────────────────────────────────────


class TestFnbRevenueReport:
    """hospitality-fnb-revenue-report"""

    def test_fnb_report(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-fnb-revenue-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "fnb_revenue"
        assert "total_fnb_revenue" in result


# ── Revenue Domain Tests ─────────────────────────────────────────────────


class TestAddRateAdjustment:
    """hospitality-add-rate-adjustment"""

    def test_add_adjustment_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-rate-adjustment"], conn,
            ns(room_type_id=env["std_room_type_id"],
               company_id=env["company_id"],
               adjustment_date="2026-04-15",
               adjustment_type="increase",
               adjustment_pct="10"),
        )
        assert is_ok(result), result
        assert result["adjustment_type"] == "increase"

    def test_add_adjustment_invalid_type(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-add-rate-adjustment"], conn,
            ns(room_type_id=env["std_room_type_id"],
               company_id=env["company_id"],
               adjustment_date="2026-04-15",
               adjustment_type="invalid"),
        )
        assert is_error(result)


class TestListRateAdjustments:
    """hospitality-list-rate-adjustments"""

    def test_list_adjustments(self, conn, env):
        call_action(
            ACTIONS["hospitality-add-rate-adjustment"], conn,
            ns(room_type_id=env["std_room_type_id"],
               company_id=env["company_id"],
               adjustment_date="2026-04-20",
               adjustment_type="decrease",
               adjustment_pct="5"),
        )
        result = call_action(
            ACTIONS["hospitality-list-rate-adjustments"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["total_count"] >= 1


class TestSetSeasonalRates:
    """hospitality-set-seasonal-rates"""

    def test_set_seasonal_ok(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-set-seasonal-rates"], conn,
            ns(room_type_id=env["std_room_type_id"],
               company_id=env["company_id"],
               start_date="2026-06-01",
               end_date="2026-08-31",
               adjusted_rate="200.00"),
        )
        assert is_ok(result), result
        assert result["adjusted_rate"] == "200.00"

    def test_set_seasonal_missing_rate(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-set-seasonal-rates"], conn,
            ns(room_type_id=env["std_room_type_id"],
               company_id=env["company_id"],
               start_date="2026-06-01",
               end_date="2026-08-31",
               adjusted_rate=None),
        )
        assert is_error(result)


class TestOccupancyForecast:
    """hospitality-occupancy-forecast"""

    def test_forecast(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-occupancy-forecast"], conn,
            ns(company_id=env["company_id"],
               start_date="2026-04-01",
               end_date="2026-04-30"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "occupancy_forecast"
        assert "occupancy_rate_pct" in result


class TestRevparReport:
    """hospitality-revpar-report"""

    def test_revpar(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-revpar-report"], conn,
            ns(company_id=env["company_id"],
               start_date="2026-04-01",
               end_date="2026-04-30"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "revpar"
        assert "revpar" in result


class TestAdrReport:
    """hospitality-adr-report"""

    def test_adr(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-adr-report"], conn,
            ns(company_id=env["company_id"],
               start_date="2026-04-01",
               end_date="2026-04-30"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "adr"
        assert "adr" in result


class TestRevenueSummary:
    """hospitality-revenue-summary"""

    def test_revenue_summary(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-revenue-summary"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "revenue_summary"
        assert "total_revenue" in result


class TestYieldAnalysis:
    """hospitality-yield-analysis"""

    def test_yield(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-yield-analysis"], conn,
            ns(company_id=env["company_id"],
               start_date="2026-04-01",
               end_date="2026-04-30"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "yield_analysis"
        assert "room_types" in result
        assert len(result["room_types"]) >= 2  # Standard + Deluxe


# ── Reports Domain Tests ─────────────────────────────────────────────────


class TestOccupancyReport:
    """hospitality-occupancy-report"""

    def test_occupancy(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-occupancy-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "occupancy"
        assert "occupancy_rate_pct" in result


class TestRevenueReport:
    """hospitality-revenue-report"""

    def test_revenue(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-revenue-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "revenue"
        assert "total_revenue" in result


class TestHousekeepingReport:
    """hospitality-housekeeping-report"""

    def test_hk_report(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-housekeeping-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "housekeeping"
        assert "completion_rate_pct" in result


class TestGuestSatisfactionReport:
    """hospitality-guest-satisfaction-report"""

    def test_satisfaction(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-guest-satisfaction-report"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "guest_satisfaction"
        assert "resolution_rate_pct" in result


class TestDailyOperationsReport:
    """hospitality-daily-operations-report"""

    def test_daily_ops(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-daily-operations-report"], conn,
            ns(company_id=env["company_id"],
               report_date="2026-04-01"),
        )
        assert is_ok(result), result
        assert result["report_type"] == "daily_operations"
        assert "expected_checkins" in result
        assert "occupancy_pct" in result


class TestDepartmentPerformance:
    """hospitality-department-performance"""

    def test_dept_perf(self, conn, env):
        result = call_action(
            ACTIONS["hospitality-department-performance"], conn,
            ns(company_id=env["company_id"]),
        )
        assert is_ok(result), result
        assert result["report_type"] == "department_performance"
        assert "front_desk_reservations" in result
        assert "housekeeping_completed" in result
