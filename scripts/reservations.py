"""HospitalityClaw -- reservations domain module

Actions for reservations, rate plans, group blocks (3 tables, 12 actions).
Imported by db_query.py (unified router).
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal

try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.decimal_utils import to_decimal, round_currency
    from erpclaw_lib.naming import get_next_name, ENTITY_PREFIXES
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row, LiteralValue, dynamic_update, update_row

    ENTITY_PREFIXES.setdefault("hospitalityclaw_reservation", "RES-")
    ENTITY_PREFIXES.setdefault("hospitalityclaw_rate_plan", "RPL-")
    ENTITY_PREFIXES.setdefault("hospitalityclaw_group_block", "GBK-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_RESERVATION_STATUSES = ("pending", "confirmed", "checked_in", "checked_out", "cancelled", "no_show")
VALID_SOURCES = ("direct", "phone", "online", "group", "walk_in")
VALID_RATE_TYPES = ("standard", "weekend", "seasonal", "promotional")
VALID_BLOCK_STATUSES = ("tentative", "confirmed", "released")


_t_company = Table("company")
_t_guest_ext = Table("hospitalityclaw_guest_ext")
_t_room_type = Table("hospitalityclaw_room_type")
_t_reservation = Table("hospitalityclaw_reservation")
_t_rate_plan = Table("hospitalityclaw_rate_plan")
_t_room = Table("hospitalityclaw_room")
_t_group_block = Table("hospitalityclaw_group_block")
_t_folio = Table("hospitalityclaw_folio_charge")
_t_customer = Table("customer")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    q = Q.from_(_t_company).select(_t_company.id).where(_t_company.id == P())
    if not conn.execute(q.get_sql(), (company_id,)).fetchone():
        err(f"Company {company_id} not found")


def _validate_guest(conn, guest_id):
    if not guest_id:
        err("--guest-id is required")
    q = Q.from_(_t_guest_ext).select(_t_guest_ext.id).where(_t_guest_ext.id == P())
    if not conn.execute(q.get_sql(), (guest_id,)).fetchone():
        err(f"Guest {guest_id} not found")


def _validate_room_type(conn, room_type_id):
    if not room_type_id:
        err("--room-type-id is required")
    q = Q.from_(_t_room_type).select(_t_room_type.id).where(_t_room_type.id == P())
    if not conn.execute(q.get_sql(), (room_type_id,)).fetchone():
        err(f"Room type {room_type_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


def _calc_nights(check_in, check_out):
    """Calculate number of nights between two date strings."""
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        return max((co - ci).days, 1)
    except (ValueError, TypeError):
        return 1


# ---------------------------------------------------------------------------
# 1. add-reservation
# ---------------------------------------------------------------------------
def add_reservation(conn, args):
    _validate_company(conn, args.company_id)
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)
    room_type_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, room_type_id)

    ci = getattr(args, "check_in_date", None)
    co = getattr(args, "check_out_date", None)
    if not ci:
        err("--check-in-date is required")
    if not co:
        err("--check-out-date is required")
    if ci >= co:
        err("check-out-date must be after check-in-date")

    rate_amount = getattr(args, "rate_amount", None)
    if not rate_amount:
        err("--rate-amount is required")

    nights = _calc_nights(ci, co)
    rate_dec = round_currency(to_decimal(rate_amount))
    total = round_currency(rate_dec * Decimal(nights))

    source = getattr(args, "source", None) or "direct"
    _validate_enum(source, VALID_SOURCES, "source")

    rate_plan_id = getattr(args, "rate_plan_id", None)
    if rate_plan_id:
        q = Q.from_(_t_rate_plan).select(_t_rate_plan.id).where(_t_rate_plan.id == P())
        if not conn.execute(q.get_sql(), (rate_plan_id,)).fetchone():
            err(f"Rate plan {rate_plan_id} not found")

    room_id = getattr(args, "room_id", None)
    if room_id:
        q = Q.from_(_t_room).select(_t_room.id).where(_t_room.id == P())
        if not conn.execute(q.get_sql(), (room_id,)).fetchone():
            err(f"Room {room_id} not found")

    res_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_reservation", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("hospitalityclaw_reservation", {
        "id": P(), "naming_series": P(), "guest_id": P(), "room_type_id": P(), "room_id": P(),
        "check_in_date": P(), "check_out_date": P(), "nights": P(), "adults": P(), "children": P(),
        "rate_plan_id": P(), "rate_amount": P(), "total_amount": P(),
        "reservation_status": P(), "source": P(), "special_requests": P(),
        "company_id": P(), "created_at": P(), "updated_at": P(),
    })
    conn.execute(sql, (
        res_id, naming, guest_id, room_type_id, room_id,
        ci, co, nights,
        int(getattr(args, "adults", None) or 1),
        int(getattr(args, "children", None) or 0),
        rate_plan_id,
        str(rate_dec), str(total),
        "pending", source,
        getattr(args, "special_requests", None),
        args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_reservation", res_id, "hospitality-add-reservation", args.company_id)
    conn.commit()
    ok({"id": res_id, "naming_series": naming, "reservation_status": "pending",
        "nights": nights, "total_amount": str(total)})


# ---------------------------------------------------------------------------
# 2. update-reservation
# ---------------------------------------------------------------------------
def update_reservation(conn, args):
    res_id = getattr(args, "reservation_id", None)
    if not res_id:
        err("--reservation-id is required")
    q = Q.from_(_t_reservation).select(_t_reservation.star).where(_t_reservation.id == P())
    row = conn.execute(q.get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    current = row_to_dict(row)

    # Can only update pending/confirmed reservations
    if current["reservation_status"] not in ("pending", "confirmed"):
        err(f"Cannot update reservation in '{current['reservation_status']}' status")

    data, changed = {}, []
    for arg_name, col_name in {
        "special_requests": "special_requests",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            data[col_name] = val
            changed.append(col_name)

    rt = getattr(args, "room_type_id", None)
    if rt is not None:
        q = Q.from_(_t_room_type).select(_t_room_type.id).where(_t_room_type.id == P())
        conn.execute(q.get_sql(), (rt,)).fetchone() or err(f"Room type {rt} not found")
        data["room_type_id"] = rt
        changed.append("room_type_id")

    adults = getattr(args, "adults", None)
    if adults is not None:
        data["adults"] = int(adults)
        changed.append("adults")

    children = getattr(args, "children", None)
    if children is not None:
        data["children"] = int(children)
        changed.append("children")

    # Recalculate if dates or rate changed
    ci = getattr(args, "check_in_date", None) or current["check_in_date"]
    co = getattr(args, "check_out_date", None) or current["check_out_date"]
    ra = getattr(args, "rate_amount", None)

    if getattr(args, "check_in_date", None):
        data["check_in_date"] = ci
        changed.append("check_in_date")
    if getattr(args, "check_out_date", None):
        data["check_out_date"] = co
        changed.append("check_out_date")
    if ra is not None:
        data["rate_amount"] = str(round_currency(to_decimal(ra)))
        changed.append("rate_amount")

    if getattr(args, "check_in_date", None) or getattr(args, "check_out_date", None) or ra is not None:
        nights = _calc_nights(ci, co)
        rate_dec = round_currency(to_decimal(ra or current["rate_amount"]))
        total = round_currency(rate_dec * Decimal(nights))
        data["nights"] = nights
        data["total_amount"] = str(total)

    if not data:
        err("No fields to update")

    data["updated_at"] = LiteralValue("datetime('now')")
    sql, params = dynamic_update("hospitalityclaw_reservation", data, {"id": res_id})
    conn.execute(sql, params)
    audit(conn, "hospitalityclaw_reservation", res_id, "hospitality-update-reservation", None, {"updated_fields": changed})
    conn.commit()
    ok({"id": res_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. get-reservation
# ---------------------------------------------------------------------------
def get_reservation(conn, args):
    res_id = getattr(args, "reservation_id", None)
    if not res_id:
        err("--reservation-id is required")
    q = Q.from_(_t_reservation).select(_t_reservation.star).where(_t_reservation.id == P())
    row = conn.execute(q.get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    data = row_to_dict(row)

    # Enrich with guest name (via guest_ext -> customer) and room type name
    ge = _t_guest_ext
    c = _t_customer
    q = (Q.from_(ge).join(c).on(c.id == ge.customer_id)
         .select(c.name).where(ge.id == P()))
    g = conn.execute(q.get_sql(), (data["guest_id"],)).fetchone()
    data["guest_name"] = g[0] if g else None

    q = Q.from_(_t_room_type).select(_t_room_type.name).where(_t_room_type.id == P())
    rt = conn.execute(q.get_sql(), (data["room_type_id"],)).fetchone()
    data["room_type_name"] = rt[0] if rt else None

    # Folio total
    q = Q.from_(_t_folio).select(fn.Coalesce(fn.Sum(Field("amount")), 0)).where(_t_folio.reservation_id == P())
    folio_total = conn.execute(q.get_sql(), (res_id,)).fetchone()[0]
    data["folio_total"] = str(round_currency(to_decimal(str(folio_total))))
    ok(data)


# ---------------------------------------------------------------------------
# 4. list-reservations
# ---------------------------------------------------------------------------
def list_reservations(conn, args):
    t = _t_reservation
    q_count = Q.from_(t).select(fn.Count("*"))
    q_rows = Q.from_(t).select(t.star)
    params = []

    if getattr(args, "company_id", None):
        q_count = q_count.where(t.company_id == P())
        q_rows = q_rows.where(t.company_id == P())
        params.append(args.company_id)
    if getattr(args, "guest_id", None):
        q_count = q_count.where(t.guest_id == P())
        q_rows = q_rows.where(t.guest_id == P())
        params.append(args.guest_id)
    if getattr(args, "reservation_status", None):
        q_count = q_count.where(t.reservation_status == P())
        q_rows = q_rows.where(t.reservation_status == P())
        params.append(args.reservation_status)
    if getattr(args, "source", None):
        q_count = q_count.where(t.source == P())
        q_rows = q_rows.where(t.source == P())
        params.append(args.source)
    if getattr(args, "check_in_date", None):
        q_count = q_count.where(t.check_in_date == P())
        q_rows = q_rows.where(t.check_in_date == P())
        params.append(args.check_in_date)

    total = conn.execute(q_count.get_sql(), params).fetchone()[0]
    q_rows = q_rows.orderby(t.check_in_date, order=Order.desc).limit(P()).offset(P())
    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 5. confirm-reservation
# ---------------------------------------------------------------------------
def confirm_reservation(conn, args):
    res_id = getattr(args, "reservation_id", None)
    if not res_id:
        err("--reservation-id is required")
    q = Q.from_(_t_reservation).select(_t_reservation.reservation_status).where(_t_reservation.id == P())
    row = conn.execute(q.get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    if row[0] != "pending":
        err(f"Cannot confirm reservation in '{row[0]}' status (must be pending)")

    sql, params = dynamic_update("hospitalityclaw_reservation",
        {"reservation_status": "confirmed", "updated_at": LiteralValue("datetime('now')")},
        {"id": res_id})
    conn.execute(sql, params)
    audit(conn, "hospitalityclaw_reservation", res_id, "hospitality-confirm-reservation", None)
    conn.commit()
    ok({"id": res_id, "reservation_status": "confirmed"})


# ---------------------------------------------------------------------------
# 6. cancel-reservation
# ---------------------------------------------------------------------------
def cancel_reservation(conn, args):
    res_id = getattr(args, "reservation_id", None)
    if not res_id:
        err("--reservation-id is required")
    q = Q.from_(_t_reservation).select(_t_reservation.reservation_status).where(_t_reservation.id == P())
    row = conn.execute(q.get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    if row[0] in ("checked_in", "checked_out", "cancelled"):
        err(f"Cannot cancel reservation in '{row[0]}' status")

    sql, params = dynamic_update("hospitalityclaw_reservation",
        {"reservation_status": "cancelled", "updated_at": LiteralValue("datetime('now')")},
        {"id": res_id})
    conn.execute(sql, params)
    audit(conn, "hospitalityclaw_reservation", res_id, "hospitality-cancel-reservation", None,
          {"reason": getattr(args, "reason", None)})
    conn.commit()
    ok({"id": res_id, "reservation_status": "cancelled"})


# ---------------------------------------------------------------------------
# 7. add-rate-plan
# ---------------------------------------------------------------------------
def add_rate_plan(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    room_type_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, room_type_id)

    rate_amount = getattr(args, "rate_amount", None)
    if not rate_amount:
        err("--rate-amount is required")
    sd = getattr(args, "start_date", None)
    ed = getattr(args, "end_date", None)
    if not sd:
        err("--start-date is required")
    if not ed:
        err("--end-date is required")

    rate_type = getattr(args, "rate_type", None) or "standard"
    _validate_enum(rate_type, VALID_RATE_TYPES, "rate-type")

    rp_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_rate_plan", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("hospitalityclaw_rate_plan", {
        "id": P(), "naming_series": P(), "name": P(), "room_type_id": P(),
        "rate_amount": P(), "start_date": P(), "end_date": P(), "rate_type": P(),
        "is_active": P(), "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
        rp_id, naming, name, room_type_id,
        str(round_currency(to_decimal(rate_amount))),
        sd, ed, rate_type, 1, args.company_id, now,
    ))
    audit(conn, "hospitalityclaw_rate_plan", rp_id, "hospitality-add-rate-plan", args.company_id)
    conn.commit()
    ok({"id": rp_id, "naming_series": naming, "name": name, "rate_type": rate_type})


# ---------------------------------------------------------------------------
# 8. list-rate-plans
# ---------------------------------------------------------------------------
def list_rate_plans(conn, args):
    t = _t_rate_plan
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
    if getattr(args, "rate_type", None):
        q_count = q_count.where(t.rate_type == P())
        q_rows = q_rows.where(t.rate_type == P())
        params.append(args.rate_type)

    total = conn.execute(q_count.get_sql(), params).fetchone()[0]
    q_rows = q_rows.orderby(t.created_at, order=Order.desc).limit(P()).offset(P())
    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 9. check-availability
# ---------------------------------------------------------------------------
def check_availability(conn, args):
    _validate_company(conn, args.company_id)
    room_type_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, room_type_id)
    ci = getattr(args, "check_in_date", None)
    co = getattr(args, "check_out_date", None)
    if not ci:
        err("--check-in-date is required")
    if not co:
        err("--check-out-date is required")

    # Total rooms of this type
    r = _t_room
    q = (Q.from_(r).select(fn.Count("*"))
         .where(r.room_type_id == P())
         .where(r.company_id == P())
         .where(r.room_status.isin(["available", "occupied"])))
    total_rooms = conn.execute(q.get_sql(), (room_type_id, args.company_id)).fetchone()[0]

    # Occupied rooms (reservations overlapping with the date range)
    rv = _t_reservation
    q = (Q.from_(rv).select(fn.Count("*"))
         .where(rv.room_type_id == P())
         .where(rv.company_id == P())
         .where(rv.reservation_status.isin(["confirmed", "checked_in"]))
         .where(rv.check_in_date < P())
         .where(rv.check_out_date > P()))
    occupied = conn.execute(q.get_sql(), (room_type_id, args.company_id, co, ci)).fetchone()[0]

    available = max(total_rooms - occupied, 0)
    ok({
        "room_type_id": room_type_id,
        "check_in_date": ci,
        "check_out_date": co,
        "total_rooms": total_rooms,
        "occupied": occupied,
        "available": available,
        "report_type": "availability",
    })


# ---------------------------------------------------------------------------
# 10. add-group-block
# ---------------------------------------------------------------------------
def add_group_block(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    room_type_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, room_type_id)

    rooms_blocked = getattr(args, "rooms_blocked", None)
    if not rooms_blocked:
        err("--rooms-blocked is required")
    ci = getattr(args, "check_in_date", None)
    co = getattr(args, "check_out_date", None)
    if not ci:
        err("--check-in-date is required")
    if not co:
        err("--check-out-date is required")

    gb_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_group_block", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("hospitalityclaw_group_block", {
        "id": P(), "naming_series": P(), "name": P(), "contact_name": P(),
        "contact_email": P(), "room_type_id": P(), "rooms_blocked": P(),
        "check_in_date": P(), "check_out_date": P(), "rate_amount": P(),
        "block_status": P(), "company_id": P(), "created_at": P(), "updated_at": P(),
    })
    conn.execute(sql, (
        gb_id, naming, name,
        getattr(args, "contact_name", None),
        getattr(args, "contact_email", None),
        room_type_id, int(rooms_blocked), ci, co,
        str(round_currency(to_decimal(getattr(args, "rate_amount", None) or "0"))),
        "tentative", args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_group_block", gb_id, "hospitality-add-group-block", args.company_id)
    conn.commit()
    ok({"id": gb_id, "naming_series": naming, "name": name, "block_status": "tentative"})


# ---------------------------------------------------------------------------
# 11. list-group-blocks
# ---------------------------------------------------------------------------
def list_group_blocks(conn, args):
    t = _t_group_block
    q_count = Q.from_(t).select(fn.Count("*"))
    q_rows = Q.from_(t).select(t.star)
    params = []

    if getattr(args, "company_id", None):
        q_count = q_count.where(t.company_id == P())
        q_rows = q_rows.where(t.company_id == P())
        params.append(args.company_id)
    if getattr(args, "block_status", None):
        q_count = q_count.where(t.block_status == P())
        q_rows = q_rows.where(t.block_status == P())
        params.append(args.block_status)

    total = conn.execute(q_count.get_sql(), params).fetchone()[0]
    q_rows = q_rows.orderby(t.created_at, order=Order.desc).limit(P()).offset(P())
    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 12. reservation-forecast-report
# ---------------------------------------------------------------------------
def reservation_forecast_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ed = getattr(args, "end_date", None) or (datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    rv = _t_reservation
    base_where = (rv.company_id == P()) & (rv.check_in_date >= P()) & (rv.check_in_date <= P()) & rv.reservation_status.isin(["pending", "confirmed"])

    q = Q.from_(rv).select(fn.Count("*")).where(base_where)
    upcoming = conn.execute(q.get_sql(), (args.company_id, sd, ed)).fetchone()[0]

    q = Q.from_(rv).select(fn.Coalesce(fn.Sum(rv.nights), 0)).where(base_where)
    total_nights = conn.execute(q.get_sql(), (args.company_id, sd, ed)).fetchone()[0]

    q = Q.from_(rv).select(fn.Coalesce(fn.Sum(rv.total_amount), 0)).where(base_where)
    total_revenue = conn.execute(q.get_sql(), (args.company_id, sd, ed)).fetchone()[0]

    ok({
        "start_date": sd,
        "end_date": ed,
        "upcoming_reservations": upcoming,
        "total_nights": total_nights,
        "forecasted_revenue": str(round_currency(to_decimal(str(total_revenue)))),
        "report_type": "reservation_forecast",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-reservation": add_reservation,
    "hospitality-update-reservation": update_reservation,
    "hospitality-get-reservation": get_reservation,
    "hospitality-list-reservations": list_reservations,
    "hospitality-confirm-reservation": confirm_reservation,
    "hospitality-cancel-reservation": cancel_reservation,
    "hospitality-add-rate-plan": add_rate_plan,
    "hospitality-list-rate-plans": list_rate_plans,
    "hospitality-check-availability": check_availability,
    "hospitality-add-group-block": add_group_block,
    "hospitality-list-group-blocks": list_group_blocks,
    "hospitality-reservation-forecast-report": reservation_forecast_report,
}
