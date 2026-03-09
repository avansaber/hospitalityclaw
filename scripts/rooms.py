"""HospitalityClaw -- rooms domain module

Actions for rooms, room types, amenities (4 tables, 12 actions).
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

    ENTITY_PREFIXES.setdefault("hospitalityclaw_room_type", "RMT-")
    ENTITY_PREFIXES.setdefault("hospitalityclaw_room", "RM-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_ROOM_STATUSES = ("available", "occupied", "maintenance", "out_of_order", "cleaning")
VALID_AMENITY_TYPES = ("room", "property", "service")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_room_type(conn, room_type_id):
    if not room_type_id:
        err("--room-type-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_room_type WHERE id = ?", (room_type_id,)).fetchone()
    if not row:
        err(f"Room type {room_type_id} not found")


def _validate_room(conn, room_id):
    if not room_id:
        err("--room-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_room WHERE id = ?", (room_id,)).fetchone()
    if not row:
        err(f"Room {room_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-room-type
# ---------------------------------------------------------------------------
def add_room_type(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    base_rate = getattr(args, "base_rate", None)
    if not base_rate:
        err("--base-rate is required")
    max_occ = getattr(args, "max_occupancy", None)
    if not max_occ:
        err("--max-occupancy is required")

    rt_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_room_type", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO hospitalityclaw_room_type (id, naming_series, name, base_rate, max_occupancy,
            description, company_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        rt_id, naming, name,
        str(round_currency(to_decimal(base_rate))),
        int(max_occ),
        getattr(args, "description", None),
        args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_room_type", rt_id, "hospitality-add-room-type", args.company_id)
    conn.commit()
    ok({"id": rt_id, "naming_series": naming, "name": name})


# ---------------------------------------------------------------------------
# 2. update-room-type
# ---------------------------------------------------------------------------
def update_room_type(conn, args):
    rt_id = getattr(args, "room_type_id", None)
    if not rt_id:
        err("--room-type-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_room_type WHERE id = ?", (rt_id,)).fetchone()
    if not row:
        err(f"Room type {rt_id} not found")

    updates, params, changed = [], [], []
    for arg_name, col_name in {"name": "name", "description": "description"}.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    br = getattr(args, "base_rate", None)
    if br is not None:
        updates.append("base_rate = ?")
        params.append(str(round_currency(to_decimal(br))))
        changed.append("base_rate")

    mo = getattr(args, "max_occupancy", None)
    if mo is not None:
        updates.append("max_occupancy = ?")
        params.append(int(mo))
        changed.append("max_occupancy")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(rt_id)
    conn.execute(f"UPDATE hospitalityclaw_room_type SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "hospitalityclaw_room_type", rt_id, "hospitality-update-room-type", None, {"updated_fields": changed})
    conn.commit()
    ok({"id": rt_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 3. list-room-types
# ---------------------------------------------------------------------------
def list_room_types(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_room_type WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_room_type WHERE {where_sql} ORDER BY name ASC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 4. add-room
# ---------------------------------------------------------------------------
def add_room(conn, args):
    _validate_company(conn, args.company_id)
    room_number = getattr(args, "room_number", None)
    if not room_number:
        err("--room-number is required")
    room_type_id = getattr(args, "room_type_id", None)
    _validate_room_type(conn, room_type_id)

    # Check unique room number per company
    dup = conn.execute(
        "SELECT id FROM hospitalityclaw_room WHERE room_number = ? AND company_id = ?",
        (room_number, args.company_id)
    ).fetchone()
    if dup:
        err(f"Room number {room_number} already exists for this company")

    rm_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_room", company_id=args.company_id)
    now = _now_iso()

    conn.execute("""
        INSERT INTO hospitalityclaw_room (id, naming_series, room_number, room_type_id, floor,
            room_status, is_smoking, notes, company_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        rm_id, naming, room_number, room_type_id,
        int(getattr(args, "floor", None) or 1),
        "available",
        1 if getattr(args, "is_smoking", None) == "1" else 0,
        getattr(args, "notes", None),
        args.company_id, now, now,
    ))
    audit(conn, "hospitalityclaw_room", rm_id, "hospitality-add-room", args.company_id)
    conn.commit()
    ok({"id": rm_id, "naming_series": naming, "room_number": room_number, "room_status": "available"})


