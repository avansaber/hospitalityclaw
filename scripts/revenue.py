"""HospitalityClaw -- revenue domain module

Actions for rate adjustments and revenue analytics (1 table, 8 actions).
Imported by db_query.py (unified router).
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_ADJUSTMENT_TYPES = ("increase", "decrease", "override")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute(Q.from_(Table("company")).select(Field("id")).where(Field("id") == P()).get_sql(), (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_room_type(conn, room_type_id):
    if not room_type_id:
        err("--room-type-id is required")
    row = conn.execute(Q.from_(Table("hospitalityclaw_room_type")).select(Field("id")).where(Field("id") == P()).get_sql(), (room_type_id,)).fetchone()
    if not row:
        err(f"Room type {room_type_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-rate-adjustment
# ---------------------------------------------------------------------------
def add_rate_adjustment(conn, args):
    rt_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, rt_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    adj_date = getattr(args, "adjustment_date", None)
    if not adj_date:
        err("--adjustment-date is required")
    adj_type = getattr(args, "adjustment_type", None)
    if not adj_type:
        err("--adjustment-type is required")
    _validate_enum(adj_type, VALID_ADJUSTMENT_TYPES, "adjustment-type")

    ra_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("hospitalityclaw_rate_adjustment", {

        "id": P(), "room_type_id": P(), "adjustment_date": P(), "adjustment_type": P(), "adjustment_pct": P(), "adjusted_rate": P(), "reason": P(), "company_id": P(), "created_at": P(),

    })

    conn.execute(sql, (
        ra_id, rt_id, adj_date, adj_type,
        getattr(args, "adjustment_pct", None),
        getattr(args, "adjusted_rate", None),
        getattr(args, "reason", None),
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_rate_adjustment", ra_id, "hospitality-add-rate-adjustment", company_id)
    conn.commit()
    ok({"id": ra_id, "adjustment_type": adj_type, "adjustment_date": adj_date})


# ---------------------------------------------------------------------------
# 2. list-rate-adjustments
# ---------------------------------------------------------------------------
def list_rate_adjustments(conn, args):
    t = Table("hospitalityclaw_rate_adjustment")

    q_count = Q.from_(t).select(fn.Count("*"))

    q_rows = Q.from_(t).select(t.star)

    params = []


    if getattr(args, "company_id", None):

        q_count = q_count.where(t.company_id == P())

        q_rows = q_rows.where(t.company_id == P())

        params.append(args.company_id)

    if getattr(args, "room_type_id", None):

        q_count = q_count.where(t.room_type_id == P())

        q_rows = q_rows.where(t.room_type_id == P())

        params.append(args.room_type_id)

    if getattr(args, "adjustment_type", None):

        q_count = q_count.where(t.adjustment_type == P())

        q_rows = q_rows.where(t.adjustment_type == P())

        params.append(args.adjustment_type)


    total = conn.execute(q_count.get_sql(), params).fetchone()[0]

    q_rows = q_rows.orderby(t.adjustment_date, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 3. occupancy-forecast
# ---------------------------------------------------------------------------
def occupancy_forecast(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")

    total_rooms = conn.execute(Q.from_(Table("hospitalityclaw_room")).select(fn.Count("*")).where(Field("company_id") == P()).get_sql(), (args.company_id,)).fetchone()[0]

    occupied_nights = conn.execute(
        "SELECT COALESCE(SUM(nights), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    from datetime import date as date_cls
    try:
        days = max((date_cls.fromisoformat(ed) - date_cls.fromisoformat(sd)).days, 1)
    except ValueError:
        days = 1

    available_nights = total_rooms * days
    occ_rate = round(occupied_nights / available_nights * 100, 1) if available_nights > 0 else 0

    ok({
        "start_date": sd,
        "end_date": ed,
        "total_rooms": total_rooms,
        "total_room_nights": available_nights,
        "occupied_nights": occupied_nights,
        "occupancy_rate_pct": occ_rate,
        "report_type": "occupancy_forecast",
    })


# ---------------------------------------------------------------------------
# 4. revpar-report (Revenue Per Available Room)
# ---------------------------------------------------------------------------
def revpar_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")

    total_rooms = conn.execute(Q.from_(Table("hospitalityclaw_room")).select(fn.Count("*")).where(Field("company_id") == P()).get_sql(), (args.company_id,)).fetchone()[0]

    from datetime import date as date_cls
    try:
        days = max((date_cls.fromisoformat(ed) - date_cls.fromisoformat(sd)).days, 1)
    except ValueError:
        days = 1

    available_nights = total_rooms * days

    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS NUMERIC)), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    revpar = round_currency(to_decimal(str(total_revenue / available_nights))) if available_nights > 0 else Decimal("0")

    ok({
        "start_date": sd,
        "end_date": ed,
        "total_rooms": total_rooms,
        "available_nights": available_nights,
        "total_revenue": str(round_currency(to_decimal(str(total_revenue)))),
        "revpar": str(revpar),
        "report_type": "revpar",
    })


# ---------------------------------------------------------------------------
# 5. adr-report (Average Daily Rate)
# ---------------------------------------------------------------------------
def adr_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")

    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS NUMERIC)), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    occupied_nights = conn.execute(
        "SELECT COALESCE(SUM(nights), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    adr = round_currency(to_decimal(str(total_revenue / occupied_nights))) if occupied_nights > 0 else Decimal("0")

    ok({
        "start_date": sd,
        "end_date": ed,
        "total_revenue": str(round_currency(to_decimal(str(total_revenue)))),
        "occupied_nights": occupied_nights,
        "adr": str(adr),
        "report_type": "adr",
    })


# ---------------------------------------------------------------------------
# 6. revenue-summary
# ---------------------------------------------------------------------------
def revenue_summary(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    room_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS NUMERIC)), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('confirmed','checked_in','checked_out')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    folio_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS NUMERIC)), 0) FROM hospitalityclaw_folio_charge "
        "WHERE company_id = ? AND charge_date >= ? AND charge_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    fnb_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS NUMERIC)), 0) FROM hospitalityclaw_room_service_order "
        "WHERE company_id = ? AND order_status != 'cancelled' "
        "AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    minibar_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total AS NUMERIC)), 0) FROM hospitalityclaw_minibar_consumption "
        "WHERE company_id = ? AND consumption_date >= ? AND consumption_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    total = room_revenue + folio_revenue + fnb_revenue + minibar_revenue

    ok({
        "start_date": sd,
        "end_date": ed,
        "room_revenue": str(round_currency(to_decimal(str(room_revenue)))),
        "folio_revenue": str(round_currency(to_decimal(str(folio_revenue)))),
        "fnb_revenue": str(round_currency(to_decimal(str(fnb_revenue)))),
        "minibar_revenue": str(round_currency(to_decimal(str(minibar_revenue)))),
        "total_revenue": str(round_currency(to_decimal(str(total)))),
        "report_type": "revenue_summary",
    })


# ---------------------------------------------------------------------------
# 7. set-seasonal-rates
# ---------------------------------------------------------------------------
def set_seasonal_rates(conn, args):
    rt_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, rt_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    adjusted_rate = getattr(args, "adjusted_rate", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")
    if not adjusted_rate:
        err("--adjusted-rate is required")

    ra_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("hospitalityclaw_rate_adjustment", {

        "id": P(), "room_type_id": P(), "adjustment_date": P(), "adjustment_type": P(), "adjustment_pct": P(), "adjusted_rate": P(), "reason": P(), "company_id": P(), "created_at": P(),

    })

    conn.execute(sql, (
        ra_id, rt_id, sd, "override", None,
        str(round_currency(to_decimal(adjusted_rate))),
        getattr(args, "reason", None) or f"Seasonal rate {sd} to {ed}",
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_rate_adjustment", ra_id, "hospitality-set-seasonal-rates", company_id)
    conn.commit()
    ok({"id": ra_id, "start_date": sd, "end_date": ed,
        "adjusted_rate": str(round_currency(to_decimal(adjusted_rate)))})


# ---------------------------------------------------------------------------
# 8. yield-analysis
# ---------------------------------------------------------------------------
def yield_analysis(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")

    # Per room type analysis
    room_types = conn.execute(
        "SELECT id, name, base_rate FROM hospitalityclaw_room_type WHERE company_id = ?",
        (args.company_id,)
    ).fetchall()

    analysis = []
    for rt in room_types:
        rt_id, rt_name, base_rate = rt[0], rt[1], rt[2]

        rooms_count = conn.execute(Q.from_(Table("hospitalityclaw_room")).select(fn.Count("*")).where(Field("room_type_id") == P()).where(Field("company_id") == P()).get_sql(), (rt_id, args.company_id)).fetchone()[0]

        rev = conn.execute(
            "SELECT COALESCE(SUM(CAST(total_amount AS NUMERIC)), 0) FROM hospitalityclaw_reservation "
            "WHERE room_type_id = ? AND company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
            "AND reservation_status IN ('confirmed','checked_in','checked_out')",
            (rt_id, args.company_id, sd, ed)
        ).fetchone()[0]

        nights_sold = conn.execute(
            "SELECT COALESCE(SUM(nights), 0) FROM hospitalityclaw_reservation "
            "WHERE room_type_id = ? AND company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
            "AND reservation_status IN ('confirmed','checked_in','checked_out')",
            (rt_id, args.company_id, sd, ed)
        ).fetchone()[0]

        analysis.append({
            "room_type_id": rt_id,
            "room_type_name": rt_name,
            "base_rate": base_rate,
            "rooms_count": rooms_count,
            "revenue": str(round_currency(to_decimal(str(rev)))),
            "nights_sold": nights_sold,
        })

    ok({"start_date": sd, "end_date": ed, "room_types": analysis, "report_type": "yield_analysis"})


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-rate-adjustment": add_rate_adjustment,
    "hospitality-list-rate-adjustments": list_rate_adjustments,
    "hospitality-occupancy-forecast": occupancy_forecast,
    "hospitality-revpar-report": revpar_report,
    "hospitality-adr-report": adr_report,
    "hospitality-revenue-summary": revenue_summary,
    "hospitality-set-seasonal-rates": set_seasonal_rates,
    "hospitality-yield-analysis": yield_analysis,
}
