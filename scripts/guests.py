"""HospitalityClaw -- guests domain module

Actions for guest profiles and preferences (2 tables, 8 actions).
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
    from erpclaw_lib.naming import get_next_name, ENTITY_PREFIXES
    from erpclaw_lib.response import ok, err, row_to_dict
    from erpclaw_lib.audit import audit

    ENTITY_PREFIXES.setdefault("hospitalityclaw_guest", "GST-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_VIP_LEVELS = ("none", "silver", "gold", "platinum")
VALID_PREFERENCE_TYPES = ("room", "pillow", "floor", "diet", "newspaper", "other")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_guest(conn, guest_id):
    if not guest_id:
        err("--guest-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_guest WHERE id = ?", (guest_id,)).fetchone()
    if not row:
        err(f"Guest {guest_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-guest
# ---------------------------------------------------------------------------
def add_guest(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    vip = getattr(args, "vip_level", None) or "none"
    _validate_enum(vip, VALID_VIP_LEVELS, "vip-level")

    guest_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_guest", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO hospitalityclaw_guest (id, naming_series, name, email, phone,
            id_type, id_number, nationality, vip_level, loyalty_points,
            total_stays, total_spent, is_active, company_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        guest_id, naming, name,
        getattr(args, "email", None),
        getattr(args, "phone", None),
        getattr(args, "id_type", None),
        getattr(args, "id_number", None),
        getattr(args, "nationality", None),
        vip, 0, 0, "0", 1,
        args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_guest", guest_id, "hospitality-add-guest", args.company_id)
    conn.commit()
    ok({"id": guest_id, "naming_series": naming, "name": name, "vip_level": vip})


# ---------------------------------------------------------------------------
# 2. update-guest
# ---------------------------------------------------------------------------
def update_guest(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "name": "name", "email": "email", "phone": "phone",
        "id_type": "id_type", "id_number": "id_number",
        "nationality": "nationality",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    vip = getattr(args, "vip_level", None)
    if vip is not None:
        _validate_enum(vip, VALID_VIP_LEVELS, "vip-level")
        updates.append("vip_level = ?")
        params.append(vip)
        changed.append("vip_level")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(guest_id)
    conn.execute(f"UPDATE hospitalityclaw_guest SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "hospitalityclaw_guest", guest_id, "hospitality-update-guest", None, {"updated_fields": changed})
    conn.commit()
    ok({"id": guest_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. get-guest
# ---------------------------------------------------------------------------
def get_guest(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)
    row = conn.execute("SELECT * FROM hospitalityclaw_guest WHERE id = ?", (guest_id,)).fetchone()
    data = row_to_dict(row)

    # Enrich with preference count and reservation count
    pref_count = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_guest_preference WHERE guest_id = ?", (guest_id,)
    ).fetchone()[0]
    res_count = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_reservation WHERE guest_id = ?", (guest_id,)
    ).fetchone()[0]
    data["preference_count"] = pref_count
    data["reservation_count"] = res_count
    ok(data)


# ---------------------------------------------------------------------------
# 4. list-guests
# ---------------------------------------------------------------------------
def list_guests(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "vip_level", None):
        where.append("vip_level = ?")
        params.append(args.vip_level)
    if getattr(args, "search", None):
        where.append("(name LIKE ? OR email LIKE ? OR phone LIKE ?)")
        s = f"%{args.search}%"
        params.extend([s, s, s])

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_guest WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_guest WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 5. add-guest-preference
# ---------------------------------------------------------------------------
def add_guest_preference(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)
    _validate_company(conn, args.company_id)

    pt = getattr(args, "preference_type", None)
    if not pt:
        err("--preference-type is required")
    _validate_enum(pt, VALID_PREFERENCE_TYPES, "preference-type")

    pv = getattr(args, "preference_value", None)
    if not pv:
        err("--preference-value is required")

    pref_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_guest_preference (id, guest_id, preference_type, preference_value, company_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (pref_id, guest_id, pt, pv, args.company_id, now))
    audit(conn, "hospitalityclaw_guest_preference", pref_id, "hospitality-add-guest-preference", args.company_id)
    conn.commit()
    ok({"id": pref_id, "guest_id": guest_id, "preference_type": pt, "preference_value": pv})


# ---------------------------------------------------------------------------
# 6. list-guest-preferences
# ---------------------------------------------------------------------------
def list_guest_preferences(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    where, params = ["guest_id = ?"], [guest_id]
    if getattr(args, "preference_type", None):
        where.append("preference_type = ?")
        params.append(args.preference_type)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_guest_preference WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_guest_preference WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 7. guest-history
# ---------------------------------------------------------------------------
def guest_history(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    where_sql = "guest_id = ?"
    params = [guest_id]
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_reservation WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_reservation WHERE {where_sql} ORDER BY check_in_date DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 8. loyalty-summary
# ---------------------------------------------------------------------------
def loyalty_summary(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    row = conn.execute(
        "SELECT name, vip_level, loyalty_points, total_stays, total_spent FROM hospitalityclaw_guest WHERE id = ?",
        (guest_id,)
    ).fetchone()
    ok({
        "guest_id": guest_id,
        "name": row[0],
        "vip_level": row[1],
        "loyalty_points": row[2],
        "total_stays": row[3],
        "total_spent": row[4],
        "report_type": "loyalty_summary",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-guest": add_guest,
    "hospitality-update-guest": update_guest,
    "hospitality-get-guest": get_guest,
    "hospitality-list-guests": list_guests,
    "hospitality-add-guest-preference": add_guest_preference,
    "hospitality-list-guest-preferences": list_guest_preferences,
    "hospitality-guest-history": guest_history,
    "hospitality-loyalty-summary": loyalty_summary,
}
