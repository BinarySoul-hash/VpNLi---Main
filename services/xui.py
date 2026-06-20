"""
Async 3X-UI client helpers used by the bot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp

from config import (
    INBOUND_REMARK,
    VLESS_KEY_NAME,
    XUI_API_TOKEN,
    XUI_HOST,
    XUI_CLIENT_IPS_TIMEOUT_SECONDS,
    XUI_INBOUND_ID,
    XUI_INBOUND_IDS,
    XUI_INBOUNDS_TIMEOUT_SECONDS,
    XUI_REQUEST_RETRIES,
    XUI_PASSWORD,
    XUI_PATH_PREFIX,
    XUI_PUBLIC_BASE_URL,
    XUI_SUB_PORT,
    XUI_SUBSCRIPTION_URL_TEMPLATE,
    XUI_USERNAME,
    XUI_VERIFY_SSL,
    XUI_ONLINES_RETRIES,
    XUI_ONLINES_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class XUIClient:
    def __init__(self) -> None:
        self.host = XUI_HOST.rstrip("/")
        self.path_prefix = XUI_PATH_PREFIX.rstrip("/")
        self.username = XUI_USERNAME
        self.password = XUI_PASSWORD
        self.api_token = XUI_API_TOKEN
        self.verify_ssl = XUI_VERIFY_SSL
        self._session: Optional[aiohttp.ClientSession] = None
        self._auth_cookie: Optional[str] = None
        self._csrf_token: Optional[str] = None
        self._auth_cookie_expires: float = 0  # Timestamp when auth_cookie expires (30 min TTL)
        self._last_lazy_login_attempt_at: float = 0.0
        self._lazy_login_cooldown_seconds: int = 30
        self._last_inbounds_request_failed: bool = False

    def build_client_email(self) -> str:
        return f"vpnli-{uuid.uuid4().hex[:12]}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Increased timeouts for remote connections and network resilience
            timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)
            # Use TCPConnector with DNS timeout to handle DNS issues
            connector = aiohttp.TCPConnector(
                ttl_dns_cache=300,  # Cache DNS for 5 minutes
                limit_per_host=5,   # Connection pooling
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
        return self._session

    async def _reset_session(self) -> None:
        self._auth_cookie = None
        self._csrf_token = None
        self._auth_cookie_expires = 0
        self._last_lazy_login_attempt_at = 0.0
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _is_unsafe_method(self, method: str) -> bool:
        return method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}

    def _headers_with_auth(self, method: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
            return headers
        # Check if auth_cookie is still valid (30 min TTL)
        if self._auth_cookie and time.time() < self._auth_cookie_expires:
            headers["Cookie"] = f"3x-ui={self._auth_cookie}"
        if self._is_unsafe_method(method) and self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token
        return headers

    async def _parse_json_response(self, resp: aiohttp.ClientResponse) -> Optional[dict]:
        text = await resp.text()
        if not text:
            return {}
        if text.lstrip().startswith("<!DOCTYPE") or text.lstrip().startswith("<html"):
            logger.warning("3X-UI returned HTML instead of JSON for %s", resp.url)
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("3X-UI returned invalid JSON for %s: %s", resp.url, text[:300])
            return None

    async def _ensure_logged_in(self) -> bool:
        """Ensure we have a valid auth cookie with cooldown-based lazy login retries."""
        if self.api_token:
            return True
        # If cookie is valid, we're done
        if self._auth_cookie and time.time() < self._auth_cookie_expires:
            return True

        now = time.time()
        elapsed = now - self._last_lazy_login_attempt_at
        if elapsed < self._lazy_login_cooldown_seconds:
            logger.warning(
                "3X-UI: lazy login cooling down (next retry in %.1fs)",
                self._lazy_login_cooldown_seconds - elapsed,
            )
            return False

        self._last_lazy_login_attempt_at = now
        return await self.login()

    async def _ensure_csrf_token(self) -> Optional[str]:
        if self.api_token:
            return None
        if self._csrf_token:
            return self._csrf_token

        session = await self._get_session()
        url = f"{self.host}{self.path_prefix}/csrf-token"
        try:
            async with session.get(url, ssl=self.verify_ssl) as resp:
                data = await self._parse_json_response(resp)
                if resp.status == 200 and data and data.get("success"):
                    token = data.get("obj")
                    if token:
                        self._csrf_token = str(token)
                        return self._csrf_token
        except Exception as exc:
            logger.error("3X-UI CSRF token error: %s", exc)
        return None

    async def login(self) -> bool:
        """Perform login. Should be called once at startup."""
        if self.api_token:
            return True
        session = await self._get_session()
        url = f"{self.host}{self.path_prefix}/login"
        payload = {"username": self.username, "password": self.password}
        csrf_token = await self._ensure_csrf_token()

        for send_json in (True, False):
            try:
                request_kwargs = {"ssl": self.verify_ssl}
                if send_json:
                    request_kwargs["json"] = payload
                    headers = {"Content-Type": "application/json"}
                    if csrf_token:
                        headers["X-CSRF-Token"] = csrf_token
                    request_kwargs["headers"] = headers
                else:
                    request_kwargs["data"] = payload
                    if csrf_token:
                        request_kwargs["headers"] = {"X-CSRF-Token": csrf_token}

                async with session.post(url, **request_kwargs) as resp:
                    data = await self._parse_json_response(resp)
                    if resp.status == 200 and data and data.get("success"):
                        cookie = resp.cookies.get("3x-ui")
                        if cookie:
                            self._auth_cookie = cookie.value
                        else:
                            for morsel in session.cookie_jar:
                                if morsel.key == "3x-ui":
                                    self._auth_cookie = morsel.value
                                    break
                        # Set TTL: auth_cookie valid for 30 minutes
                        self._auth_cookie_expires = time.time() + 30 * 60
                        self._last_lazy_login_attempt_at = 0.0
                        logger.info("3X-UI login succeeded (valid for 30 min)")
                        return True
                    elif resp.status in {401, 403}:
                        logger.error("3X-UI login: invalid credentials (status %d)", resp.status)
                        return False
            except Exception as exc:
                logger.error("3X-UI login error: %s", exc)

        logger.error("3X-UI login failed")
        return False

    async def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        url = f"{self.host}{self.path_prefix}{path}"
        extra_headers = dict(kwargs.pop("headers", {}))
        request_kwargs = dict(kwargs)
        suppress_timeout_error = bool(request_kwargs.pop("_suppress_timeout_error", False))
        retries = max(1, int(request_kwargs.pop("_retries", 1)))
        retry_delay = float(request_kwargs.pop("_retry_delay", 1.0))

        for attempt in range(1, retries + 1):
            # Ensure we're logged in for each attempt (auth may expire between retries).
            if not await self._ensure_logged_in():
                logger.error("3X-UI request %s %s: not authenticated", method, path)
                return None
            if self._is_unsafe_method(method):
                await self._ensure_csrf_token()

            session = await self._get_session()
            headers = self._headers_with_auth(method)
            headers.update(extra_headers)

            try:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    ssl=self.verify_ssl,
                    **request_kwargs,
                ) as resp:
                    if resp.status == 403 and not self.api_token:
                        self._csrf_token = None
                        if attempt < retries:
                            await asyncio.sleep(min(retry_delay * attempt, 5))
                            continue

                    if resp.status in {401, 403}:
                        logger.error(
                            "3X-UI auth failed for %s %s with status %s, relogin",
                            method,
                            path,
                            resp.status,
                        )
                        await self._reset_session()
                        if attempt < retries:
                            await asyncio.sleep(min(retry_delay * attempt, 5))
                            continue
                        return None

                    if resp.status == 404:
                        logger.error("3X-UI not found for %s %s", method, path)
                        return None

                    data = await self._parse_json_response(resp)
                    if data is None:
                        logger.error(
                            "3X-UI returned invalid response for %s %s with status %s",
                            method,
                            path,
                            resp.status,
                        )
                        if attempt < retries:
                            await asyncio.sleep(min(retry_delay * attempt, 5))
                            continue
                        return None

                    return data
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientSSLError) as exc:
                if attempt < retries:
                    await asyncio.sleep(min(retry_delay * attempt, 5))
                    continue
                if suppress_timeout_error and isinstance(exc, asyncio.TimeoutError):
                    logger.debug("3X-UI %s %s timeout: %s", method, path, exc.__class__.__name__)
                else:
                    logger.error("3X-UI %s %s error: %s", method, path, exc.__class__.__name__)
                return None
            except Exception as exc:
                if attempt < retries:
                    await asyncio.sleep(min(retry_delay * attempt, 5))
                    continue
                logger.error("3X-UI request error %s %s: %s", method, path, exc)
                return None

        return None

    def _parse_settings(self, inbound: dict) -> dict:
        raw = inbound.get("settings") or "{}"
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            raw = "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("3X-UI inbound %s has invalid settings JSON", inbound.get("id"))
            return {}

    def _build_update_payload(self, inbound: dict, settings: dict | None = None) -> dict:
        return {
            "id": inbound.get("id"),
            "up": inbound.get("up", 0),
            "down": inbound.get("down", 0),
            "total": inbound.get("total", 0),
            "remark": inbound.get("remark", ""),
            "enable": inbound.get("enable", True),
            "expiryTime": inbound.get("expiryTime", 0),
            "listen": inbound.get("listen", ""),
            "port": inbound.get("port", 0),
            "protocol": inbound.get("protocol", "vless"),
            "settings": json.dumps(settings if settings is not None else self._parse_settings(inbound)),
            "streamSettings": inbound.get("streamSettings", "{}"),
            "sniffing": inbound.get("sniffing", "{}"),
        }

    async def _update_inbound(self, inbound: dict, settings: dict | None = None) -> bool:
        payload = self._build_update_payload(inbound, settings=settings)
        result = await self._request(
            "POST",
            f"/panel/api/inbounds/update/{inbound['id']}",
            json=payload,
        )
        return bool(result and result.get("success"))

    def _find_client(
        self,
        settings: dict,
        *,
        client_id: str | None = None,
        email: str | None = None,
    ) -> Optional[dict]:
        clients = settings.get("clients") or []
        for client in clients:
            if client_id and client.get("id") == client_id:
                return client
            if email and client.get("email") == email:
                return client
        return None

    async def _find_client_in_any_inbound(
        self,
        *,
        client_id: str | None = None,
        email: str | None = None,
    ) -> tuple[Optional[dict], Optional[dict]]:
        for inbound in await self.get_inbounds():
            settings = self._parse_settings(inbound)
            client = self._find_client(settings, client_id=client_id, email=email)
            if client:
                return inbound, client
        return None, None

    async def get_inbounds(self) -> list[dict]:
        total_timeout = max(20, int(XUI_INBOUNDS_TIMEOUT_SECONDS))
        inbounds_timeout = aiohttp.ClientTimeout(
            total=total_timeout,
            connect=min(15, max(5, total_timeout // 4)),
            sock_read=max(15, total_timeout - 10),
        )
        result = await self._request(
            "GET",
            "/panel/api/inbounds/list",
            timeout=inbounds_timeout,
            _suppress_timeout_error=True,
            _retries=max(1, int(XUI_REQUEST_RETRIES)),
            _retry_delay=1.2,
        )
        if not result or not result.get("success"):
            self._last_inbounds_request_failed = True
            logger.warning("3X-UI get_inbounds unavailable: %s", result)
            return []
        self._last_inbounds_request_failed = False
        return result.get("obj") or []

    async def get_inbound(self, inbound_id: int | None = None) -> Optional[dict]:
        target_id = inbound_id or XUI_INBOUND_ID
        inbounds = await self.get_inbounds()
        if not inbounds and self._last_inbounds_request_failed:
            logger.warning(
                "3X-UI inbound %s lookup skipped: inbounds list temporarily unavailable",
                target_id,
            )
            return None
        for inbound in inbounds:
            if inbound.get("id") == target_id:
                return inbound
        logger.error("3X-UI inbound %s not found", target_id)
        return None

    def _resolve_public_base_url(self) -> str:
        if XUI_PUBLIC_BASE_URL:
            return XUI_PUBLIC_BASE_URL.rstrip("/")
        parsed = urlparse(self.host)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return f"http://{self.host.split('/', 1)[0]}"

    def _resolve_subscription_base_url(self) -> str:
        parsed = urlparse(self.host)
        hostname = parsed.hostname or self.host.split("/")[0].split(":")[0]
        return f"https://{hostname}:{XUI_SUB_PORT}"

    def _build_subscription_url(self, subscription_id: str, email: str) -> str:
        base_url = self._resolve_subscription_base_url()
        if XUI_SUBSCRIPTION_URL_TEMPLATE:
            try:
                return XUI_SUBSCRIPTION_URL_TEMPLATE.format(
                    base_url=base_url,
                    sub_id=subscription_id,
                    email=quote(email, safe=""),
                )
            except Exception as exc:
                logger.warning("Invalid XUI_SUBSCRIPTION_URL_TEMPLATE: %s", exc)
        return f"{base_url}/VpNLi-JS/{subscription_id}"

    def _build_vless_key(self, inbound: dict, client_id: str) -> str:
        try:
            raw_stream = inbound.get("streamSettings", "{}")
            if isinstance(raw_stream, str):
                stream = json.loads(raw_stream)
            elif isinstance(raw_stream, dict):
                stream = raw_stream
            else:
                stream = {}
            network = stream.get("network", "tcp")
            security = stream.get("security", "none")
            host = urlparse(XUI_HOST).hostname or XUI_HOST.split("/")[0].split(":")[0]
            port = inbound.get("port", 443)

            params: list[str] = []
            if security == "reality":
                reality = stream.get("realitySettings", {})
                settings = reality.get("settings", {})
                params.extend(
                    [
                        "security=reality",
                        f"pbk={settings.get('publicKey', '')}",
                        "fp=chrome",
                        f"sni={reality.get('serverNames', [''])[0]}",
                        f"sid={reality.get('shortIds', [''])[0]}",
                        "spx=%2F",
                        "flow=xtls-rprx-vision",
                    ]
                )
            elif security == "tls":
                tls_settings = stream.get("tlsSettings", {})
                params.extend(
                    [
                        "security=tls",
                        f"sni={tls_settings.get('serverName', host)}",
                    ]
                )

            if network == "ws":
                ws_settings = stream.get("wsSettings", {})
                params.extend(["type=ws", f"path={ws_settings.get('path', '/')}"])
            elif network == "grpc":
                grpc_settings = stream.get("grpcSettings", {})
                params.extend(
                    ["type=grpc", f"serviceName={grpc_settings.get('serviceName', '')}"]
                )
            else:
                params.append(f"type={network}")

            label = (VLESS_KEY_NAME or "").strip()
            fragment = ""
            if label and label != INBOUND_REMARK:
                fragment = f"#{quote(label, safe='')}"

            return f"vless://{client_id}@{host}:{port}?{'&'.join(params)}{fragment}"
        except Exception as exc:
            logger.error("Failed to build VLESS key: %s", exc)
            return ""

    def _target_inbound_ids(self, inbound_id: int) -> list[int]:
        configured = [int(item) for item in XUI_INBOUND_IDS if int(item) > 0]
        if int(inbound_id) == int(XUI_INBOUND_ID) and configured:
            return list(dict.fromkeys(configured))
        return [int(inbound_id)]

    def _determine_flow(self, inbound: dict | None) -> str:
        if not inbound:
            return "xtls-rprx-vision"
        raw_stream = inbound.get("streamSettings", "{}")
        if isinstance(raw_stream, str):
            try:
                stream = json.loads(raw_stream)
            except json.JSONDecodeError:
                stream = {}
        elif isinstance(raw_stream, dict):
            stream = raw_stream
        else:
            stream = {}
        security = stream.get("security", "none")
        if security == "reality":
            return "xtls-rprx-vision"
        return ""

    def _build_client_payload(
        self,
        *,
        client_id: str,
        email: str,
        subscription_id: str,
        limit_ip: int,
        expire_ms: int,
        total_bytes: int,
        enabled: bool = True,
        flow: str = "xtls-rprx-vision",
    ) -> dict:
        return {
            "id": client_id,
            "alterId": 0,
            "security": "auto",
            "email": email,
            "limitIp": limit_ip,
            "totalGB": total_bytes,
            "expiryTime": expire_ms,
            "enable": bool(enabled),
            "tgId": 0,
            "subId": subscription_id,
            "reset": 0,
            "flow": flow,
            "comment": "",
            "group": "",
        }

    async def _add_client_via_clients_api(self, inbound_ids: list[int], client: dict) -> Optional[bool]:
        result = await self._request(
            "POST",
            "/panel/api/clients/add",
            json={"client": client, "inboundIds": inbound_ids},
            _retries=max(1, int(XUI_REQUEST_RETRIES)),
        )
        if result is None:
            return None
        return bool(result.get("success"))

    async def _update_client_via_clients_api(self, email: str, client: dict) -> Optional[bool]:
        result = await self._request(
            "POST",
            f"/panel/api/clients/update/{quote(email, safe='')}",
            json=client,
            _retries=max(1, int(XUI_REQUEST_RETRIES)),
        )
        if result is None:
            return None
        return bool(result.get("success"))

    async def _delete_client_via_clients_api(self, email: str) -> Optional[bool]:
        result = await self._request(
            "POST",
            f"/panel/api/clients/del/{quote(email, safe='')}",
            _retries=max(1, int(XUI_REQUEST_RETRIES)),
        )
        if result is None:
            return None
        return bool(result.get("success"))

    async def _append_client_to_inbound(self, inbound_id: int, client: dict) -> Optional[dict]:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return None

        settings = self._parse_settings(inbound)
        settings.setdefault("clients", [])
        if self._find_client(settings, client_id=client["id"]) or self._find_client(settings, email=client["email"]):
            logger.warning("3X-UI client %s already exists in inbound %s", client["email"], inbound_id)
            return None

        legacy_client = dict(client)
        settings["clients"].append(legacy_client)

        if not await self._update_inbound(inbound, settings=settings):
            logger.error("3X-UI failed to add client %s to inbound %s", client["email"], inbound_id)
            return None
        return inbound

    async def _add_client_to_single_inbound(
        self,
        inbound_id: int,
        client: dict,
        email: str,
    ) -> bool:
        result = await self._add_client_via_clients_api([inbound_id], client)
        if result is True:
            return True
        appended = await self._append_client_to_inbound(inbound_id, client)
        if appended:
            return True
        logger.error("3X-UI: failed to add client %s to inbound %s", email, inbound_id)
        return False

    async def _verify_client_in_inbound(
        self,
        inbound_id: int,
        client_id: str,
        email: str,
    ) -> bool:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return False
        settings = self._parse_settings(inbound)
        found = self._find_client(settings, client_id=client_id, email=email)
        return found is not None

    async def add_client(
        self,
        inbound_id: int,
        email: str,
        devices: int,
        expire_days: int,
        traffic_gb: int = 0,
    ) -> Optional[dict]:
        client_id = str(uuid.uuid4())
        subscription_id = uuid.uuid4().hex
        expire_ms = int((time.time() + max(1, expire_days) * 86400) * 1000)
        total_bytes = traffic_gb * 1024**3 if traffic_gb else 0
        limit_ip = max(1, int(devices or 1))
        inbound_ids = self._target_inbound_ids(inbound_id)
        primary_inbound = await self.get_inbound(inbound_ids[0])
        flow = self._determine_flow(primary_inbound)
        client = self._build_client_payload(
            client_id=client_id,
            email=email,
            subscription_id=subscription_id,
            limit_ip=limit_ip,
            expire_ms=expire_ms,
            total_bytes=total_bytes,
            flow=flow,
        )

        added_inbound_ids: list[int] = []

        for target_id in inbound_ids:
            if await self._add_client_to_single_inbound(target_id, client, email):
                added_inbound_ids.append(target_id)
            else:
                logger.warning("3X-UI: skipping inbound %s for client %s", target_id, email)

        if not added_inbound_ids:
            logger.error("3X-UI: client %s could not be added to any inbound", email)
            return None

        verified_ids: list[int] = []
        for target_id in added_inbound_ids:
            if await self._verify_client_in_inbound(target_id, client_id, email):
                verified_ids.append(target_id)
            else:
                logger.warning(
                    "3X-UI: client %s not verified in inbound %s, retrying append",
                    email,
                    target_id,
                )
                if await self._append_client_to_inbound(target_id, client):
                    if await self._verify_client_in_inbound(target_id, client_id, email):
                        verified_ids.append(target_id)
                    else:
                        logger.error(
                            "3X-UI: client %s still not found in inbound %s after retry",
                            email,
                            target_id,
                        )

        if not primary_inbound or primary_inbound.get("id") not in verified_ids:
            primary_inbound = await self.get_inbound(verified_ids[0])

        vless_keys: dict[int, str] = {}
        subscription_urls: dict[int, str] = {}
        for target in verified_ids:
            try:
                inbound_obj = await self.get_inbound(target)
                vless_keys[int(target)] = self._build_vless_key(inbound_obj, client_id) if inbound_obj else ""
            except Exception:
                vless_keys[int(target)] = ""
            try:
                subscription_urls[int(target)] = self._build_subscription_url(subscription_id, email)
            except Exception:
                subscription_urls[int(target)] = ""

        return {
            "client_id": client_id,
            "subscription_id": subscription_id,
            "subscription_url": subscription_urls.get(verified_ids[0]) or "",
            "vless_key": vless_keys.get(verified_ids[0]) or "",
            "vless_keys": vless_keys,
            "subscription_urls": subscription_urls,
            "email": email,
        }

    async def get_client_vless_keys(
        self,
        *,
        client_id: str | None = None,
        email: str | None = None,
    ) -> dict[int, str]:
        keys: dict[int, str] = {}
        for inbound in await self.get_inbounds():
            inbound_id = inbound.get("id")
            if inbound_id is None:
                continue
            settings = self._parse_settings(inbound)
            client = self._find_client(settings, client_id=client_id, email=email)
            if not client:
                continue
            keys[int(inbound_id)] = self._build_vless_key(inbound, client.get("id") or "")
        return keys

    async def rename_client_email(self, inbound_id: int, client_id: str, new_email: str) -> bool:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return False

        settings = self._parse_settings(inbound)
        client = self._find_client(settings, client_id=client_id)
        if not client:
            logger.warning("rename_client_email: client %s not found", client_id)
            return False

        client["email"] = new_email
        return await self._update_inbound(inbound, settings=settings)

    async def set_inbound_remark(self, inbound_id: int, remark: str) -> bool:
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return False
        inbound = dict(inbound)
        inbound["remark"] = remark
        return await self._update_inbound(inbound, settings=self._parse_settings(inbound))

    async def update_client(
        self,
        inbound_id: int,
        *,
        client_id: str | None = None,
        email: str | None = None,
        new_email: str | None = None,
        limit_ip: int | None = None,
        expires_at: str | None = None,
        enabled: bool | None = None,
        total_gb: int | None = None,
    ) -> bool:
        inbound = await self.get_inbound(inbound_id)
        settings = self._parse_settings(inbound) if inbound else None
        client = self._find_client(settings, client_id=client_id, email=email) if settings else None

        if not client:
            fallback_inbound, fallback_client = await self._find_client_in_any_inbound(
                client_id=client_id,
                email=email,
            )
            if fallback_client:
                logger.info(
                    "update_client: found client in inbound %s via fallback search",
                    fallback_inbound.get("id") if fallback_inbound else inbound_id,
                )
                inbound = fallback_inbound
                settings = self._parse_settings(inbound)
                client = fallback_client

        if not client:
            if email:
                api_client: dict = {"email": new_email or email}
                if limit_ip is not None:
                    api_client["limitIp"] = max(1, int(limit_ip))
                if expires_at is not None:
                    api_client["expiryTime"] = int(datetime.fromisoformat(expires_at).timestamp() * 1000)
                if enabled is not None:
                    api_client["enable"] = bool(enabled)
                if total_gb is not None:
                    api_client["totalGB"] = total_gb * 1024**3 if total_gb > 0 else 0

                if len(api_client) > 1 or new_email:
                    api_updated = await self._update_client_via_clients_api(email, api_client)
                    if api_updated is not None:
                        return api_updated
                    logger.warning(
                        "update_client: client not found in inbound %s and clients API update failed for email=%s",
                        inbound_id,
                        email,
                    )
                    return False

            logger.warning(
                "update_client: client not found in inbound %s (client_id=%s, email=%s)",
                inbound_id,
                client_id,
                email,
            )
            return False

        target_email = email or client.get("email")
        if target_email:
            api_client: dict = {"email": new_email or target_email}
            if limit_ip is not None:
                api_client["limitIp"] = max(1, int(limit_ip))
            if expires_at is not None:
                api_client["expiryTime"] = int(datetime.fromisoformat(expires_at).timestamp() * 1000)
            if enabled is not None:
                api_client["enable"] = bool(enabled)
            if total_gb is not None:
                api_client["totalGB"] = total_gb * 1024**3 if total_gb > 0 else 0

            if len(api_client) > 1 or new_email:
                await self._update_client_via_clients_api(target_email, api_client)

        changed = False
        if new_email and client.get("email") != new_email:
            client["email"] = new_email
            changed = True

        if limit_ip is not None:
            value = max(1, int(limit_ip))
            if client.get("limitIp") != value:
                client["limitIp"] = value
                changed = True

        if expires_at is not None:
            expires_ms = int(datetime.fromisoformat(expires_at).timestamp() * 1000)
            client["expiryTime"] = expires_ms
            changed = True

        if enabled is not None:
            client["enable"] = bool(enabled)
            changed = True

        if total_gb is not None:
            total_bytes = total_gb * 1024**3 if total_gb > 0 else 0
            if client.get("totalGB") != total_bytes:
                client["totalGB"] = total_bytes
                changed = True

        if not changed:
            return True

        return await self._update_inbound(inbound, settings=settings)

    async def sync_subscription_client(
        self,
        inbound_id: int,
        *,
        client_id: str | None = None,
        email: str | None = None,
        limit_ip: int | None = None,
        expires_at: str | None = None,
        enabled: bool | None = True,
        total_gb: int | None = None,
    ) -> bool:
        return await self.update_client(
            inbound_id,
            client_id=client_id,
            email=email,
            limit_ip=limit_ip,
            expires_at=expires_at,
            enabled=enabled,
            total_gb=total_gb,
        )

    async def set_client_enabled(self, inbound_id: int, email: str, *, enabled: bool) -> bool:
        return await self.update_client(inbound_id, email=email, enabled=enabled)

    async def disable_client(self, inbound_id: int, email: str) -> bool:
        return await self.set_client_enabled(inbound_id, email, enabled=False)

    async def enable_client(self, inbound_id: int, email: str) -> bool:
        return await self.set_client_enabled(inbound_id, email, enabled=True)

    async def ensure_client_limit_ip(self, inbound_id: int, email: str, limit_ip: int) -> bool:
        return await self.update_client(inbound_id, email=email, limit_ip=limit_ip)

    def get_all_inbound_ids(self) -> list[int]:
        return self._target_inbound_ids(XUI_INBOUND_ID)

    async def del_client(self, inbound_id: int, client_id: str) -> bool:
        inbound = await self.get_inbound(inbound_id)
        if inbound:
            settings = self._parse_settings(inbound)
            client = self._find_client(settings, client_id=client_id)
            email = client.get("email") if client else None
            if email:
                deleted = await self._delete_client_via_clients_api(email)
                if deleted is not None:
                    return deleted

        result = await self._request(
            "POST",
            f"/panel/api/inbounds/{inbound_id}/delClient/{client_id}",
        )
        if result and result.get("success"):
            return True

        if not inbound:
            return False

        settings = self._parse_settings(inbound)
        clients = settings.get("clients") or []
        filtered = [client for client in clients if client.get("id") != client_id]
        if len(filtered) == len(clients):
            logger.warning("del_client fallback: client %s not found", client_id)
            return False

        settings["clients"] = filtered
        return await self._update_inbound(inbound, settings=settings)

    async def delete_client_by_email(self, email: str) -> bool:
        deleted = await self._delete_client_via_clients_api(email)
        if deleted is not None:
            return deleted

        found = False
        ok = True
        for inbound in await self.get_inbounds():
            settings = self._parse_settings(inbound)
            client = self._find_client(settings, email=email)
            if not client:
                continue
            found = True
            ok = await self.del_client(inbound["id"], client["id"]) and ok
        if not found:
            logger.warning("delete_client_by_email: client %s not found in any inbound", email)
        return found and ok

    async def delete_client(self, client_id: str, email: str | None = None) -> bool:
        if email:
            deleted = await self._delete_client_via_clients_api(email)
            if deleted is not None:
                return deleted

        for inbound in await self.get_inbounds():
            settings = self._parse_settings(inbound)
            client = self._find_client(settings, client_id=client_id)
            if client:
                client_email = email or client.get("email")
                if client_email:
                    deleted = await self._delete_client_via_clients_api(client_email)
                    if deleted is not None:
                        return deleted
                return await self.del_client(inbound["id"], client_id)

        if email:
            logger.debug("delete_client: client %s (email=%s) not found in inbound settings, clients API handled it", client_id, email)
            return True

        logger.debug("delete_client: client %s not found in any inbound settings", client_id)
        return False

    async def reissue_subscription_client(self, subscription: dict) -> Optional[dict]:
        expires_at_raw = subscription.get("expires_at")
        if not expires_at_raw:
            return None

        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            logger.error("Invalid expires_at on subscription %s", subscription.get("id"))
            return None

        remaining_seconds = max(0, (expires_at - datetime.utcnow()).total_seconds())
        expire_days = max(1, math.ceil(remaining_seconds / 86400))
        inbound_id = subscription.get("inbound_id") or XUI_INBOUND_ID
        devices = subscription.get("devices") or 1
        new_email = self.build_client_email()

        created = await self.add_client(
            inbound_id=inbound_id,
            email=new_email,
            devices=devices,
            expire_days=expire_days,
        )
        if not created:
            return None

        old_client_id = subscription.get("xui_client_id")
        if old_client_id:
            deleted = await self.del_client(inbound_id, old_client_id)
            if not deleted:
                await self.del_client(inbound_id, created["client_id"])
                logger.error(
                    "Failed to delete old client %s for subscription %s",
                    old_client_id,
                    subscription.get("id"),
                )
                return None

        return created

    async def get_client_stats(self, email: str) -> Optional[dict]:
        result = await self._request(
            "GET",
            f"/panel/api/clients/traffic/{quote(email, safe='')}",
        )
        if result and result.get("success"):
            return result.get("obj")

        result = await self._request(
            "GET",
            f"/panel/api/inbounds/getClientTraffics/{quote(email, safe='')}",
        )
        if result and result.get("success"):
            return result.get("obj")
        return None

    async def _normalize_client_ips_obj(self, obj: object | None) -> list[str]:
        if isinstance(obj, list):
            return [str(item) for item in obj if item]
        if isinstance(obj, str) and obj and obj != "No IP Record":
            try:
                parsed = json.loads(obj)
                if isinstance(parsed, list):
                    normalized: list[str] = []
                    for item in parsed:
                        if isinstance(item, dict) and item.get("ip"):
                            normalized.append(str(item["ip"]))
                        elif item:
                            normalized.append(str(item))
                    return normalized
            except json.JSONDecodeError:
                return [obj]
        return []

    async def _request_client_ips(self, method: str, endpoint: str, timeout: aiohttp.ClientTimeout) -> object | None:
        result = await self._request(
            method,
            endpoint,
            timeout=timeout,
            _suppress_timeout_error=True,
            _retries=max(1, int(XUI_REQUEST_RETRIES)),
            _retry_delay=1.0,
        )
        return result.get("obj") if result and result.get("success") else None

    async def get_client_ips(self, email: str) -> list[str]:
        total_timeout = max(10, int(XUI_CLIENT_IPS_TIMEOUT_SECONDS))
        ips_timeout = aiohttp.ClientTimeout(
            total=total_timeout,
            connect=min(10, max(4, total_timeout // 3)),
            sock_read=max(8, total_timeout - 8),
        )
        endpoints = [
            f"/panel/api/clients/ips/{quote(email, safe='')}",
            f"/panel/api/clients/clientIps/{quote(email, safe='')}",
            f"/panel/api/inbounds/clientIps/{quote(email, safe='')}",
            f"/panel/api/inbounds/ips/{quote(email, safe='')}",
        ]
        for endpoint in endpoints:
            for method in ("POST", "GET"):
                obj = await self._request_client_ips(method, endpoint, ips_timeout)
                ips = await self._normalize_client_ips_obj(obj)
                if ips:
                    logger.debug("get_client_ips: found %s ips for %s via %s %s", len(ips), email, method, endpoint)
                    return ips

        return []

    async def clear_client_ips(self, email: str) -> bool:
        result = await self._request(
            "POST",
            f"/panel/api/clients/clearIps/{quote(email, safe='')}",
        )
        if result is not None:
            return bool(result.get("success"))

        result = await self._request(
            "POST",
            f"/panel/api/inbounds/clearClientIps/{quote(email, safe='')}",
        )
        return bool(result and result.get("success"))

    async def get_all_onlines(self) -> Optional[dict[str, int]]:
        # Endpoint may lag on busy panels: retry a couple of times before fallback.
        total_timeout = max(30, int(XUI_ONLINES_TIMEOUT_SECONDS))
        onlines_timeout = aiohttp.ClientTimeout(
            total=total_timeout,
            connect=min(20, max(5, total_timeout // 4)),
            sock_read=max(20, total_timeout - 20),
        )
        attempts = max(1, int(XUI_ONLINES_RETRIES))
        result = None
        for attempt in range(1, attempts + 1):
            result = await self._request(
                "POST",
                "/panel/api/clients/onlines",
                timeout=onlines_timeout,
                _suppress_timeout_error=True,
            )
            if result and result.get("success"):
                break
            if attempt < attempts:
                await asyncio.sleep(min(2 * attempt, 5))
        if not result or not result.get("success"):
            for attempt in range(1, attempts + 1):
                result = await self._request(
                    "POST",
                    "/panel/api/inbounds/onlines",
                    timeout=onlines_timeout,
                    _suppress_timeout_error=True,
                )
                if result and result.get("success"):
                    break
                if attempt < attempts:
                    await asyncio.sleep(min(2 * attempt, 5))
        if not result or not result.get("success"):
            return None

        counts: dict[str, int] = {}
        mapping: dict[str, str] = {}

        for inbound in await self.get_inbounds():
            settings = self._parse_settings(inbound)
            for client in settings.get("clients", []):
                client_id = client.get("id")
                sub_id = client.get("subId")
                email = client.get("email")
                if client_id and email:
                    mapping[str(client_id)] = email
                if sub_id and email:
                    mapping[str(sub_id)] = email

        for item in result.get("obj") or []:
            resolved_email = None
            if isinstance(item, str):
                resolved_email = mapping.get(item, item)
            elif isinstance(item, dict):
                raw = item.get("email") or item.get("id") or item.get("subId")
                if raw:
                    resolved_email = mapping.get(str(raw), str(raw))

            if resolved_email:
                counts[resolved_email] = counts.get(resolved_email, 0) + 1

        return counts

    async def get_online_ips_count(self, email: str) -> int:
        try:
            counts = await self.get_all_onlines()
            if counts:
                return counts.get(email, 0)
            return 0
        except Exception as exc:
            logger.error("get_online_ips_count failed for %s: %s", email, exc)
            return 0

    async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
        result = await self._request(
            "POST",
            f"/panel/api/clients/resetTraffic/{quote(email, safe='')}",
        )
        if result is not None:
            return bool(result.get("success"))

        result = await self._request(
            "POST",
            f"/panel/api/inbounds/{inbound_id}/resetClientTraffic/{quote(email, safe='')}",
        )
        return bool(result and result.get("success"))

    async def is_client_online(self, email: str) -> int:
        return await self.get_online_ips_count(email)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


xui = XUIClient()
