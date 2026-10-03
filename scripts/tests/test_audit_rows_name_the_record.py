"""Audit rows name the record: skill=module, action=action, entity triple.

Every hospitalityclaw action that writes an audit row must store
(skill, action, entity_type, entity_id) = ("hospitalityclaw", "<action>",
"<table>", <record id>) so the trail for a record is found under that
record's id. Reads go through PyPika with bound parameters.
"""
import argparse
import importlib.util
import io
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from unittest.mock import patch

from hospitality_helpers import (
    call_action, ns, is_ok, load_db_query, SRC_DIR,
    seed_reservation, seed_room,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import P, Q, Table

SKILL = "hospitalityclaw"

_FOUNDATION_SCRIPTS = os.path.join(SRC_DIR, "erpclaw", "scripts")


def _load_foundation(domain):
    path = os.path.join(_FOUNDATION_SCRIPTS, domain, "db_query.py")
    spec = importlib.util.spec_from_file_location("_fnd_%s" % domain, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delegate_customers_in_process(conn, monkeypatch):
    selling = _load_foundation("erpclaw-selling")
    from erpclaw_lib.cross_skill import CrossSkillError
    from erpclaw_lib import cross_skill as _cs

    def _run(fn, args_ns):
        buf = io.StringIO()

        def _fake_exit(code=0):
            raise SystemExit(code)

        try:
            with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
                fn(conn, args_ns)
        except SystemExit:
            pass
        return json.loads(buf.getvalue().strip())

    def _in_process(skill_name, action, args=None, db_path=None, timeout=30):
        if action != "add-customer":
            raise AssertionError("unexpected cross-skill action %r" % (action,))
        result = _run(selling.add_customer, argparse.Namespace(
            name=(args or {}).get("--name"),
            company_id=(args or {}).get("--company-id"),
            customer_type=(args or {}).get("--customer-type"),
            customer_group=None, payment_terms_id=None, tax_id=None,
            credit_limit=None, exempt_from_sales_tax=None,
            primary_address=None, primary_contact=None,
            email=(args or {}).get("--email"), phone=(args or {}).get("--phone"),
            default_price_list_id=None, custom_fields=None))
        if result.get("status") == "error":
            raise CrossSkillError(result.get("message", "add-customer failed"))
        return result

    monkeypatch.setattr(_cs, "call_skill_action", _in_process)


def _audit_rows_for(conn, entity_id):
    t = Table("audit_log")
    q = (Q.from_(t).select(t.skill, t.action, t.entity_type, t.entity_id,
                           t.new_values).where(t.entity_id == P()))
    return conn.execute(q.get_sql(), (entity_id,)).fetchall()


def _assert_single_audit(conn, record_id, skill, action, entity_type):
    rows = _audit_rows_for(conn, record_id)
    matched = [r for r in rows
               if r[0] == skill and r[1] == action
               and r[2] == entity_type and r[3] == record_id]
    assert len(matched) == 1, (
        "expected exactly one audit row %r for entity %r, got %d of %d rows: %r"
        % ((skill, action, entity_type), record_id, len(matched), len(rows),
           [(r[0], r[1], r[2], r[3]) for r in rows]))
    return matched[0]


def _confirm(conn, reservation_id):
    result = call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=reservation_id))
    assert is_ok(result), result


def _check_in(conn, reservation_id, room_id):
    result = call_action(
        ACTIONS["hospitality-check-in"], conn,
        ns(reservation_id=reservation_id, room_id=room_id))
    assert is_ok(result), result
    return result


