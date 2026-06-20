"""
Async SQLite database layer for the VPN bot.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import aiosqlite

from config import DB_BACKUP_DIR, DB_BACKUP_KEEP, DB_PATH, TRIAL_LOCK_STALE_SECONDS


def _utcnow() -> datetime:
    return datetime.utcnow()


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _is_trial_lock_stale(lock_at_raw: str | None, *, now: datetime | None = None) -> bool:
    if not lock_at_raw:
        return True
    try:
        lock_at = datetime.fromisoformat(lock_at_raw)
    except ValueError:
        return True
    ref_now = now or _utcnow()
    return (ref_now - lock_at).total_seconds() >= max(60, int(TRIAL_LOCK_STALE_SECONDS))


async def _has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return any(row[1] == column for row in rows)


async def _ensure_column(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if not await _has_column(db, table, column):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _row_to_dict(row: aiosqlite.Row | None) -> Optional[dict]:
    return dict(row) if row else None


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS users (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id             INTEGER UNIQUE NOT NULL,
                username          TEXT,
                full_name         TEXT,
                referral_code     TEXT UNIQUE NOT NULL,
                referred_by       INTEGER REFERENCES users(id),
                balance           INTEGER DEFAULT 0,
                trial_used        INTEGER DEFAULT 0,
                trial_lock_at     TEXT,
                offer_accepted_at TEXT,
                is_banned         INTEGER DEFAULT 0,
                created_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(id),
                xui_client_id     TEXT UNIQUE,
                inbound_id        INTEGER NOT NULL,
                devices           INTEGER NOT NULL,
                months            INTEGER NOT NULL,
                vless_key         TEXT,
                started_at        TEXT NOT NULL,
                expires_at        TEXT NOT NULL,
                is_active         INTEGER DEFAULT 1,
                auto_renew        INTEGER DEFAULT 0,
                subscription_url  TEXT,
                subscription_id   TEXT,
                link_reveals      INTEGER DEFAULT 0,
                email             TEXT
            );

            CREATE TABLE IF NOT EXISTS payments (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id              INTEGER NOT NULL REFERENCES users(id),
                yookassa_id          TEXT UNIQUE,
                confirmation_url     TEXT,
                base_amount          INTEGER,
                promo_code           TEXT,
                promo_discount       INTEGER DEFAULT 0,
                promo_usage_id       INTEGER REFERENCES promo_usages(id),
                amount               INTEGER NOT NULL,
                devices              INTEGER,
                months               INTEGER,
                payment_type         TEXT DEFAULT 'new',
                target_sub_id        INTEGER REFERENCES subscriptions(id),
                status               TEXT DEFAULT 'pending',
                is_referral_reward   INTEGER DEFAULT 0,
                created_at           TEXT NOT NULL,
                processing_started_at TEXT,
                paid_at              TEXT
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                code              TEXT UNIQUE NOT NULL,
                discount_value    INTEGER NOT NULL,
                max_activations   INTEGER NOT NULL,
                used_count        INTEGER DEFAULT 0,
                expires_at        TEXT,
                created_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_usages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id     INTEGER NOT NULL REFERENCES promo_codes(id),
                user_id      INTEGER NOT NULL REFERENCES users(id),
                payment_id   INTEGER REFERENCES payments(id),
                used_at      TEXT NOT NULL,
                UNIQUE(promo_id, user_id)
            );
            
            CREATE TABLE IF NOT EXISTS promo_canceled_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                promo_code  TEXT NOT NULL,
                canceled_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_attempts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                attempted_at TEXT NOT NULL,
                is_success   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS vpn_clients (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id     INTEGER NOT NULL REFERENCES subscriptions(id),
                xui_client_id       TEXT UNIQUE NOT NULL,
                inbound_id          INTEGER NOT NULL,
                email               TEXT,
                subscription_url    TEXT,
                subscription_id_xui TEXT,
                created_at          TEXT NOT NULL,
                is_active           INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS referral_rewards (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL REFERENCES users(id),
                referred_id INTEGER NOT NULL REFERENCES users(id),
                payment_id  INTEGER NOT NULL REFERENCES payments(id),
                amount      INTEGER NOT NULL,
                paid_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS violations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_id       INTEGER NOT NULL REFERENCES subscriptions(id),
                user_id      INTEGER NOT NULL REFERENCES users(id),
                client_email TEXT,
                ip_count     INTEGER,
                ip_limit     INTEGER,
                detected_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);
            CREATE INDEX IF NOT EXISTS idx_subs_user_id ON subscriptions(user_id);
            CREATE INDEX IF NOT EXISTS idx_subs_is_active ON subscriptions(is_active, expires_at);
            CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
            CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_payments_yookassa_id ON payments(yookassa_id);
            CREATE INDEX IF NOT EXISTS idx_promo_codes_code ON promo_codes(code);
            CREATE INDEX IF NOT EXISTS idx_promo_usages_user_id ON promo_usages(user_id);
            CREATE INDEX IF NOT EXISTS idx_promo_usages_payment_id ON promo_usages(payment_id);
            CREATE INDEX IF NOT EXISTS idx_promo_canceled_user_id ON promo_canceled_history(user_id, canceled_at);
            CREATE INDEX IF NOT EXISTS idx_promo_attempts_user_time ON promo_attempts(user_id, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_vpn_clients_subscription_id ON vpn_clients(subscription_id);
            CREATE INDEX IF NOT EXISTS idx_vpn_clients_xui_client_id ON vpn_clients(xui_client_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_payment_id
                ON referral_rewards(payment_id);
            """
        )

        await _ensure_column(db, "users", "offer_accepted_at", "TEXT")
        await _ensure_column(db, "users", "trial_lock_at", "TEXT")
        await _ensure_column(db, "subscriptions", "subscription_url", "TEXT")
        await _ensure_column(db, "subscriptions", "subscription_id", "TEXT")
        await _ensure_column(db, "subscriptions", "link_reveals", "INTEGER DEFAULT 0")
        await _ensure_column(db, "subscriptions", "email", "TEXT")
        await _ensure_column(db, "payments", "confirmation_url", "TEXT")
        await _ensure_column(db, "payments", "base_amount", "INTEGER")
        await _ensure_column(db, "payments", "promo_code", "TEXT")
        await _ensure_column(db, "payments", "promo_discount", "INTEGER DEFAULT 0")
        await _ensure_column(db, "payments", "promo_usage_id", "INTEGER")
        await _ensure_column(db, "payments", "payment_type", "TEXT DEFAULT 'new'")
        await _ensure_column(db, "payments", "target_sub_id", "INTEGER")
        await _ensure_column(db, "payments", "processing_started_at", "TEXT")

        await db.commit()


