#!/usr/bin/env python3
"""HospitalityClaw schema extension -- adds hospitality domain tables to the shared database.

AI-native hospitality management ERP.
17 tables across 7 table-owning domains: rooms, guests, reservations,
front_desk, housekeeping, revenue, fnb. The eighth domain, reports, reads what
the others write and owns no table of its own.

Prerequisite: ERPClaw init_db.py must have run first (creates foundation tables).
Run: python3 init_db.py [db_path]

ADR-0034 phase 2 bulk-39. Schema declared as metadata and provisioned through
`erpclaw_lib.seam`, which emits dialect-correct DDL, replacing a hand-written
``CREATE TABLE`` block opened with ``sqlite3.connect`` that could not run on
PostgreSQL at all. Conversion rules are the pilot's (`erpclaw-esign`): seam
vocabulary only, and every amount a hotel books -- room-type base rates, rate
plans and their adjustments, reservation and group-block totals, folio charges,
room-service orders, minibar unit prices, guest lifetime spend -- stays TEXT,
which is the rule that matters most in a module that quotes a rate and settles a
folio against it.

The pre-conversion docstring said "22 tables across 8 domains". The installer
has 17, the module's scripts read exactly those 17, and the eighth domain
(reports) never owned one -- corrected here rather than carried.
"""
import importlib.util
import os
import sys

