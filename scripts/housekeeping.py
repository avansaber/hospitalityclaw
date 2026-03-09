"""HospitalityClaw -- housekeeping domain module

Actions for housekeeping tasks and room inspections (2 tables, 8 actions).
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

VALID_TASK_TYPES = ("checkout_clean", "stayover_clean", "deep_clean", "turndown", "inspection")
VALID_TASK_STATUSES = ("pending", "in_progress", "completed", "skipped")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute("SELECT id FROM company WHERE id = ?", (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


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
# 1. add-housekeeping-task
# ---------------------------------------------------------------------------
def add_housekeeping_task(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    tt = getattr(args, "task_type", None)
    if not tt:
        err("--task-type is required")
    _validate_enum(tt, VALID_TASK_TYPES, "task-type")

    sd = getattr(args, "scheduled_date", None)
    if not sd:
        err("--scheduled-date is required")

    task_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_housekeeping_task (id, room_id, task_type, assigned_to,
            scheduled_date, task_status, started_at, completed_at, notes, company_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        task_id, room_id, tt,
        getattr(args, "assigned_to", None),
        sd, "pending", None, None,
        getattr(args, "notes", None),
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_housekeeping_task", task_id, "hospitality-add-housekeeping-task", company_id)
    conn.commit()
    ok({"id": task_id, "task_type": tt, "task_status": "pending", "scheduled_date": sd})


# ---------------------------------------------------------------------------
# 2. list-housekeeping-tasks
# ---------------------------------------------------------------------------
def list_housekeeping_tasks(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "room_id", None):
        where.append("room_id = ?")
        params.append(args.room_id)
    if getattr(args, "task_status", None):
        where.append("task_status = ?")
        params.append(args.task_status)
    if getattr(args, "scheduled_date", None):
        where.append("scheduled_date = ?")
        params.append(args.scheduled_date)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_housekeeping_task WHERE {where_sql} ORDER BY scheduled_date ASC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 3. start-housekeeping-task
# ---------------------------------------------------------------------------
def start_housekeeping_task(conn, args):
    task_id = getattr(args, "task_id", None)
    if not task_id:
        err("--task-id is required")
    row = conn.execute("SELECT task_status FROM hospitalityclaw_housekeeping_task WHERE id = ?", (task_id,)).fetchone()
    if not row:
        err(f"Housekeeping task {task_id} not found")
    if row[0] != "pending":
        err(f"Cannot start task in '{row[0]}' status (must be pending)")

    now = _now_iso()
    conn.execute(
        "UPDATE hospitalityclaw_housekeeping_task SET task_status = 'in_progress', started_at = ? WHERE id = ?",
        (now, task_id)
    )
    audit(conn, "hospitalityclaw_housekeeping_task", task_id, "hospitality-start-housekeeping-task", None)
    conn.commit()
    ok({"id": task_id, "task_status": "in_progress"})


# ---------------------------------------------------------------------------
# 4. complete-housekeeping-task
# ---------------------------------------------------------------------------
def complete_housekeeping_task(conn, args):
    task_id = getattr(args, "task_id", None)
    if not task_id:
        err("--task-id is required")
    row = conn.execute("SELECT task_status, room_id FROM hospitalityclaw_housekeeping_task WHERE id = ?", (task_id,)).fetchone()
    if not row:
        err(f"Housekeeping task {task_id} not found")
    if row[0] == "completed":
        err("Task is already completed")

    now = _now_iso()
    notes = getattr(args, "notes", None)
    updates = "task_status = 'completed', completed_at = ?"
    params = [now]
    if notes:
        updates += ", notes = ?"
        params.append(notes)
    params.append(task_id)

    conn.execute(f"UPDATE hospitalityclaw_housekeeping_task SET {updates} WHERE id = ?", params)

    # If room was in cleaning status, mark available
    room_id = row[1]
    room_row = conn.execute("SELECT room_status FROM hospitalityclaw_room WHERE id = ?", (room_id,)).fetchone()
    if room_row and room_row[0] == "cleaning":
        conn.execute(
            "UPDATE hospitalityclaw_room SET room_status = 'available', updated_at = ? WHERE id = ?",
            (now, room_id)
        )

    audit(conn, "hospitalityclaw_housekeeping_task", task_id, "hospitality-complete-housekeeping-task", None)
    conn.commit()
    ok({"id": task_id, "task_status": "completed"})


# ---------------------------------------------------------------------------
# 5. add-inspection
# ---------------------------------------------------------------------------
def add_inspection(conn, args):
    room_id = getattr(args, "room_id", None)
    _validate_room(conn, room_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    inspector = getattr(args, "inspector", None)
    if not inspector:
        err("--inspector is required")
    insp_date = getattr(args, "inspection_date", None)
    if not insp_date:
        err("--inspection-date is required")
    score = getattr(args, "score", None)
    if score is None:
        err("--score is required")

    score_int = int(score)
    passed = 1 if score_int >= 70 else 0

    insp_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute("""
        INSERT INTO hospitalityclaw_inspection (id, room_id, inspector, inspection_date,
            score, passed, notes, company_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        insp_id, room_id, inspector, insp_date, score_int, passed,
        getattr(args, "notes", None), company_id, now,
    ))
    audit(conn, "hospitalityclaw_inspection", insp_id, "hospitality-add-inspection", company_id)
    conn.commit()
    ok({"id": insp_id, "score": score_int, "passed": passed})


