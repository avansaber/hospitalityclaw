"""Checkout bills the stay's room nights once, in the checkout posting.

A stay's room revenue is nights x nightly rate. Whatever part of it is not
already on the folio as positive `room` charges is added as one `room` row
in the same transaction as the checkout's single ledger posting. Manual
`room` rows are never billed twice; a negative `room` row is a discount
adjustment and stays as it is. A checkout that cannot post refuses and
leaves nothing behind.
"""
import json
import os
import sys
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import pytest

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query,
)

A = load_db_query().ACTIONS
CO = A["hospitality-check-out"]


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setitem(CO.__globals__, "_now_iso", lambda: "2026-09-26T11:00:00Z")


def _make_stay(conn, env, rate="199.00",
               check_in="2026-09-23", check_out="2026-09-26"):
    result = call_action(
        A["hospitality-add-reservation"], conn,
        ns(company_id=env["company_id"], guest_id=env["guest_id"],
           room_type_id=env["std_room_type_id"],
           check_in_date=check_in, check_out_date=check_out,
           rate_amount=rate),
    )
    assert is_ok(result), result
    rid = result["id"]
    if rate == "199.00" and (check_in, check_out) == ("2026-09-23", "2026-09-26"):
        assert result["total_amount"] == "597.00"
    result = call_action(
        A["hospitality-confirm-reservation"], conn,
        ns(reservation_id=rid),
    )
    assert is_ok(result), result
    result = call_action(
        A["hospitality-check-in"], conn,
        ns(reservation_id=rid, room_id=env["room_101_id"]),
    )
    assert is_ok(result), result
    return rid


def _add_charge(conn, env, rid, charge_type, amount, description=None):
    result = call_action(
        A["hospitality-add-charge"], conn,
        ns(reservation_id=rid, company_id=env["company_id"],
           charge_type=charge_type, description=description or charge_type,
           amount=amount),
    )
    assert is_ok(result), result
    return result


def _full(conn, env, rid):
    res = conn.execute(
        "SELECT reservation_status, gl_entry_ids FROM hospitalityclaw_reservation"
        " WHERE id = ?",
        (rid,),
    ).fetchone()
    room = conn.execute(
        "SELECT room_status FROM hospitalityclaw_room WHERE id = ?",
        (env["room_101_id"],),
    ).fetchone()
    guest = conn.execute(
        "SELECT total_spent FROM hospitalityclaw_guest_ext WHERE id = ?",
        (env["guest_id"],),
    ).fetchone()
    folio = conn.execute(
        "SELECT id, charge_type, description, amount FROM hospitalityclaw_folio_charge"
        " WHERE reservation_id = ? ORDER BY created_at, id",
        (rid,),
    ).fetchall()
    gl_count = conn.execute("SELECT COUNT(*) FROM gl_entry").fetchone()[0]
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    return ((res[0], res[1]), room[0], guest[0],
            [tuple(r) for r in folio], gl_count, audit_count)


def _folio(conn, rid):
    rows = conn.execute(
        "SELECT charge_type, amount, description FROM hospitalityclaw_folio_charge"
        " WHERE reservation_id = ?",
        (rid,),
    ).fetchall()
    return sorted((r[0], r[1], r[2]) for r in rows)


