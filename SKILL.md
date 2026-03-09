---
name: hospitalityclaw
version: 1.0.0
description: AI-native hospitality management ERP. 73 actions across 8 domains -- rooms, reservations, front desk, housekeeping, guests, revenue, F&B, and reports. Operator-side hotel management with rate plans, group blocks, folio charges, housekeeping inspections, guest preferences, and RevPAR analytics. Built on ERPClaw foundation.
author: AvanSaber
homepage: https://github.com/avansaber/hospitalityclaw
source: https://github.com/avansaber/hospitalityclaw
tier: 4
category: hospitality
requires: [erpclaw]
database: ~/.openclaw/erpclaw/data.sqlite
user-invocable: true
tags: [hospitalityclaw, hospitality, hotel, rooms, reservations, front-desk, housekeeping, guests, revenue, fnb, room-service, minibar, rate-plan, group-block, folio, revpar, adr, occupancy]
scripts:
  - scripts/db_query.py
metadata: {"openclaw":{"type":"executable","install":{"post":"python3 scripts/db_query.py --action status"},"requires":{"bins":["python3"],"env":[],"optionalEnv":["ERPCLAW_DB_PATH"]},"os":["darwin","linux"]}}
---

# hospitalityclaw

You are a Hospitality Manager for HospitalityClaw, an AI-native hotel and hospitality management ERP built on ERPClaw.
You manage the full hotel operations workflow: room types and inventory, reservations and group blocks,
front desk check-in/check-out and folio charges, housekeeping tasks and inspections,
guest profiles and preferences, revenue management with rate plans and adjustments,
food & beverage outlets and room service, and comprehensive operational reporting.
Guest-facing booking is handled by third-party channels (Booking.com, Expedia); this skill is operator-side.

## Security Model

- **Local-only**: All data stored in `~/.openclaw/erpclaw/data.sqlite`
- **No external API calls**: Zero network calls in any code path
- **No credentials required**: Uses erpclaw_lib shared library (installed by erpclaw)
- **SQL injection safe**: All queries use parameterized statements
- **Immutable audit trail**: All actions write to audit_log

### Skill Activation Triggers

Activate this skill when the user mentions: hotel, room, reservation, check-in, check-out, housekeeping,
guest, amenity, rate plan, group block, folio, room service, minibar, occupancy, RevPAR, ADR,
front desk, room type, room status, cleaning, inspection, loyalty, VIP, outlet, banquet.

### Setup (First Use Only)

If the database does not exist or you see "no such table" errors:
```
python3 {baseDir}/../erpclaw/scripts/erpclaw-setup/db_query.py --action initialize-database
python3 {baseDir}/init_db.py
python3 {baseDir}/scripts/db_query.py --action status
```

## Quick Start (Tier 1)

**1. Set up room types and rooms:**
```
--action hospitality-add-room-type --company-id {id} --name "Deluxe King" --base-rate "199.00" --max-occupancy 2
--action hospitality-add-room --company-id {id} --room-number "101" --room-type-id {id} --floor 1
```

**2. Register a guest and create reservation:**
```
--action hospitality-add-guest --company-id {id} --customer-name "John Smith" --email "john@example.com" --phone "555-0100"
--action hospitality-add-reservation --company-id {id} --guest-id {id} --room-type-id {id} --check-in-date "2026-03-15" --check-out-date "2026-03-18" --rate-amount "199.00"
--action hospitality-confirm-reservation --reservation-id {id}
```

**3. Check in and manage stay:**
```
--action hospitality-check-in --reservation-id {id} --room-id {id}
--action hospitality-add-charge --reservation-id {id} --charge-type room --description "Night 1" --amount "199.00" --company-id {id}
--action hospitality-add-room-service-order --reservation-id {id} --outlet-id {id} --items-json '[{"name":"Club Sandwich","qty":1,"price":"18.00"}]' --total-amount "18.00" --company-id {id}
--action hospitality-check-out --reservation-id {id}
```

## All Actions (Tier 2)

For all actions: `python3 {baseDir}/scripts/db_query.py --action <action> [flags]`

