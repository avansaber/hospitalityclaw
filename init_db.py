#!/usr/bin/env python3
"""HospitalityClaw schema extension -- adds hospitality domain tables to the shared database.

AI-native hospitality management ERP.
22 tables across 8 domains: rooms, reservations, front_desk, housekeeping,
guests, revenue, fnb, reports.

Prerequisite: ERPClaw init_db.py must have run first (creates foundation tables).
Run: python3 init_db.py [db_path]
"""
import os
import sqlite3
import sys
import uuid


DEFAULT_DB_PATH = os.path.expanduser("~/.openclaw/erpclaw/data.sqlite")
DISPLAY_NAME = "HospitalityClaw"

# Foundation tables that must exist before HospitalityClaw can install
REQUIRED_FOUNDATION = [
    "company", "naming_series", "audit_log",
]


def create_hospitalityclaw_tables(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")

    # -- Verify ERPClaw foundation --
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    missing = [t for t in REQUIRED_FOUNDATION if t not in tables]
    if missing:
        print(f"ERROR: Foundation tables missing: {', '.join(missing)}")
        print("Run erpclaw-setup first: clawhub install erpclaw-setup")
        conn.close()
        sys.exit(1)

    # -- Create all HospitalityClaw domain tables --
    conn.executescript("""
        -- ==========================================================
        -- HospitalityClaw Domain Tables
        -- 22 tables, 8 domains, hospitalityclaw_ prefix
        -- Convention: TEXT for IDs (UUID4), TEXT for money (Decimal),
        --             TEXT for dates (ISO-8601)
        -- ==========================================================

        -- ── ROOMS domain ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_room_type (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            name TEXT NOT NULL,
            base_rate TEXT NOT NULL DEFAULT '0',
            max_occupancy INTEGER NOT NULL DEFAULT 2,
            description TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_room_type_company ON hospitalityclaw_room_type(company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_room (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            room_number TEXT NOT NULL,
            room_type_id TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id),
            floor INTEGER,
            room_status TEXT NOT NULL DEFAULT 'available'
                CHECK(room_status IN ('available','occupied','maintenance','out_of_order','cleaning')),
            is_smoking INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_room_company ON hospitalityclaw_room(company_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_room_type ON hospitalityclaw_room(room_type_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_room_status ON hospitalityclaw_room(room_status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hospitalityclaw_room_number_company ON hospitalityclaw_room(room_number, company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_amenity (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            amenity_type TEXT NOT NULL DEFAULT 'room'
                CHECK(amenity_type IN ('room','property','service')),
            description TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_amenity_company ON hospitalityclaw_amenity(company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_room_amenity (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES hospitalityclaw_room(id),
            amenity_id TEXT NOT NULL REFERENCES hospitalityclaw_amenity(id),
            company_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_room_amenity_room ON hospitalityclaw_room_amenity(room_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hospitalityclaw_room_amenity_unique ON hospitalityclaw_room_amenity(room_id, amenity_id);

        -- ── GUESTS domain ────────────────────────────────────────────
        -- Extension table: links to core customer(id) for name/email/phone.
        -- DO NOT store name, email, phone here — they live in core customer table.
        CREATE TABLE IF NOT EXISTS hospitalityclaw_guest_ext (
            id TEXT PRIMARY KEY,
            naming_series TEXT DEFAULT 'HGST-',
            customer_id TEXT NOT NULL REFERENCES customer(id),
            id_type TEXT,
            id_number TEXT,
            nationality TEXT,
            vip_level TEXT NOT NULL DEFAULT 'regular'
                CHECK(vip_level IN ('regular','silver','gold','platinum','diamond')),
            loyalty_points INTEGER NOT NULL DEFAULT 0,
            total_stays INTEGER NOT NULL DEFAULT 0,
            total_spent TEXT NOT NULL DEFAULT '0',
            is_active INTEGER NOT NULL DEFAULT 1,
            company_id TEXT NOT NULL REFERENCES company(id),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_ext_company ON hospitalityclaw_guest_ext(company_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_ext_vip ON hospitalityclaw_guest_ext(vip_level);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_ext_customer ON hospitalityclaw_guest_ext(customer_id, company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_guest_preference (
            id TEXT PRIMARY KEY,
            guest_id TEXT NOT NULL REFERENCES hospitalityclaw_guest_ext(id),
            preference_type TEXT NOT NULL
                CHECK(preference_type IN ('room','pillow','floor','diet','newspaper','other')),
            preference_value TEXT NOT NULL,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_pref_guest ON hospitalityclaw_guest_preference(guest_id);

        -- ── RESERVATIONS domain ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_rate_plan (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            name TEXT NOT NULL,
            room_type_id TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id),
            rate_amount TEXT NOT NULL DEFAULT '0',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            rate_type TEXT NOT NULL DEFAULT 'standard'
                CHECK(rate_type IN ('standard','weekend','seasonal','promotional')),
            is_active INTEGER NOT NULL DEFAULT 1,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_rate_plan_room_type ON hospitalityclaw_rate_plan(room_type_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_rate_plan_company ON hospitalityclaw_rate_plan(company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_reservation (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            guest_id TEXT NOT NULL REFERENCES hospitalityclaw_guest_ext(id),
            room_type_id TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id),
            room_id TEXT REFERENCES hospitalityclaw_room(id),
            check_in_date TEXT NOT NULL,
            check_out_date TEXT NOT NULL,
            nights INTEGER NOT NULL DEFAULT 1,
            adults INTEGER NOT NULL DEFAULT 1,
            children INTEGER NOT NULL DEFAULT 0,
            rate_plan_id TEXT REFERENCES hospitalityclaw_rate_plan(id),
            rate_amount TEXT NOT NULL DEFAULT '0',
            total_amount TEXT NOT NULL DEFAULT '0',
            reservation_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(reservation_status IN ('pending','confirmed','checked_in','checked_out','cancelled','no_show')),
            source TEXT DEFAULT 'direct'
                CHECK(source IN ('direct','phone','online','group','walk_in')),
            special_requests TEXT,
            gl_entry_ids TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_reservation_guest ON hospitalityclaw_reservation(guest_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_reservation_room_type ON hospitalityclaw_reservation(room_type_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_reservation_status ON hospitalityclaw_reservation(reservation_status);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_reservation_dates ON hospitalityclaw_reservation(check_in_date, check_out_date);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_reservation_company ON hospitalityclaw_reservation(company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_group_block (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            name TEXT NOT NULL,
            contact_name TEXT,
            contact_email TEXT,
            room_type_id TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id),
            rooms_blocked INTEGER NOT NULL DEFAULT 1,
            check_in_date TEXT NOT NULL,
            check_out_date TEXT NOT NULL,
            rate_amount TEXT NOT NULL DEFAULT '0',
            block_status TEXT NOT NULL DEFAULT 'tentative'
                CHECK(block_status IN ('tentative','confirmed','released')),
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_group_block_company ON hospitalityclaw_group_block(company_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_group_block_status ON hospitalityclaw_group_block(block_status);

        -- ── FRONT DESK domain ────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_guest_request (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL REFERENCES hospitalityclaw_reservation(id),
            request_type TEXT NOT NULL
                CHECK(request_type IN ('housekeeping','maintenance','amenity','food','other')),
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal'
                CHECK(priority IN ('low','normal','high','urgent')),
            request_status TEXT NOT NULL DEFAULT 'open'
                CHECK(request_status IN ('open','in_progress','completed','cancelled')),
            assigned_to TEXT,
            completed_at TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_request_reservation ON hospitalityclaw_guest_request(reservation_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_guest_request_status ON hospitalityclaw_guest_request(request_status);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_folio_charge (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL REFERENCES hospitalityclaw_reservation(id),
            charge_date TEXT NOT NULL,
            charge_type TEXT NOT NULL
                CHECK(charge_type IN ('room','food','minibar','phone','laundry','parking','other')),
            description TEXT,
            amount TEXT NOT NULL DEFAULT '0',
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_folio_charge_reservation ON hospitalityclaw_folio_charge(reservation_id);

        -- ── HOUSEKEEPING domain ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_housekeeping_task (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES hospitalityclaw_room(id),
            task_type TEXT NOT NULL
                CHECK(task_type IN ('checkout_clean','stayover_clean','deep_clean','turndown','inspection')),
            assigned_to TEXT,
            scheduled_date TEXT NOT NULL,
            task_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(task_status IN ('pending','in_progress','completed','skipped')),
            started_at TEXT,
            completed_at TEXT,
            notes TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_hk_task_room ON hospitalityclaw_housekeeping_task(room_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_hk_task_status ON hospitalityclaw_housekeeping_task(task_status);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_hk_task_date ON hospitalityclaw_housekeeping_task(scheduled_date);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_inspection (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES hospitalityclaw_room(id),
            inspector TEXT NOT NULL,
            inspection_date TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            passed INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_inspection_room ON hospitalityclaw_inspection(room_id);

        -- ── REVENUE domain ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_rate_adjustment (
            id TEXT PRIMARY KEY,
            room_type_id TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id),
            adjustment_date TEXT NOT NULL,
            adjustment_type TEXT NOT NULL
                CHECK(adjustment_type IN ('increase','decrease','override')),
            adjustment_pct TEXT,
            adjusted_rate TEXT,
            reason TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_rate_adj_room_type ON hospitalityclaw_rate_adjustment(room_type_id);

        -- ── F&B domain ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS hospitalityclaw_outlet (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            outlet_type TEXT NOT NULL
                CHECK(outlet_type IN ('restaurant','bar','room_service','banquet','pool')),
            operating_hours TEXT,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_outlet_company ON hospitalityclaw_outlet(company_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_room_service_order (
            id TEXT PRIMARY KEY,
            naming_series TEXT,
            reservation_id TEXT NOT NULL REFERENCES hospitalityclaw_reservation(id),
            outlet_id TEXT NOT NULL REFERENCES hospitalityclaw_outlet(id),
            order_time TEXT NOT NULL,
            items_json TEXT NOT NULL DEFAULT '[]',
            total_amount TEXT NOT NULL DEFAULT '0',
            order_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(order_status IN ('pending','preparing','delivered','cancelled')),
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_rso_reservation ON hospitalityclaw_room_service_order(reservation_id);
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_rso_outlet ON hospitalityclaw_room_service_order(outlet_id);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_minibar_consumption (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL REFERENCES hospitalityclaw_reservation(id),
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit_price TEXT NOT NULL DEFAULT '0',
            total TEXT NOT NULL DEFAULT '0',
            consumption_date TEXT NOT NULL,
            company_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hospitalityclaw_minibar_reservation ON hospitalityclaw_minibar_consumption(reservation_id);
    """)

    conn.commit()
    print(f"{DISPLAY_NAME} tables created successfully in {db_path}")
    conn.close()


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