async def backup_db() -> str:
    os.makedirs(DB_BACKUP_DIR, exist_ok=True)
    msk_now = datetime.now(ZoneInfo("Europe/Moscow"))
    stamp = msk_now.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(DB_BACKUP_DIR, f"vpn_bot_{stamp}.db")

    def _do_backup() -> None:
        source = sqlite3.connect(DB_PATH, timeout=30)
        target = sqlite3.connect(backup_path, timeout=30)
        try:
            # Ensure WAL changes are checkpointed before creating a backup snapshot.
            source.execute("PRAGMA wal_checkpoint(FULL);")
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    await asyncio.to_thread(_do_backup)

    keep = max(1, int(DB_BACKUP_KEEP))
    backups = sorted(
        (
            os.path.join(DB_BACKUP_DIR, name)
            for name in os.listdir(DB_BACKUP_DIR)
            if name.startswith("vpn_bot_") and name.endswith(".db")
        ),
        key=os.path.getmtime,
        reverse=True,
    )
    for old_path in backups[keep:]:
        try:
            os.remove(old_path)
        except OSError:
            pass

    return backup_path


def calculate_discount(base_amount: int, discount_value: int) -> tuple[int, int]:
    """
    Returns (discount_amount, final_amount).
    Positive value means percent. Negative value means fixed RUB discount.
    """
    if base_amount <= 0:
        return 0, 0

    discount = 0
    if discount_value > 0:
        discount = int(base_amount * discount_value / 100)
        if discount > 0:
            discount = max(1, discount)
    elif discount_value < 0:
        discount = abs(discount_value)

    final_amount = max(1, base_amount - discount)
    discount = max(0, base_amount - final_amount)
    return discount, final_amount


