import os
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ── YooKassa ──────────────────────────────────────────────────────────────────
YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "")
YOOKASSA_RETURN_URL: str = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/")

# ── 3X-UI Panel ───────────────────────────────────────────────────────────────
XUI_HOST: str = os.getenv("XUI_HOST", "https://localhost:2053")
# Optional: if your panel is behind a reverse proxy with a custom path prefix,
# this will be prepended to all API endpoints (e.g., "/d8pj23YOnuHR558ajb")
XUI_PATH_PREFIX: str = os.getenv("XUI_PATH_PREFIX", "").rstrip("/")
XUI_USERNAME: str = os.getenv("XUI_USERNAME", "admin")
XUI_PASSWORD: str = os.getenv("XUI_PASSWORD", "admin")
XUI_INBOUND_ID: int = int(os.getenv("XUI_INBOUND_ID", "1"))
XUI_VERIFY_SSL: bool = _env_bool("XUI_VERIFY_SSL", True)
XUI_PUBLIC_BASE_URL: str = os.getenv("XUI_PUBLIC_BASE_URL", "").rstrip("/")
XUI_SUBSCRIPTION_URL_TEMPLATE: str = os.getenv("XUI_SUBSCRIPTION_URL_TEMPLATE", "").strip()
XUI_SUB_PORT: int = int(os.getenv("XUI_SUB_PORT", "2096"))
XUI_ONLINES_TIMEOUT_SECONDS: int = int(os.getenv("XUI_ONLINES_TIMEOUT_SECONDS", "90"))
XUI_ONLINES_RETRIES: int = int(os.getenv("XUI_ONLINES_RETRIES", "2"))
XUI_REQUEST_RETRIES: int = int(os.getenv("XUI_REQUEST_RETRIES", "3"))
XUI_INBOUNDS_TIMEOUT_SECONDS: int = int(os.getenv("XUI_INBOUNDS_TIMEOUT_SECONDS", "45"))
XUI_CLIENT_IPS_TIMEOUT_SECONDS: int = int(os.getenv("XUI_CLIENT_IPS_TIMEOUT_SECONDS", "25"))

# ── Bot settings ──────────────────────────────────────────────────────────────
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "")
WEBHOOK_PATH: str = "/webhook"
WEBAPP_HOST: str = "0.0.0.0"
WEBAPP_PORT: int = int(os.getenv("PORT", "8080"))

# ── Inbound Settings ──────────────────────────────────────────────────────────
# Красивое имя для всех ссылок в инбаунде
INBOUND_REMARK: str = os.getenv("INBOUND_REMARK", "VpNLi 🔷 | 🇳🇱 Amsterdam")

# ── Enforcement ───────────────────────────────────────────────────────────────
# If 3X-UI `limitIp` is not enforced reliably (varies by setup/version),
# the bot can enforce it by temporarily disabling the client when over-limit.
HARD_IP_LIMIT_ENFORCEMENT: bool = _env_bool("HARD_IP_LIMIT_ENFORCEMENT", True)
HARD_IP_LIMIT_COOLDOWN_SECONDS: int = int(os.getenv("HARD_IP_LIMIT_COOLDOWN_SECONDS", "60"))

# ── Referral ──────────────────────────────────────────────────────────────────
# Бонусные дни начисляются обоим после успешной оплаты рефералом.
REFERRAL_BONUS_DAYS_BY_MONTHS: dict[int, int] = {
    1: 7,   # 1 месяц -> +7 дней
    3: 14,  # 3 месяца -> +14 дней
}

# ── Trial ─────────────────────────────────────────────────────────────────────
TRIAL_DAYS: int = 3
TRIAL_TRAFFIC_GB: int = 15
TRIAL_LOCK_STALE_SECONDS: int = int(os.getenv("TRIAL_LOCK_STALE_SECONDS", "900"))

# ── Pricing (RUB) ─────────────────────────────────────────────────────────────
# Format: {devices: {months: price}}
PRICES: dict = {
    1: {1: 99,  3: 249},
    2: {1: 129, 3: 325},
    3: {1: 159, 3: 400},
    5: {1: 219, 3: 555},
}

# Красивые подписи для периодов
PERIOD_LABELS: dict = {
    1:  "1 месяц",
    3:  "3 месяца",
}

# Скидки для подписей (отображаемые)
PERIOD_DISCOUNTS: dict = {
    1:  None,
    3:  "−16%",
}

# ── DB ────────────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "data/vpn_bot.db")
DB_BACKUP_DIR: str = os.getenv("DB_BACKUP_DIR", "data/backups")
DB_BACKUP_KEEP: int = int(os.getenv("DB_BACKUP_KEEP", "10"))

# ── Offer text ────────────────────────────────────────────────────────────────
OFFER_PDF_PATH: str = os.getenv(
    "OFFER_PDF_PATH",
    os.path.join(BASE_DIR, "ПУБЛИЧНАЯ ОФЕРТА.pdf"),
)

PRIVACY_PDF_PATH: str = os.getenv(
    "PRIVACY_PDF_PATH",
    os.path.join(BASE_DIR, "ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ.pdf"),
)

# Название соединения в VLESS-ссылке (часть после #).
VLESS_KEY_NAME: str = os.getenv("VLESS_KEY_NAME", INBOUND_REMARK)
