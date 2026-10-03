"""Checkout posts its folio ledger in the same transaction or is refused.

Covers hospitality-check-out with folio charges:
  - posts a balanced ledger on the same connection (journal_entry)
  - refuses (and rolls back) when accounts are missing, posting fails,
    a stock account is given, the folio is negative, or GL is unavailable
  - a zero folio checks out with no posting
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import pytest

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query, seed_account,
)

A = load_db_query().ACTIONS
CO = A["hospitality-check-out"]


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setitem(CO.__globals__, "_now_iso", lambda: "2026-04-03T11:00:00Z")


def _stay(conn, env, charges):
    call_action(
        A["hospitality-confirm-reservation"], conn,
        ns(reservation_id=env["reservation_id"]),
    )
    result = call_action(
        A["hospitality-check-in"], conn,
        ns(reservation_id=env["reservation_id"], room_id=env["room_101_id"]),
    )
    assert is_ok(result), result
    for charge_type, amount in charges:
        result = call_action(
            A["hospitality-add-charge"], conn,
            ns(reservation_id=env["reservation_id"],
               charge_type=charge_type, description=charge_type,
               amount=amount, company_id=env["company_id"]),
        )
        assert is_ok(result), result
    return env["reservation_id"]


def _legs(conn, rid):
    rows = conn.execute(
        'SELECT account_id, debit, credit, cost_center_id, party_type, party_id'
        ' FROM gl_entry WHERE voucher_id = ?',
        (rid,),
    ).fetchall()
    return sorted(
        (r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows
    )


def _state(conn, env, rid):
    res = conn.execute(
        'SELECT reservation_status, gl_entry_ids FROM hospitalityclaw_reservation'
        ' WHERE id = ?',
        (rid,),
    ).fetchone()
    room = conn.execute(
        'SELECT room_status FROM hospitalityclaw_room WHERE id = ?',
        (env["room_101_id"],),
    ).fetchone()
    guest = conn.execute(
        'SELECT total_spent, total_stays FROM hospitalityclaw_guest_ext WHERE id = ?',
        (env["guest_id"],),
    ).fetchone()
    count = conn.execute('SELECT COUNT(*) FROM gl_entry').fetchone()[0]
    return ((res[0], res[1]), room[0], (guest[0], guest[1]), count)


THREE = [("room", "300.00"), ("minibar", "12.50"), ("parking", "20.00")]


def test_charges_post_ledger(conn, env):
    ar, rev, cc, cu = env["ar"], env["revenue"], env["cost_center_id"], env["core_customer_id"]
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    rid = _stay(conn, env, THREE)
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_ok(result), result
    assert "gl_warning" not in result
    assert result["gl_posted"] is True
    assert result["folio_total"] == "332.50"
    assert _legs(conn, rid) == sorted([
        (ar, "332.50", "0.00", None, "customer", cu),
        (rev, "0.00", "300.00", cc, None, None),
        (rev, "0.00", "12.50", cc, None, None),
        (rev, "0.00", "20.00", cc, None, None),
    ])
    rows = conn.execute(
        'SELECT id, voucher_type, posting_date, entry_set FROM gl_entry'
        ' WHERE voucher_id = ?',
        (rid,),
    ).fetchall()
    assert len(rows) == 4
    for r in rows:
        assert r[1] == "journal_entry"
        assert r[2] == "2026-04-03"
        assert r[3] == "primary"
    assert set(result["gl_entry_ids"]) == {r[0] for r in rows}
    stored = conn.execute(
        'SELECT gl_entry_ids FROM hospitalityclaw_reservation WHERE id = ?',
        (rid,),
    ).fetchone()[0]
    assert set(json.loads(stored)) == {r[0] for r in rows}
    assert set(json.loads(stored)) == set(result["gl_entry_ids"])
    res = conn.execute(
        'SELECT reservation_status FROM hospitalityclaw_reservation WHERE id = ?',
        (rid,),
    ).fetchone()
    assert res[0] == "checked_out"
    room = conn.execute(
        'SELECT room_status FROM hospitalityclaw_room WHERE id = ?',
        (env["room_101_id"],),
    ).fetchone()
    assert room[0] == "cleaning"
    guest = conn.execute(
        'SELECT total_spent, total_stays FROM hospitalityclaw_guest_ext WHERE id = ?',
        (env["guest_id"],),
    ).fetchone()
    assert guest[0] == "332.50"
    assert guest[1] == 1


@pytest.mark.parametrize("extra", [{}, {"ar_only": True}, {"rev_only": True}])
def test_charges_without_accounts_are_refused(conn, env, extra):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _stay(conn, env, THREE)
    if extra == {}:
        kwargs = {}
    elif "ar_only" in extra:
        kwargs = dict(receivable_account_id=ar, cost_center_id=cc)
    else:
        kwargs = dict(revenue_account_id=rev, cost_center_id=cc)
    before = _state(conn, env, rid)
    assert before == (("checked_in", None), "occupied", ("0", 1), 0)
    result = call_action(CO, conn, ns(reservation_id=rid, **kwargs))
    assert is_error(result), result
    assert "--receivable-account-id" in result["message"]
    assert "--revenue-account-id" in result["message"]
    assert _state(conn, env, rid) == before


def test_posting_failure_rolls_back(conn, env):
    ar, rev, cc, cu = env["ar"], env["revenue"], env["cost_center_id"], env["core_customer_id"]
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    rid = _stay(conn, env, THREE)
    before = _state(conn, env, rid)
    assert before == (("checked_in", None), "occupied", ("0", 1), 0)
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=None),
    )
    assert is_error(result), result
    assert result["message"].startswith("GL posting failed, checkout rolled back:")
    assert "GL Validation Step 6 Failed" in result["message"]
    assert _state(conn, env, rid) == before
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_ok(result), result
    assert _legs(conn, rid) == sorted([
        (ar, "332.50", "0.00", None, "customer", cu),
        (rev, "0.00", "300.00", cc, None, None),
        (rev, "0.00", "12.50", cc, None, None),
        (rev, "0.00", "20.00", cc, None, None),
    ])


def test_zero_folio_checks_out_without_posting(conn, env):
    result = call_action(
        A["hospitality-update-reservation"], conn,
        ns(reservation_id=env["reservation_id"], rate_amount="0.00"),
    )
    assert is_ok(result), result
    rid = _stay(conn, env, [])
    result = call_action(CO, conn, ns(reservation_id=rid))
    assert is_ok(result), result
    assert result["folio_total"] == "0.00"
    assert result["gl_posted"] is False
    assert "gl_entry_ids" not in result
    state = _state(conn, env, rid)
    assert state[0] == ("checked_out", None)
    assert state[1] == "cleaning"
    assert state[2] == ("0.00", 1)
    assert state[3] == 0


@pytest.mark.parametrize("role", ["receivable_account_id", "revenue_account_id"])
def test_stock_account_is_refused(conn, env, role):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    st = seed_account(conn, env["company_id"], "Stock In Hand", "asset", "stock", "1400")
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    full[role] = st
    rid = _stay(conn, env, THREE)
    before = _state(conn, env, rid)
    assert before == (("checked_in", None), "occupied", ("0", 1), 0)
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_error(result), result
    assert "cannot use a stock account" in result["message"]
    assert _state(conn, env, rid) == before


def test_negative_line_debits_revenue(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    rid = _stay(conn, env, [("room", "300.00"), ("other", "-20.00")])
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_ok(result), result
    assert "gl_warning" not in result
    assert result["folio_total"] == "280.00"
    cu = env["core_customer_id"]
    assert _legs(conn, rid) == sorted([
        (ar, "280.00", "0.00", None, "customer", cu),
        (rev, "0.00", "300.00", cc, None, None),
        (rev, "20.00", "0.00", cc, None, None),
    ])
    guest = conn.execute(
        'SELECT total_spent FROM hospitalityclaw_guest_ext WHERE id = ?',
        (env["guest_id"],),
    ).fetchone()
    assert guest[0] == "280.00"


def test_negative_folio_is_refused(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    rid = _stay(conn, env, [("room", "300.00"), ("other", "-350.00")])
    before = _state(conn, env, rid)
    assert before == (("checked_in", None), "occupied", ("0", 1), 0)
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_error(result), result
    assert "folio total -50.00 is negative" in result["message"]
    assert _state(conn, env, rid) == before


def test_no_gl_library_is_refused(conn, env, monkeypatch):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    full = dict(receivable_account_id=ar, revenue_account_id=rev, cost_center_id=cc)
    monkeypatch.setitem(CO.__globals__, "HAS_GL", False)
    rid = _stay(conn, env, THREE)
    before = _state(conn, env, rid)
    assert before == (("checked_in", None), "occupied", ("0", 1), 0)
    result = call_action(CO, conn, ns(reservation_id=rid, **full))
    assert is_error(result), result
    assert "GL posting is not available; a checkout with folio charges cannot be completed" in result["message"]
    assert _state(conn, env, rid) == before