# ---------------------------------------------------------------------------
# 6. list-inspections
# ---------------------------------------------------------------------------
def list_inspections(conn, args):
    where, params = ["1=1"], []
    if getattr(args, "company_id", None):
        where.append("company_id = ?")
        params.append(args.company_id)
    if getattr(args, "room_id", None):
        where.append("room_id = ?")
        params.append(args.room_id)

    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM hospitalityclaw_inspection WHERE {where_sql}", params).fetchone()[0]
    params.extend([args.limit, args.offset])
    rows = conn.execute(
        f"SELECT * FROM hospitalityclaw_inspection WHERE {where_sql} ORDER BY inspection_date DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 7. housekeeping-dashboard
# ---------------------------------------------------------------------------
def housekeeping_dashboard(conn, args):
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    sd = getattr(args, "scheduled_date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_housekeeping_task WHERE company_id = ? AND scheduled_date = ?",
        (company_id, sd)
    ).fetchone()[0]

    status_counts = conn.execute(
        "SELECT task_status, COUNT(*) FROM hospitalityclaw_housekeeping_task "
        "WHERE company_id = ? AND scheduled_date = ? GROUP BY task_status",
        (company_id, sd)
    ).fetchall()
    breakdown = {r[0]: r[1] for r in status_counts}

    # Rooms in cleaning status
    cleaning = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE company_id = ? AND room_status = 'cleaning'",
        (company_id,)
    ).fetchone()[0]

    ok({
        "scheduled_date": sd,
        "total_tasks": total,
        "pending": breakdown.get("pending", 0),
        "in_progress": breakdown.get("in_progress", 0),
        "completed": breakdown.get("completed", 0),
        "skipped": breakdown.get("skipped", 0),
        "rooms_in_cleaning": cleaning,
        "report_type": "housekeeping_dashboard",
    })


# ---------------------------------------------------------------------------
# 8. laundry-summary
# ---------------------------------------------------------------------------
def laundry_summary(conn, args):
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    # Laundry charges from folio
    laundry_total = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM hospitalityclaw_folio_charge "
        "WHERE company_id = ? AND charge_type = 'laundry' AND charge_date >= ? AND charge_date <= ?",
        (company_id, sd, ed)
    ).fetchone()[0]

    laundry_count = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_folio_charge "
        "WHERE company_id = ? AND charge_type = 'laundry' AND charge_date >= ? AND charge_date <= ?",
        (company_id, sd, ed)
    ).fetchone()[0]

    ok({
        "start_date": sd,
        "end_date": ed,
        "laundry_charges": laundry_count,
        "laundry_revenue": str(round_currency(to_decimal(str(laundry_total)))),
        "report_type": "laundry_summary",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-housekeeping-task": add_housekeeping_task,
    "hospitality-list-housekeeping-tasks": list_housekeeping_tasks,
    "hospitality-start-housekeeping-task": start_housekeeping_task,
    "hospitality-complete-housekeeping-task": complete_housekeeping_task,
    "hospitality-add-inspection": add_inspection,
    "hospitality-list-inspections": list_inspections,
    "hospitality-housekeeping-dashboard": housekeeping_dashboard,
    "hospitality-laundry-summary": laundry_summary,
}
