#!/usr/bin/env python3
"""
Sync XUI inbounds with DB subscriptions.

For each inbound:
- Deletes clients that have NO matching subscription in DB
- Adds clients from DB that are missing in the inbound

Usage:
    python3 tools/xui_sync_clients.py
    python3 tools/xui_sync_clients.py --dry-run
    python3 tools/xui_sync_clients.py --verbose
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import time
import uuid
import argparse

from services.xui import xui
from config import ADMIN_IDS, XUI_INBOUND_ID
from tools.dates import safe_parse_expires_at, remaining_days


def parse_args():
    p = argparse.ArgumentParser(description="Sync XUI clients with DB")
    p.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    p.add_argument("--verbose", action="store_true", help="Print full details")
    return p.parse_args()


async def run(args) -> int:
    import database as db

    print("=" * 60)
    print("XUI Client Sync Script")
    print("=" * 60)

    await db.init_db()

    print("\n[1] Logging in to XUI...")
    if not await xui.login():
        print("    Login FAILED")
        return 1
    print("    Login OK")

    print("\n[2] Fetching subscriptions from DB...")
    all_subs = await db.get_all_subscriptions_map()
    print(f"    Total subscriptions with email: {len(all_subs)}")

    admin_emails = set()
    for email, sub in all_subs.items():
        if sub.get("user_id") in ADMIN_IDS:
            admin_emails.add(email)
    print(f"    Admin emails (will skip): {len(admin_emails)}")

    if args.verbose:
        for email in list(admin_emails)[:3]:
            print(f"    - admin: {email}")

    inbound_ids = xui.get_all_inbound_ids()
    print(f"\n[3] Processing {len(inbound_ids)} inbounds: {inbound_ids}")

    total_deleted = 0
    total_added = 0
    errors = 0

    for inbound_id in inbound_ids:
        print(f"\n    --- Inbound {inbound_id} ---")

        inbound = await xui.get_inbound(inbound_id)
        if not inbound:
            print(f"    ERROR: Inbound {inbound_id} not found")
            errors += 1
            continue

        settings = xui._parse_settings(inbound)
        current_clients = settings.get("clients", [])
        current_emails = {c.get("email") for c in current_clients if c.get("email")}
        db_emails = set(all_subs.keys()) - admin_emails

        to_delete = current_emails - db_emails
        to_add = db_emails - current_emails

        print(f"    Current clients: {len(current_clients)}")
        print(f"    DB subscriptions (non-admin): {len(db_emails)}")
        print(f"    To delete (not in DB): {len(to_delete)}")
        print(f"    To add (missing from inbound): {len(to_add)}")

        if to_delete:
            print(f"\n    Deleting {len(to_delete)} extra clients...")
            for email in sorted(to_delete):
                print(f"    - {email}")
                if args.dry_run:
                    continue
                client_obj = None
                for c in current_clients:
                    if c.get("email") == email:
                        client_obj = c
                        break
                cid = client_obj.get("id", "") if client_obj else ""
                result = await xui.delete_client(cid, email=email)
                if result:
                    total_deleted += 1
                else:
                    errors += 1
                    print(f"      WARN: delete returned {result}")

        if to_add:
            print(f"\n    Adding {len(to_add)} missing clients...")
            flow = xui._determine_flow(inbound)
            for email in sorted(to_add):
                sub = all_subs[email]
                expires_dt = safe_parse_expires_at(sub.get("expires_at"))
                if expires_dt:
                    days = max(1, remaining_days(expires_dt))
                else:
                    days = 30

                devices = max(1, int(sub.get("devices") or 1))
                expire_ms = int((time.time() + days * 86400) * 1000)

                client_id = str(uuid.uuid4())
                subscription_id = sub.get("subscription_id") or uuid.uuid4().hex

                payload = xui._build_client_payload(
                    client_id=client_id,
                    email=email,
                    subscription_id=subscription_id,
                    limit_ip=devices,
                    expire_ms=expire_ms,
                    total_bytes=0,
                    flow=flow,
                )

                print(f"    + {email} (days={days}, devices={devices})")
                if args.dry_run:
                    continue

                ok = await xui._add_client_to_single_inbound(inbound_id, payload, email)
                if ok:
                    total_added += 1
                else:
                    errors += 1
                    print(f"      ERROR: failed to add {email}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Deleted:  {total_deleted}")
    print(f"  Added:    {total_added}")
    print(f"  Errors:   {errors}")
    print("=" * 60)

    await xui.close()
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    args = parse_args()
    try:
        code = asyncio.run(run(args))
    except Exception as e:
        print(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()
        code = 3
    sys.exit(code)