# ── Users ────────────────────────────────────────────────────────────────────

async def get_user(tg_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_user_by_ref_code(code: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE referral_code = ?",
            (code.strip().upper(),),
        ) as cur:
            return _row_to_dict(await cur.fetchone())


async def create_user(
    tg_id: int,
    username: str,
    full_name: str,
    referred_by_id: Optional[int] = None,
) -> dict:
    existing = await get_user(tg_id)
    if existing:
        return existing

    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            ref_code = uuid.uuid4().hex[:8].upper()
            try:
                await db.execute(
                    """
                    INSERT INTO users
                    (tg_id, username, full_name, referral_code, referred_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tg_id,
                        username,
                        full_name,
                        ref_code,
                        referred_by_id,
                        _utcnow_iso(),
                    ),
                )
                await db.commit()
                break
            except aiosqlite.IntegrityError:
                existing = await get_user(tg_id)
                if existing:
                    return existing
        return await get_user(tg_id) or {}


async def set_user_referred_by(user_id: int, referred_by_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE users
            SET referred_by = ?
            WHERE id = ? AND referred_by IS NULL
            """,
            (referred_by_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_user_balance(user_id: int, delta: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (delta, user_id),
        )
        await db.commit()


async def has_accepted_offer(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT offer_accepted_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def mark_offer_accepted(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users
            SET offer_accepted_at = COALESCE(offer_accepted_at, ?)
            WHERE id = ?
            """,
            (_utcnow_iso(), user_id),
        )
        await db.commit()


async def mark_trial_used(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET trial_used = 1, trial_lock_at = NULL WHERE id = ?",
            (user_id,),
        )
        await db.commit()


async def claim_trial_slot(user_id: int) -> bool:
    """
    Atomically reserves a user's trial slot.
    Uses trial_used=2 as an in-progress marker to prevent concurrent trial issuance.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            now = _utcnow()
            async with db.execute(
                "SELECT trial_used, trial_lock_at FROM users WHERE id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await db.rollback()
                return False
            trial_used = int(row[0] or 0)
            trial_lock_at = row[1]
            if trial_used == 1:
                await db.rollback()
                return False
            if trial_used == 2:
                if not _is_trial_lock_stale(trial_lock_at, now=now):
                    await db.rollback()
                    return False
                await db.execute(
                    "UPDATE users SET trial_used = 0, trial_lock_at = NULL WHERE id = ? AND trial_used = 2",
                    (user_id,),
                )

            async with db.execute(
                "SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'paid'",
                (user_id,),
            ) as cur:
                paid_count = int((await cur.fetchone())[0])
            if paid_count > 0:
                await db.rollback()
                return False

            cur = await db.execute(
                "UPDATE users SET trial_used = 2, trial_lock_at = ? WHERE id = ? AND trial_used = 0",
                (now.isoformat(), user_id),
            )
            if cur.rowcount <= 0:
                await db.rollback()
                return False

            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def finalize_trial_slot(user_id: int, *, success: bool) -> None:
    target = 1 if success else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET trial_used = ?, trial_lock_at = NULL WHERE id = ? AND trial_used = 2",
            (target, user_id),
        )
        await db.commit()


async def is_trial_available(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT trial_used, trial_lock_at FROM users WHERE id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                await db.rollback()
                return False

            trial_used = int(row[0] or 0)
            trial_lock_at = row[1]
            if trial_used == 1:
                await db.rollback()
                return False

            if trial_used == 2 and _is_trial_lock_stale(trial_lock_at):
                await db.execute(
                    "UPDATE users SET trial_used = 0, trial_lock_at = NULL WHERE id = ? AND trial_used = 2",
                    (user_id,),
                )
                trial_used = 0

            if trial_used != 0:
                await db.rollback()
                return False

            async with db.execute(
                "SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'paid'",
                (user_id,),
            ) as cur:
                paid_count = int((await cur.fetchone())[0])

            await db.commit()
            return paid_count == 0
        except Exception:
            await db.rollback()
            raise


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_users_page(offset: int = 0, limit: int = 20) -> list[dict]:
    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(int(limit), 100))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (safe_limit, safe_offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def search_users(query: str, limit: int = 20) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []

    safe_limit = max(1, min(limit, 100))
    like = f"%{q.lower()}%"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if q.isdigit():
            async with db.execute(
                """
                SELECT *
                FROM users
                WHERE tg_id = ?
                   OR CAST(id AS TEXT) = ?
                   OR lower(username) LIKE ?
                   OR lower(full_name) LIKE ?
                ORDER BY
                    CASE WHEN tg_id = ? THEN 0 ELSE 1 END,
                    created_at DESC
                LIMIT ?
                """,
                (int(q), q, like, like, int(q), safe_limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

        async with db.execute(
            """
            SELECT *
            FROM users
            WHERE lower(username) LIKE ?
               OR lower(full_name) LIKE ?
               OR lower(referral_code) LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (like, like, like, safe_limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_users_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def ban_user(tg_id: int, banned: bool = True) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = ? WHERE tg_id = ?",
            (int(banned), tg_id),
        )
        await db.commit()


# ── Referrals ────────────────────────────────────────────────────────────────

async def get_referral_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?",
            (user_id,),
        ) as cur:
            invited = int((await cur.fetchone())[0])

        async with db.execute(
            """
            SELECT COUNT(DISTINCT u.id)
            FROM users u
            JOIN payments p ON p.user_id = u.id
            WHERE u.referred_by = ? AND p.status = 'paid'
            """,
            (user_id,),
        ) as cur:
            paid = int((await cur.fetchone())[0])

        # Count paid referrals in the last 30 days
        month_ago = (_utcnow() - timedelta(days=30)).isoformat()
        async with db.execute(
            """
            SELECT COUNT(DISTINCT u.id)
            FROM users u
            JOIN payments p ON p.user_id = u.id
            WHERE u.referred_by = ? AND p.status = 'paid' AND p.paid_at >= ?
            """,
            (user_id, month_ago),
        ) as cur:
            paid_this_month = int((await cur.fetchone())[0])

    return {"invited": invited, "paid": paid, "paid_this_month": paid_this_month}


async def extend_active_subscriptions(user_id: int, days: int) -> None:
    subs = await get_active_subscriptions(user_id)
    if not subs or days <= 0:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        for sub in subs:
            if sub.get("months") == 0:
                continue
            current_exp = datetime.fromisoformat(sub["expires_at"])
            base = current_exp if current_exp > _utcnow() else _utcnow()
            new_expires = base + timedelta(days=days)
            await db.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE id = ?",
                (new_expires.isoformat(), sub["id"]),
            )
        await db.commit()


async def extend_latest_active_subscription(user_id: int, days: int) -> Optional[dict]:
    if days <= 0:
        return None

    subs = await get_active_subscriptions(user_id)
    if not subs:
        return None

    latest = max(subs, key=lambda item: item["expires_at"])
    if latest.get("months") == 0:
        return None

    current_exp = datetime.fromisoformat(latest["expires_at"])
    base = current_exp if current_exp > _utcnow() else _utcnow()
    new_expires = base + timedelta(days=days)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET expires_at = ? WHERE id = ?",
            (new_expires.isoformat(), latest["id"]),
        )
        await db.commit()

    return await get_subscription_by_id(latest["id"])


async def create_referral_reward(
    referrer_id: int,
    referred_id: int,
    payment_id: int,
    amount: int,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO referral_rewards
            (referrer_id, referred_id, payment_id, amount, paid_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (referrer_id, referred_id, payment_id, amount, _utcnow_iso()),
        )
        await db.commit()


async def has_referral_reward_for_payment(payment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM referral_rewards WHERE payment_id = ?",
            (payment_id,),
        ) as cur:
            return int((await cur.fetchone())[0]) > 0


async def has_referral_reward_for_pair(referrer_id: int, referred_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM referral_rewards
            WHERE referrer_id = ? AND referred_id = ?
            """,
            (referrer_id, referred_id),
        ) as cur:
            return int((await cur.fetchone())[0]) > 0


# ── Promo Codes ──────────────────────────────────────────────────────────────

async def create_promocode(
    code: str,
    discount_value: int,
    max_activations: int,
    expires_at: Optional[str] = None,
) -> dict:
    promo_code = code.strip().upper()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO promo_codes
            (code, discount_value, max_activations, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (promo_code, discount_value, max_activations, expires_at, _utcnow_iso()),
        )
        await db.commit()
        promo_id = cur.lastrowid

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,)) as cur:
            return _row_to_dict(await cur.fetchone()) or {}


async def get_promocode(code: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (code.strip().upper(),),
        ) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_open_promocode_usage(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT pu.id AS usage_id,
                   pu.user_id,
                   pu.used_at,
                   pc.id AS promo_id,
                   pc.code,
                   pc.discount_value,
                   pc.max_activations,
                   pc.used_count,
                   pc.expires_at
            FROM promo_usages pu
            JOIN promo_codes pc ON pc.id = pu.promo_id
            WHERE pu.user_id = ? AND pu.payment_id IS NULL
            ORDER BY pu.used_at DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            return _row_to_dict(await cur.fetchone())


async def attach_promocode_usage_to_payment(usage_id: int, payment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE promo_usages
            SET payment_id = ?
            WHERE id = ? AND payment_id IS NULL
            """,
            (payment_id, usage_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def detach_promocode_usage(usage_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE promo_usages
            SET payment_id = NULL
            WHERE id = ?
            """,
            (usage_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def consume_promocode(code: str, user_id: int) -> dict:
    promo_code = code.strip().upper()
    if not promo_code:
        return {"ok": False, "error": "empty"}

    now = _utcnow()
    now_iso = now.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT pu.id AS usage_id, pc.code
                FROM promo_usages pu
                JOIN promo_codes pc ON pc.id = pu.promo_id
                WHERE pu.user_id = ? AND pu.payment_id IS NULL
                ORDER BY pu.used_at DESC
                LIMIT 1
                """,
                (user_id,),
            ) as cur:
                open_usage = await cur.fetchone()

            if open_usage:
                await db.rollback()
                return {
                    "ok": False,
                    "error": "already_applied",
                    "code": open_usage["code"],
                }

            async with db.execute(
                "SELECT * FROM promo_codes WHERE code = ?",
                (promo_code,),
            ) as cur:
                promo = await cur.fetchone()

            if not promo:
                await db.rollback()
                return {"ok": False, "error": "not_found"}

            if promo["expires_at"]:
                try:
                    expires_at = datetime.fromisoformat(promo["expires_at"])
                except ValueError:
                    await db.rollback()
                    return {"ok": False, "error": "invalid_expiry"}

                if now > expires_at:
                    await db.rollback()
                    return {"ok": False, "error": "expired"}

            if promo["used_count"] >= promo["max_activations"]:
                await db.rollback()
                return {"ok": False, "error": "exhausted"}

            async with db.execute(
                "SELECT COUNT(*) FROM promo_usages WHERE promo_id = ? AND user_id = ?",
                (promo["id"], user_id),
            ) as cur:
                already_used = int((await cur.fetchone())[0])

            if already_used > 0:
                await db.rollback()
                return {"ok": False, "error": "already_used"}

            usage_cur = await db.execute(
                """
                INSERT INTO promo_usages (promo_id, user_id, used_at)
                VALUES (?, ?, ?)
                """,
                (promo["id"], user_id, now_iso),
            )
            usage_id = usage_cur.lastrowid

            await db.execute(
                "UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?",
                (promo["id"],),
            )
            await db.commit()

            remaining = max(0, promo["max_activations"] - (promo["used_count"] + 1))
            return {
                "ok": True,
                "usage": {
                    "usage_id": usage_id,
                    "promo_id": promo["id"],
                    "code": promo["code"],
                    "discount_value": promo["discount_value"],
                    "used_at": now_iso,
                },
                "remaining": remaining,
                "expires_at": promo["expires_at"],
            }
        except Exception:
            await db.rollback()
            raise


async def cancel_open_promocode_usage(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT pu.id AS usage_id, pu.promo_id, pc.code
                FROM promo_usages pu
                JOIN promo_codes pc ON pc.id = pu.promo_id
                WHERE pu.user_id = ? AND pu.payment_id IS NULL
                ORDER BY pu.used_at DESC
                LIMIT 1
                """,
                (user_id,),
            ) as cur:
                usage = await cur.fetchone()

            if not usage:
                await db.rollback()
                return {"ok": False, "error": "not_found"}

            await db.execute("DELETE FROM promo_usages WHERE id = ?", (usage["usage_id"],))
            await db.execute(
                "UPDATE promo_codes SET used_count = MAX(0, used_count - 1) WHERE id = ?",
                (usage["promo_id"],),
            )
            await db.execute(
                """
                INSERT INTO promo_canceled_history (user_id, promo_code, canceled_at)
                VALUES (?, ?, ?)
                """,
                (user_id, usage["code"], _utcnow_iso()),
            )
            await db.commit()
            return {"ok": True, "code": usage["code"]}
        except Exception:
            await db.rollback()
            raise


async def get_canceled_promocode_history(user_id: int, limit: int = 10) -> list[dict]:
    safe_limit = max(1, min(int(limit), 50))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT promo_code, MAX(canceled_at) AS canceled_at
            FROM promo_canceled_history
            WHERE user_id = ?
            GROUP BY promo_code
            ORDER BY MAX(canceled_at) DESC
            LIMIT ?
            """,
            (user_id, safe_limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def is_promocode_rate_limited(user_id: int, *, limit: int = 8, window_seconds: int = 300) -> bool:
    safe_limit = max(1, min(int(limit), 100))
    safe_window = max(30, min(int(window_seconds), 86400))
    threshold = (_utcnow() - timedelta(seconds=safe_window)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM promo_attempts
            WHERE user_id = ? AND attempted_at >= ? AND is_success = 0
            """,
            (user_id, threshold),
        ) as cur:
            failed_attempts = int((await cur.fetchone())[0])
    return failed_attempts >= safe_limit


async def log_promocode_attempt(user_id: int, *, success: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO promo_attempts (user_id, attempted_at, is_success) VALUES (?, ?, ?)",
            (user_id, _utcnow_iso(), 1 if success else 0),
        )
        await db.commit()


async def list_promocodes(limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(int(limit), 1000))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT *
            FROM promo_codes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_promocode_by_id(promo_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,)) as cur:
            return _row_to_dict(await cur.fetchone())


async def update_promocode_conditions(
    promo_id: int,
    *,
    discount_value: int | None = None,
    max_activations: int | None = None,
    expires_at: str | None | object = ...,
) -> bool:
    fields: list[str] = []
    values: list[object] = []
    if discount_value is not None:
        fields.append("discount_value = ?")
        values.append(discount_value)
    if max_activations is not None:
        fields.append("max_activations = ?")
        values.append(max_activations)
    if expires_at is not ...:
        fields.append("expires_at = ?")
        values.append(expires_at)
    if not fields:
        return False

    values.append(promo_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE promo_codes SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_promocode(promo_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM promo_usages WHERE promo_id = ? AND payment_id IS NULL", (promo_id,))
        cur = await db.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Subscriptions ────────────────────────────────────────────────────────────

async def create_subscription(
    user_id: int,
    xui_client_id: Optional[str],
    inbound_id: int,
    devices: int,
    months: int,
    vless_key: str,
    days: Optional[int] = None,
    subscription_url: Optional[str] = None,
    subscription_id: Optional[str] = None,
    email: Optional[str] = None,
) -> dict:
    now = _utcnow()
    expires_at = now + timedelta(days=days if days is not None else months * 30)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO subscriptions
            (user_id, xui_client_id, inbound_id, devices, months, vless_key,
             started_at, expires_at, subscription_url, subscription_id, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                xui_client_id,
                inbound_id,
                devices,
                months,
                vless_key,
                now.isoformat(),
                expires_at.isoformat(),
                subscription_url,
                subscription_id,
                email,
            ),
        )
        await db.commit()
        sub_id = cur.lastrowid

    return await get_subscription_by_id(sub_id) or {}


async def create_vpn_client(
    subscription_id: int,
    xui_client_id: str,
    inbound_id: int,
    email: str,
    subscription_url: str,
    subscription_id_xui: str,
) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO vpn_clients
            (subscription_id, xui_client_id, inbound_id, email, subscription_url, subscription_id_xui, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subscription_id,
                xui_client_id,
                inbound_id,
                email,
                subscription_url,
                subscription_id_xui,
                _utcnow_iso(),
            ),
        )
        await db.commit()
        row_id = cur.lastrowid

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vpn_clients WHERE id = ?", (row_id,)) as cur:
            return _row_to_dict(await cur.fetchone()) or {}


async def get_active_vpn_clients_for_subscription(subscription_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM vpn_clients
            WHERE subscription_id = ? AND is_active = 1
            ORDER BY created_at DESC
            """,
            (subscription_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_active_vpn_clients_for_user(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT vc.*
            FROM vpn_clients vc
            JOIN subscriptions s ON s.id = vc.subscription_id
            WHERE s.user_id = ? AND s.is_active = 1 AND vc.is_active = 1
            ORDER BY vc.created_at DESC
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_all_active_vpn_clients() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vpn_clients WHERE is_active = 1 ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def deactivate_vpn_client(xui_client_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE vpn_clients
            SET is_active = 0
            WHERE xui_client_id = ? AND is_active = 1
            """,
            (xui_client_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_subscription_by_id(sub_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_active_subscriptions(user_id: int) -> list[dict]:
    now_iso = _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM subscriptions
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
            ORDER BY expires_at DESC
            """,
            (user_id, now_iso),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_all_subscriptions(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY started_at DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_subscription_display_map(user_id: int) -> dict[int, int]:
    """
    Returns a per-user stable numbering map: {subscription_id: display_number}.
    Numbering is local to the user and ordered from oldest to newest ACTIVE subscription.
    """
    now_iso = _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id
            FROM subscriptions
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
            ORDER BY started_at ASC, id ASC
            """,
            (user_id, now_iso),
        ) as cur:
            rows = await cur.fetchall()
    return {int(row["id"]): idx for idx, row in enumerate(rows, start=1)}


async def deactivate_subscription(sub_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE subscriptions SET is_active = 0 WHERE id = ?", (sub_id,))
        await db.execute(
            "UPDATE vpn_clients SET is_active = 0 WHERE subscription_id = ?",
            (sub_id,),
        )
        await db.commit()


async def get_expiring_subscriptions(days: int = 3) -> list[dict]:
    now = _utcnow()
    threshold = (now + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT s.*, u.tg_id, u.full_name
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1
              AND s.expires_at > ?
              AND s.expires_at <= ?
            """,
            (now.isoformat(), threshold),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_expired_subscriptions() -> list[dict]:
    now_iso = _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT s.*, u.tg_id
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1 AND s.expires_at <= ?
            """,
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_old_expired_subscriptions(days_after_expiry: int = 2) -> list[dict]:
    threshold = (_utcnow() - timedelta(days=days_after_expiry)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT s.*, u.tg_id
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1 AND s.expires_at <= ?
            """,
            (threshold,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def increment_link_reveals(sub_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET link_reveals = link_reveals + 1 WHERE id = ?",
            (sub_id,),
        )
        await db.commit()
        async with db.execute(
            "SELECT link_reveals FROM subscriptions WHERE id = ?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def reset_link_reveals(sub_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET link_reveals = 0 WHERE id = ?",
            (sub_id,),
        )
        await db.commit()


async def update_subscription_credentials(
    sub_id: int,
    *,
    xui_client_id: str,
    subscription_id: str,
    subscription_url: str,
    email: str,
) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE subscriptions
            SET xui_client_id = ?,
                subscription_id = ?,
                subscription_url = ?,
                email = ?,
                link_reveals = 0
            WHERE id = ?
            """,
            (xui_client_id, subscription_id, subscription_url, email, sub_id),
        )
        await db.commit()

    return await get_subscription_by_id(sub_id)


async def extend_subscription(sub_id: int, months: int) -> Optional[dict]:
    return await renew_subscription(sub_id, months, bonus_days=0)


async def renew_subscription(
    sub_id: int,
    months: int,
    *,
    devices: Optional[int] = None,
    bonus_days: int = 0,
    reset_remaining: bool = False,
) -> Optional[dict]:
    sub = await get_subscription_by_id(sub_id)
    if not sub:
        return None

    # Prohibit renewal of trial subscriptions (months=0)
    if sub["months"] == 0:
        return None

    now = _utcnow()
    current_exp = datetime.fromisoformat(sub["expires_at"])
    base = now if reset_remaining else (current_exp if current_exp > now else now)
    extension_days = months * 30 + max(0, bonus_days)
    new_expires = base + timedelta(days=extension_days)
    new_devices = devices if devices is not None else sub["devices"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE subscriptions
            SET expires_at = ?,
                devices = ?,
                months = ?,
                is_active = 1
            WHERE id = ?
            """,
            (new_expires.isoformat(), new_devices, months, sub_id),
        )
        await db.commit()

    return await get_subscription_by_id(sub_id)


# ── Payments ─────────────────────────────────────────────────────────────────

async def create_payment(
    user_id: int,
    amount: int,
    devices: int,
    months: int,
    yookassa_id: Optional[str] = None,
    *,
    base_amount: Optional[int] = None,
    promo_code: Optional[str] = None,
    promo_discount: int = 0,
    promo_usage_id: Optional[int] = None,
    payment_type: str = "new",
    target_sub_id: Optional[int] = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO payments
            (user_id, yookassa_id, confirmation_url, base_amount, promo_code,
             promo_discount, promo_usage_id, amount, devices, months,
             payment_type, target_sub_id, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                yookassa_id,
                base_amount if base_amount is not None else amount,
                promo_code,
                promo_discount,
                promo_usage_id,
                amount,
                devices,
                months,
                payment_type,
                target_sub_id,
                _utcnow_iso(),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_payment(payment_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_latest_pending_payment(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM payments
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cur:
            return _row_to_dict(await cur.fetchone())


async def get_payment_by_yookassa(yookassa_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE yookassa_id = ?",
            (yookassa_id,),
        ) as cur:
            return _row_to_dict(await cur.fetchone())


async def set_payment_gateway_data(
    payment_id: int,
    yookassa_id: str,
    confirmation_url: Optional[str],
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE payments
            SET yookassa_id = ?, confirmation_url = ?
            WHERE id = ?
            """,
            (yookassa_id, confirmation_url, payment_id),
        )
        await db.commit()


async def claim_payment_for_processing(payment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE payments
            SET status = 'processing',
                processing_started_at = ?
            WHERE id = ? AND status IN ('pending', 'canceled')
            """,
            (_utcnow_iso(), payment_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def release_payment_processing(payment_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE payments
            SET status = 'pending',
                processing_started_at = NULL
            WHERE id = ? AND status = 'processing'
            """,
            (payment_id,),
        )
        await db.commit()


async def confirm_payment(payment_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE payments
            SET status = 'paid',
                paid_at = ?,
                processing_started_at = NULL
            WHERE id = ? AND status != 'paid'
            """,
            (_utcnow_iso(), payment_id),
        )
        await db.commit()


async def set_payment_status(payment_id: int, status: str) -> None:
    processing_started_at = None if status != "processing" else _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE payments
            SET status = ?, processing_started_at = ?
            WHERE id = ?
            """,
            (status, processing_started_at, payment_id),
        )
        await db.commit()


async def get_revenue_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'paid'"
        ) as cur:
            total = int((await cur.fetchone())[0])
        async with db.execute(
            "SELECT COUNT(*) FROM payments WHERE status = 'paid'"
        ) as cur:
            count = int((await cur.fetchone())[0])
    return {"total": total, "count": count}


async def has_paid_before(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'paid'",
            (user_id,),
        ) as cur:
            return int((await cur.fetchone())[0]) > 0


# ── Violations ───────────────────────────────────────────────────────────────

async def log_violation(
    sub_id: int,
    user_id: int,
    email: str,
    ip_count: int,
    ip_limit: int,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO violations
            (sub_id, user_id, client_email, ip_count, ip_limit, detected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sub_id, user_id, email, ip_count, ip_limit, _utcnow_iso()),
        )
        await db.commit()


async def get_active_subscriptions_map() -> dict[str, dict]:
    now_iso = _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM subscriptions
            WHERE is_active = 1 AND email IS NOT NULL AND expires_at > ?
            """,
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
            return {row["email"]: dict(row) for row in rows}