def _legs(conn, rid):
    rows = conn.execute(
        "SELECT account_id, debit, credit, cost_center_id, party_type, party_id"
        " FROM gl_entry WHERE voucher_id = ?",
        (rid,),
    ).fetchall()
    return sorted((r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows)


def test_room_nights_and_extras_post_once(conn, env):
    ar, rev, cc, cu = env["ar"], env["revenue"], env["cost_center_id"], env["core_customer_id"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    assert result["folio_total"] == "622.50"
    assert result["room_nights_billed"] == "597.00"
    assert result["gl_posted"] is True
    legs = _legs(conn, rid)
    assert legs == sorted([
        (ar, "622.50", "0.00", None, "customer", cu),
        (rev, "0.00", "597.00", cc, None, None),
        (rev, "0.00", "25.50", cc, None, None),
    ])
    assert sum(Decimal(r[1]) for r in legs) == sum(Decimal(r[2]) for r in legs)
    assert _folio(conn, rid) == sorted([
        ("minibar", "25.50", "minibar"),
        ("room", "597.00", "Room nights: 3 x 199.00"),
    ])
    guest = conn.execute(
        "SELECT total_spent FROM hospitalityclaw_guest_ext WHERE id = ?",
        (env["guest_id"],),
    ).fetchone()
    assert guest[0] == "622.50"
    room_row_id = conn.execute(
        "SELECT id FROM hospitalityclaw_folio_charge"
        " WHERE reservation_id = ? AND charge_type = 'room'",
        (rid,),
    ).fetchone()[0]
    audit_row = conn.execute(
        "SELECT action, entity_type, new_values FROM audit_log WHERE entity_id = ?",
        (room_row_id,),
    ).fetchone()
    assert audit_row[0] == "hospitality-check-out"
    assert audit_row[1] == "hospitalityclaw_folio_charge"
    assert json.loads(audit_row[2]) == {"amount": "597.00", "nights": 3, "rate_amount": "199.00"}


def test_manual_room_charge_not_billed_twice(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "room", "199.00", description="Night 1")
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    assert result["folio_total"] == "597.00"
    assert result["room_nights_billed"] == "398.00"
    folio = _folio(conn, rid)
    room_rows = [r for r in folio if r[0] == "room"]
    assert len(folio) == 2
    assert len(room_rows) == 2
    assert sorted(r[1] for r in room_rows) == ["199.00", "398.00"]
    auto = [r for r in room_rows if r[1] == "398.00"]
    assert auto[0][2] == "Room nights: 3 x 199.00"
    cu = env["core_customer_id"]
    assert _legs(conn, rid) == sorted([
        (ar, "597.00", "0.00", None, "customer", cu),
        (rev, "0.00", "597.00", cc, None, None),
    ])


def test_room_fully_on_folio_adds_nothing(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "room", "597.00", description="Prepaid nights")
    before = _folio(conn, rid)
    assert len(before) == 1
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    assert result["room_nights_billed"] == "0.00"
    assert result["folio_total"] == "597.00"
    assert _folio(conn, rid) == before
    cu = env["core_customer_id"]
    assert _legs(conn, rid) == sorted([
        (ar, "597.00", "0.00", None, "customer", cu),
        (rev, "0.00", "597.00", cc, None, None),
    ])


def test_room_nights_need_accounts(conn, env):
    rid = _make_stay(conn, env)
    before = _full(conn, env, rid)
    result = call_action(CO, conn, ns(reservation_id=rid))
    assert is_error(result), result
    assert result["message"] == "--receivable-account-id and --revenue-account-id are required to check out a reservation with folio charges"
    assert _full(conn, env, rid) == before
    assert _folio(conn, rid) == []


def test_posting_failure_leaves_no_room_row(conn, env):
    ar, rev = env["ar"], env["revenue"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    before = _full(conn, env, rid)
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=None),
    )
    assert is_error(result), result
    assert result["message"].startswith("GL posting failed, checkout rolled back:")
    assert _full(conn, env, rid) == before
    assert _folio(conn, rid) == [("minibar", "25.50", "minibar")]


def test_second_checkout_refused(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    folio_before = _folio(conn, rid)
    gl_before = _legs(conn, rid)
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_error(result), result
    assert result["message"] == "Cannot check out reservation in 'checked_out' status (must be checked_in)"
    assert _folio(conn, rid) == folio_before
    assert _legs(conn, rid) == gl_before


def test_late_checkout_bills_extended_nights(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _make_stay(conn, env)
    result = call_action(
        A["hospitality-late-checkout"], conn,
        ns(reservation_id=rid, new_checkout_date="2026-09-27"),
    )
    assert is_ok(result), result
    assert result["total_amount"] == "796.00"
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    assert result["folio_total"] == "796.00"
    assert result["room_nights_billed"] == "796.00"
    assert _folio(conn, rid) == [("room", "796.00", "Room nights: 4 x 199.00")]
    cu = env["core_customer_id"]
    assert _legs(conn, rid) == sorted([
        (ar, "796.00", "0.00", None, "customer", cu),
        (rev, "0.00", "796.00", cc, None, None),
    ])


def test_zero_rate_stay_posts_nothing(conn, env):
    rid = _make_stay(conn, env, rate="0.00")
    result = call_action(CO, conn, ns(reservation_id=rid))
    assert is_ok(result), result
    assert result["folio_total"] == "0.00"
    assert result["room_nights_billed"] == "0.00"
    assert result["gl_posted"] is False
    assert _folio(conn, rid) == []


def test_room_discount_is_kept(conn, env):
    ar, rev, cc = env["ar"], env["revenue"], env["cost_center_id"]
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "room", "-50.00", description="Loyalty discount")
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=ar,
           revenue_account_id=rev, cost_center_id=cc),
    )
    assert is_ok(result), result
    assert result["room_nights_billed"] == "597.00"
    assert result["folio_total"] == "547.00"
    assert _folio(conn, rid) == sorted([
        ("room", "-50.00", "Loyalty discount"),
        ("room", "597.00", "Room nights: 3 x 199.00"),
    ])
    cu = env["core_customer_id"]
    assert _legs(conn, rid) == sorted([
        (ar, "547.00", "0.00", None, "customer", cu),
        (rev, "0.00", "547.00", cc, None, None),
    ])