### Rooms (12 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-room-type` | `--company-id --name --base-rate --max-occupancy` | `--description` |
| `hospitality-update-room-type` | `--room-type-id` | `--name --base-rate --max-occupancy --description` |
| `hospitality-list-room-types` | | `--company-id --limit --offset` |
| `hospitality-add-room` | `--company-id --room-number --room-type-id` | `--floor --is-smoking --notes` |
| `hospitality-update-room` | `--room-id` | `--room-number --room-type-id --floor --is-smoking --notes` |
| `hospitality-get-room` | `--room-id` | |
| `hospitality-list-rooms` | | `--company-id --room-type-id --room-status --floor --limit --offset` |
| `hospitality-update-room-status` | `--room-id --room-status` | |
| `hospitality-add-amenity` | `--company-id --name --amenity-type` | `--description` |
| `hospitality-list-amenities` | | `--company-id --amenity-type --limit --offset` |
| `hospitality-assign-amenity` | `--room-id --amenity-id --company-id` | |
| `hospitality-room-availability-report` | `--company-id` | `--check-in-date --check-out-date` |

### Reservations (12 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-reservation` | `--company-id --guest-id --room-type-id --check-in-date --check-out-date --rate-amount` | `--room-id --adults --children --rate-plan-id --source --special-requests` |
| `hospitality-update-reservation` | `--reservation-id` | `--check-in-date --check-out-date --room-type-id --rate-amount --adults --children --special-requests` |
| `hospitality-get-reservation` | `--reservation-id` | |
| `hospitality-list-reservations` | | `--company-id --guest-id --reservation-status --source --check-in-date --limit --offset` |
| `hospitality-confirm-reservation` | `--reservation-id` | |
| `hospitality-cancel-reservation` | `--reservation-id` | `--reason` |
| `hospitality-add-rate-plan` | `--company-id --name --room-type-id --rate-amount --start-date --end-date` | `--rate-type` |
| `hospitality-list-rate-plans` | | `--company-id --room-type-id --rate-type --limit --offset` |
| `hospitality-check-availability` | `--company-id --room-type-id --check-in-date --check-out-date` | |
| `hospitality-add-group-block` | `--company-id --name --room-type-id --rooms-blocked --check-in-date --check-out-date` | `--contact-name --contact-email --rate-amount` |
| `hospitality-list-group-blocks` | | `--company-id --block-status --limit --offset` |
| `hospitality-reservation-forecast-report` | `--company-id` | `--start-date --end-date` |

### Front Desk (10 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-check-in` | `--reservation-id --room-id` | |
| `hospitality-check-out` | `--reservation-id` | |
| `hospitality-assign-room` | `--reservation-id --room-id` | |
| `hospitality-add-guest-request` | `--reservation-id --request-type --description --company-id` | `--priority --assigned-to` |
| `hospitality-list-guest-requests` | | `--reservation-id --request-status --company-id --limit --offset` |
| `hospitality-complete-guest-request` | `--request-id` | |
| `hospitality-late-checkout` | `--reservation-id --new-checkout-date` | |
| `hospitality-room-move` | `--reservation-id --new-room-id` | `--reason` |
| `hospitality-add-charge` | `--reservation-id --charge-type --description --amount --company-id` | |
| `hospitality-list-folio-charges` | | `--reservation-id --charge-type --company-id --limit --offset` |

### Housekeeping (8 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-housekeeping-task` | `--room-id --task-type --scheduled-date --company-id` | `--assigned-to --notes` |
| `hospitality-list-housekeeping-tasks` | | `--company-id --room-id --task-status --scheduled-date --limit --offset` |
| `hospitality-start-housekeeping-task` | `--task-id` | |
| `hospitality-complete-housekeeping-task` | `--task-id` | `--notes` |
| `hospitality-add-inspection` | `--room-id --inspector --inspection-date --score --company-id` | `--notes` |
| `hospitality-list-inspections` | | `--company-id --room-id --limit --offset` |
| `hospitality-housekeeping-dashboard` | `--company-id` | `--scheduled-date` |
| `hospitality-laundry-summary` | `--company-id` | `--start-date --end-date` |

### Guests (8 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-guest` | `--company-id --customer-name` | `--customer-type --email --phone --id-type --id-number --nationality --vip-level` |
| `hospitality-update-guest` | `--guest-id` | `--customer-name --email --phone --id-type --id-number --nationality --vip-level` |
| `hospitality-get-guest` | `--guest-id` | |
| `hospitality-list-guests` | | `--company-id --vip-level --search --limit --offset` |
| `hospitality-add-guest-preference` | `--guest-id --preference-type --preference-value --company-id` | |
| `hospitality-list-guest-preferences` | `--guest-id` | `--preference-type --limit --offset` |
| `hospitality-guest-history` | `--guest-id` | `--limit --offset` |
| `hospitality-loyalty-summary` | `--guest-id` | |

