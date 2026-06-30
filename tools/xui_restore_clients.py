#!/usr/bin/env python3
"""
Standalone script to restore XUI clients: enable disabled ones and restore expiryTime from DB.

Usage:
    python3 tools/xui_restore_clients.py
    python3 tools/xui_restore_clients.py --dry-run
    python3 tools/xui_restore_clients.py --verbose

This script:
1. Logs into 3X-UI
2. Reads all active subscriptions from the local DB
3. For each client in XUI whose email matches an active subscription:
   - Enables the client if disabled
   - Restores expiryTime if it was lost (set to 0)
4. Uses BOTH approaches: clients API (per-client) AND inbound settings update
5. Verifies changes after each write
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import json
import argparse

from services.xui import xui
from config import XUI_INBOUND_ID
from tools.dates import safe_parse_expires_at, expires_dt_to_ms


def parse_args():
    p = argparse.ArgumentParser(description="Restore XUI clients from DB")
    p.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    p.add_argument("--verbose", action="store_true", help="Print full API responses")
    return p.parse_args()


async def run(args) -> int:
    import database as db

    print("=" * 60)
    print("XUI Client Restore Script")
    print("=" * 60)

    # 1. Init DB
    print("\n[1] Initializing database...")
    await db.init_db()

    # 2. Login to XUI
    print("\n[2] Logging in to XUI...")
    login_ok = await xui.login()
    print(f"    Login: {'OK' if login_ok else 'FAILED'}")
    if not login_ok:
        print("    ERROR: Cannot login. Check XUI_HOST, XUI_USERNAME, XUI_PASSWORD.")
        try:
            await xui.close()
        except Exception:
            pass
        return 1

    # 3. Get ALL subscriptions from DB (active + expired)
    print("\n[3] Fetching all subscriptions from DB...")
    active_subs = await db.get_all_subscriptions_map()
    print(f"    Found {len(active_subs)} subscriptions with email (all states)")

    if args.verbose:
        for email, sub in list(active_subs.items())[:5]:
            print(f"    - {email}: expires_at={sub.get('expires_at')}, is_active={sub.get('is_active')}")
        if len(active_subs) > 5:
            print(f"    ... and {len(active_subs) - 5} more")

    # 4. Process each inbound
    print("\n[4] Processing inbounds...")
    inbound_ids = xui.get_all_inbound_ids()
    print(f"    Target inbound IDs: {inbound_ids}")

    total_enabled = 0
    total_expiry_restored = 0
    total_already_ok = 0
    total_not_in_db = 0
    total_deleted = 0
    errors = 0
    verify_failures = 0

    for inbound_id in inbound_ids:
        print(f"\n    --- Inbound {inbound_id} ---")

        inbound = await xui.get_inbound(inbound_id)
        if not inbound:
            print(f"    ERROR: Inbound {inbound_id} not found or failed to fetch")
            errors += 1
            continue

        settings = xui._parse_settings(inbound)
        clients = settings.get("clients", [])
        print(f"    Clients in inbound: {len(clients)}")

        # Collect clients that need fixing
        clients_to_fix = []
        disabled_no_db = []
        for client in clients:
            email = client.get("email")
            if not email:
                continue
            sub = active_subs.get(email)

            need_enable = not client.get("enable")
            current_expiry = client.get("expiryTime", 0)
            need_expiry = current_expiry == 0

            if sub:
                if not need_enable and not need_expiry:
                    total_already_ok += 1
                    continue
                clients_to_fix.append({
                    "client": client,
                    "sub": sub,
                    "need_enable": need_enable,
                    "need_expiry": need_expiry,
                    "current_expiry": current_expiry,
                })
            else:
                if need_enable:
                    disabled_no_db.append(client)
                total_not_in_db += 1

        print(f"    Clients needing fix (with DB): {len(clients_to_fix)}")
        print(f"    Disabled without DB: {len(disabled_no_db)}")
        if not clients_to_fix and not disabled_no_db:
            print(f"    All clients OK, skipping inbound update")
            continue

        # Apply fixes via clients API (per-client update with FULL payload)
        print(f"\n    Applying fixes via clients API...")
        for item in clients_to_fix:
            client = item["client"]
            sub = item["sub"]
            email = client["email"]

            # Build FULL client payload from existing fields
            update_payload = {}
            for key in ["id", "alterId", "security", "email", "limitIp", "totalGB",
                         "expiryTime", "enable", "tgId", "subId", "reset", "flow",
                         "comment", "group"]:
                if key in client:
                    update_payload[key] = client[key]

            changes = []
            if item["need_enable"]:
                update_payload["enable"] = True
                changes.append("enable=True")
            if item["need_expiry"]:
                expires_dt = safe_parse_expires_at(sub.get("expires_at"))
                if expires_dt:
                    new_expiry_ms = expires_dt_to_ms(expires_dt)
                    update_payload["expiryTime"] = new_expiry_ms
                    changes.append(f"expiryTime={new_expiry_ms}")
                else:
                    print(f"    WARN: Cannot parse expires_at '{sub.get('expires_at')}' for {email}")

            if not changes:
                continue

            print(f"    Updating {email}: {', '.join(changes)}")

            if args.dry_run:
                print(f"    [DRY RUN] Would send: {json.dumps(update_payload, ensure_ascii=False)}")
                total_enabled += 1 if item["need_enable"] else 0
                total_expiry_restored += 1 if item["need_expiry"] else 0
                continue

            result = await xui._update_client_via_clients_api(email, update_payload)
            print(f"    API response: success={result}")
            if args.verbose:
                print(f"    Payload sent: {json.dumps(update_payload, ensure_ascii=False)}")

            if result is None:
                print(f"    WARN: API returned None (possible auth/connection issue)")
                errors += 1
                continue

            if result:
                if item["need_enable"]:
                    total_enabled += 1
                if item["need_expiry"]:
                    total_expiry_restored += 1
            else:
                errors += 1
                print(f"    ERROR: API returned success=false")

        # Delete ALL clients without DB subscription
        if disabled_no_db:
            print(f"\n    Deleting {len(disabled_no_db)} clients without DB subscription...")
            for client in disabled_no_db:
                email = client["email"]
                client_id = client.get("id", "")

                print(f"    Deleting (no DB): {email}")
                if args.dry_run:
                    print(f"    [DRY RUN] Would delete {email}")
                    total_deleted += 1
                    continue

                result = await xui.delete_client(client_id, email=email)
                print(f"    Delete result: {result}")
                if result:
                    total_deleted += 1
                else:
                    errors += 1

        # ALSO apply via inbound update (belt-and-suspenders)
        if not args.dry_run:
            print(f"\n    Also updating via inbound settings (backup approach)...")
            changed = False
            for item in clients_to_fix:
                client = item["client"]
                sub = item["sub"]
                if item["need_enable"]:
                    client["enable"] = True
                    changed = True
                if item["need_expiry"]:
                    expires_dt = safe_parse_expires_at(sub.get("expires_at"))
                    if expires_dt:
                        client["expiryTime"] = expires_dt_to_ms(expires_dt)
                        changed = True

            if changed:
                ok = await xui._update_inbound(inbound, settings=settings)
                print(f"    Inbound update result: {ok}")
                if not ok:
                    errors += 1

        # 5. Verify changes
        print(f"\n    Verifying changes...")
        inbound_after = await xui.get_inbound(inbound_id)
        if not inbound_after:
            print(f"    ERROR: Cannot re-fetch inbound {inbound_id} for verification")
            errors += 1
            continue

        settings_after = xui._parse_settings(inbound_after)
        clients_after = settings_after.get("clients", [])

        for item in clients_to_fix:
            email = item["client"]["email"]
            client_after = None
            for c in clients_after:
                if c.get("email") == email:
                    client_after = c
                    break

            if not client_after:
                print(f"    FAIL: Client {email} not found after update!")
                verify_failures += 1
                continue

            issues = []
            if item["need_enable"] and not client_after.get("enable"):
                issues.append("still disabled")
            if item["need_expiry"] and client_after.get("expiryTime", 0) == 0:
                issues.append("expiryTime still 0")

            if issues:
                print(f"    FAIL: {email} - {', '.join(issues)}")
                verify_failures += 1
            else:
                print(f"    OK: {email} - enable={client_after.get('enable')}, expiryTime={client_after.get('expiryTime', 0)}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Enabled:           {total_enabled}")
    print(f"  Expiry restored:   {total_expiry_restored}")
    print(f"  Deleted (no DB):   {total_deleted}")
    print(f"  Already OK:        {total_already_ok}")
    print(f"  Errors:            {errors}")
    print(f"  Verify failures:   {verify_failures}")
    print("=" * 60)

    try:
        await xui.close()
    except Exception:
        pass
    return 0 if errors == 0 and verify_failures == 0 else 1


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