def test_front_desk_audit_rows(conn, env, monkeypatch):
    monkeypatch.setitem(ACTIONS["hospitality-check-out"].__globals__, "_now_iso", lambda: "2026-04-03T11:00:00Z")
    cid = env["company_id"]
    r_assign = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-01", check_out="2026-05-03",
        rate_amount="150.00", reservation_status="pending")
    result = call_action(
        ACTIONS["hospitality-assign-room"], conn,
        ns(reservation_id=r_assign, room_id=env["room_102_id"]))
    assert is_ok(result), result
    _assert_single_audit(conn, r_assign,
                         "hospitalityclaw", "hospitality-assign-room",
                         "hospitalityclaw_reservation")

    r_in = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-04", check_out="2026-05-06",
        rate_amount="150.00", reservation_status="confirmed")
    _check_in(conn, r_in, env["room_101_id"])
    _assert_single_audit(conn, r_in,
                         "hospitalityclaw", "hospitality-check-in",
                         "hospitalityclaw_reservation")

    result = call_action(
        ACTIONS["hospitality-add-guest-request"], conn,
        ns(reservation_id=env["reservation_id"], company_id=cid,
           request_type="amenity", description="Extra towels",
           priority="normal"))
    assert is_ok(result), result
    req_id = result["id"]
    _assert_single_audit(conn, req_id,
                         "hospitalityclaw", "hospitality-add-guest-request",
                         "hospitalityclaw_guest_request")

    result = call_action(
        ACTIONS["hospitality-complete-guest-request"], conn,
        ns(request_id=req_id))
    assert is_ok(result), result
    _assert_single_audit(conn, req_id,
                         "hospitalityclaw",
                         "hospitality-complete-guest-request",
                         "hospitalityclaw_guest_request")

    r_late = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-07", check_out="2026-05-09",
        rate_amount="150.00", reservation_status="confirmed")
    _check_in(conn, r_late, env["room_201_id"])
    result = call_action(
        ACTIONS["hospitality-late-checkout"], conn,
        ns(reservation_id=r_late, new_checkout_date="2026-05-11"))
    assert is_ok(result), result
    _assert_single_audit(conn, r_late,
                         "hospitalityclaw", "hospitality-late-checkout",
                         "hospitalityclaw_reservation")

    room_a = seed_room(conn, env["std_room_type_id"], cid, "801", 8)
    room_b = seed_room(conn, env["std_room_type_id"], cid, "802", 8)
    r_move = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-12", check_out="2026-05-14",
        rate_amount="150.00", reservation_status="confirmed")
    _check_in(conn, r_move, room_a)
    result = call_action(
        ACTIONS["hospitality-room-move"], conn,
        ns(reservation_id=r_move, new_room_id=room_b,
           reason="Guest preference"))
    assert is_ok(result), result
    _assert_single_audit(conn, r_move,
                         "hospitalityclaw", "hospitality-room-move",
                         "hospitalityclaw_reservation")

    room_c = seed_room(conn, env["std_room_type_id"], cid, "803", 8)
    r_charge = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-15", check_out="2026-05-17",
        rate_amount="150.00", reservation_status="confirmed")
    _check_in(conn, r_charge, room_c)
    result = call_action(
        ACTIONS["hospitality-add-charge"], conn,
        ns(reservation_id=r_charge, company_id=cid,
           charge_type="room", description="Room night",
           amount="150.00"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-charge",
                         "hospitalityclaw_folio_charge")

    room_d = seed_room(conn, env["std_room_type_id"], cid, "804", 8)
    r_out = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-05-18", check_out="2026-05-20",
        rate_amount="150.00", reservation_status="confirmed")
    _check_in(conn, r_out, room_d)
    result = call_action(
        ACTIONS["hospitality-check-out"], conn,
        ns(reservation_id=r_out,
           receivable_account_id=env["ar"],
           revenue_account_id=env["revenue"],
           cost_center_id=env["cost_center_id"]))
    assert is_ok(result), result
    _assert_single_audit(conn, r_out,
                         "hospitalityclaw", "hospitality-check-out",
                         "hospitalityclaw_reservation")


