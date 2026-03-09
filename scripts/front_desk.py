"""HospitalityClaw -- front_desk domain module

Actions for check-in/out, room assignments, guest requests, folio charges (2 tables, 10 actions).
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
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_REQUEST_TYPES = ("housekeeping", "maintenance", "amenity", "food", "other")
VALID_PRIORITIES = ("low", "normal", "high", "urgent")
VALID_CHARGE_TYPES = ("room", "food", "minibar", "phone", "laundry", "parking", "other")


def _validate_reservation(conn, res_id):
    if not res_id:
        err("--reservation-id is required")
    row = conn.execute("SELECT * FROM hospitalityclaw_reservation WHERE id = ?", (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    return row_to_dict(row)


def _validate_room(conn, room_id):
    if not room_id:
        err("--room-id is required")
    row = conn.execute("SELECT * FROM hospitalityclaw_room WHERE id = ?", (room_id,)).fetchone()
    if not row:
        err(f"Room {room_id} not found")
    return row_to_dict(row)


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. check-in
# ---------------------------------------------------------------------------
def check_in(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))
    if res["reservation_status"] not in ("confirmed", "pending"):
        err(f"Cannot check in reservation in '{res['reservation_status']}' status")

    room_id = getattr(args, "room_id", None)
    room = _validate_room(conn, room_id)
    if room["room_status"] != "available":
        err(f"Room {room['room_number']} is not available (status: {room['room_status']})")

    now = _now_iso()
    # Update reservation
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET reservation_status = 'checked_in', room_id = ?, updated_at = ? WHERE id = ?",
        (room_id, now, res["id"])
    )
    # Mark room occupied
    conn.execute(
        "UPDATE hospitalityclaw_room SET room_status = 'occupied', updated_at = ? WHERE id = ?",
        (now, room_id)
    )
    # Update guest total_stays
    conn.execute(
        "UPDATE hospitalityclaw_guest SET total_stays = total_stays + 1, updated_at = ? WHERE id = ?",
        (now, res["guest_id"])
    )

    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-check-in", None, {"room_id": room_id})
    conn.commit()
    ok({"reservation_id": res["id"], "room_id": room_id,
        "reservation_status": "checked_in", "room_number": room["room_number"]})


# ---------------------------------------------------------------------------
# 2. check-out
# ---------------------------------------------------------------------------
def check_out(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))
    if res["reservation_status"] != "checked_in":
        err(f"Cannot check out reservation in '{res['reservation_status']}' status (must be checked_in)")

    now = _now_iso()
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET reservation_status = 'checked_out', updated_at = ? WHERE id = ?",
        (now, res["id"])
    )

    # Release room
    if res.get("room_id"):
        conn.execute(
            "UPDATE hospitalityclaw_room SET room_status = 'cleaning', updated_at = ? WHERE id = ?",
            (now, res["room_id"])
        )

    # Update guest total_spent
    folio_total = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM hospitalityclaw_folio_charge WHERE reservation_id = ?",
        (res["id"],)
    ).fetchone()[0]
    conn.execute(
        "UPDATE hospitalityclaw_guest SET total_spent = CAST((CAST(total_spent AS REAL) + ?) AS TEXT), updated_at = ? WHERE id = ?",
        (folio_total, now, res["guest_id"])
    )

    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-check-out", None)
    conn.commit()
    ok({"reservation_id": res["id"], "reservation_status": "checked_out",
        "folio_total": str(round_currency(to_decimal(str(folio_total))))})


# ---------------------------------------------------------------------------
# 3. assign-room
# ---------------------------------------------------------------------------
def assign_room(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))
    if res["reservation_status"] not in ("pending", "confirmed"):
        err(f"Cannot assign room for reservation in '{res['reservation_status']}' status")

    room_id = getattr(args, "room_id", None)
    room = _validate_room(conn, room_id)

    conn.execute(
        "UPDATE hospitalityclaw_reservation SET room_id = ?, updated_at = datetime('now') WHERE id = ?",
        (room_id, res["id"])
    )
    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-assign-room", None, {"room_id": room_id})
    conn.commit()
    ok({"reservation_id": res["id"], "room_id": room_id, "room_number": room["room_number"]})


# ---------------------------------------------------------------------------
# 4. add-guest-request
# ---------------------------------------------------------------------------
def add_guest_request(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))

    rt = getattr(args, "request_type", None)
    if not rt:
        err("--request-type is required")
    _validate_enum(rt, VALID_REQUEST_TYPES, "request-type")

    desc = getattr(args, "description", None)
    if not desc:
        err("--description is required")

    priority = getattr(args, "priority", None) or "normal"
    _validate_enum(priority, VALID_PRIORITIES, "priority")

    company_id = getattr(args, "company_id", None)
    if not company_id:
        err("--company-id is required")

    req_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_guest_request (id, reservation_id, request_type, description,
            priority, request_status, assigned_to, completed_at, company_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        req_id, res["id"], rt, desc, priority, "open",
        getattr(args, "assigned_to", None), None,
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_guest_request", req_id, "hospitality-add-guest-request", company_id)
    conn.commit()
    ok({"id": req_id, "request_type": rt, "priority": priority, "request_status": "open"})


# ---------------------------------------------------------------------------
# 5. list-guest-requests
# ---------------------------------------------------------------------------
def list_guest_requests(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "reservation_id", None):
        where.append("reservation_id = ?")
        params.append(args.reservation_id)
    if getattr(args, "request_status", None):
        where.append("request_status = ?")
        params.append(args.request_status)
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_guest_request WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_guest_request WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 6. complete-guest-request
# ---------------------------------------------------------------------------
def complete_guest_request(conn, args):
    req_id = getattr(args, "request_id", None)
    if not req_id:
        err("--request-id is required")
    row = conn.execute("SELECT request_status FROM hospitalityclaw_guest_request WHERE id = ?", (req_id,)).fetchone()
    if not row:
        err(f"Guest request {req_id} not found")
    if row[0] == "completed":
        err("Request is already completed")

    now = _now_iso()
    conn.execute(
        "UPDATE hospitalityclaw_guest_request SET request_status = 'completed', completed_at = ? WHERE id = ?",
        (now, req_id)
    )
    audit(conn, "hospitalityclaw_guest_request", req_id, "hospitality-complete-guest-request", None)
    conn.commit()
    ok({"id": req_id, "request_status": "completed"})


# ---------------------------------------------------------------------------
# 7. late-checkout
# ---------------------------------------------------------------------------
def late_checkout(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))
    if res["reservation_status"] != "checked_in":
        err(f"Cannot set late checkout for reservation in '{res['reservation_status']}' status")

    new_co = getattr(args, "new_checkout_date", None)
    if not new_co:
        err("--new-checkout-date is required")
    if new_co <= res["check_out_date"]:
        err("New checkout date must be after current checkout date")

    from datetime import date as date_cls
    nights = max((date_cls.fromisoformat(new_co) - date_cls.fromisoformat(res["check_in_date"])).days, 1)
    rate_dec = round_currency(to_decimal(res["rate_amount"]))
    total = round_currency(rate_dec * Decimal(nights))

    conn.execute(
        "UPDATE hospitalityclaw_reservation SET check_out_date = ?, nights = ?, total_amount = ?, updated_at = datetime('now') WHERE id = ?",
        (new_co, nights, str(total), res["id"])
    )
    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-late-checkout", None, {"new_checkout_date": new_co})
    conn.commit()
    ok({"reservation_id": res["id"], "new_checkout_date": new_co, "nights": nights, "total_amount": str(total)})


# ---------------------------------------------------------------------------
# 8. room-move
# ---------------------------------------------------------------------------
def room_move(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))
    if res["reservation_status"] != "checked_in":
        err(f"Cannot move room for reservation in '{res['reservation_status']}' status")

    new_room_id = getattr(args, "new_room_id", None)
    new_room = _validate_room(conn, new_room_id)
    if new_room["room_status"] != "available":
        err(f"New room {new_room['room_number']} is not available (status: {new_room['room_status']})")

    now = _now_iso()
    old_room_id = res.get("room_id")

    # Release old room
    if old_room_id:
        conn.execute(
            "UPDATE hospitalityclaw_room SET room_status = 'cleaning', updated_at = ? WHERE id = ?",
            (now, old_room_id)
        )

    # Assign new room
    conn.execute(
        "UPDATE hospitalityclaw_room SET room_status = 'occupied', updated_at = ? WHERE id = ?",
        (now, new_room_id)
    )
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET room_id = ?, updated_at = ? WHERE id = ?",
        (new_room_id, now, res["id"])
    )

    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-room-move", None,
          {"old_room_id": old_room_id, "new_room_id": new_room_id,
           "reason": getattr(args, "reason", None)})
    conn.commit()
    ok({"reservation_id": res["id"], "old_room_id": old_room_id,
        "new_room_id": new_room_id, "new_room_number": new_room["room_number"]})


# ---------------------------------------------------------------------------
# 9. add-charge (folio charge)
# ---------------------------------------------------------------------------
def add_charge(conn, args):
    res = _validate_reservation(conn, getattr(args, "reservation_id", None))

    ct = getattr(args, "charge_type", None)
    if not ct:
        err("--charge-type is required")
    _validate_enum(ct, VALID_CHARGE_TYPES, "charge-type")

    desc = getattr(args, "description", None)
    if not desc:
        err("--description is required")

    amount = getattr(args, "amount", None)
    if not amount:
        err("--amount is required")

    company_id = getattr(args, "company_id", None)
    if not company_id:
        err("--company-id is required")

    charge_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_folio_charge (id, reservation_id, charge_date, charge_type,
            description, amount, company_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        charge_id, res["id"], now[:10], ct, desc,
        str(round_currency(to_decimal(amount))),
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_folio_charge", charge_id, "hospitality-add-charge", company_id)
    conn.commit()
    ok({"id": charge_id, "charge_type": ct, "amount": str(round_currency(to_decimal(amount)))})


# ---------------------------------------------------------------------------
# 10. list-folio-charges
# ---------------------------------------------------------------------------
def list_folio_charges(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "reservation_id", None):
        where.append("reservation_id = ?")
        params.append(args.reservation_id)
    if getattr(args, "charge_type", None):
        where.append("charge_type = ?")
        params.append(args.charge_type)
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_folio_charge WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_folio_charge WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()

    # Calculate folio total
    folio_total = conn.execute(
        f"SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM hospitalityclaw_folio_charge WHERE {' AND '.join(where[:len(where)])}",
        params[:len(params) - 2]
    ).fetchone()[0]

    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "folio_total": str(round_currency(to_decimal(str(folio_total)))),
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-check-in": check_in,
    "hospitality-check-out": check_out,
    "hospitality-assign-room": assign_room,
    "hospitality-add-guest-request": add_guest_request,
    "hospitality-list-guest-requests": list_guest_requests,
    "hospitality-complete-guest-request": complete_guest_request,
    "hospitality-late-checkout": late_checkout,
    "hospitality-room-move": room_move,
    "hospitality-add-charge": add_charge,
    "hospitality-list-folio-charges": list_folio_charges,
}
