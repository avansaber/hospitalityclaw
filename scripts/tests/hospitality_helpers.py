"""Shared helper functions for HospitalityClaw L1 unit tests.

Provides:
  - DB bootstrap via init_schema.init_db() + create_hospitalityclaw_tables()
  - load_db_query() for explicit module loading (avoids sys.path collisions)
  - call_action() / ns() / is_error() / is_ok()
  - Seed functions for company, customers, guests, naming_series
  - build_env() for a complete hospitality test environment
"""
import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)          # hospitalityclaw/scripts/
MODULE_DIR = os.path.dirname(SCRIPTS_DIR)          # hospitalityclaw/
INIT_DB_PATH = os.path.join(MODULE_DIR, "init_db.py")

# Foundation init_schema.py (erpclaw-setup)
SRC_DIR = os.path.dirname(MODULE_DIR)              # source/
ERPCLAW_DIR = os.path.join(SRC_DIR, "erpclaw", "scripts", "erpclaw-setup")
INIT_SCHEMA_PATH = os.path.join(ERPCLAW_DIR, "init_schema.py")

# Make erpclaw_lib importable
ERPCLAW_LIB = os.path.expanduser("~/.openclaw/erpclaw/lib")
if ERPCLAW_LIB not in sys.path:
    sys.path.insert(0, ERPCLAW_LIB)

from erpclaw_lib.db import setup_pragmas

# Make scripts dir importable so domain modules (rooms, reservations, ...) resolve
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def load_db_query():
    """Load hospitalityclaw db_query.py explicitly to avoid sys.path collisions."""
    db_query_path = os.path.join(SCRIPTS_DIR, "db_query.py")
    spec = importlib.util.spec_from_file_location("db_query_hospitality", db_query_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def init_all_tables(db_path: str):
    """Create foundation tables + hospitalityclaw extension tables.

    1. Runs erpclaw-setup init_schema.init_db()  (core tables)
    2. Extends customer table with columns hospitalityclaw code expects
    3. Runs hospitalityclaw init_db.create_hospitalityclaw_tables()
    """
    # Step 1: Foundation schema
    spec = importlib.util.spec_from_file_location("init_schema", INIT_SCHEMA_PATH)
    schema_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema_mod)
    schema_mod.init_db(db_path)

    # Step 2: The hospitalityclaw guests code references c.name, c.email,
    # c.phone on the customer table. email/phone/modified may not be in
    # the core schema — add them if missing so JOIN queries work in tests.
    conn = sqlite3.connect(db_path)
    for col_def in [
        "ALTER TABLE customer ADD COLUMN email TEXT",
        "ALTER TABLE customer ADD COLUMN phone TEXT",
        "ALTER TABLE customer ADD COLUMN modified TEXT",
    ]:
        try:
            conn.execute(col_def)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()

    # Step 3: HospitalityClaw extension tables
    spec2 = importlib.util.spec_from_file_location("hospitality_init_db", INIT_DB_PATH)
    hosp_mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(hosp_mod)
    hosp_mod.create_hospitalityclaw_tables(db_path)


class _ConnWrapper:
    """Thin wrapper so conn.company_id works (some actions set it)."""
    def __init__(self, real_conn):
        self._conn = real_conn
        self.company_id = None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        return self._conn.executescript(*a, **kw)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


class _DecimalSum:
    """Custom SQLite aggregate: SUM using Python Decimal for precision."""
    def __init__(self):
        self.total = Decimal("0")

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))

    def finalize(self):
        return str(self.total)


