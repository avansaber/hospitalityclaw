"""L1 depth tests for HospitalityClaw hospitality-add-guest.

`hospitality-add-guest` previously had NO test: the guests test module notes it
is skipped because the action reaches the core customer table through
``cross_skill.create_customer`` -- a subprocess hop to the installed skill
tree, which is neither this worktree's code nor the test database.

These tests close that gap behaviourally. The shared-library hop is redirected
to the REAL foundation ``add-customer`` run in-process on this test's
connection (the same delegate idiom legalclaw/constructclaw use: the genuine
write still happens, only the subprocess is cut out), and then the tests assert
the stored effect, not the envelope:

  - a core ``customer`` row with the exact name/email/phone/company/type,
  - a ``hospitalityclaw_guest_ext`` row linked to it with the exact
    vip/id/nationality values and zeroed counters,
  - byte-identical tables on the refusal paths.

Signal class: stored-row. This action reaches no ledger: it inserts a customer
plus its extension row and two audit rows. There are no debit/credit legs, so
no balanced-legs assertion can hold -- and none is added. The gl_entry
no-change assertion below pins that down so a later reader does not add a
legs assertion that cannot hold.
Money discipline: the only monetary column touched is
``hospitalityclaw_guest_ext.total_spent`` (TEXT); it is asserted as the exact
string "0" and as ``Decimal("0")``. Never float, never round.
"""
import argparse
import importlib.util
import io
import json
import pytest
import sys
import os
from decimal import Decimal
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from hospitality_helpers import (
    call_action, ns, is_ok, is_error, load_db_query, SRC_DIR,
)

_mod = load_db_query()
ACTIONS = _mod.ACTIONS

from erpclaw_lib.query import P, Q, Table, fn
from erpclaw_lib.response import row_to_dict

_FOUNDATION_SCRIPTS = os.path.join(SRC_DIR, "erpclaw", "scripts")


