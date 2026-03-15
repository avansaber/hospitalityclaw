"""HospitalityClaw -- guests domain module

Actions for guest profiles and preferences (2 tables, 8 actions).
Uses core customer table for name/email/phone via cross_skill.create_customer().
hospitalityclaw_guest_ext is an extension table linking to customer(id).

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
    from erpclaw_lib.cross_skill import create_customer, CrossSkillError
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row

    ENTITY_PREFIXES.setdefault("hospitalityclaw_guest_ext", "HGST-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_VIP_LEVELS = ("regular", "silver", "gold", "platinum", "diamond")
VALID_PREFERENCE_TYPES = ("room", "pillow", "floor", "diet", "newspaper", "other")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute(Q.from_(Table("company")).select(Field("id")).where(Field("id") == P()).get_sql(), (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_guest(conn, guest_id):
    if not guest_id:
        err("--guest-id is required")
    row = conn.execute(Q.from_(Table("hospitalityclaw_guest_ext")).select(Field("id")).where(Field("id") == P()).get_sql(), (guest_id,)).fetchone()
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
    name = getattr(args, "customer_name", None) or getattr(args, "name", None)
    if not name:
        err("--customer-name (or --name) is required")
    vip = getattr(args, "vip_level", None) or "regular"
    _validate_enum(vip, VALID_VIP_LEVELS, "vip-level")

    # Step 1: Create core customer via cross_skill
    email = getattr(args, "email", None)
    phone = getattr(args, "phone", None)
    customer_type = getattr(args, "customer_type", None) or "individual"

    try:
        cust_result = create_customer(
            customer_name=name,
            company_id=args.company_id,
            customer_type=customer_type,
            email=email,
            phone=phone,
        )
    except CrossSkillError as e:
        err(f"Failed to create core customer: {e}")

    customer_id = cust_result.get("id") or cust_result.get("customer_id")
    if not customer_id:
        err(f"Core customer creation returned no ID: {cust_result}")

    # Step 2: Create extension record
    guest_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_guest_ext", company_id=args.company_id)
    now = _now_iso()

    sql, _ = insert_row("hospitalityclaw_guest_ext", {


        "id": P(), "naming_series": P(), "customer_id": P(), "id_type": P(), "id_number": P(), "nationality": P(), "vip_level": P(), "loyalty_points": P(), "total_stays": P(), "total_spent": P(), "is_active": P(), "company_id": P(), "created_at": P(), "updated_at": P(),


    })


    conn.execute(sql, (
        guest_id, naming, customer_id,
        getattr(args, "id_type", None),
        getattr(args, "id_number", None),
        getattr(args, "nationality", None),
        vip, 0, 0, "0", 1,
        args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_guest_ext", guest_id, "hospitality-add-guest", args.company_id)
    conn.commit()
    ok({"id": guest_id, "customer_id": customer_id, "naming_series": naming,
        "customer_name": name, "vip_level": vip})


# ---------------------------------------------------------------------------
# 2. update-guest
# ---------------------------------------------------------------------------
def update_guest(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    # Fetch the ext row to get customer_id
    ext_row = conn.execute(Q.from_(Table("hospitalityclaw_guest_ext")).select(Field("customer_id")).where(Field("id") == P()).get_sql(), (guest_id,)).fetchone()
    customer_id = ext_row[0]

    # Core customer fields: update directly in customer table
    core_updates, core_params, changed = [], [], []
    for arg_name, col_name in {
        "customer_name": "customer_name",
        "name": "customer_name",
        "email": "email",
        "phone": "phone",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            # Avoid duplicate customer_name if both --name and --customer-name given
            if col_name == "customer_name" and "customer_name" in changed:
                continue
            core_updates.append(f"{col_name} = ?")
            core_params.append(val)
            changed.append(col_name)

    if core_updates:
        core_updates.append("updated_at = datetime('now')")
        core_params.append(customer_id)
        conn.execute(f"UPDATE customer SET {', '.join(core_updates)} WHERE id = ?", core_params)

    # Extension fields: update in guest_ext table
    ext_updates, ext_params = [], []
    for arg_name, col_name in {
        "id_type": "id_type", "id_number": "id_number",
        "nationality": "nationality",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            ext_updates.append(f"{col_name} = ?")
            ext_params.append(val)
            changed.append(col_name)

    vip = getattr(args, "vip_level", None)
    if vip is not None:
        _validate_enum(vip, VALID_VIP_LEVELS, "vip-level")
        ext_updates.append("vip_level = ?")
        ext_params.append(vip)
        changed.append("vip_level")

    if not changed:
        err("No fields to update")

    if ext_updates:
        ext_updates.append("updated_at = datetime('now')")
        ext_params.append(guest_id)
        conn.execute(f"UPDATE hospitalityclaw_guest_ext SET {', '.join(ext_updates)} WHERE id = ?", ext_params)

    audit(conn, "hospitalityclaw_guest_ext", guest_id, "hospitality-update-guest", None, {"updated_fields": changed})
    conn.commit()
    ok({"id": guest_id, "customer_id": customer_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. get-guest
# ---------------------------------------------------------------------------
def get_guest(conn, args):
    guest_id = getattr(args, "guest_id", None)
    _validate_guest(conn, guest_id)

    row = conn.execute("""
        SELECT g.*, c.customer_name, c.email, c.phone
        FROM hospitalityclaw_guest_ext g
        JOIN customer c ON c.id = g.customer_id
        WHERE g.id = ?
    """, (guest_id,)).fetchone()
    data = row_to_dict(row)

    # Enrich with preference count and reservation count
    pref_count = conn.execute(Q.from_(Table("hospitalityclaw_guest_preference")).select(fn.Count("*")).where(Field("guest_id") == P()).get_sql(), (guest_id,)).fetchone()[0]
    res_count = conn.execute(Q.from_(Table("hospitalityclaw_reservation")).select(fn.Count("*")).where(Field("guest_id") == P()).get_sql(), (guest_id,)).fetchone()[0]
    data["preference_count"] = pref_count
    data["reservation_count"] = res_count
    ok(data)


# ---------------------------------------------------------------------------
# 4. list-guests
# ---------------------------------------------------------------------------
def list_guests(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("g.company_id = ?")
        params.append(args.company_id)
    if getattr(args, "vip_level", None):
        where.append("g.vip_level = ?")
        params.append(args.vip_level)
    if getattr(args, "search", None):
        where.append("(c.customer_name LIKE ? OR c.email LIKE ? OR c.phone LIKE ?)")
        s = f"%{args.search}%"
        params.extend([s, s, s])

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM hospitalityclaw_guest_ext g JOIN customer c ON c.id = g.customer_id WHERE {where_sql}",
        params,
    ).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"""SELECT g.*, c.customer_name, c.email, c.phone
            FROM hospitalityclaw_guest_ext g
            JOIN customer c ON c.id = g.customer_id
            WHERE {where_sql}
            ORDER BY g.created_at DESC LIMIT ? OFFSET ?""",
        params,
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
    sql, _ = insert_row("hospitalityclaw_guest_preference", {

        "id": P(), "guest_id": P(), "preference_type": P(), "preference_value": P(), "company_id": P(), "created_at": P(),

    })

    conn.execute(sql, (pref_id, guest_id, pt, pv, args.company_id, now))
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

    row = conn.execute("""
        SELECT c.customer_name, g.vip_level, g.loyalty_points, g.total_stays, g.total_spent
        FROM hospitalityclaw_guest_ext g
        JOIN customer c ON c.id = g.customer_id
        WHERE g.id = ?
    """, (guest_id,)).fetchone()
    ok({
        "guest_id": guest_id,
        "customer_name": row[0],
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
