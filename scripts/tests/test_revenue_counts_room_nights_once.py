"""Revenue reports count each stay's room nights once, from the reservation.

`check_out` posts one `room` folio row for the stay's unbilled room nights,
so a stay's room revenue already lives in
`hospitalityclaw_reservation.total_amount`. Both revenue reports must exclude
`room` folio rows of stays the room sum already counts instead of adding the
nights a second time. `room` rows of stays outside the room sum stay in the
folio sum as before.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import pytest

from hospitality_helpers import (
    build_env, call_action, ns, is_ok, load_db_query,
)

A = load_db_query().ACTIONS
CO = A["hospitality-check-out"]
REPORT = A["hospitality-revenue-report"]
SUMMARY = A["hospitality-revenue-summary"]

SD = "2026-09-23"
ED = "2026-09-26"


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


def _check_out(conn, env, rid):
    result = call_action(
        CO, conn,
        ns(reservation_id=rid, receivable_account_id=env["ar"],
           revenue_account_id=env["revenue"],
           cost_center_id=env["cost_center_id"]),
    )
    assert is_ok(result), result
    return result


def test_checked_out_stay_counts_nights_once_in_revenue_report(conn, env):
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    _check_out(conn, env, rid)
    result = call_action(
        REPORT, conn,
        ns(company_id=env["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(result), result
    assert result["room_revenue"] == "597.00"
    assert result["folio_revenue"] == "25.50"
    assert result["total_revenue"] == "622.50"


def test_checked_out_stay_counts_nights_once_in_revenue_summary(conn, env):
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    _check_out(conn, env, rid)
    result = call_action(
        SUMMARY, conn,
        ns(company_id=env["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(result), result
    assert result["room_revenue"] == "597.00"
    assert result["folio_revenue"] == "25.50"
    assert result["fnb_revenue"] == "0.00"
    assert result["minibar_revenue"] == "0.00"
    assert result["total_revenue"] == "622.50"


def test_checked_in_stay_counts_reservation_only(conn, env):
    _make_stay(conn, env)
    result = call_action(
        REPORT, conn,
        ns(company_id=env["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(result), result
    assert result["room_revenue"] == "597.00"
    assert result["folio_revenue"] == "0.00"
    assert result["total_revenue"] == "597.00"


def test_room_row_of_a_stay_outside_the_range_stays_in_folio(conn, env):
    rid = _make_stay(conn, env, check_in="2026-08-10", check_out="2026-08-12")
    _add_charge(conn, env, rid, "room", "50.00", description="Night 1")
    result = call_action(
        REPORT, conn,
        ns(company_id=env["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(result), result
    assert result["room_revenue"] == "0.00"
    assert result["folio_revenue"] == "50.00"
    assert result["total_revenue"] == "50.00"
    summary = call_action(
        SUMMARY, conn,
        ns(company_id=env["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(summary), summary
    assert summary["room_revenue"] == "0.00"
    assert summary["folio_revenue"] == "50.00"


def test_other_company_not_counted(conn, env):
    env2 = build_env(conn)
    rid = _make_stay(conn, env2)
    _add_charge(conn, env2, rid, "minibar", "25.50")
    _check_out(conn, env2, rid)
    for action in (REPORT, SUMMARY):
        result = call_action(
            action, conn,
            ns(company_id=env["company_id"], start_date=SD, end_date=ED),
        )
        assert is_ok(result), result
        assert result["room_revenue"] == "0.00"
        assert result["folio_revenue"] == "0.00"
        assert result["total_revenue"] == "0.00"
    own = call_action(
        REPORT, conn,
        ns(company_id=env2["company_id"], start_date=SD, end_date=ED),
    )
    assert is_ok(own), own
    assert own["room_revenue"] == "597.00"
    assert own["folio_revenue"] == "25.50"
    assert own["total_revenue"] == "622.50"


def test_reports_write_nothing(conn, env):
    rid = _make_stay(conn, env)
    _add_charge(conn, env, rid, "minibar", "25.50")
    _check_out(conn, env, rid)
    tables = ("hospitalityclaw_reservation", "hospitalityclaw_folio_charge",
              "audit_log", "gl_entry")
    before = [conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
              for table in tables]
    for action in (REPORT, SUMMARY):
        result = call_action(
            action, conn,
            ns(company_id=env["company_id"], start_date=SD, end_date=ED),
        )
        assert is_ok(result), result
    after = [conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
             for table in tables]
    assert before == after