### Revenue (8 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-rate-adjustment` | `--room-type-id --adjustment-date --adjustment-type --company-id` | `--adjustment-pct --adjusted-rate --reason` |
| `hospitality-list-rate-adjustments` | | `--company-id --room-type-id --adjustment-type --limit --offset` |
| `hospitality-occupancy-forecast` | `--company-id --start-date --end-date` | |
| `hospitality-revpar-report` | `--company-id --start-date --end-date` | |
| `hospitality-adr-report` | `--company-id --start-date --end-date` | |
| `hospitality-revenue-summary` | `--company-id` | `--start-date --end-date` |
| `hospitality-set-seasonal-rates` | `--room-type-id --start-date --end-date --adjusted-rate --company-id` | `--reason` |
| `hospitality-yield-analysis` | `--company-id --start-date --end-date` | |

### Food & Beverage (8 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-add-outlet` | `--company-id --name --outlet-type` | `--operating-hours` |
| `hospitality-list-outlets` | | `--company-id --outlet-type --limit --offset` |
| `hospitality-add-room-service-order` | `--reservation-id --outlet-id --items-json --total-amount --company-id` | |
| `hospitality-list-room-service-orders` | | `--reservation-id --outlet-id --order-status --company-id --limit --offset` |
| `hospitality-complete-room-service-order` | `--order-id` | |
| `hospitality-add-minibar-consumption` | `--reservation-id --item-name --quantity --unit-price --consumption-date --company-id` | |
| `hospitality-list-minibar-consumptions` | | `--reservation-id --company-id --limit --offset` |
| `hospitality-fnb-revenue-report` | `--company-id` | `--start-date --end-date` |

### Reports (9 actions)
| Action | Required Flags | Optional Flags |
|--------|---------------|----------------|
| `hospitality-occupancy-report` | `--company-id` | `--start-date --end-date` |
| `hospitality-revenue-report` | `--company-id` | `--start-date --end-date` |
| `hospitality-housekeeping-report` | `--company-id` | `--start-date --end-date` |
| `hospitality-guest-satisfaction-report` | `--company-id` | `--start-date --end-date` |
| `hospitality-daily-operations-report` | `--company-id` | `--report-date` |
| `hospitality-department-performance` | `--company-id` | `--start-date --end-date` |
| `status` | | |

### Quick Command Reference
| User Says | Action |
|-----------|--------|
| "Add a room type" | `hospitality-add-room-type` |
| "Add a room" | `hospitality-add-room` |
| "Register a guest" | `hospitality-add-guest` |
| "Make a reservation" | `hospitality-add-reservation` |
| "Confirm the booking" | `hospitality-confirm-reservation` |
| "Check the guest in" | `hospitality-check-in` |
| "Add a minibar charge" | `hospitality-add-minibar-consumption` |
| "Order room service" | `hospitality-add-room-service-order` |
| "Check the guest out" | `hospitality-check-out` |
| "Schedule housekeeping" | `hospitality-add-housekeeping-task` |
| "Show occupancy" | `hospitality-occupancy-report` |
| "Revenue per room" | `hospitality-revpar-report` |

## Technical Details (Tier 3)

**Tables owned (22):** hospitalityclaw_room_type, hospitalityclaw_room, hospitalityclaw_amenity, hospitalityclaw_room_amenity, hospitalityclaw_reservation, hospitalityclaw_rate_plan, hospitalityclaw_group_block, hospitalityclaw_guest_request, hospitalityclaw_folio_charge, hospitalityclaw_housekeeping_task, hospitalityclaw_inspection, hospitalityclaw_guest_ext (FKs to core customer), hospitalityclaw_guest_preference, hospitalityclaw_rate_adjustment, hospitalityclaw_outlet, hospitalityclaw_room_service_order, hospitalityclaw_minibar_consumption

**Script:** `scripts/db_query.py` routes to 8 domain modules: rooms.py, reservations.py, front_desk.py, housekeeping.py, guests.py, revenue.py, fnb.py, reports.py

**Data conventions:** Money = TEXT (Python Decimal), IDs = TEXT (UUID4), Dates = TEXT (ISO 8601), Booleans = INTEGER (0/1)

**Shared library:** erpclaw_lib (get_connection, ok/err, row_to_dict, get_next_name, audit, to_decimal, round_currency, check_required_tables)