def get_conn(db_path: str):
    """Return a wrapped sqlite3.Connection with FK enabled and Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_pragmas(conn)
    conn.create_aggregate("decimal_sum", 1, _DecimalSum)
    return _ConnWrapper(conn)


# ---------------------------------------------------------------------------
# Action invocation helpers
# ---------------------------------------------------------------------------

def call_action(fn, conn, args) -> dict:
    """Invoke a domain function, capture stdout JSON, return parsed dict."""
    buf = io.StringIO()

    def _fake_exit(code=0):
        raise SystemExit(code)

    try:
        with patch("sys.stdout", buf), patch("sys.exit", side_effect=_fake_exit):
            fn(conn, args)
    except SystemExit:
        pass

    output = buf.getvalue().strip()
    if not output:
        return {"status": "error", "message": "no output captured"}
    return json.loads(output)


def ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from keyword args (mimics CLI flags)."""
    defaults = {
        "limit": 50,
        "offset": 0,
        "company_id": None,
        "search": None,
        "notes": None,
        "description": None,
        "name": None,
        "reason": None,
        # ROOMS domain
        "room_type_id": None,
        "base_rate": None,
        "max_occupancy": None,
        "room_id": None,
        "room_number": None,
        "floor": None,
        "room_status": None,
        "is_smoking": None,
        "amenity_id": None,
        "amenity_type": None,
        # GUESTS domain
        "guest_id": None,
        "customer_name": None,
        "customer_type": None,
        "email": None,
        "phone": None,
        "id_type": None,
        "id_number": None,
        "nationality": None,
        "vip_level": None,
        "preference_type": None,
        "preference_value": None,
        # RESERVATIONS domain
        "reservation_id": None,
        "check_in_date": None,
        "check_out_date": None,
        "adults": None,
        "children": None,
        "rate_plan_id": None,
        "rate_amount": None,
        "reservation_status": None,
        "source": None,
        "special_requests": None,
        "rate_type": None,
        "start_date": None,
        "end_date": None,
        "rooms_blocked": None,
        "contact_name": None,
        "contact_email": None,
        "block_status": None,
        # FRONT DESK domain
        "request_id": None,
        "request_type": None,
        "priority": None,
        "request_status": None,
        "assigned_to": None,
        "new_checkout_date": None,
        "new_room_id": None,
        "charge_type": None,
        "amount": None,
        "receivable_account_id": None,
        "revenue_account_id": None,
        "cost_center_id": None,
        # HOUSEKEEPING domain
        "task_id": None,
        "task_type": None,
        "task_status": None,
        "scheduled_date": None,
        "inspector": None,
        "inspection_date": None,
        "score": None,
        # REVENUE domain
        "adjustment_date": None,
        "adjustment_type": None,
        "adjustment_pct": None,
        "adjusted_rate": None,
        # FNB domain
        "outlet_id": None,
        "outlet_type": None,
        "operating_hours": None,
        "order_id": None,
        "order_status": None,
        "items_json": None,
        "total_amount": None,
        "item_name": None,
        "quantity": None,
        "unit_price": None,
        "consumption_date": None,
        # REPORTS domain
        "report_date": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def is_error(result: dict) -> bool:
    return result.get("status") == "error"


def is_ok(result: dict) -> bool:
    return result.get("status") == "ok"


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def seed_company(conn, name="Grand Hotel LLC", abbr="GH") -> str:
    """Insert a test company and return its ID."""
    cid = _uuid()
    conn.execute(
        """INSERT INTO company (id, name, abbr, default_currency, country,
           fiscal_year_start_month)
           VALUES (?, ?, ?, 'USD', 'United States', 1)""",
        (cid, f"{name} {cid[:6]}", f"{abbr}{cid[:4]}")
    )
    conn.commit()
    return cid


def seed_customer(conn, company_id: str, name="Alice Guest",
                  email=None, phone=None) -> str:
    """Insert a core customer and return its ID."""
    cid = _uuid()
    conn.execute(
        """INSERT INTO customer (id, name, email, phone,
           company_id, customer_type, status)
           VALUES (?, ?, ?, ?, ?, 'individual', 'active')""",
        (cid, name, email, phone, company_id)
    )
    conn.commit()
    return cid


def seed_guest_ext(conn, customer_id: str, company_id: str,
                   vip_level="regular", nationality=None) -> str:
    """Insert a hospitalityclaw guest extension row and return its ID."""
    guest_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO hospitalityclaw_guest_ext (
               id, naming_series, customer_id,
               id_type, id_number, nationality, vip_level,
               loyalty_points, total_stays, total_spent,
               is_active, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (guest_id, f"HGST-{guest_id[:6]}", customer_id,
         "passport", "P123456", nationality, vip_level,
         0, 0, "0", 1,
         company_id, now, now)
    )
    conn.commit()
    return guest_id


def seed_room_type(conn, company_id: str, name="Standard",
                   base_rate="150.00", max_occupancy=2) -> str:
    """Insert a room type and return its ID."""
    rt_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type (
               id, naming_series, name, base_rate, max_occupancy,
               description, company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rt_id, f"RMT-{rt_id[:6]}", name, base_rate, max_occupancy,
         f"{name} room type", company_id, now, now)
    )
    conn.commit()
    return rt_id


def seed_room(conn, room_type_id: str, company_id: str,
              room_number="101", floor=1,
              room_status="available") -> str:
    """Insert a room and return its ID."""
    rm_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO hospitalityclaw_room (
               id, naming_series, room_number, room_type_id, floor,
               room_status, is_smoking, notes, company_id,
               created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rm_id, f"RM-{rm_id[:6]}", room_number, room_type_id, floor,
         room_status, 0, None, company_id, now, now)
    )
    conn.commit()
    return rm_id


