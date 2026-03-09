#!/usr/bin/env python3
"""HospitalityClaw -- db_query.py (unified router)

AI-native hospitality management ERP.
Routes all actions across 8 domain modules: rooms, reservations, front_desk,
housekeeping, guests, revenue, fnb, reports.

Usage: python3 db_query.py --action <action-name> [--flags ...]
Output: JSON to stdout, exit 0 on success, exit 1 on error.
"""
import argparse
import json
import os
import sys

# Add shared lib to path
try:
    sys.path.insert(0, os.path.expanduser("~/.openclaw/erpclaw/lib"))
    from erpclaw_lib.db import get_connection, ensure_db_exists, DEFAULT_DB_PATH
    from erpclaw_lib.validation import check_input_lengths
    from erpclaw_lib.response import ok, err
    from erpclaw_lib.dependencies import check_required_tables
except ImportError:
    import json as _json
    print(_json.dumps({
        "status": "error",
        "error": "ERPClaw foundation not installed. Install erpclaw-setup first: clawhub install erpclaw-setup",
        "suggestion": "clawhub install erpclaw-setup"
    }))
    sys.exit(1)

# Add this script's directory so domain modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rooms import ACTIONS as ROOMS_ACTIONS
from reservations import ACTIONS as RESERVATIONS_ACTIONS
from front_desk import ACTIONS as FRONT_DESK_ACTIONS
from housekeeping import ACTIONS as HOUSEKEEPING_ACTIONS
from guests import ACTIONS as GUESTS_ACTIONS
from revenue import ACTIONS as REVENUE_ACTIONS
from fnb import ACTIONS as FNB_ACTIONS
from reports import ACTIONS as REPORTS_ACTIONS

# ---------------------------------------------------------------------------
# Merge all domain actions into one router
# ---------------------------------------------------------------------------
SKILL = "hospitalityclaw"
REQUIRED_TABLES = ["company", "hospitalityclaw_room_type"]

ACTIONS = {}
ACTIONS.update(ROOMS_ACTIONS)
ACTIONS.update(GUESTS_ACTIONS)
ACTIONS.update(RESERVATIONS_ACTIONS)
ACTIONS.update(FRONT_DESK_ACTIONS)
ACTIONS.update(HOUSEKEEPING_ACTIONS)
ACTIONS.update(REVENUE_ACTIONS)
ACTIONS.update(FNB_ACTIONS)
ACTIONS.update(REPORTS_ACTIONS)
ACTIONS["status"] = lambda conn, args: ok({
    "skill": SKILL,
    "version": "1.0.0",
    "actions_available": len([k for k in ACTIONS if k != "status"]),
    "domains": ["rooms", "reservations", "front_desk", "housekeeping", "guests", "revenue", "fnb", "reports"],
    "database": DEFAULT_DB_PATH,
})


def main():
    parser = argparse.ArgumentParser(description="hospitalityclaw")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS.keys()))
    parser.add_argument("--db-path", default=None)

    # -- Shared --
    parser.add_argument("--company-id")
    parser.add_argument("--search")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--notes")
    parser.add_argument("--description")
    parser.add_argument("--name")
    parser.add_argument("--reason")

    # -- ROOMS domain --
    parser.add_argument("--room-type-id")
    parser.add_argument("--base-rate")
    parser.add_argument("--max-occupancy")
    parser.add_argument("--room-id")
    parser.add_argument("--room-number")
    parser.add_argument("--floor")
    parser.add_argument("--room-status")
    parser.add_argument("--is-smoking")
    parser.add_argument("--amenity-id")
    parser.add_argument("--amenity-type")

    # -- GUESTS domain --
    parser.add_argument("--guest-id")
    parser.add_argument("--email")
    parser.add_argument("--phone")
    parser.add_argument("--id-type")
    parser.add_argument("--id-number")
    parser.add_argument("--nationality")
    parser.add_argument("--vip-level")
    parser.add_argument("--preference-type")
    parser.add_argument("--preference-value")

    # -- RESERVATIONS domain --
    parser.add_argument("--reservation-id")
    parser.add_argument("--check-in-date")
    parser.add_argument("--check-out-date")
    parser.add_argument("--adults")
    parser.add_argument("--children")
    parser.add_argument("--rate-plan-id")
    parser.add_argument("--rate-amount")
    parser.add_argument("--reservation-status")
    parser.add_argument("--source")
    parser.add_argument("--special-requests")
    parser.add_argument("--rate-type")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--rooms-blocked")
    parser.add_argument("--contact-name")
    parser.add_argument("--contact-email")
    parser.add_argument("--block-status")

    # -- FRONT DESK domain --
    parser.add_argument("--request-id")
    parser.add_argument("--request-type")
    parser.add_argument("--priority")
    parser.add_argument("--request-status")
    parser.add_argument("--assigned-to")
    parser.add_argument("--new-checkout-date")
    parser.add_argument("--new-room-id")
    parser.add_argument("--charge-type")
    parser.add_argument("--amount")

    # -- HOUSEKEEPING domain --
    parser.add_argument("--task-id")
    parser.add_argument("--task-type")
    parser.add_argument("--task-status")
    parser.add_argument("--scheduled-date")
    parser.add_argument("--inspector")
    parser.add_argument("--inspection-date")
    parser.add_argument("--score")

    # -- REVENUE domain --
    parser.add_argument("--adjustment-date")
    parser.add_argument("--adjustment-type")
    parser.add_argument("--adjustment-pct")
    parser.add_argument("--adjusted-rate")

    # -- FNB domain --
    parser.add_argument("--outlet-id")
    parser.add_argument("--outlet-type")
    parser.add_argument("--operating-hours")
    parser.add_argument("--order-id")
    parser.add_argument("--order-status")
    parser.add_argument("--items-json")
    parser.add_argument("--total-amount")
    parser.add_argument("--item-name")
    parser.add_argument("--quantity")
    parser.add_argument("--unit-price")
    parser.add_argument("--consumption-date")

    # -- REPORTS domain --
    parser.add_argument("--report-date")

    args, _unknown = parser.parse_known_args()
    check_input_lengths(args)

    db_path = args.db_path or DEFAULT_DB_PATH
    ensure_db_exists(db_path)
    conn = get_connection(db_path)

    _dep = check_required_tables(conn, REQUIRED_TABLES)
    if _dep:
        _dep["suggestion"] = "clawhub install erpclaw-setup && clawhub install hospitalityclaw"
        print(json.dumps(_dep, indent=2))
        conn.close()
        sys.exit(1)

    try:
        ACTIONS[args.action](conn, args)
    except Exception as e:
        conn.rollback()
        sys.stderr.write(f"[{SKILL}] {e}\n")
        err(str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
