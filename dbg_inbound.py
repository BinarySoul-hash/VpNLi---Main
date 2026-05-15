#!/usr/bin/env python3
"""Debug inbound issue."""
import os
import sys

import pexpect

SSH_HOST = os.getenv("DBG_SSH_HOST")
SSH_USER = os.getenv("DBG_SSH_USER", "root")
SSH_PASSWORD = os.getenv("DBG_SSH_PASSWORD")
XUI_BASE = os.getenv("DBG_XUI_BASE_URL")
XUI_USERNAME = os.getenv("DBG_XUI_USERNAME")
XUI_PASSWORD = os.getenv("DBG_XUI_PASSWORD")

required = {
    "DBG_SSH_HOST": SSH_HOST,
    "DBG_SSH_PASSWORD": SSH_PASSWORD,
    "DBG_XUI_BASE_URL": XUI_BASE,
    "DBG_XUI_USERNAME": XUI_USERNAME,
    "DBG_XUI_PASSWORD": XUI_PASSWORD,
}
missing = [name for name, value in required.items() if not value]
if missing:
    print(f"Missing required env vars: {', '.join(missing)}")
    sys.exit(1)

child = pexpect.spawn(
    f"ssh -o StrictHostKeyChecking=no {SSH_USER}@{SSH_HOST}",
    timeout=120, encoding="utf-8"
)

idx = child.expect(["[Pp]assword:", "yes/no", pexpect.EOF, pexpect.TIMEOUT])
if idx == 1:
    child.sendline("yes")
    child.expect("[Pp]assword:")
    child.sendline(SSH_PASSWORD)
elif idx == 0:
    child.sendline(SSH_PASSWORD)
else:
    sys.exit(1)

child.expect(r"root@.*[#\$] ")

cmds = [
    "systemctl status x-ui --no-pager | head -5",
    f"curl -sk -X POST {XUI_BASE}/login -H 'Content-Type: application/json' -d '{{\"username\":\"{XUI_USERNAME}\",\"password\":\"{XUI_PASSWORD}\"}}' -c /tmp/xui_dbg.txt",
    f"curl -sk {XUI_BASE}/panel/api/inbounds/list -b /tmp/xui_dbg.txt | python3 -c \"import json,sys; d=json.load(sys.stdin); ibs=d.get('obj',[]); print(f'inbounds: {{len(ibs)}}'); [print(f'  id={{i[\"id\"]}} port={{i[\"port\"]}} proto={{i[\"protocol\"]}} settings_len={{len(i.get(\"settings\",\"\"))}}') for i in ibs]\"",
    "python3 -c \"import json; c=json.load(open('/usr/local/x-ui/bin/config.json')); print('config valid, inbounds:', len(c.get('inbounds',[])))\"",
]

for cmd in cmds:
    child.sendline(cmd)
    child.expect(r"root@.*[#\$] ")
    out = child.before.strip()
    print(out.decode() if isinstance(out, bytes) else out)

child.sendline("exit")
child.close()
