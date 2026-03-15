"""HospitalityClaw -- fnb (food & beverage) domain module

Actions for outlets, room service, minibar (3 tables, 8 actions).
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
    from erpclaw_lib.query import Q, P, Table, Field, fn, Order, insert_row

    ENTITY_PREFIXES.setdefault("hospitalityclaw_room_service_order", "RSO-")
except ImportError:
    pass

_now_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_OUTLET_TYPES = ("restaurant", "bar", "room_service", "banquet", "pool")
VALID_ORDER_STATUSES = ("pending", "preparing", "delivered", "cancelled")


def _validate_company(conn, company_id):
    if not company_id:
        err("--company-id is required")
    row = conn.execute(Q.from_(Table("company")).select(Field("id")).where(Field("id") == P()).get_sql(), (company_id,)).fetchone()
    if not row:
        err(f"Company {company_id} not found")


def _validate_reservation(conn, res_id):
    if not res_id:
        err("--reservation-id is required")
    row = conn.execute(Q.from_(Table("hospitalityclaw_reservation")).select(Field("id")).where(Field("id") == P()).get_sql(), (res_id,)).fetchone()
    if not row:
        err(f"Reservation {res_id} not found")


def _validate_enum(value, valid_values, field_name):
    if value and value not in valid_values:
        err(f"Invalid {field_name}: {value}. Must be one of: {', '.join(valid_values)}")


# ---------------------------------------------------------------------------
# 1. add-outlet
# ---------------------------------------------------------------------------
def add_outlet(conn, args):
    _validate_company(conn, args.company_id)
    name = getattr(args, "name", None)
    if not name:
        err("--name is required")
    ot = getattr(args, "outlet_type", None)
    if not ot:
        err("--outlet-type is required")
    _validate_enum(ot, VALID_OUTLET_TYPES, "outlet-type")

    out_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("hospitalityclaw_outlet", {

        "id": P(), "name": P(), "outlet_type": P(), "operating_hours": P(), "company_id": P(), "created_at": P(),

    })

    conn.execute(sql, (out_id, name, ot, getattr(args, "operating_hours", None), args.company_id, now))
    audit(conn, "hospitalityclaw_outlet", out_id, "hospitality-add-outlet", args.company_id)
    conn.commit()
    ok({"id": out_id, "name": name, "outlet_type": ot})


# ---------------------------------------------------------------------------
# 2. list-outlets
# ---------------------------------------------------------------------------
def list_outlets(conn, args):
    t = Table("hospitalityclaw_outlet")

    q_count = Q.from_(t).select(fn.Count("*"))

    q_rows = Q.from_(t).select(t.star)

    params = []


    if getattr(args, "company_id", None):

        q_count = q_count.where(t.company_id == P())

        q_rows = q_rows.where(t.company_id == P())

        params.append(args.company_id)

    if getattr(args, "outlet_type", None):

        q_count = q_count.where(t.outlet_type == P())

        q_rows = q_rows.where(t.outlet_type == P())

        params.append(args.outlet_type)


    total = conn.execute(q_count.get_sql(), params).fetchone()[0]

    q_rows = q_rows.orderby(t.name, order=Order.asc).limit(P()).offset(P())

    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 3. add-room-service-order
# ---------------------------------------------------------------------------
def add_room_service_order(conn, args):
    res_id = getattr(args, "reservation_id", None)
    _validate_reservation(conn, res_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    outlet_id = getattr(args, "outlet_id", None)
    if not outlet_id:
        err("--outlet-id is required")
    out_row = conn.execute(Q.from_(Table("hospitalityclaw_outlet")).select(Field("id")).where(Field("id") == P()).get_sql(), (outlet_id,)).fetchone()
    if not out_row:
        err(f"Outlet {outlet_id} not found")

    items_json = getattr(args, "items_json", None)
    if not items_json:
        err("--items-json is required")
    # Validate JSON
    try:
        json.loads(items_json)
    except (json.JSONDecodeError, TypeError):
        err("--items-json must be valid JSON")

    total_amount = getattr(args, "total_amount", None)
    if not total_amount:
        err("--total-amount is required")

    order_id = str(uuid.uuid4())
    naming = get_next_name(conn, "hospitalityclaw_room_service_order", company_id=company_id)
    now = _now_iso()

    sql, _ = insert_row("hospitalityclaw_room_service_order", {


        "id": P(), "naming_series": P(), "reservation_id": P(), "outlet_id": P(), "order_time": P(), "items_json": P(), "total_amount": P(), "order_status": P(), "company_id": P(), "created_at": P(),


    })


    conn.execute(sql, (
        order_id, naming, res_id, outlet_id,
        now, items_json,
        str(round_currency(to_decimal(total_amount))),
        "pending", company_id, now,
    ))
    audit(conn, "hospitalityclaw_room_service_order", order_id, "hospitality-add-room-service-order", company_id)
    conn.commit()
    ok({"id": order_id, "naming_series": naming, "order_status": "pending",
        "total_amount": str(round_currency(to_decimal(total_amount)))})


# ---------------------------------------------------------------------------
# 4. list-room-service-orders
# ---------------------------------------------------------------------------
def list_room_service_orders(conn, args):
    t = Table("hospitalityclaw_room_service_order")

    q_count = Q.from_(t).select(fn.Count("*"))

    q_rows = Q.from_(t).select(t.star)

    params = []


    if getattr(args, "reservation_id", None):

        q_count = q_count.where(t.reservation_id == P())

        q_rows = q_rows.where(t.reservation_id == P())

        params.append(args.reservation_id)

    if getattr(args, "outlet_id", None):

        q_count = q_count.where(t.outlet_id == P())

        q_rows = q_rows.where(t.outlet_id == P())

        params.append(args.outlet_id)

    if getattr(args, "order_status", None):

        q_count = q_count.where(t.order_status == P())

        q_rows = q_rows.where(t.order_status == P())

        params.append(args.order_status)

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
# 5. complete-room-service-order
# ---------------------------------------------------------------------------
def complete_room_service_order(conn, args):
    order_id = getattr(args, "order_id", None)
    if not order_id:
        err("--order-id is required")
    row = conn.execute(Q.from_(Table("hospitalityclaw_room_service_order")).select(Field("order_status")).where(Field("id") == P()).get_sql(), (order_id,)).fetchone()
    if not row:
        err(f"Room service order {order_id} not found")
    if row[0] == "delivered":
        err("Order is already delivered")
    if row[0] == "cancelled":
        err("Cannot complete a cancelled order")

    conn.execute(
        "UPDATE hospitalityclaw_room_service_order SET order_status = 'delivered' WHERE id = ?",
        (order_id,)
    )
    audit(conn, "hospitalityclaw_room_service_order", order_id, "hospitality-complete-room-service-order", None)
    conn.commit()
    ok({"id": order_id, "order_status": "delivered"})


# ---------------------------------------------------------------------------
# 6. add-minibar-consumption
# ---------------------------------------------------------------------------
def add_minibar_consumption(conn, args):
    res_id = getattr(args, "reservation_id", None)
    _validate_reservation(conn, res_id)
    company_id = getattr(args, "company_id", None)
    _validate_company(conn, company_id)

    item_name = getattr(args, "item_name", None)
    if not item_name:
        err("--item-name is required")
    quantity = getattr(args, "quantity", None)
    if not quantity:
        err("--quantity is required")
    unit_price = getattr(args, "unit_price", None)
    if not unit_price:
        err("--unit-price is required")
    consumption_date = getattr(args, "consumption_date", None)
    if not consumption_date:
        err("--consumption-date is required")

    qty = int(quantity)
    up = round_currency(to_decimal(unit_price))
    total = round_currency(up * Decimal(qty))

    mb_id = str(uuid.uuid4())
    now = _now_iso()
    sql, _ = insert_row("hospitalityclaw_minibar_consumption", {

        "id": P(), "reservation_id": P(), "item_name": P(), "quantity": P(), "unit_price": P(), "total": P(), "consumption_date": P(), "company_id": P(), "created_at": P(),

    })

    conn.execute(sql, (
        mb_id, res_id, item_name, qty,
        str(up), str(total), consumption_date,
        company_id, now,
    ))
    audit(conn, "hospitalityclaw_minibar_consumption", mb_id, "hospitality-add-minibar-consumption", company_id)
    conn.commit()
    ok({"id": mb_id, "item_name": item_name, "quantity": qty, "total": str(total)})


# ---------------------------------------------------------------------------
# 7. list-minibar-consumptions
# ---------------------------------------------------------------------------
def list_minibar_consumptions(conn, args):
    t = Table("hospitalityclaw_minibar_consumption")

    q_count = Q.from_(t).select(fn.Count("*"))

    q_rows = Q.from_(t).select(t.star)

    params = []


    if getattr(args, "reservation_id", None):

        q_count = q_count.where(t.reservation_id == P())

        q_rows = q_rows.where(t.reservation_id == P())

        params.append(args.reservation_id)

    if getattr(args, "company_id", None):

        q_count = q_count.where(t.company_id == P())

        q_rows = q_rows.where(t.company_id == P())

        params.append(args.company_id)


    total = conn.execute(q_count.get_sql(), params).fetchone()[0]

    q_rows = q_rows.orderby(t.consumption_date, order=Order.desc).limit(P()).offset(P())

    rows = conn.execute(q_rows.get_sql(), params + [args.limit, args.offset]).fetchall()
    ok({"rows": [row_to_dict(r) for r in rows], "total_count": total,
        "limit": args.limit, "offset": args.offset, "has_more": (args.offset + args.limit) < total})


# ---------------------------------------------------------------------------
# 8. fnb-revenue-report
# ---------------------------------------------------------------------------
def fnb_revenue_report(conn, args):
    _validate_company(conn, args.company_id)
    sd = getattr(args, "start_date", None) or "2000-01-01"
    ed = getattr(args, "end_date", None) or "2099-12-31"

    rso_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total_amount AS REAL)), 0) FROM hospitalityclaw_room_service_order "
        "WHERE company_id = ? AND order_status != 'cancelled' "
        "AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    rso_count = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_room_service_order "
        "WHERE company_id = ? AND order_status != 'cancelled' "
        "AND created_at >= ? AND created_at <= ?",
        (args.company_id, sd, ed + "T23:59:59Z")
    ).fetchone()[0]

    minibar_revenue = conn.execute(
        "SELECT COALESCE(SUM(CAST(total AS REAL)), 0) FROM hospitalityclaw_minibar_consumption "
        "WHERE company_id = ? AND consumption_date >= ? AND consumption_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    minibar_count = conn.execute(
        "SELECT COUNT(*) FROM hospitalityclaw_minibar_consumption "
        "WHERE company_id = ? AND consumption_date >= ? AND consumption_date <= ?",
        (args.company_id, sd, ed)
    ).fetchone()[0]

    total_fnb = rso_revenue + minibar_revenue

    ok({
        "start_date": sd,
        "end_date": ed,
        "room_service_revenue": str(round_currency(to_decimal(str(rso_revenue)))),
        "room_service_orders": rso_count,
        "minibar_revenue": str(round_currency(to_decimal(str(minibar_revenue)))),
        "minibar_items": minibar_count,
        "total_fnb_revenue": str(round_currency(to_decimal(str(total_fnb)))),
        "report_type": "fnb_revenue",
    })


# ---------------------------------------------------------------------------
# Action Router
# ---------------------------------------------------------------------------
ACTIONS = {
    "hospitality-add-outlet": add_outlet,
    "hospitality-list-outlets": list_outlets,
    "hospitality-add-room-service-order": add_room_service_order,
    "hospitality-list-room-service-orders": list_room_service_orders,
    "hospitality-complete-room-service-order": complete_room_service_order,
    "hospitality-add-minibar-consumption": add_minibar_consumption,
    "hospitality-list-minibar-consumptions": list_minibar_consumptions,
    "hospitality-fnb-revenue-report": fnb_revenue_report,
}