def seed_reservation(conn, guest_id: str, room_type_id: str,
                     company_id: str, room_id=None,
                     check_in="2026-04-01", check_out="2026-04-03",
                     rate_amount="150.00",
                     reservation_status="pending") -> str:
    """Insert a reservation and return its ID."""
    res_id = _uuid()
    now = _now()
    nights = max((
        __import__("datetime").date.fromisoformat(check_out) -
        __import__("datetime").date.fromisoformat(check_in)
    ).days, 1)
    total = str(Decimal(rate_amount) * Decimal(nights))
    conn.execute(
        """INSERT INTO hospitalityclaw_reservation (
               id, naming_series, guest_id, room_type_id, room_id,
               check_in_date, check_out_date, nights, adults, children,
               rate_plan_id, rate_amount, total_amount,
               reservation_status, source, special_requests,
               company_id, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (res_id, f"RES-{res_id[:6]}", guest_id, room_type_id, room_id,
         check_in, check_out, nights, 1, 0,
         None, rate_amount, total,
         reservation_status, "direct", None,
         company_id, now, now)
    )
    conn.commit()
    return res_id


def seed_outlet(conn, company_id: str, name="Room Service",
                outlet_type="room_service") -> str:
    """Insert an F&B outlet and return its ID."""
    out_id = _uuid()
    now = _now()
    conn.execute(
        """INSERT INTO hospitalityclaw_outlet (id, name, outlet_type,
               operating_hours, company_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (out_id, name, outlet_type, "24/7", company_id, now)
    )
    conn.commit()
    return out_id


def seed_naming_series(conn, company_id: str):
    """Seed naming series for hospitalityclaw entity types."""
    series = [
        ("hospitalityclaw_room_type", "RMT-", 0),
        ("hospitalityclaw_room", "RM-", 0),
        ("hospitalityclaw_guest_ext", "HGST-", 0),
        ("hospitalityclaw_reservation", "RES-", 0),
        ("hospitalityclaw_rate_plan", "RPL-", 0),
        ("hospitalityclaw_group_block", "GBK-", 0),
        ("hospitalityclaw_room_service_order", "RSO-", 0),
    ]
    for entity_type, prefix, current in series:
        conn.execute(
            """INSERT OR IGNORE INTO naming_series
               (id, entity_type, prefix, current_value, company_id)
               VALUES (?, ?, ?, ?, ?)""",
            (_uuid(), entity_type, prefix, current, company_id)
        )
    conn.commit()


def seed_account(conn, company_id: str, name="Test Account",
                 root_type="asset", account_type=None,
                 account_number=None) -> str:
    """Insert a GL account and return its ID."""
    aid = _uuid()
    direction = "debit_normal" if root_type in ("asset", "expense") else "credit_normal"
    conn.execute(
        """INSERT INTO account (id, name, account_number, root_type, account_type,
           balance_direction, company_id, depth)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (aid, name, account_number or f"ACC-{aid[:6]}", root_type,
         account_type, direction, company_id)
    )
    conn.commit()
    return aid


def seed_fiscal_year(conn, company_id: str,
                     start="2026-01-01", end="2026-12-31") -> str:
    """Insert a fiscal year and return its ID."""
    fid = _uuid()
    conn.execute(
        """INSERT INTO fiscal_year (id, name, start_date, end_date, company_id)
           VALUES (?, ?, ?, ?, ?)""",
        (fid, f"FY-{fid[:6]}", start, end, company_id)
    )
    conn.commit()
    return fid


def seed_cost_center(conn, company_id: str, name="Hotel Ops") -> str:
    """Insert a cost center and return its ID."""
    ccid = _uuid()
    conn.execute(
        """INSERT INTO cost_center (id, name, company_id, is_group)
           VALUES (?, ?, ?, 0)""",
        (ccid, name, company_id)
    )
    conn.commit()
    return ccid


def build_env(conn) -> dict:
    """Create a complete hospitality test environment.

    Returns dict with all IDs needed for tests:
    company, room_type, rooms, guest, reservation, outlet, accounts, etc.
    """
    cid = seed_company(conn)
    seed_naming_series(conn, cid)
    fyid = seed_fiscal_year(conn, cid)
    ccid = seed_cost_center(conn, cid)

    # GL accounts
    ar = seed_account(conn, cid, "Accounts Receivable", "asset", "receivable", "1100")
    revenue = seed_account(conn, cid, "Room Revenue", "income", "revenue", "4000")

    # Room types
    std_rt = seed_room_type(conn, cid, "Standard", "150.00", 2)
    dlx_rt = seed_room_type(conn, cid, "Deluxe", "250.00", 3)

    # Rooms
    room_101 = seed_room(conn, std_rt, cid, "101", 1)
    room_102 = seed_room(conn, std_rt, cid, "102", 1)
    room_201 = seed_room(conn, dlx_rt, cid, "201", 2)

    # Guest (core customer + extension)
    core_cust = seed_customer(conn, cid, "Alice Guest", "alice@test.com", "555-0100")
    guest = seed_guest_ext(conn, core_cust, cid)

    # Reservation (pending)
    reservation = seed_reservation(
        conn, guest, std_rt, cid,
        check_in="2026-04-01", check_out="2026-04-03",
        rate_amount="150.00", reservation_status="pending",
    )

    # F&B outlet
    outlet = seed_outlet(conn, cid, "Room Service", "room_service")

    return {
        "company_id": cid,
        "fiscal_year_id": fyid,
        "cost_center_id": ccid,
        "ar": ar,
        "revenue": revenue,
        "std_room_type_id": std_rt,
        "dlx_room_type_id": dlx_rt,
        "room_101_id": room_101,
        "room_102_id": room_102,
        "room_201_id": room_201,
        "core_customer_id": core_cust,
        "guest_id": guest,
        "reservation_id": reservation,
        "outlet_id": outlet,
    }