def _load_foundation(domain):
    """Load a foundation domain script by explicit path.

    Same idiom as load_db_query(): two modules both name their entry point
    db_query.py, so importing by name would collide.
    """
    path = os.path.join(_FOUNDATION_SCRIPTS, domain, "db_query.py")
    spec = importlib.util.spec_from_file_location("_fnd_%s" % domain, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delegate_customers_in_process(conn, monkeypatch):
    """Redirect cross_skill.call_skill_action to the REAL add-customer.

    hospitality-add-guest reaches the core customer table through
    ``create_customer`` -> ``call_skill_action("erpclaw", "add-customer")``,
    which shells out to the INSTALLED skill tree. Running the genuine
    foundation function in-process on this test's connection keeps every
    stored-row assertion real while recording exactly which skill/action and
    flags hospitality sent -- proof it goes through the owner's action and
    never writes the customer table directly.
    """
    selling = _load_foundation("erpclaw-selling")
    from erpclaw_lib.cross_skill import CrossSkillError
    from erpclaw_lib import cross_skill as _cs
    captured = {}

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
        flags = dict(args or {})
        captured.setdefault("calls", []).append(
            {"skill": skill_name, "action": action, "args": flags})
        if action != "add-customer":
            raise AssertionError("unexpected cross-skill action %r" % (action,))
        result = _run(selling.add_customer, argparse.Namespace(
            name=flags.get("--name"),
            company_id=flags.get("--company-id"),
            customer_type=flags.get("--customer-type"),
            customer_group=None, payment_terms_id=None, tax_id=None,
            credit_limit=None, exempt_from_sales_tax=None,
            primary_address=None, primary_contact=None,
            email=flags.get("--email"), phone=flags.get("--phone"),
            default_price_list_id=None, custom_fields=None))
        if result.get("status") == "error":
            raise CrossSkillError(result.get("message", "add-customer failed"))
        return result

    monkeypatch.setattr(_cs, "call_skill_action", _in_process)
    return captured


# ---------------------------------------------------------------------------
# Stored-row readers (PyPika through erpclaw_lib.query -- no raw SQL here)
# ---------------------------------------------------------------------------

def _read_customer(conn, customer_id):
    t = Table("customer")
    q = Q.from_(t).select(t.star).where(t.id == P())
    return row_to_dict(conn.execute(q.get_sql(), (customer_id,)).fetchone())


def _read_guest_ext(conn, guest_id):
    t = Table("hospitalityclaw_guest_ext")
    q = Q.from_(t).select(t.star).where(t.id == P())
    return row_to_dict(conn.execute(q.get_sql(), (guest_id,)).fetchone())


def _count(conn, table):
    t = Table(table)
    q = Q.from_(t).select(fn.Count("*"))
    return conn.execute(q.get_sql()).fetchone()[0]


def _snapshot(conn, table):
    """Every row of `table` in id order, values as repr strings (None stays None)."""
    t = Table(table)
    q = Q.from_(t).select(t.star).orderby(t.id)
    return [tuple(repr(v) for v in tuple(r))
            for r in conn.execute(q.get_sql()).fetchall()]


def _fingerprint(conn):
    """Full-row snapshot of every table the action can touch."""
    return {t: _snapshot(conn, t) for t in (
        "customer", "hospitalityclaw_guest_ext",
        "hospitalityclaw_guest_preference", "hospitalityclaw_reservation",
        "audit_log", "naming_series")}


# ---------------------------------------------------------------------------
# hospitality-add-guest -- stored-row depth
# ---------------------------------------------------------------------------

class TestAddGuestBehaviour:
    """hospitality-add-guest: effect on the database, not the envelope."""

    def test_add_guest_writes_customer_and_ext_rows(
            self, conn, env, db_path, monkeypatch):
        from erpclaw_lib import seam
        assert seam.table_exists("customer", db_path=db_path)
        assert seam.table_exists("hospitalityclaw_guest_ext", db_path=db_path)
        assert "total_spent" in seam.column_names(
            "hospitalityclaw_guest_ext", db_path=db_path)

        captured = _delegate_customers_in_process(conn, monkeypatch)
        # The seeded guest must survive the action untouched.
        seeded_before = _read_guest_ext(conn, env["guest_id"])
        customers_before = _count(conn, "customer")
        audits_before = _count(conn, "audit_log")
        gl_before = _count(conn, "gl_entry")

        result = call_action(
            ACTIONS["hospitality-add-guest"], conn,
            ns(company_id=env["company_id"], customer_name="Depth Guest",
               customer_type="individual", email="depth@example.com",
               phone="555-0199", id_type="passport", id_number="D1234567",
               nationality="US", vip_level="gold"),
        )
        assert is_ok(result), result
        assert result["id"]
        assert result["customer_id"]
        assert result["customer_name"] == "Depth Guest"
        assert result["vip_level"] == "gold"
        assert result["naming_series"].startswith("HGST-")

        # The hop went through the owner's action, not a direct table write.
        assert len(captured["calls"]) == 1
        hop = captured["calls"][0]
        assert hop["skill"] == "erpclaw"
        assert hop["action"] == "add-customer"
        assert hop["args"]["--name"] == "Depth Guest"
        assert hop["args"]["--company-id"] == env["company_id"]

        # Core customer row: exact stored values.
        cust = _read_customer(conn, result["customer_id"])
        assert cust["name"] == "Depth Guest"
        assert cust["email"] == "depth@example.com"
        assert cust["phone"] == "555-0199"
        assert cust["company_id"] == env["company_id"]
        assert cust["customer_type"] == "individual"
        assert cust["status"] == "active"

        # Extension row: exact stored values, linked to the new customer.
        ext = _read_guest_ext(conn, result["id"])
        assert ext["customer_id"] == result["customer_id"]
        assert ext["vip_level"] == "gold"
        assert ext["id_type"] == "passport"
        assert ext["id_number"] == "D1234567"
        assert ext["nationality"] == "US"
        assert ext["company_id"] == env["company_id"]
        assert ext["naming_series"] == result["naming_series"]
        assert int(ext["loyalty_points"]) == 0
        assert int(ext["total_stays"]) == 0
        assert ext["total_spent"] == "0"
        assert Decimal(ext["total_spent"]) == Decimal("0")
        assert int(ext["is_active"]) == 1

        # What should NOT have changed.
        assert _read_guest_ext(conn, env["guest_id"]) == seeded_before
        assert _count(conn, "customer") == customers_before + 1
        # Two audit rows: the owner's add-customer plus hospitality-add-guest.
        assert _count(conn, "audit_log") == audits_before + 2
        # No ledger: gl_entry is untouched (this action has no debit/credit legs).
        assert _count(conn, "gl_entry") == gl_before

    def test_add_guest_defaults_are_stored_not_echoed(
            self, conn, env, monkeypatch):
        captured = _delegate_customers_in_process(conn, monkeypatch)
        result = call_action(
            ACTIONS["hospitality-add-guest"], conn,
            ns(company_id=env["company_id"], customer_name="Minimal Guest"),
        )
        assert is_ok(result), result

        cust = _read_customer(conn, result["customer_id"])
        assert cust["name"] == "Minimal Guest"
        assert cust["customer_type"] == "individual"
        assert cust["email"] is None
        assert cust["phone"] is None

        ext = _read_guest_ext(conn, result["id"])
        assert ext["vip_level"] == "regular"
        assert ext["id_type"] is None
        assert ext["id_number"] is None
        assert ext["nationality"] is None
        assert ext["total_spent"] == "0"
        assert Decimal(ext["total_spent"]) == Decimal("0")
        assert len(captured["calls"]) == 1

    def test_add_guest_refuses_invalid_vip_level(
            self, conn, env, monkeypatch):
        captured = _delegate_customers_in_process(conn, monkeypatch)
        before = _fingerprint(conn)

        result = call_action(
            ACTIONS["hospitality-add-guest"], conn,
            ns(company_id=env["company_id"], customer_name="Refused Guest",
               vip_level="bogus"),
        )
        assert is_error(result)
        # The message names the field and the offending value: truthful.
        assert "Invalid vip-level" in result["message"]
        assert "bogus" in result["message"]
        assert "id" not in result
        # Refused before the cross-skill hop: the owner was never called.
        assert captured.get("calls", []) == []
        # Byte-identical: the refusal half-writes nothing.
        assert _fingerprint(conn) == before

    def test_add_guest_refuses_missing_name(self, conn, env, monkeypatch):
        captured = _delegate_customers_in_process(conn, monkeypatch)
        customers_before = _count(conn, "customer")
        guests_before = _count(conn, "hospitalityclaw_guest_ext")

        result = call_action(
            ACTIONS["hospitality-add-guest"], conn,
            ns(company_id=env["company_id"], customer_name=None, name=None),
        )
        assert is_error(result)
        assert "--customer-name" in result["message"]
        assert captured.get("calls", []) == []
        assert _count(conn, "customer") == customers_before
        assert _count(conn, "hospitalityclaw_guest_ext") == guests_before
