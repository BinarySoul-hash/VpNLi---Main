#!/usr/bin/env python3
"""
XUI onlines diagnostic tool.

Usage examples:
    python3 tools/xui_onlines_debug.py --email user12_sub3_1
    python3 tools/xui_onlines_debug.py --sub-id 3

This script logs into 3X-UI using the same settings as the bot, fetches
the raw /panel/api/inbounds/onlines response, lists inbound clients and
verifies mapping between onlines and clients. Use it to debug why a
connected client isn't reported as online.
"""
import os
import sys

# Ensure project root is on sys.path so local imports (database, services) work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

import argparse
import asyncio
import json

from services.xui import xui
from config import XUI_INBOUND_ID


async def run(args) -> int:
    print("Logging in to XUI...")
    login_ok = await xui.login()
    print("Login:", login_ok)
    if not login_ok:
        print("Login failed — please check XUI credentials and network.")
        try:
            await xui.close()
        except Exception:
            pass
        return 2

    print("\nFetching raw /panel/api/inbounds/onlines response...")
    raw = await xui._request("POST", "/panel/api/inbounds/onlines")
    print(json.dumps(raw, ensure_ascii=False, indent=2))

    online_obj = (raw.get("obj") if raw and raw.get("success") else raw) or []
    if isinstance(online_obj, list):
        print(f"\nOnline items count: {len(online_obj)}")
        for i, item in enumerate(online_obj[:200]):
            print(f"[{i}] ({type(item).__name__}) {item}")
    else:
        print("Unexpected onlines object:", type(online_obj), online_obj)

    inbound_id = args.inbound or XUI_INBOUND_ID
    print(f"\nInspecting inbound {inbound_id} clients...")
    inbound = await xui.get_inbound(inbound_id)
    if not inbound:
        print("Inbound not found or failed to fetch.")
    else:
        settings_raw = inbound.get("settings") or "{}"
        try:
            settings = json.loads(settings_raw)
        except Exception as e:
            print("Failed to parse inbound.settings:", e)
            settings = {}
        clients = settings.get("clients", [])
        print(f"Found {len(clients)} clients in inbound {inbound_id}.")
        for c in clients:
            print("-", c.get("id"), c.get("email"), "limitIp=", c.get("limitIp"), "enable=", c.get("enable"))

    if args.sub_id:
        print(f"\nQuerying DB active vpn_clients for subscription {args.sub_id}...")
        try:
            import database as db
            db_clients = await db.get_active_vpn_clients_for_subscription(int(args.sub_id))
            print(f"DB returned {len(db_clients)} active clients:")
            for c in db_clients:
                print("-", c.get("id"), c.get("xui_client_id"), c.get("email"), c.get("created_at"))
        except Exception as e:
            print("DB query failed:", e)

    if args.email:
        email = args.email
        print(f"\nChecking client stats for email: {email}")
        # Match in raw onlines
        matches = 0
        for item in online_obj:
            if isinstance(item, str) and item == email:
                matches += 1
            elif isinstance(item, dict) and item.get("email") == email:
                matches += 1
        print("Matches in /onlines list:", matches)

        # Use helper endpoints
        try:
            ips = await xui.get_online_ips_count(email)
            print("get_online_ips_count:", ips)
        except Exception as e:
            print("get_online_ips_count failed:", e)

        try:
            stats = await xui.get_client_stats(email)
            print("get_client_stats:\n", json.dumps(stats, ensure_ascii=False, indent=2))
        except Exception as e:
            print("get_client_stats failed:", e)

        try:
            ips = await xui.get_client_ips(email)
            print("get_client_ips:\n", json.dumps(ips, ensure_ascii=False, indent=2))
        except Exception as e:
            print("get_client_ips failed:", e)

    print("\nDone.")
    try:
        await xui.close()
    except Exception:
        pass
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="XUI onlines diagnostic tool")
    p.add_argument("--email", help="Client email to check (exact match)")
    p.add_argument("--sub-id", type=int, help="Subscription ID to fetch DB clients")
    p.add_argument("--inbound", type=int, help="Inbound ID to inspect (defaults to config XUI_INBOUND_ID)")
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()
    try:
        code = asyncio.run(run(args))
    except Exception as e:
        print("Unhandled error:", e)
        code = 3
    sys.exit(code)