# Bootstrap the shared lib only when it is not already reachable — an
# unconditional insert at position 0 overrides a caller that deliberately bound a
# different tree (ADR-0034 phase 2 step 2d).
if importlib.util.find_spec("erpclaw_lib") is None:
    sys.path.insert(0, os.path.join(os.path.expanduser(
        os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "lib"))

from erpclaw_lib.seam import (  # noqa: E402
    CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Table, Text,
    provision, reference_table, text,
)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser(os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "data.sqlite")
DISPLAY_NAME = "HospitalityClaw"

# Foundation tables that must exist before HospitalityClaw can install
REQUIRED_FOUNDATION = [
    "company", "naming_series", "audit_log",
]

METADATA = MetaData()

# Foundation tables this module points at but does not own. Declared for foreign
# key resolution only and never created here — see `seam.reference_table`.
reference_table("company", METADATA)
reference_table("customer", METADATA)

# ==================================================================
# Convention: TEXT for IDs (UUID4), TEXT for money (Decimal),
#             TEXT for dates (ISO-8601). All tables hospitalityclaw_ prefixed.
# ==================================================================

# ==================================================================
# DOMAIN 1: ROOMS (4 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 1. hospitalityclaw_room_type
# ---------------------------------------------------------------------------
ROOM_TYPE = Table(
    "hospitalityclaw_room_type", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("name", Text, nullable=False),
    Column("base_rate", Text, nullable=False, server_default=text("'0'")),
    Column("max_occupancy", Integer, nullable=False, server_default=text("2")),
    Column("description", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

Index("idx_hospitalityclaw_room_type_company", ROOM_TYPE.c.company_id)

# ---------------------------------------------------------------------------
# 2. hospitalityclaw_room
# ---------------------------------------------------------------------------
ROOM = Table(
    "hospitalityclaw_room", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("room_number", Text, nullable=False),
    Column("room_type_id", Text, ForeignKey("hospitalityclaw_room_type.id"),
           nullable=False),
    Column("floor", Integer),
    Column("room_status", Text, nullable=False,
           server_default=text("'available'")),
    Column("is_smoking", Integer, nullable=False, server_default=text("0")),
    Column("notes", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint(
        "room_status IN ('available','occupied','maintenance','out_of_order',"
        "'cleaning')",
        name="ck_hospitalityclaw_room_room_status"),
)

Index("idx_hospitalityclaw_room_company", ROOM.c.company_id)
Index("idx_hospitalityclaw_room_type", ROOM.c.room_type_id)
Index("idx_hospitalityclaw_room_status", ROOM.c.room_status)
Index("idx_hospitalityclaw_room_number_company",
      ROOM.c.room_number, ROOM.c.company_id, unique=True)

# ---------------------------------------------------------------------------
# 3. hospitalityclaw_amenity
# ---------------------------------------------------------------------------
AMENITY = Table(
    "hospitalityclaw_amenity", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("name", Text, nullable=False),
    Column("amenity_type", Text, nullable=False, server_default=text("'room'")),
    Column("description", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("amenity_type IN ('room','property','service')",
                    name="ck_hospitalityclaw_amenity_amenity_type"),
)

Index("idx_hospitalityclaw_amenity_company", AMENITY.c.company_id)

# ---------------------------------------------------------------------------
# 4. hospitalityclaw_room_amenity
# ---------------------------------------------------------------------------
ROOM_AMENITY = Table(
    "hospitalityclaw_room_amenity", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("room_id", Text, ForeignKey("hospitalityclaw_room.id"),
           nullable=False),
    Column("amenity_id", Text, ForeignKey("hospitalityclaw_amenity.id"),
           nullable=False),
    Column("company_id", Text, nullable=False),
)

Index("idx_hospitalityclaw_room_amenity_room", ROOM_AMENITY.c.room_id)
Index("idx_hospitalityclaw_room_amenity_unique",
      ROOM_AMENITY.c.room_id, ROOM_AMENITY.c.amenity_id, unique=True)

# ==================================================================
# DOMAIN 2: GUESTS (2 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 5. hospitalityclaw_guest_ext
#
# Extension table: links to core customer(id) for name/email/phone.
# DO NOT store name, email, phone here — they live in core customer table.
# ---------------------------------------------------------------------------
GUEST_EXT = Table(
    "hospitalityclaw_guest_ext", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text, server_default=text("'HGST-'")),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("id_type", Text),
    Column("id_number", Text),
    Column("nationality", Text),
    Column("vip_level", Text, nullable=False, server_default=text("'regular'")),
    Column("loyalty_points", Integer, nullable=False, server_default=text("0")),
    Column("total_stays", Integer, nullable=False, server_default=text("0")),
    Column("total_spent", Text, nullable=False, server_default=text("'0'")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    # The only company_id in this module that carries a foreign key. The other
    # sixteen tables spell the column without one; that asymmetry is what
    # shipped and is preserved, not tidied.
    Column("company_id", Text, ForeignKey("company.id"), nullable=False),
    Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "vip_level IN ('regular','silver','gold','platinum','diamond')",
        name="ck_hospitalityclaw_guest_ext_vip_level"),
)

Index("idx_hospitalityclaw_guest_ext_company", GUEST_EXT.c.company_id)
Index("idx_hospitalityclaw_guest_ext_vip", GUEST_EXT.c.vip_level)
Index("idx_hospitalityclaw_guest_ext_customer",
      GUEST_EXT.c.customer_id, GUEST_EXT.c.company_id, unique=True)

# ---------------------------------------------------------------------------
# 6. hospitalityclaw_guest_preference
# ---------------------------------------------------------------------------
GUEST_PREFERENCE = Table(
    "hospitalityclaw_guest_preference", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("guest_id", Text, ForeignKey("hospitalityclaw_guest_ext.id"),
           nullable=False),
    Column("preference_type", Text, nullable=False),
    Column("preference_value", Text, nullable=False),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "preference_type IN ('room','pillow','floor','diet','newspaper',"
        "'other')",
        name="ck_hospitalityclaw_guest_preference_preference_type"),
)

Index("idx_hospitalityclaw_guest_pref_guest", GUEST_PREFERENCE.c.guest_id)

# ==================================================================
# DOMAIN 3: RESERVATIONS (3 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 7. hospitalityclaw_rate_plan
# ---------------------------------------------------------------------------
RATE_PLAN = Table(
    "hospitalityclaw_rate_plan", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("name", Text, nullable=False),
    Column("room_type_id", Text, ForeignKey("hospitalityclaw_room_type.id"),
           nullable=False),
    Column("rate_amount", Text, nullable=False, server_default=text("'0'")),
    Column("start_date", Text, nullable=False),
    Column("end_date", Text, nullable=False),
    Column("rate_type", Text, nullable=False,
           server_default=text("'standard'")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "rate_type IN ('standard','weekend','seasonal','promotional')",
        name="ck_hospitalityclaw_rate_plan_rate_type"),
)

Index("idx_hospitalityclaw_rate_plan_room_type", RATE_PLAN.c.room_type_id)
Index("idx_hospitalityclaw_rate_plan_company", RATE_PLAN.c.company_id)

# ---------------------------------------------------------------------------
# 8. hospitalityclaw_reservation
# ---------------------------------------------------------------------------
RESERVATION = Table(
    "hospitalityclaw_reservation", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("guest_id", Text, ForeignKey("hospitalityclaw_guest_ext.id"),
           nullable=False),
    Column("room_type_id", Text, ForeignKey("hospitalityclaw_room_type.id"),
           nullable=False),
    Column("room_id", Text, ForeignKey("hospitalityclaw_room.id")),
    Column("check_in_date", Text, nullable=False),
    Column("check_out_date", Text, nullable=False),
    Column("nights", Integer, nullable=False, server_default=text("1")),
    Column("adults", Integer, nullable=False, server_default=text("1")),
    Column("children", Integer, nullable=False, server_default=text("0")),
    Column("rate_plan_id", Text, ForeignKey("hospitalityclaw_rate_plan.id")),
    Column("rate_amount", Text, nullable=False, server_default=text("'0'")),
    Column("total_amount", Text, nullable=False, server_default=text("'0'")),
    Column("reservation_status", Text, nullable=False,
           server_default=text("'pending'")),
    # `source` is the one status-ish column here that is nullable and still
    # defaulted; every other CHECKed column in this table is NOT NULL.
    Column("source", Text, server_default=text("'direct'")),
    Column("special_requests", Text),
    Column("gl_entry_ids", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint(
        "reservation_status IN ('pending','confirmed','checked_in',"
        "'checked_out','cancelled','no_show')",
        name="ck_hospitalityclaw_reservation_reservation_status"),
    CheckConstraint(
        "source IN ('direct','phone','online','group','walk_in')",
        name="ck_hospitalityclaw_reservation_source"),
)

Index("idx_hospitalityclaw_reservation_guest", RESERVATION.c.guest_id)
Index("idx_hospitalityclaw_reservation_room_type", RESERVATION.c.room_type_id)
Index("idx_hospitalityclaw_reservation_status",
      RESERVATION.c.reservation_status)
Index("idx_hospitalityclaw_reservation_dates",
      RESERVATION.c.check_in_date, RESERVATION.c.check_out_date)
Index("idx_hospitalityclaw_reservation_company", RESERVATION.c.company_id)

# ---------------------------------------------------------------------------
# 9. hospitalityclaw_group_block
# ---------------------------------------------------------------------------
GROUP_BLOCK = Table(
    "hospitalityclaw_group_block", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("name", Text, nullable=False),
    Column("contact_name", Text),
    Column("contact_email", Text),
    Column("room_type_id", Text, ForeignKey("hospitalityclaw_room_type.id"),
           nullable=False),
    Column("rooms_blocked", Integer, nullable=False, server_default=text("1")),
    Column("check_in_date", Text, nullable=False),
    Column("check_out_date", Text, nullable=False),
    Column("rate_amount", Text, nullable=False, server_default=text("'0'")),
    Column("block_status", Text, nullable=False,
           server_default=text("'tentative'")),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    CheckConstraint("block_status IN ('tentative','confirmed','released')",
                    name="ck_hospitalityclaw_group_block_block_status"),
)

Index("idx_hospitalityclaw_group_block_company", GROUP_BLOCK.c.company_id)
Index("idx_hospitalityclaw_group_block_status", GROUP_BLOCK.c.block_status)

# ==================================================================
# DOMAIN 4: FRONT DESK (2 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 10. hospitalityclaw_guest_request
# ---------------------------------------------------------------------------
GUEST_REQUEST = Table(
    "hospitalityclaw_guest_request", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("reservation_id", Text,
           ForeignKey("hospitalityclaw_reservation.id"), nullable=False),
    Column("request_type", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("priority", Text, nullable=False, server_default=text("'normal'")),
    Column("request_status", Text, nullable=False,
           server_default=text("'open'")),
    Column("assigned_to", Text),
    Column("completed_at", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "request_type IN ('housekeeping','maintenance','amenity','food',"
        "'other')",
        name="ck_hospitalityclaw_guest_request_request_type"),
    CheckConstraint("priority IN ('low','normal','high','urgent')",
                    name="ck_hospitalityclaw_guest_request_priority"),
    CheckConstraint(
        "request_status IN ('open','in_progress','completed','cancelled')",
        name="ck_hospitalityclaw_guest_request_request_status"),
)

Index("idx_hospitalityclaw_guest_request_reservation",
      GUEST_REQUEST.c.reservation_id)
Index("idx_hospitalityclaw_guest_request_status",
      GUEST_REQUEST.c.request_status)

# ---------------------------------------------------------------------------
# 11. hospitalityclaw_folio_charge
# ---------------------------------------------------------------------------
FOLIO_CHARGE = Table(
    "hospitalityclaw_folio_charge", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("reservation_id", Text,
           ForeignKey("hospitalityclaw_reservation.id"), nullable=False),
    Column("charge_date", Text, nullable=False),
    Column("charge_type", Text, nullable=False),
    Column("description", Text),
    Column("amount", Text, nullable=False, server_default=text("'0'")),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "charge_type IN ('room','food','minibar','phone','laundry','parking',"
        "'other')",
        name="ck_hospitalityclaw_folio_charge_charge_type"),
)

Index("idx_hospitalityclaw_folio_charge_reservation",
      FOLIO_CHARGE.c.reservation_id)

# ==================================================================
# DOMAIN 5: HOUSEKEEPING (2 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 12. hospitalityclaw_housekeeping_task
# ---------------------------------------------------------------------------
HOUSEKEEPING_TASK = Table(
    "hospitalityclaw_housekeeping_task", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("room_id", Text, ForeignKey("hospitalityclaw_room.id"),
           nullable=False),
    Column("task_type", Text, nullable=False),
    Column("assigned_to", Text),
    Column("scheduled_date", Text, nullable=False),
    Column("task_status", Text, nullable=False,
           server_default=text("'pending'")),
    Column("started_at", Text),
    Column("completed_at", Text),
    Column("notes", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "task_type IN ('checkout_clean','stayover_clean','deep_clean',"
        "'turndown','inspection')",
        name="ck_hospitalityclaw_housekeeping_task_task_type"),
    CheckConstraint(
        "task_status IN ('pending','in_progress','completed','skipped')",
        name="ck_hospitalityclaw_housekeeping_task_task_status"),
)

Index("idx_hospitalityclaw_hk_task_room", HOUSEKEEPING_TASK.c.room_id)
Index("idx_hospitalityclaw_hk_task_status", HOUSEKEEPING_TASK.c.task_status)
Index("idx_hospitalityclaw_hk_task_date", HOUSEKEEPING_TASK.c.scheduled_date)

# ---------------------------------------------------------------------------
# 13. hospitalityclaw_inspection
# ---------------------------------------------------------------------------
INSPECTION = Table(
    "hospitalityclaw_inspection", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("room_id", Text, ForeignKey("hospitalityclaw_room.id"),
           nullable=False),
    Column("inspector", Text, nullable=False),
    Column("inspection_date", Text, nullable=False),
    Column("score", Integer, nullable=False, server_default=text("0")),
    Column("passed", Integer, nullable=False, server_default=text("1")),
    Column("notes", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

Index("idx_hospitalityclaw_inspection_room", INSPECTION.c.room_id)

# ==================================================================
# DOMAIN 6: REVENUE (1 table)
# ==================================================================

# ---------------------------------------------------------------------------
# 14. hospitalityclaw_rate_adjustment
# ---------------------------------------------------------------------------
RATE_ADJUSTMENT = Table(
    "hospitalityclaw_rate_adjustment", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("room_type_id", Text, ForeignKey("hospitalityclaw_room_type.id"),
           nullable=False),
    Column("adjustment_date", Text, nullable=False),
    Column("adjustment_type", Text, nullable=False),
    # Both the percentage and the resulting rate are TEXT and nullable: an
    # override carries a rate with no percentage, a percentage move carries the
    # reverse.
    Column("adjustment_pct", Text),
    Column("adjusted_rate", Text),
    Column("reason", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "adjustment_type IN ('increase','decrease','override')",
        name="ck_hospitalityclaw_rate_adjustment_adjustment_type"),
)

Index("idx_hospitalityclaw_rate_adj_room_type", RATE_ADJUSTMENT.c.room_type_id)

# ==================================================================
# DOMAIN 7: F&B (3 tables)
# ==================================================================

# ---------------------------------------------------------------------------
# 15. hospitalityclaw_outlet
# ---------------------------------------------------------------------------
OUTLET = Table(
    "hospitalityclaw_outlet", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("name", Text, nullable=False),
    Column("outlet_type", Text, nullable=False),
    Column("operating_hours", Text),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "outlet_type IN ('restaurant','bar','room_service','banquet','pool')",
        name="ck_hospitalityclaw_outlet_outlet_type"),
)

Index("idx_hospitalityclaw_outlet_company", OUTLET.c.company_id)

# ---------------------------------------------------------------------------
# 16. hospitalityclaw_room_service_order
# ---------------------------------------------------------------------------
ROOM_SERVICE_ORDER = Table(
    "hospitalityclaw_room_service_order", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("naming_series", Text),
    Column("reservation_id", Text,
           ForeignKey("hospitalityclaw_reservation.id"), nullable=False),
    Column("outlet_id", Text, ForeignKey("hospitalityclaw_outlet.id"),
           nullable=False),
    Column("order_time", Text, nullable=False),
    Column("items_json", Text, nullable=False, server_default=text("'[]'")),
    Column("total_amount", Text, nullable=False, server_default=text("'0'")),
    Column("order_status", Text, nullable=False,
           server_default=text("'pending'")),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint(
        "order_status IN ('pending','preparing','delivered','cancelled')",
        name="ck_hospitalityclaw_room_service_order_order_status"),
)

Index("idx_hospitalityclaw_rso_reservation",
      ROOM_SERVICE_ORDER.c.reservation_id)
Index("idx_hospitalityclaw_rso_outlet", ROOM_SERVICE_ORDER.c.outlet_id)

# ---------------------------------------------------------------------------
# 17. hospitalityclaw_minibar_consumption
# ---------------------------------------------------------------------------
MINIBAR_CONSUMPTION = Table(
    "hospitalityclaw_minibar_consumption", METADATA,
    Column("id", Text, primary_key=True, nullable=True),
    Column("reservation_id", Text,
           ForeignKey("hospitalityclaw_reservation.id"), nullable=False),
    Column("item_name", Text, nullable=False),
    Column("quantity", Integer, nullable=False, server_default=text("1")),
    Column("unit_price", Text, nullable=False, server_default=text("'0'")),
    Column("total", Text, nullable=False, server_default=text("'0'")),
    Column("consumption_date", Text, nullable=False),
    Column("company_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

Index("idx_hospitalityclaw_minibar_reservation",
      MINIBAR_CONSUMPTION.c.reservation_id)


def _require_foundation(db_path):
    """The pre-conversion installer's foundation probe, asked through the seam.

    The original read ``sqlite_master`` directly, so the guard that exists to
    produce a friendly error was itself SQLite-only. ``seam.table_exists``
    answers on both backends (ADR-0034 bulk-39). The wording is this module's
    own, unchanged.
    """
    from erpclaw_lib import seam

    missing = [t for t in REQUIRED_FOUNDATION if not seam.table_exists(t, db_path)]
    if missing:
        print(f"ERROR: Foundation tables missing: {', '.join(missing)}")
        print("Run erpclaw-setup first: clawhub install erpclaw-setup")
        sys.exit(1)


def create_hospitalityclaw_tables(db_path):
    """Create HospitalityClaw tables and indexes on whichever backend is configured.

    Same contract as before the ADR-0034 conversion: idempotent, and the counts
    it reports are what was ACTUALLY created rather than what was declared.
    """
    _require_foundation(db_path)
    result = provision(METADATA, db_path)
    print(f"{DISPLAY_NAME} tables created successfully in {db_path}")
    return {
        "database": db_path,
        "tables": result["tables"],
        "indexes": result["indexes"],
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Support both --db-path and positional arg
        db = sys.argv[1]
        if db == "--db-path" and len(sys.argv) > 2:
            db = sys.argv[2]
    else:
        db = DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(db), exist_ok=True)
    create_hospitalityclaw_tables(db)
