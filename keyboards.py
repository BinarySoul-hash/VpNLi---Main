"""
Inline keyboard builders for the bot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, quote_plus

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import PERIOD_DISCOUNTS, PERIOD_LABELS, PRICES


def _remaining_days(expires_at: datetime, now: datetime) -> int:
    remaining_seconds = max(0, (expires_at - now).total_seconds())
    return int((remaining_seconds + 86399) // 86400)



def main_menu_kb(
    trial_available: bool = False,
    has_active_sub: bool = False,
    days_left: int | None = None,) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_active_sub:
        if days_left is not None:
            label = f"📡 Мои подписки · {days_left} дн."
        else:
            label = "📡 Мои подписки"
        kb.row(InlineKeyboardButton(text=label, callback_data="my_subs", style=ButtonStyle.SUCCESS))
    elif trial_available:
        kb.row(
            InlineKeyboardButton(
                text="🎁 Получить 3 дня бесплатно",
                callback_data="trial",
                style=ButtonStyle.SUCCESS,
            )
        )
    else:
        kb.row(
            InlineKeyboardButton(
                text="💳 Купить / продлить VPN",
                callback_data="buy",
                style=ButtonStyle.SUCCESS,
            )
        )
    kb.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
    kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data="install"))
    kb.row(
        InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
        InlineKeyboardButton(text="📄 Документы", callback_data="docs"),
    )

    return kb.as_markup()


def start_kb(trial_available: bool = False, has_active_sub: bool = False, days_left: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not has_active_sub and trial_available:
        kb.row(
            InlineKeyboardButton(
                text="🎁 Получить 3 дня бесплатно",
                callback_data="trial",
                style=ButtonStyle.SUCCESS,
            )
        )
    elif not has_active_sub:
        kb.row(
            InlineKeyboardButton(
                text="💳 Купить / продлить VPN",
                callback_data="buy",
                style=ButtonStyle.SUCCESS,
            )
        )
    else:
        label = "📡 Мои подписки"
        if days_left is not None:
            label = f"📡 Мои подписки · {days_left} дн."
        kb.row(InlineKeyboardButton(text=label, callback_data="my_subs"))
    return kb.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def back_kb(back_callback: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def buy_hub_kb(trial_available: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🆕 Купить новую подписку", callback_data="buy_new"))
    kb.row(InlineKeyboardButton(text="🔄 Продлить существующую подписку", callback_data="buy_renew_list"))
    if trial_available:
        kb.row(
            InlineKeyboardButton(
                text="🎁 Получить 3 дня бесплатно",
                callback_data="trial",
                style=ButtonStyle.SUCCESS,
            )
        )
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def devices_kb(
    trial_available: bool = False,
    *,
    back_callback: str = "buy",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if trial_available:
        kb.row(
            InlineKeyboardButton(
                text="Получить 3 дня бесплатно",
                callback_data="trial",
                style=ButtonStyle.SUCCESS,
            )
        )

    kb.row(InlineKeyboardButton(text="✦ Solo · 1 устройство", callback_data="dev_1"))
    kb.row(InlineKeyboardButton(text="✦ Duo · 2 устройства", callback_data="dev_2"))
    kb.row(InlineKeyboardButton(text="✦ Trinity · 3 устройства", callback_data="dev_3"))
    kb.row(InlineKeyboardButton(text="✦ Family · 5 устройств", callback_data="dev_5"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    return kb.as_markup()


def period_kb(devices: int, *, back_callback: str = "back_to_devices") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for months, label in PERIOD_LABELS.items():
        price = PRICES[devices][months]
        discount = PERIOD_DISCOUNTS.get(months)
        text = f"🪐 {label} · {price} ₽"
        if discount:
            text += f" · {discount}"
        kb.row(InlineKeyboardButton(text=text, callback_data=f"period_{months}"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    return kb.as_markup()


def confirm_payment_kb(
    devices: int,
    months: int,
    *,
    price_override: int | None = None,
    has_active_promo: bool = False,
    back_callback: str = "back_to_periods",
) -> InlineKeyboardMarkup:
    price = price_override if price_override is not None else PRICES[devices][months]
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"💳 Перейти к оплате · {price} ₽",
            callback_data=f"pay_{devices}_{months}",
            style=ButtonStyle.SUCCESS,
        )
    )
    kb.row(InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promo"))
    if has_active_promo:
        kb.row(
            InlineKeyboardButton(
                text="❌ Отменить промокод",
                callback_data="cancel_open_promo",
                style=ButtonStyle.DANGER,
            )
        )
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def payment_link_kb(url: str, payment_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🔗 Открыть оплату", url=url)
    )
    kb.row(
        InlineKeyboardButton(
            text="✅ Я оплатил",
            callback_data=f"check_payment_{payment_id}",
            style=ButtonStyle.SUCCESS,
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="❌ Отменить платёж",
            callback_data=f"cancel_payment_{payment_id}",
            style=ButtonStyle.DANGER,
        )
    )
    return kb.as_markup()


def payment_resume_kb(payment_id: int, url: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if url:
        kb.row(InlineKeyboardButton(text="🔗 Вернуться к оплате", url=url))
    kb.row(
        InlineKeyboardButton(
            text="✅ Я оплатил",
            callback_data=f"check_payment_{payment_id}",
            style=ButtonStyle.SUCCESS,
        )
    )
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def profile_kb(has_subs: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_subs:
        kb.row(InlineKeyboardButton(text="📡 Мои подписки", callback_data="my_subs"))
        kb.row(InlineKeyboardButton(text="🔗 Реферальная программа", callback_data="referral"))
        kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help"))
    else:
        kb.row(
            InlineKeyboardButton(
                text="💳 Купить / продлить VPN",
                callback_data="buy",
                style=ButtonStyle.SUCCESS,
            )
        )
        kb.row(InlineKeyboardButton(text="🔗 Реферальная программа", callback_data="referral"))
        kb.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def subscription_detail_kb(sub_id: int, is_active: bool, is_expired: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if is_active:
        kb.row(InlineKeyboardButton(text="🔗 Получить ссылку", callback_data=f"get_link_{sub_id}", style=ButtonStyle.SUCCESS))
        kb.row(InlineKeyboardButton(text="🔄 Продлить текущий тариф", callback_data=f"renew_same_sub_{sub_id}"))
        kb.row(InlineKeyboardButton(text="🔄 Сменить тариф", callback_data=f"renew_change_{sub_id}"))
        kb.row(InlineKeyboardButton(text="♻️ Перевыпустить ссылку", callback_data=f"reset_reveals_{sub_id}"))
        kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data=f"install_sub_{sub_id}"))
    else:
        kb.row(InlineKeyboardButton(text="💳 Купить новую подписку", callback_data="buy", style=ButtonStyle.SUCCESS))
        if is_expired:
            kb.row(InlineKeyboardButton(text="🔄 Продлить подписку", callback_data=f"renew_same_sub_{sub_id}"))
    kb.row(InlineKeyboardButton(text="◀️ К подпискам", callback_data="my_subs"))
    return kb.as_markup()


def limit_reached_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔗 Управление ссылками", callback_data=f"manage_links_{sub_id}"))
    kb.row(InlineKeyboardButton(text="🔄 Продлить с другим лимитом", callback_data=f"renew_options_{sub_id}"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"sub_{sub_id}"))
    return kb.as_markup()


def limit_violation_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 Продлить эту подписку", callback_data=f"renew_options_{sub_id}"))
    kb.row(InlineKeyboardButton(text="📡 Открыть подписку", callback_data=f"sub_{sub_id}"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def subs_list_kb(subs: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    now = _utcnow()
    for sub in subs:
        expires_at = datetime.fromisoformat(sub["expires_at"])
        days_left = _remaining_days(expires_at, now)
        if not sub["is_active"] or expires_at <= now:
            badge = "🔴"
            right = f"Истек {expires_at.strftime('%d.%m')}"
        elif days_left <= 1:
            badge = "🔴"
            right = f"{days_left} дн."
        elif days_left <= 7:
            badge = "🟡"
            right = f"{days_left} дн."
        else:
            badge = "🟢"
            right = f"{days_left} дн."
        number = sub.get("display_no", sub["id"])
        label = f"{badge} №{number} · {sub['devices']} устр. · {right}"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"sub_{sub['id']}"))
    kb.row(InlineKeyboardButton(text="💳 Купить / продлить", callback_data="buy", style=ButtonStyle.SUCCESS))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def renew_subscriptions_kb(subs: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    now = _utcnow()
    for sub in subs:
        expires_at = datetime.fromisoformat(sub["expires_at"])
        days_left = _remaining_days(expires_at, now)
        number = sub.get("display_no", sub["id"])
        label = f"🛰️ №{number} · {sub['devices']} устр. · {days_left} дн."
        kb.row(InlineKeyboardButton(text=label, callback_data=f"renew_pick_{sub['id']}"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data="buy"))
    return kb.as_markup()


def renewal_options_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔄 Продлить текущий тариф", callback_data=f"renew_same_{sub_id}"))
    kb.row(InlineKeyboardButton(text="🔄 Сменить количество устройств", callback_data=f"renew_change_{sub_id}"))
    kb.row(InlineKeyboardButton(text="◀️ К подписке", callback_data=f"sub_{sub_id}"))
    return kb.as_markup()


def referral_kb(bot_username: str, ref_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    ref_url = f"https://t.me/{bot_username}?start=ref_{ref_code}"
    share_text = f"VpNLi: быстрый приватный VPN. Подключение за 1 минуту."
    share_url = (
        f"https://t.me/share/url?url={quote(ref_url, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    kb.row(InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def help_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data="install"))
    kb.row(InlineKeyboardButton(text="📡 Мои подписки", callback_data="my_subs"))
    kb.row(InlineKeyboardButton(text="📄 Документы", callback_data="docs"))
    kb.row(InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="support"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def documents_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📄 Публичная оферта", callback_data="doc_offer"))
    kb.row(InlineKeyboardButton(text="🔒 Политика конфиденциальности", callback_data="doc_privacy"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def install_kb(
    *,
    howto_callback: str = "howto",
    routing_callback: str = "routing_v2raytun",
    back_callback: str = "main_menu",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📋 Инструкции по платформам", callback_data=howto_callback))
    kb.row(InlineKeyboardButton(text="🔁 Маршрутизация", callback_data=routing_callback))
    kb.row(InlineKeyboardButton(text="💬 Поддержка", callback_data="support"))
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def trial_result_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📡 Мои подписки", callback_data="my_subs"))
    kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data="install"))
    kb.row(InlineKeyboardButton(text="💬 Поддержка", callback_data="support"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def howto_kb(*, back_callback: str = "install") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🤖 Android", callback_data="howto_android"),
        InlineKeyboardButton(text="🍎 iPhone / iPad", callback_data="howto_ios"),
    )
    kb.row(
        InlineKeyboardButton(text="🪟 Windows", callback_data="howto_windows"),
        InlineKeyboardButton(text="🍏 macOS", callback_data="howto_macos"),
    )
    kb.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback))
    return kb.as_markup()


def after_howto_platform_kb(sub_available: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if sub_available:
        kb.row(InlineKeyboardButton(text="📡 Открыть мои подписки", callback_data="my_subs"))
    else:
        kb.row(InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy", style=ButtonStyle.SUCCESS))
    kb.row(InlineKeyboardButton(text="◀️ Другая платформа", callback_data="howto"))
    kb.row(InlineKeyboardButton(text="💬 Нужна помощь", callback_data="support"))
    kb.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu"))
    return kb.as_markup()


def back_to_sub_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data=f"install_sub_{sub_id}"))
    kb.row(InlineKeyboardButton(text="◀️ К подписке", callback_data=f"sub_{sub_id}"))
    return kb.as_markup()


def after_reissue_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📲 Как подключиться", callback_data=f"install_sub_{sub_id}"))
    kb.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"))
    return kb.as_markup()


def admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats", style=ButtonStyle.PRIMARY))
    kb.row(
        InlineKeyboardButton(text="📣 Рассылка", callback_data="adm_broadcast"),
        InlineKeyboardButton(text="🎯 Рассылка +", callback_data="adm_broadcast_kb"),
    )
    kb.row(
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm_promo_menu"),
        InlineKeyboardButton(text="👥 Юзеры", callback_data="adm_find_user"),
    )
    kb.row(InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu"))
    return kb.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back", style=ButtonStyle.PRIMARY))
    return kb.as_markup()


def manage_links_kb(sub_id: int, clients: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for index, client in enumerate(clients, start=1):
        kb.row(
            InlineKeyboardButton(
                text=f"❌ Отключить ссылку #{index}",
                callback_data=f"deactivate_client_{sub_id}_{client['id']}",
                style=ButtonStyle.DANGER,
            )
        )
    kb.row(InlineKeyboardButton(text="◀️ К подписке", callback_data=f"sub_{sub_id}"))
    return kb.as_markup()
