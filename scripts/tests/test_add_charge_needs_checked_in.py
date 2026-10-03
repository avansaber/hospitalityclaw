"""A folio charge is accepted only while the guest is checked in.

Refusals leave hospitalityclaw_folio_charge, hospitalityclaw_reservation
and audit_log byte-identical; an accepted charge writes exactly one folio
row and one audit row. Reads go through PyPika with bound parameters.
"""
import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import call_action, ns, is_ok, is_error, load_db_query

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import P, Q, Table


def _snapshot(conn, table):
    t = Table(table)
    q = Q.from_(t).select(t.star).orderby(t.id)
    return [tuple(r) for r in conn.execute(q.get_sql()).fetchall()]


def _audit_rows_for(conn, entity_id):
    t = Table("audit_log")
    q = (Q.from_(t).select(t.skill, t.action, t.entity_type, t.entity_id,
                           t.new_values).where(t.entity_id == P()))
    return conn.execute(q.get_sql(), (entity_id,)).fetchall()


def test_add_charge_refused_when_confirmed(conn, env):
    result = call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=env["reservation_id"]))
    assert is_ok(result), result
    rid = env["reservation_id"]
    expected = ("Reservation %s is 'confirmed'; "
                "charges can be added only while checked in") % rid

    before = {t: _snapshot(conn, t) for t in (
        "hospitalityclaw_folio_charge", "hospitalityclaw_reservation",
        "audit_log")}
    result = call_action(
        ACTIONS["hospitality-add-charge"], conn,
        ns(reservation_id=rid, company_id=env["company_id"],
           charge_type="room", description="Room night",
           amount="150.00"))
    assert is_error(result), result
    assert result["message"] == expected
    for t in before:
        assert _snapshot(conn, t) == before[t], (
            "table %s changed on refusal" % t)


def test_add_charge_refused_when_checked_out(conn, env, monkeypatch):
    monkeypatch.setitem(ACTIONS["hospitality-check-out"].__globals__, "_now_iso", lambda: "2026-04-03T11:00:00Z")
    result = call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=env["reservation_id"]))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-check-in"], conn,
        ns(reservation_id=env["reservation_id"],
           room_id=env["room_101_id"]))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-check-out"], conn,
        ns(reservation_id=env["reservation_id"],
           receivable_account_id=env["ar"],
           revenue_account_id=env["revenue"],
           cost_center_id=env["cost_center_id"]))
    assert is_ok(result), result
    rid = env["reservation_id"]
    expected = ("Reservation %s is 'checked_out'; "
                "charges can be added only while checked in") % rid

    before = {t: _snapshot(conn, t) for t in (
        "hospitalityclaw_folio_charge", "hospitalityclaw_reservation",
        "audit_log")}
    result = call_action(
        ACTIONS["hospitality-add-charge"], conn,
        ns(reservation_id=rid, company_id=env["company_id"],
           charge_type="minibar", description="Drinks",
           amount="25.00"))
    assert is_error(result), result
    assert result["message"] == expected
    for t in before:
        assert _snapshot(conn, t) == before[t], (
            "table %s changed on refusal" % t)


def test_add_charge_accepted_when_checked_in(conn, env):
    result = call_action(
        ACTIONS["hospitality-confirm-reservation"], conn,
        ns(reservation_id=env["reservation_id"]))
    assert is_ok(result), result
    result = call_action(
        ACTIONS["hospitality-check-in"], conn,
        ns(reservation_id=env["reservation_id"],
           room_id=env["room_101_id"]))
    assert is_ok(result), result

    result = call_action(
        ACTIONS["hospitality-add-charge"], conn,
        ns(reservation_id=env["reservation_id"],
           company_id=env["company_id"],
           charge_type="room", description="Room night",
           amount="150.00"))
    assert is_ok(result), result
    charge_id = result["id"]

    t = Table("hospitalityclaw_folio_charge")
    q = (Q.from_(t).select(t.id, t.amount)
         .where(t.reservation_id == P()))
    rows = conn.execute(q.get_sql(), (env["reservation_id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "150.00"

    rows = _audit_rows_for(conn, charge_id)
    assert len(rows) == 1
    assert (rows[0][0], rows[0][1], rows[0][2], rows[0][3]) == (
        "hospitalityclaw", "hospitality-add-charge",
        "hospitalityclaw_folio_charge", charge_id)