# ---------------------------------------------------------------------------
# 5. update-room
# ---------------------------------------------------------------------------
def update_room(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)

    updates, params, changed = [], [], []
    for arg_name, col_name in {
        "room_number": "room_number", "notes": "notes",
    }.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            updates.append(f"{col_name} = ?")
            params.append(val)
            changed.append(col_name)

    rt = getattr(args, "room_type_id", None)
    if rt is not None:
        _validate_room_type(conn, rt)
        updates.append("room_type_id = ?")
        params.append(rt)
        changed.append("room_type_id")

    fl = getattr(args, "floor", None)
    if fl is not None:
        updates.append("floor = ?")
        params.append(int(fl))
        changed.append("floor")

    ism = getattr(args, "is_smoking", None)
    if ism is not None:
        updates.append("is_smoking = ?")
        params.append(1 if ism == "1" else 0)
        changed.append("is_smoking")

    if not updates:
        err("No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(room_id)
    conn.execute(f"UPDATE hospitalityclaw_room SET {', '.join(updates)} WHERE id = ?", params)
    audit(conn, "hospitalityclaw_room", room_id, "hospitality-update-room", None, {"updated_fields": changed})
    conn.commit()
    ok({"id": room_id, "updated_fields": changed})


# ---------------------------------------------------------------------------
# 6. get-room
# ---------------------------------------------------------------------------
def get_room(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)
    row = conn.execute("SELECT * FROM hospitalityclaw_room WHERE id = ?", (room_id,)).fetchone()
    data = row_to_dict(row)

    # Enrich with room type name
    rt_row = conn.execute("SELECT name FROM hospitalityclaw_room_type WHERE id = ?", (data["room_type_id"],)).fetchone()
    data["room_type_name"] = rt_row[0] if rt_row else None

    # Amenities
    amenities = conn.execute(
        "SELECT a.name, a.amenity_type FROM hospitalityclaw_room_amenity ra "
        "JOIN hospitalityclaw_amenity a ON ra.amenity_id = a.id WHERE ra.room_id = ?",
        (room_id,)
    ).fetchall()
    data["amenities"] = [{"name": a[0], "amenity_type": a[1]} for a in amenities]
    ok(data)


# ---------------------------------------------------------------------------
# 7. list-rooms
# ---------------------------------------------------------------------------
def list_rooms(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "room_type_id", None):
        where.append("room_type_id = ?")
        params.append(args.room_type_id)
    if getattr(args, "room_status", None):
        where.append("room_status = ?")
        params.append(args.room_status)
    if getattr(args, "floor", None):
        where.append("floor = ?")
        params.append(int(args.floor))

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_room WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_room WHERE {where_sql} ORDER BY room_number ASC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 8. update-room-status
# ---------------------------------------------------------------------------
def update_room_status(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)
    rs = getattr(args, "room_status", None)
    if not rs:
        err("--room-status is required")
    _validate_enum(rs, VALID_ROOM_STATUSES, "room-status")

    conn.execute("UPDATE hospitalityclaw_room SET room_status = ?, updated_at = datetime('now') WHERE id = ?",
                 (rs, room_id))
    audit(conn, "hospitalityclaw_room", room_id, "hospitality-update-room-status", None, {"room_status": rs})
    conn.commit()
    ok({"id": room_id, "room_status": rs})


# ---------------------------------------------------------------------------
# 9. add-amenity
# ---------------------------------------------------------------------------
def add_amenity(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    at = getattr(args, "amenity_type", None)
    if not at:
        err("--amenity-type is required")
    _validate_enum(at, VALID_AMENITY_TYPES, "amenity-type")

    am_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_amenity (id, name, amenity_type, description, company_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (am_id, name, at, getattr(args, "description", None), args.company_id, now))
    audit(conn, "hospitalityclaw_amenity", am_id, "hospitality-add-amenity", args.company_id)
    conn.commit()
    ok({"id": am_id, "name": name, "amenity_type": at})


# ---------------------------------------------------------------------------
# 10. list-amenities
# ---------------------------------------------------------------------------
def list_amenities(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "amenity_type", None):
        where.append("amenity_type = ?")
        params.append(args.amenity_type)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_amenity WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_amenity WHERE {where_sql} ORDER BY name ASC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 11. assign-amenity
# ---------------------------------------------------------------------------
def assign_amenity(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)
    amenity_id = getattr(args, "amenity_id", None)
    if not amenity_id:
        err("--amenity-id is required")
    row = conn.execute("SELECT id FROM hospitalityclaw_amenity WHERE id = ?", (amenity_id,)).fetchone()
    if not row:
        err(f"Amenity {amenity_id} not found")
    _validate_company(conn, args.company_id)

    # Check duplicate
    dup = conn.execute(
        "SELECT id FROM hospitalityclaw_room_amenity WHERE room_id = ? AND amenity_id = ?",
        (room_id, amenity_id)
    ).fetchone()
    if dup:
        err("This amenity is already assigned to this room")

    ra_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO hospitalityclaw_room_amenity (id, room_id, amenity_id, company_id) VALUES (?,?,?,?)",
        (ra_id, room_id, amenity_id, args.company_id)
    )
    audit(conn, "hospitalityclaw_room_amenity", ra_id, "hospitality-assign-amenity", args.company_id)
    conn.commit()
    ok({"id": ra_id, "room_id": room_id, "amenity_id": amenity_id})


# ---------------------------------------------------------------------------
# 12. room-availability-report
# ---------------------------------------------------------------------------
def room_availability_report(conn, args):
    _validate_company(conn, args.company_id)

    total_rooms = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE company_id = ?", (args.company_id,)
    ).fetchone()[0]

    status_counts = conn.execute(
        "SELECT room_status, COUNT(*) FROM hospitalityclaw_room WHERE company_id = ? GROUP BY room_status",
        (args.company_id,)
    ).fetchall()
    breakdown = {r[0]: r[1] for r in status_counts}

    ok({
        "total_rooms": total_rooms,
        "available": breakdown.get("available", 0),
        "occupied": breakdown.get("occupied", 0),
        "maintenance": breakdown.get("maintenance", 0),
        "out_of_order": breakdown.get("out_of_order", 0),
        "cleaning": breakdown.get("cleaning", 0),
        "report_type": "room_availability",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-room-type": add_room_type,
    "hospitality-update-room-type": update_room_type,
    "hospitality-list-room-types": list_room_types,
    "hospitality-add-room": add_room,
    "hospitality-update-room": update_room,
    "hospitality-get-room": get_room,
    "hospitality-list-rooms": list_rooms,
    "hospitality-update-room-status": update_room_status,
    "hospitality-add-amenity": add_amenity,
    "hospitality-list-amenities": list_amenities,
    "hospitality-assign-amenity": assign_amenity,
    "hospitality-room-availability-report": room_availability_report,
}
