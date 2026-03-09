"""HospitalityClaw -- reports domain module

Aggregate reporting actions (0 tables, 7 actions + status).
Imported by db_query.py (unified router).
"""
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection, DEFAULT_DB_PATH
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.response import ok, err, row_to_dict
except ImportError:
    DEFAULT_DB_PATH = "~/.openclaw/erpclaw/data.sqlite"
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


# ---------------------------------------------------------------------------
# 1. occupancy-report
# ---------------------------------------------------------------------------
def occupancy_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    total_rooms = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE company_id = ?", (args.company_id,)
    ).fetchone()[0]

    from datetime import date as date_cls
    try:
        days = max((date_cls.fromisoformat(ed) - date_cls.fromisoformat(sd)).days, 1)
    except ValueError:
        days = 1

    available_nights = total_rooms * days

    occupied_nights = conn.execute(
        "SELECT COALESCE(SUM(nights), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    occ_rate = round(occupied_nights / available_nights * 100, 1) if available_nights > 0 else 0

    ok({
        "start_date": sd, "end_date": ed,
        "total_rooms": total_rooms,
        "available_nights": available_nights,
        "occupied_nights": occupied_nights,
        "occupancy_rate_pct": occ_rate,
        "report_type": "occupancy",
    })


# ---------------------------------------------------------------------------
# 2. revenue-report
# ---------------------------------------------------------------------------
def revenue_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    room_rev = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS REAL)), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    folio_rev = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM hospitalityclaw_folio_charge "
        "WHERE company_id = ? AND charge_date >= ? AND charge_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    ok({
        "start_date": sd, "end_date": ed,
        "room_revenue": str(round_currency(to_decimal(str(room_rev)))),
        "folio_revenue": str(round_currency(to_decimal(str(folio_rev)))),
        "total_revenue": str(round_currency(to_decimal(str(room_rev + folio_rev)))),
        "report_type": "revenue",
    })


# ---------------------------------------------------------------------------
# 3. housekeeping-report
# ---------------------------------------------------------------------------
def housekeeping_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    total = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task "
        "WHERE company_id = ? AND scheduled_date >= ? AND scheduled_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    completed = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task "
        "WHERE company_id = ? AND scheduled_date >= ? AND scheduled_date <= ? AND task_status = 'completed'",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    avg_score = conn.execute(
        "SELECT COALESCE(AVG(score), 0) FROM hospitalityclaw_inspection "
        "WHERE company_id = ? AND inspection_date >= ? AND inspection_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    pass_rate = conn.execute(
        "SELECT COALESCE(AVG(passed) * 100, 0) FROM hospitalityclaw_inspection "
        "WHERE company_id = ? AND inspection_date >= ? AND inspection_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    ok({
        "start_date": sd, "end_date": ed,
        "total_tasks": total,
        "completed_tasks": completed,
        "completion_rate_pct": round(completed / total * 100, 1) if total > 0 else 0,
        "avg_inspection_score": round(avg_score, 1),
        "inspection_pass_rate_pct": round(pass_rate, 1),
        "report_type": "housekeeping",
    })


# ---------------------------------------------------------------------------
# 4. guest-satisfaction-report
# ---------------------------------------------------------------------------
def guest_satisfaction_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    total_requests = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_guest_request "
        "WHERE company_id = ? AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    completed_requests = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_guest_request "
        "WHERE company_id = ? AND request_status = 'completed' AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    vip_guests = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_guest "
        "WHERE company_id = ? AND vip_level != 'none'",
        (args.company_id,)
    ).fetchone()[0]

    ok({
        "start_date": sd, "end_date": ed,
        "total_requests": total_requests,
        "completed_requests": completed_requests,
        "resolution_rate_pct": round(completed_requests / total_requests * 100, 1) if total_requests > 0 else 0,
        "vip_guests": vip_guests,
        "report_type": "guest_satisfaction",
    })


# ---------------------------------------------------------------------------
# 5. daily-operations-report
# ---------------------------------------------------------------------------
def daily_operations_report(conn, args):
    _validate_company(conn, args.company_id)
    report_date = getattr(args, "report_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Today's check-ins
    checkins = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date = ? AND reservation_status IN ('confirmed','checked_in')",
        (args.company_id, report_date)
    ).fetchone()[0]

    # Today's check-outs
    checkouts = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_out_date = ? AND reservation_status IN ('checked_in','checked_out')",
        (args.company_id, report_date)
    ).fetchone()[0]

    # Currently occupied
    occupied = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room "
        "WHERE company_id = ? AND room_status = 'occupied'",
        (args.company_id,)
    ).fetchone()[0]

    total_rooms = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE company_id = ?",
        (args.company_id,)
    ).fetchone()[0]

    # Open requests
    open_requests = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_guest_request "
        "WHERE company_id = ? AND request_status IN ('open','in_progress')",
        (args.company_id,)
    ).fetchone()[0]

    # Pending HK tasks
    pending_hk = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task "
        "WHERE company_id = ? AND scheduled_date = ? AND task_status = 'pending'",
        (args.company_id, report_date)
    ).fetchone()[0]

    ok({
        "report_date": report_date,
        "expected_checkins": checkins,
        "expected_checkouts": checkouts,
        "currently_occupied": occupied,
        "total_rooms": total_rooms,
        "occupancy_pct": round(occupied / total_rooms * 100, 1) if total_rooms > 0 else 0,
        "open_requests": open_requests,
        "pending_housekeeping": pending_hk,
        "report_type": "daily_operations",
    })


# ---------------------------------------------------------------------------
# 6. department-performance
# ---------------------------------------------------------------------------
def department_performance(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    # Front desk: reservations processed
    reservations = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    # Housekeeping: tasks completed
    hk_completed = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task "
        "WHERE company_id = ? AND scheduled_date >= ? AND scheduled_date <= ? AND task_status = 'completed'",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    # F&B: orders delivered
    fnb_delivered = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room_service_order "
        "WHERE company_id = ? AND order_status = 'delivered' "
        "AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    ok({
        "start_date": sd, "end_date": ed,
        "front_desk_reservations": reservations,
        "housekeeping_completed": hk_completed,
        "fnb_orders_delivered": fnb_delivered,
        "report_type": "department_performance",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-occupancy-report": occupancy_report,
    "hospitality-revenue-report": revenue_report,
    "hospitality-housekeeping-report": housekeeping_report,
    "hospitality-guest-satisfaction-report": guest_satisfaction_report,
    "hospitality-daily-operations-report": daily_operations_report,
    "hospitality-department-performance": department_performance,
}