def test_fnb_audit_rows(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-outlet"], conn,
        ns(company_id=cid, name="Audit Bar", outlet_type="bar"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-outlet",
                         "hospitalityclaw_outlet")

    result = call_action(
        ACTIONS["hospitality-add-room-service-order"], conn,
        ns(reservation_id=env["reservation_id"], company_id=cid,
           outlet_id=env["outlet_id"],
           items_json=json.dumps([{"name": "Burger", "qty": 1}]),
           total_amount="18.00"))
    assert is_ok(result), result
    order_id = result["id"]
    _assert_single_audit(conn, order_id,
                         "hospitalityclaw",
                         "hospitality-add-room-service-order",
                         "hospitalityclaw_room_service_order")

    result = call_action(
        ACTIONS["hospitality-complete-room-service-order"], conn,
        ns(order_id=order_id))
    assert is_ok(result), result
    _assert_single_audit(conn, order_id,
                         "hospitalityclaw",
                         "hospitality-complete-room-service-order",
                         "hospitalityclaw_room_service_order")

    result = call_action(
        ACTIONS["hospitality-add-minibar-consumption"], conn,
        ns(reservation_id=env["reservation_id"], company_id=cid,
           item_name="Soda", quantity="2", unit_price="5.00",
           consumption_date="2026-04-02"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw",
                         "hospitality-add-minibar-consumption",
                         "hospitalityclaw_minibar_consumption")


def test_guests_audit_rows(conn, env, monkeypatch):
    cid = env["company_id"]
    _delegate_customers_in_process(conn, monkeypatch)
    result = call_action(
        ACTIONS["hospitality-add-guest"], conn,
        ns(company_id=cid, customer_name="Audit Guest",
           customer_type="individual", email="audit@example.com",
           phone="555-0142", vip_level="silver"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-guest",
                         "hospitalityclaw_guest_ext")

    result = call_action(
        ACTIONS["hospitality-update-guest"], conn,
        ns(guest_id=env["guest_id"], vip_level="gold"))
    assert is_ok(result), result
    _assert_single_audit(conn, env["guest_id"],
                         "hospitalityclaw", "hospitality-update-guest",
                         "hospitalityclaw_guest_ext")

    result = call_action(
        ACTIONS["hospitality-add-guest-preference"], conn,
        ns(guest_id=env["guest_id"], company_id=cid,
           preference_type="room", preference_value="High floor"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw",
                         "hospitality-add-guest-preference",
                         "hospitalityclaw_guest_preference")


def test_housekeeping_audit_rows(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-housekeeping-task"], conn,
        ns(room_id=env["room_101_id"], company_id=cid,
           task_type="checkout_clean", scheduled_date="2026-04-03"))
    assert is_ok(result), result
    task_id = result["id"]
    _assert_single_audit(conn, task_id,
                         "hospitalityclaw",
                         "hospitality-add-housekeeping-task",
                         "hospitalityclaw_housekeeping_task")

    result = call_action(
        ACTIONS["hospitality-start-housekeeping-task"], conn,
        ns(task_id=task_id))
    assert is_ok(result), result
    _assert_single_audit(conn, task_id,
                         "hospitalityclaw",
                         "hospitality-start-housekeeping-task",
                         "hospitalityclaw_housekeeping_task")

    result = call_action(
        ACTIONS["hospitality-complete-housekeeping-task"], conn,
        ns(task_id=task_id, notes="All done"))
    assert is_ok(result), result
    _assert_single_audit(conn, task_id,
                         "hospitalityclaw",
                         "hospitality-complete-housekeeping-task",
                         "hospitalityclaw_housekeeping_task")

    result = call_action(
        ACTIONS["hospitality-add-inspection"], conn,
        ns(room_id=env["room_101_id"], company_id=cid,
           inspector="Audit Inspector", inspection_date="2026-04-03",
           score="85"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-inspection",
                         "hospitalityclaw_inspection")


def test_reservations_audit_rows(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-reservation"], conn,
        ns(company_id=cid, guest_id=env["guest_id"],
           room_type_id=env["std_room_type_id"],
           check_in_date="2026-06-01", check_out_date="2026-06-03",
           rate_amount="150.00"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-reservation",
                         "hospitalityclaw_reservation")

    r_upd = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-06-04", check_out="2026-06-06",
        rate_amount="150.00", reservation_status="pending")
    result = call_action(
        ACTIONS["hospitality-update-reservation"], conn,
        ns(reservation_id=r_upd, special_requests="Late arrival"))
    assert is_ok(result), result
    _assert_single_audit(conn, r_upd,
                         "hospitalityclaw",
                         "hospitality-update-reservation",
                         "hospitalityclaw_reservation")

    r_conf = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-06-07", check_out="2026-06-09",
        rate_amount="150.00", reservation_status="pending")
    result = call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=r_conf))
    assert is_ok(result), result
    _assert_single_audit(conn, r_conf,
                         "hospitalityclaw",
                         "hospitality-confirm-reservation",
                         "hospitalityclaw_reservation")

    r_can = seed_reservation(
        conn, env["guest_id"], env["std_room_type_id"], cid,
        check_in="2026-06-10", check_out="2026-06-12",
        rate_amount="150.00", reservation_status="pending")
    result = call_action(
        ACTIONS["hospitality-cancel-reservation"], conn,
        ns(reservation_id=r_can, reason="Changed plans"))
    assert is_ok(result), result
    _assert_single_audit(conn, r_can,
                         "hospitalityclaw",
                         "hospitality-cancel-reservation",
                         "hospitalityclaw_reservation")

    result = call_action(
        ACTIONS["hospitality-add-rate-plan"], conn,
        ns(company_id=cid, name="Audit Plan",
           room_type_id=env["std_room_type_id"], rate_amount="120.00",
           start_date="2026-06-01", end_date="2026-08-31",
           rate_type="seasonal"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-rate-plan",
                         "hospitalityclaw_rate_plan")

    result = call_action(
        ACTIONS["hospitality-add-group-block"], conn,
        ns(company_id=cid, name="Audit Block",
           room_type_id=env["std_room_type_id"], rooms_blocked="5",
           check_in_date="2026-07-01", check_out_date="2026-07-05",
           contact_name="Audit Contact", rate_amount="140.00"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-group-block",
                         "hospitalityclaw_group_block")


def test_revenue_audit_rows(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-rate-adjustment"], conn,
        ns(room_type_id=env["std_room_type_id"], company_id=cid,
           adjustment_date="2026-04-15", adjustment_type="increase",
           adjustment_pct="10"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw",
                         "hospitality-add-rate-adjustment",
                         "hospitalityclaw_rate_adjustment")

    result = call_action(
        ACTIONS["hospitality-set-seasonal-rates"], conn,
        ns(room_type_id=env["std_room_type_id"], company_id=cid,
           start_date="2026-06-01", end_date="2026-08-31",
           adjusted_rate="200.00"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw",
                         "hospitality-set-seasonal-rates",
                         "hospitalityclaw_rate_adjustment")


def test_rooms_audit_rows(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-room-type"], conn,
        ns(company_id=cid, name="Audit Suite",
           base_rate="500.00", max_occupancy="4"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-room-type",
                         "hospitalityclaw_room_type")

    from hospitality_helpers import seed_room_type
    rt_upd = seed_room_type(conn, cid, "UpdateMe", "100.00", 2)
    result = call_action(
        ACTIONS["hospitality-update-room-type"], conn,
        ns(room_type_id=rt_upd, name="Updated Name"))
    assert is_ok(result), result
    _assert_single_audit(conn, rt_upd,
                         "hospitalityclaw", "hospitality-update-room-type",
                         "hospitalityclaw_room_type")

    result = call_action(
        ACTIONS["hospitality-add-room"], conn,
        ns(company_id=cid, room_number="901",
           room_type_id=env["std_room_type_id"], floor="9"))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-add-room",
                         "hospitalityclaw_room")

    room_upd = seed_room(conn, env["std_room_type_id"], cid, "902", 9)
    result = call_action(
        ACTIONS["hospitality-update-room"], conn,
        ns(room_id=room_upd, notes="Audit note"))
    assert is_ok(result), result
    _assert_single_audit(conn, room_upd,
                         "hospitalityclaw", "hospitality-update-room",
                         "hospitalityclaw_room")

    room_st = seed_room(conn, env["std_room_type_id"], cid, "903", 9)
    result = call_action(
        ACTIONS["hospitality-update-room-status"], conn,
        ns(room_id=room_st, room_status="maintenance"))
    assert is_ok(result), result
    _assert_single_audit(conn, room_st,
                         "hospitalityclaw", "hospitality-update-room-status",
                         "hospitalityclaw_room")

    result = call_action(
        ACTIONS["hospitality-add-amenity"], conn,
        ns(company_id=cid, name="Audit WiFi", amenity_type="room"))
    assert is_ok(result), result
    amenity_id = result["id"]
    _assert_single_audit(conn, amenity_id,
                         "hospitalityclaw", "hospitality-add-amenity",
                         "hospitalityclaw_amenity")

    result = call_action(
        ACTIONS["hospitality-assign-amenity"], conn,
        ns(company_id=cid, room_id=env["room_101_id"],
           amenity_id=amenity_id))
    assert is_ok(result), result
    _assert_single_audit(conn, result["id"],
                         "hospitalityclaw", "hospitality-assign-amenity",
                         "hospitalityclaw_room_amenity")


def test_no_audit_row_is_keyed_by_company(conn, env):
    cid = env["company_id"]
    result = call_action(
        ACTIONS["hospitality-add-room-type"], conn,
        ns(company_id=cid, name="Company Check",
           base_rate="300.00", max_occupancy="2"))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-add-guest-preference"], conn,
        ns(guest_id=env["guest_id"], company_id=cid,
           preference_type="pillow", preference_value="Firm"))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-add-housekeeping-task"], conn,
        ns(room_id=env["room_101_id"], company_id=cid,
           task_type="deep_clean", scheduled_date="2026-04-05"))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-add-outlet"], conn,
        ns(company_id=cid, name="Company Diner",
           outlet_type="restaurant"))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-add-rate-adjustment"], conn,
        ns(room_type_id=env["std_room_type_id"], company_id=cid,
           adjustment_date="2026-04-20", adjustment_type="decrease",
           adjustment_pct="5"))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-add-reservation"], conn,
        ns(company_id=cid, guest_id=env["guest_id"],
           room_type_id=env["std_room_type_id"],
           check_in_date="2026-08-01", check_out_date="2026-08-03",
           rate_amount="150.00"))
    assert is_ok(result), result

    t = Table("audit_log")
    q = Q.from_(t).select(t.skill, t.action, t.entity_type, t.entity_id)
    rows = conn.execute(q.get_sql()).fetchall()
    assert rows, "expected audit rows from the flows above"
    for row in rows:
        skill, action, entity_type, entity_id = row[0], row[1], row[2], row[3]
        assert not str(skill).startswith("hospitalityclaw_"), (
            "skill must be the module name, got %r" % (skill,))
        if skill == "hospitalityclaw":
            assert entity_id != cid, (
                "no hospitalityclaw audit row may be keyed by company id")
            assert str(action).startswith("hospitality-"), (
                "action must start with the module prefix, got %r" % (action,))


def test_update_room_status_new_values(conn, env):
    from hospitality_helpers import seed_room as _seed_room
    room_id = _seed_room(conn, env["std_room_type_id"],
                         env["company_id"], "904", 9)
    result = call_action(
        ACTIONS["hospitality-update-room-status"], conn,
        ns(room_id=room_id, room_status="maintenance"))
    assert is_ok(result), result
    row = _assert_single_audit(conn, room_id,
                               "hospitalityclaw",
                               "hospitality-update-room-status",
                               "hospitalityclaw_room")
    assert json.loads(row[4]) == {"room_status": "maintenance"}
