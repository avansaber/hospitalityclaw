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
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row
except ImportError:
    pass

# GL posting -- optional (graceful degradation if erpclaw-setup not installed)
try:
    from erpclaw_lib.gl_posting import insert_gl_entries
    HAS_GL = True
except ImportError:
    HAS_GL = False

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_REQUEST_TYPES = ("housekeeping", "maintenance", "amenity", "food", "other")
VALID_PRIORITIES = ("low", "normal", "high", "urgent")
VALID_CHARGE_TYPES = ("room", "food", "minibar", "phone", "laundry", "parking", "other")

_t_reservation = Table("hospitalityclaw_reservation")
_t_room = Table("hospitalityclaw_room")
_t_guest_ext = Table("hospitalityclaw_guest_ext")
_t_guest_request = Table("hospitalityclaw_guest_request")
_t_folio = Table("hospitalityclaw_folio_charge")


def _validate_reservation(conn, res_id):
    if not res_id:
        err("--reservation-id is required")
    q = Q.from_(_t_reservation).select(_t_reservation.star).where(_t_reservation.id == P())
    row = conn.execute(q.get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")
    return row_to_dict(row)


def _validate_room(conn, room_id):
    if not room_id:
        err("--room-id is required")
    q = Q.from_(_t_room).select(_t_room.star).where(_t_room.id == P())
    row = conn.execute(q.get_sql(), (room_id,)).fetchone()
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
    # Update guest_ext total_stays
    conn.execute(
        "UPDATE hospitalityclaw_guest_ext SET total_stays = total_stays + 1, updated_at = ? WHERE id = ?",
        (now, res["guest_id"])
    )

    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-check-in", None, {"room_id": room_id})
    conn.commit()
    ok({"reservation_id": res["id"], "room_id": room_id,
        "reservation_status": "checked_in", "room_number": room["room_number"]})


# ---------------------------------------------------------------------------
# 2. check-out (with optional GL posting for folio close)
# ---------------------------------------------------------------------------
def _build_checkout_gl_entries(conn, reservation_id, receivable_account_id,
                                revenue_account_id, cost_center_id, customer_id):
    """Build balanced GL entries for guest checkout / folio close.

    DR: Accounts Receivable (total folio) -- party_type=customer, party_id=customer_id
    CR: Revenue accounts by charge type:
        - 'room' charges  -> revenue_account_id (Room Revenue)
        - 'food','minibar' charges -> revenue_account_id (F&B Revenue)
        - all other charges -> revenue_account_id (Other Revenue)

    If only one revenue account is provided, all credits go there.
    Returns list of entry dicts, or empty list if no charges.
    """
    # Fetch all folio charges and aggregate by category using Python Decimal
    q = Q.from_(_t_folio).select(_t_folio.charge_type, _t_folio.amount).where(_t_folio.reservation_id == P())
    rows = conn.execute(q.get_sql(), (reservation_id,)).fetchall()

    if not rows:
        return []

    # Map charge_types to revenue categories
    ROOM_TYPES = {"room"}
    FNB_TYPES = {"food", "minibar"}
    # everything else is "other": phone, laundry, parking, other

    room_total = Decimal("0")
    fnb_total = Decimal("0")
    other_total = Decimal("0")

    for row in rows:
        ct = row[0]  # charge_type
        amt = to_decimal(row[1])
        if ct in ROOM_TYPES:
            room_total += amt
        elif ct in FNB_TYPES:
            fnb_total += amt
        else:
            other_total += amt

    room_total = round_currency(room_total)
    fnb_total = round_currency(fnb_total)
    other_total = round_currency(other_total)
    grand_total = round_currency(room_total + fnb_total + other_total)

    if grand_total <= Decimal("0"):
        return []

    entries = []

    # DR: Accounts Receivable for grand total
    entries.append({
        "account_id": receivable_account_id,
        "debit": str(grand_total),
        "credit": "0",
        "party_type": "customer",
        "party_id": customer_id,
    })

    # CR: Revenue entries (only add non-zero buckets)
    # All go to the same revenue_account_id (single revenue account provided)
    # but separated for clarity in the ledger via remarks
    if room_total > Decimal("0"):
        entries.append({
            "account_id": revenue_account_id,
            "debit": "0",
            "credit": str(room_total),
            "cost_center_id": cost_center_id,
        })

    if fnb_total > Decimal("0"):
        entries.append({
            "account_id": revenue_account_id,
            "debit": "0",
            "credit": str(fnb_total),
            "cost_center_id": cost_center_id,
        })

    if other_total > Decimal("0"):
        entries.append({
            "account_id": revenue_account_id,
            "debit": "0",
            "credit": str(other_total),
            "cost_center_id": cost_center_id,
        })

    return entries


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

    # Update guest_ext total_spent (use Decimal for accuracy)
    q = Q.from_(_t_folio).select(_t_folio.amount).where(_t_folio.reservation_id == P())
    folio_rows = conn.execute(q.get_sql(), (res["id"],)).fetchall()
    folio_total = sum(to_decimal(r[0]) for r in folio_rows) if folio_rows else Decimal("0")
    folio_total = round_currency(folio_total)

    # Update guest_ext total_spent using Decimal math (not CAST AS REAL)
    q = Q.from_(_t_guest_ext).select(_t_guest_ext.total_spent).where(_t_guest_ext.id == P())
    guest_row = conn.execute(q.get_sql(), (res["guest_id"],)).fetchone()
    prev_spent = to_decimal(guest_row[0]) if guest_row else Decimal("0")
    new_spent = round_currency(prev_spent + folio_total)
    conn.execute(
        "UPDATE hospitalityclaw_guest_ext SET total_spent = ?, updated_at = ? WHERE id = ?",
        (str(new_spent), now, res["guest_id"])
    )

    # --- GL Posting (optional -- graceful degradation) ---
    gl_entry_ids = []
    gl_error = None
    receivable_account_id = getattr(args, "receivable_account_id", None)
    revenue_account_id = getattr(args, "revenue_account_id", None)
    cost_center_id = getattr(args, "cost_center_id", None)

    if HAS_GL and receivable_account_id and revenue_account_id and folio_total > Decimal("0"):
        try:
            # Look up customer_id from guest_ext
            q = Q.from_(_t_guest_ext).select(_t_guest_ext.customer_id).where(_t_guest_ext.id == P())
            guest_ext = conn.execute(q.get_sql(), (res["guest_id"],)).fetchone()
            customer_id = guest_ext[0] if guest_ext else None

            if not customer_id:
                gl_error = "Guest has no linked customer_id; GL posting skipped"
            else:
                gl_entries = _build_checkout_gl_entries(
                    conn, res["id"],
                    receivable_account_id, revenue_account_id,
                    cost_center_id, customer_id,
                )

                if gl_entries:
                    posting_date = now[:10]  # ISO date portion
                    gl_entry_ids = insert_gl_entries(
                        conn, gl_entries,
                        voucher_type="hospitality_checkout",
                        voucher_id=res["id"],
                        posting_date=posting_date,
                        company_id=res["company_id"],
                        remarks=f"HospitalityClaw checkout folio close for reservation {res['id']}",
                    )
                    # Store GL entry IDs on the reservation
                    conn.execute(
                        "UPDATE hospitalityclaw_reservation SET gl_entry_ids = ? WHERE id = ?",
                        (json.dumps(gl_entry_ids), res["id"])
                    )
        except Exception as e:
            # GL posting is optional -- do not block checkout
            gl_error = str(e)

    audit(conn, "hospitalityclaw_reservation", res["id"], "hospitality-check-out", None)
    conn.commit()

    result = {
        "reservation_id": res["id"],
        "reservation_status": "checked_out",
        "folio_total": str(folio_total),
    }
    if gl_entry_ids:
        result["gl_entry_ids"] = gl_entry_ids
        result["gl_posted"] = True
    elif gl_error:
        result["gl_posted"] = False
        result["gl_warning"] = gl_error
    else:
        result["gl_posted"] = False

    ok(result)


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
    sql, _ = insert_row("hospitalityclaw_guest_request", {
        "id": P(), "reservation_id": P(), "request_type": P(), "description": P(),
        "priority": P(), "request_status": P(), "assigned_to": P(),
        "completed_at": P(), "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
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
    t = _t_guest_request
    q_count = Q.from_(t).select(fn.Count("*"))
    q_rows = Q.from_(t).select(t.star)
    params = []

    if getattr(args, "reservation_id", None):
        q_count = q_count.where(t.reservation_id == P())
        q_rows = q_rows.where(t.reservation_id == P())
        params.append(args.reservation_id)
    if getattr(args, "request_status", None):
        q_count = q_count.where(t.request_status == P())
        q_rows = q_rows.where(t.request_status == P())
        params.append(args.request_status)
    if getattr(args, "company_id", None):
        q_count = q_count.where(t.company_id == P())
        q_rows = q_rows.where(t.company_id == P())
        params.append(args.company_id)

    total = conn.execute(q_count.get_sql(), params).fetchone()[0]
    q_rows = q_rows.orderby(t.created_at, order=Order.desc).limit(P()).offset(P())
    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 6. complete-guest-request
# ---------------------------------------------------------------------------
def complete_guest_request(conn, args):
    req_id = getattr(args, "request_id", None)
    if not req_id:
        err("--request-id is required")
    q = Q.from_(_t_guest_request).select(_t_guest_request.request_status).where(_t_guest_request.id == P())
    row = conn.execute(q.get_sql(), (req_id,)).fetchone()
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
    sql, _ = insert_row("hospitalityclaw_folio_charge", {
        "id": P(), "reservation_id": P(), "charge_date": P(), "charge_type": P(),
        "description": P(), "amount": P(), "company_id": P(), "created_at": P(),
    })
    conn.execute(sql, (
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
    t = _t_folio
    q_count = Q.from_(t).select(fn.Count("*"))
    q_rows = Q.from_(t).select(t.star)
    q_total = Q.from_(t).select(fn.Coalesce(fn.Sum(t.amount), 0))
    params = []

    if getattr(args, "reservation_id", None):
        q_count = q_count.where(t.reservation_id == P())
        q_rows = q_rows.where(t.reservation_id == P())
        q_total = q_total.where(t.reservation_id == P())
        params.append(args.reservation_id)
    if getattr(args, "charge_type", None):
        q_count = q_count.where(t.charge_type == P())
        q_rows = q_rows.where(t.charge_type == P())
        q_total = q_total.where(t.charge_type == P())
        params.append(args.charge_type)
    if getattr(args, "company_id", None):
        q_count = q_count.where(t.company_id == P())
        q_rows = q_rows.where(t.company_id == P())
        q_total = q_total.where(t.company_id == P())
        params.append(args.company_id)

    total = conn.execute(q_count.get_sql(), params).fetchone()[0]
    q_rows = q_rows.orderby(t.created_at, order=Order.desc).limit(P()).offset(P())
    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()

    # Calculate folio total
    folio_total = conn.execute(q_total.get_sql(), params).fetchone()[0]

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
