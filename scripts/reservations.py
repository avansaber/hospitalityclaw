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


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_guest(conn, guest_id):
    if not guest_id:
        err("--guest-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_guest_ext WHERE id = ?", (guest_id,)).fetchone()
    if not row:
        err(f"Guest {guest_id} not found")


def _validate_room_type(conn, room_type_id):
    if not room_type_id:
        err("--room-type-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_room_type WHERE id = ?", (room_type_id,)).fetchone()
    if not row:
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
        rp = conn.execute("SELECT id FROM hospitalityclaw_rate_plan WHERE id = ?", (rate_plan_id,)).fetchone()
        if not rp:
            err(f"Rate plan {rate_plan_id} not found")

    room_id = getattr(args, "room_id", None)
    if room_id:
        rm = conn.execute("SELECT id FROM hospitalityclaw_room WHERE id = ?", (room_id,)).fetchone()
        if not rm:
            err(f"Room {room_id} not found")

    res_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_reservation", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO hospitalityclaw_reservation (id, naming_series, guest_id, room_type_id, room_id,
            check_in_date, check_out_date, nights, adults, children,
            rate_plan_id, rate_amount, total_amount,
            reservation_status, source, special_requests, company_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
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
    row = conn.execute("SELECT * FROM hospitalityclaw_reservation WHERE id = ?", (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    current = row_to_dict(row)

    # Can only update pending/confirmed reservations
    if current["reservation_status"] not in ("pending", "confirmed"):
        err(f"Cannot update reservation in '{current['reservation_status']}' status")

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "special_requests": "special_requests",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    rt = getattr(args, "room_type_id", None)
    if rt is not None:
        conn.execute("SELECT id FROM hospitalityclaw_room_type WHERE id = ?", (rt,)).fetchone() or err(f"Room type {rt} not found")
        updates.append("room_type_id = ?")
        params.append(rt)
        changed.append("room_type_id")

    adults = getattr(args, "adults", None)
    if adults is not None:
        updates.append("adults = ?")
        params.append(int(adults))
        changed.append("adults")

    children = getattr(args, "children", None)
    if children is not None:
        updates.append("children = ?")
        params.append(int(children))
        changed.append("children")

    # Recalculate if dates or rate changed
    ci = getattr(args, "check_in_date", None) or current["check_in_date"]
    co = getattr(args, "check_out_date", None) or current["check_out_date"]
    ra = getattr(args, "rate_amount", None)

    if getattr(args, "check_in_date", None):
        updates.append("check_in_date = ?")
        params.append(ci)
        changed.append("check_in_date")
    if getattr(args, "check_out_date", None):
        updates.append("check_out_date = ?")
        params.append(co)
        changed.append("check_out_date")
    if ra is not None:
        updates.append("rate_amount = ?")
        params.append(str(round_currency(to_decimal(ra))))
        changed.append("rate_amount")

    if getattr(args, "check_in_date", None) or getattr(args, "check_out_date", None) or ra is not None:
        nights = _calc_nights(ci, co)
        rate_dec = round_currency(to_decimal(ra or current["rate_amount"]))
        total = round_currency(rate_dec * Decimal(nights))
        updates.append("nights = ?")
        params.append(nights)
        updates.append("total_amount = ?")
        params.append(str(total))

    if not updates:
        err("No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(res_id)
    conn.execute(f"UPDATE hospitalityclaw_reservation SET {', '.join(updates)} WHERE id = ?", params)
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
    row = conn.execute("SELECT * FROM hospitalityclaw_reservation WHERE id = ?", (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    data = row_to_dict(row)

    # Enrich with guest name (via guest_ext → customer) and room type name
    g = conn.execute(
        "SELECT c.customer_name FROM hospitalityclaw_guest_ext ge JOIN customer c ON c.id = ge.customer_id WHERE ge.id = ?",
        (data["guest_id"],)
    ).fetchone()
    data["guest_name"] = g[0] if g else None
    rt = conn.execute("SELECT name FROM hospitalityclaw_room_type WHERE id = ?", (data["room_type_id"],)).fetchone()
    data["room_type_name"] = rt[0] if rt else None

    # Folio total
    folio_total = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM hospitalityclaw_folio_charge WHERE reservation_id = ?",
        (res_id,)
    ).fetchone()[0]
    data["folio_total"] = str(round_currency(to_decimal(str(folio_total))))
    ok(data)


# ---------------------------------------------------------------------------
# 4. list-reservations
# ---------------------------------------------------------------------------
def list_reservations(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "guest_id", None):
        where.append("guest_id = ?")
        params.append(args.guest_id)
    if getattr(args, "reservation_status", None):
        where.append("reservation_status = ?")
        params.append(args.reservation_status)
    if getattr(args, "source", None):
        where.append("source = ?")
        params.append(args.source)
    if getattr(args, "check_in_date", None):
        where.append("check_in_date = ?")
        params.append(args.check_in_date)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_reservation WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_reservation WHERE {where_sql} ORDER BY check_in_date DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 5. confirm-reservation
# ---------------------------------------------------------------------------
def confirm_reservation(conn, args):
    res_id = getattr(args, "reservation_id", None)
    if not res_id:
        err("--reservation-id is required")
    row = conn.execute("SELECT reservation_status FROM hospitalityclaw_reservation WHERE id = ?", (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    if row[0] != "pending":
        err(f"Cannot confirm reservation in '{row[0]}' status (must be pending)")

    conn.execute(
        "UPDATE hospitalityclaw_reservation SET reservation_status = 'confirmed', updated_at = datetime('now') WHERE id = ?",
        (res_id,)
    )
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
    row = conn.execute("SELECT reservation_status FROM hospitalityclaw_reservation WHERE id = ?", (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    if row[0] in ("checked_in", "checked_out", "cancelled"):
        err(f"Cannot cancel reservation in '{row[0]}' status")

    conn.execute(
        "UPDATE hospitalityclaw_reservation SET reservation_status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
        (res_id,)
    )
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

    conn.execute("""
        INSERT INTO hospitalityclaw_rate_plan (id, naming_series, name, room_type_id, rate_amount,
            start_date, end_date, rate_type, is_active, company_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
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
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "room_type_id", None):
        where.append("room_type_id = ?")
        params.append(args.room_type_id)
    if getattr(args, "rate_type", None):
        where.append("rate_type = ?")
        params.append(args.rate_type)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_rate_plan WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_rate_plan WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
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
    total_rooms = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE room_type_id = ? AND company_id = ? AND room_status IN ('available','occupied')",
        (room_type_id, args.company_id)
    ).fetchone()[0]

    # Occupied rooms (reservations overlapping with the date range)
    occupied = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation "
        "WHERE room_type_id = ? AND company_id = ? "
        "AND reservation_status IN ('confirmed','checked_in') "
        "AND check_in_date < ? AND check_out_date > ?",
        (room_type_id, args.company_id, co, ci)
    ).fetchone()[0]

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

    conn.execute("""
        INSERT INTO hospitalityclaw_group_block (id, naming_series, name, contact_name, contact_email,
            room_type_id, rooms_blocked, check_in_date, check_out_date, rate_amount,
            block_status, company_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
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
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "block_status", None):
        where.append("block_status = ?")
        params.append(args.block_status)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_group_block WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_group_block WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 12. reservation-forecast-report
# ---------------------------------------------------------------------------
def reservation_forecast_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ed = getattr(args, "end_date", None) or (datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    upcoming = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('pending','confirmed')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    total_nights = conn.execute(
        "SELECT COALESCE(SUM(nights), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('pending','confirmed')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS REAL)), 0) FROM hospitalityclaw_reservation "
        "WHERE company_id = ? AND check_in_date >= ? AND check_in_date <= ? "
        "AND reservation_status IN ('pending','confirmed')",
        (args.company_id, sd, ed)
    ).fetchone()[0]

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
