"""
Admin-only handlers: statistics, broadcast, user lookup, ban.
"""
import logging
import asyncio
import html
from datetime import datetime
import aiosqlite
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.enums import ButtonStyle
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
import keyboards as kb
from config import ADMIN_IDS, INBOUND_REMARK, PRICES, XUI_INBOUND_ID
from services.xui import xui

logger = logging.getLogger(__name__)
router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_broadcast_text = State()
    waiting_broadcast_btn_label = State()
    waiting_broadcast_btn_action = State()
    waiting_user_query = State()
    waiting_promo_code = State()
    waiting_promo_discount = State()
    waiting_promo_activations = State()
    waiting_promo_expires = State()
    waiting_edit_promo_discount = State()
    waiting_edit_promo_activations = State()
    waiting_edit_promo_expires = State()


# ── Guard ──────────────────────────────────────────────────────────────────────

def admin_only(handler):
    async def wrapper(event, **kwargs):
        tg_id = event.from_user.id if hasattr(event, "from_user") else None
        if not tg_id or not is_admin(tg_id):
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Только для администраторов.", show_alert=True)
            return
        return await handler(event, **kwargs)
    return wrapper


# ── /admin ────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 <b>VpNLi Admin</b>\n\n"
        "Статистика, рассылки, промокоды, пользователи — всё здесь.",
        reply_markup=kb.admin_menu_kb(),
    )


@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return

    await call.answer("⏳ Загрузка...")

    users = await db.get_users_count()
    revenue = await db.get_revenue_stats()

    all_users = await db.get_all_users()
    active_subs = 0
    trial_users = 0
    paid_users = 0
    for u in all_users:
        subs = await db.get_active_subscriptions(u["id"])
        active_subs += len(subs)
        if u.get("trial_used") and not await db.has_paid_before(u["id"]):
            trial_users += 1
        if await db.has_paid_before(u["id"]):
            paid_users += 1

    new_week = sum(
        1 for u in all_users
        if (datetime.utcnow() - datetime.fromisoformat(u["created_at"])).days <= 7
    )
    new_day = sum(
        1 for u in all_users
        if (datetime.utcnow() - datetime.fromisoformat(u["created_at"])).days < 1
    )

    conversion = f"{paid_users / users * 100:.1f}%" if users > 0 else "0%"

    text = (
        "📊 <b>Панель управления VpNLi</b>\n"
        "\n"
        "👥 <b>Пользователи</b>\n"
        f"  Всего: <b>{users}</b>\n"
        f"  За сегодня: <b>+{new_day}</b> · за неделю: <b>+{new_week}</b>\n\n"
        "📡 <b>Подписки</b>\n"
        f"  Активных сейчас: <b>{active_subs}</b>\n"
        f"  Только триал: <b>{trial_users}</b>\n\n"
        "💳 <b>Оплаты</b>\n"
        f"  Всего: <b>{revenue['total']} ₽</b> · <b>{revenue['count']}</b> шт.\n"
        f"  Конверсия: <b>{conversion}</b>\n"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_menu_kb())


# ── Broadcast ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.message.edit_text(
        "📣 <b>Простая рассылка</b>\n"
        "\n"
        "Отправьте сообщение для рассылки.\n\n"
        "✅ Поддерживаются:\n"
        "  • Текст с HTML-тегами\n"
        "  • Фото, видео, документы, GIF\n\n"
        "⚡ Сообщение уйдёт <b>в точности</b> как вы его пришлёте.",
        reply_markup=kb.admin_back_kb(),
    )
    await state.set_state(AdminState.waiting_broadcast)


@router.message(AdminState.waiting_broadcast)
async def adm_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    users = await db.get_all_users()
    sent = 0
    failed = 0

    status_msg = await message.answer(f"📤 Рассылка: 0 / {len(users)}...")

    for i, user in enumerate(users):
        try:
            await bot.copy_message(
                chat_id=user["tg_id"],
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 20 == 0:
            try:
                await status_msg.edit_text(f"📤 Рассылка: {i + 1} / {len(users)}... ({sent} ✅ {failed} ❌)")
            except Exception:
                logger.debug("Failed to update broadcast progress message", exc_info=True)

        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📤 Отправлено: <b>{sent}</b>\n"
        f"❌ Ошибок (бот заблокирован и т.п.): <b>{failed}</b>",
        reply_markup=kb.admin_menu_kb(),
    )
    await state.clear()


@router.callback_query(F.data == "adm_broadcast_kb")
async def adm_broadcast_with_buttons_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.message.edit_text(
        "🎯 <b>Рассылка с кнопками</b>\n"
        "\n"
        "<b>Шаг 1</b> — отправьте сообщение (текст + медиа).\n\n"
        "После этого вы сможете добавить кнопки перед отправкой.",
        reply_markup=kb.admin_back_kb(),
    )
    await state.set_state(AdminState.waiting_broadcast_text)
    await state.update_data(broadcast_buttons=[], broadcast_media=None)


@router.message(AdminState.waiting_broadcast_text)
async def adm_broadcast_text_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    broadcast_text = message.text or message.caption or ""
    if not broadcast_text.strip():
        await message.answer("❌ Текст пустой. Отправьте сообщение для рассылки.")
        return

    media_type = None
    media_id = None
    if message.photo:
        media_type = "photo"
        media_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_id = message.video.file_id
    elif message.document:
        media_type = "document"
        media_id = message.document.file_id
    elif message.animation:
        media_type = "animation"
        media_id = message.animation.file_id

    raw_entities = message.entities or message.caption_entities
    serializable_entities = None
    if raw_entities:
        serializable_entities = [e.model_dump() for e in raw_entities]

    await state.update_data(
        broadcast_text=broadcast_text,
        broadcast_media_type=media_type,
        broadcast_media_id=media_id,
        broadcast_entities=serializable_entities,
        broadcast_source_chat_id=message.chat.id,
        broadcast_source_message_id=message.message_id,
    )
    buttons = []
    await _show_broadcast_preview(message, state, buttons)


async def _show_broadcast_preview(message: Message, state: FSMContext, buttons: list[dict]):
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text", "")
    media_type = data.get("broadcast_media_type")
    btn_count = len(buttons)

    media_badge = ""
    if media_type == "photo":
        media_badge = "🖼 Фото + "
    elif media_type == "video":
        media_badge = "🎬 Видео + "
    elif media_type == "document":
        media_badge = "📎 Документ + "
    elif media_type == "animation":
        media_badge = "🎞 GIF + "

    text_preview = broadcast_text[:200]
    if len(broadcast_text) > 200:
        text_preview += "..."

    preview = (
        f"🎯 <b>Предпросмотр рассылки</b>\n"
        "\n"
        f"📝 {media_badge}<b>Текст:</b>\n"
        f"{text_preview}\n\n"
    )
    if buttons:
        preview += f"🔘 <b>Кнопки</b> ({btn_count}/8):\n"
        for i, btn in enumerate(buttons, 1):
            kind = "🔗" if btn.get("type") == "url" else "📌"
            preview += f"  {i}. {kind} {btn['label']}\n"
    else:
        preview += "🔘 Без кнопок\n"

    kb_builder = InlineKeyboardBuilder()
    for btn in buttons:
        if btn.get("type") == "url":
            kb_builder.row(InlineKeyboardButton(text=f"🔗 {btn['label']}", url=btn["url"]))
        else:
            kb_builder.row(InlineKeyboardButton(text=f"📌 {btn['label']}", callback_data=btn["callback"]))

    if btn_count < 8:
        kb_builder.row(InlineKeyboardButton(text="➕ Добавить кнопку", callback_data="broadcast_add_btn", style=ButtonStyle.PRIMARY))
    kb_builder.row(InlineKeyboardButton(text="🚀 Отправить рассылку", callback_data="broadcast_send_now", style=ButtonStyle.SUCCESS))
    kb_builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back", style=ButtonStyle.DANGER))

    await message.answer(preview, reply_markup=kb_builder.as_markup())
    await state.update_data(broadcast_buttons=buttons)


@router.callback_query(F.data == "broadcast_add_btn")
async def adm_broadcast_add_btn(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "➕ <b>Добавление кнопки</b>\n\n"
        "<b>Шаг 2/3</b> — отправьте <b>текст кнопки</b>.\n\n"
        "Например: <code>Открыть канал</code> или <code>Купить VPN</code>",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel_add")
        ).as_markup(),
    )
    await state.set_state(AdminState.waiting_broadcast_btn_label)


@router.callback_query(F.data == "broadcast_cancel_add")
async def adm_broadcast_cancel_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    data = await state.get_data()
    buttons = data.get("broadcast_buttons", [])
    await call.answer()
    await _show_broadcast_preview(call.message, state, buttons)
    await state.set_state(AdminState.waiting_broadcast_text)


@router.message(AdminState.waiting_broadcast_btn_label)
async def adm_broadcast_btn_label_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    label = (message.text or "").strip()
    if not label:
        await message.answer("❌ Текст кнопки пустой. Попробуйте ещё раз.")
        return
    if len(label) > 64:
        await message.answer("❌ Текст кнопки слишком длинный (максимум 64 символа).")
        return

    await state.update_data(pending_btn_label=label)
    kb_builder = InlineKeyboardBuilder()
    kb_builder.row(
        InlineKeyboardButton(text="📌 Callback-кнопка", callback_data="broadcast_btn_type_callback"),
        InlineKeyboardButton(text="🔗 URL-кнопка", callback_data="broadcast_btn_type_url"),
    )
    kb_builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel_add"))
    await message.answer(
        f"Текст кнопки: <b>{html.escape(label)}</b>\n\n"
        "<b>Шаг 3/3</b> — выберите тип кнопки:",
        reply_markup=kb_builder.as_markup(),
    )
    await state.set_state(AdminState.waiting_broadcast_btn_action)


@router.callback_query(F.data == "broadcast_btn_type_callback")
async def adm_broadcast_btn_type_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "📌 <b>Callback-кнопка</b>\n\n"
        "Отправьте callback data (идентификатор действия).\n\n"
        "Пример: <code>buy</code>, <code>my_subs</code>, <code>support</code>",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel_add")
        ).as_markup(),
    )
    await state.update_data(pending_btn_type="callback")


@router.callback_query(F.data == "broadcast_btn_type_url")
async def adm_broadcast_btn_type_url(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🔗 <b>URL-кнопка</b>\n\n"
        "Отправьте URL-адрес.\n\n"
        "Пример: <code>https://t.me/your_channel</code>",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel_add")
        ).as_markup(),
    )
    await state.update_data(pending_btn_type="url")


@router.message(AdminState.waiting_broadcast_btn_action)
async def adm_broadcast_btn_action_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    btn_type = data.get("pending_btn_type", "callback")
    btn_label = data.get("pending_btn_label", "")
    action = (message.text or "").strip()

    if not action:
        await message.answer("❌ Действие пустое. Попробуйте ещё раз.")
        return

    if btn_type == "url":
        if not action.startswith("http://") and not action.startswith("https://"):
            await message.answer("❌ URL должен начинаться с http:// или https://")
            return
        btn = {"label": btn_label, "type": "url", "url": action}
    else:
        btn = {"label": btn_label, "type": "callback", "callback": action}

    buttons = data.get("broadcast_buttons", [])
    buttons.append(btn)
    await state.update_data(broadcast_buttons=buttons, pending_btn_label=None, pending_btn_type=None)
    await _show_broadcast_preview(message, state, buttons)
    await state.set_state(AdminState.waiting_broadcast_text)


@router.callback_query(F.data == "broadcast_send_now")
async def adm_broadcast_send_now(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text", "")
    buttons = data.get("broadcast_buttons", [])
    source_chat_id = data.get("broadcast_source_chat_id")
    source_message_id = data.get("broadcast_source_message_id")

    if not broadcast_text.strip():
        await call.answer("Текст рассылки пуст.", show_alert=True)
        return

    reply_markup = None
    if buttons:
        builder = InlineKeyboardBuilder()
        for btn in buttons:
            if btn.get("type") == "url":
                builder.row(InlineKeyboardButton(text=btn["label"], url=btn["url"]))
            else:
                builder.row(InlineKeyboardButton(text=btn["label"], callback_data=btn["callback"]))
        reply_markup = builder.as_markup()

    users = await db.get_all_users()
    sent = 0
    failed = 0
    await call.answer("🚀 Отправка...")
    status_msg = await call.message.edit_text(f"📤 Рассылка: 0 / {len(users)}...")

    for i, user in enumerate(users):
        try:
            await bot.copy_message(
                chat_id=user["tg_id"],
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                reply_markup=reply_markup,
            )
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 20 == 0:
            try:
                await status_msg.edit_text(
                    f"📤 Рассылка: {i + 1} / {len(users)}... ({sent} ✅ {failed} ❌)"
                )
            except Exception:
                logger.debug("Failed to update broadcast progress message", exc_info=True)

        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n"
        "\n"
        f"📤 Успешно: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>\n\n"
        f"{'🎉 Все доставлено!' if failed == 0 else '⚠️ Часть сообщений не доставлена (бот заблокирован и т.п.)'}",
        reply_markup=kb.admin_menu_kb(),
    )
    await state.clear()


# ── Promo codes ───────────────────────────────────────────────────────────────

def _promo_discount_text(discount_value: int) -> str:
    if discount_value > 0:
        return f"{discount_value}%"
    return f"{abs(discount_value)} ₽"


@router.callback_query(F.data == "adm_create_promo")
async def adm_create_promo_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return

    await call.message.edit_text(
        "✨ <b>Создание промокода</b>\n"
        "\n"
        "<b>Шаг 1/4</b> — Название кода\n\n"
        "Введите код (без пробелов, 3+ символов).\n"
        "Пример: <code>SUMMER50</code>, <code>VPN30</code>",
        reply_markup=kb.admin_back_kb(),
    )
    await state.set_state(AdminState.waiting_promo_code)


@router.callback_query(F.data == "adm_promo_menu")
async def adm_promo_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promos = await db.list_promocodes(limit=100)
    active_count = sum(1 for p in promos if not p.get("expires_at") or p["expires_at"] > datetime.utcnow().isoformat())
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Создать", callback_data="adm_create_promo", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="📚 Список", callback_data="adm_list_promos"),
    )
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))
    await call.message.edit_text(
        "🎟 <b>Промокоды</b>\n"
        "\n"
        f"Всего: <b>{len(promos)}</b> · Активных: <b>{active_count}</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "adm_list_promos")
async def adm_list_promos(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promos = await db.list_promocodes(limit=100)
    if not promos:
        await call.message.edit_text(
            "📚 <b>Промокоды</b>\n\nПока нет промокодов.\nСоздайте первый!",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="➕ Создать", callback_data="adm_create_promo", style=ButtonStyle.SUCCESS)
            ).row(
                InlineKeyboardButton(text="◀️ Назад", callback_data="adm_promo_menu")
            ).as_markup(),
        )
        return
    builder = InlineKeyboardBuilder()
    lines = []
    for promo in promos[:15]:
        remaining = max(0, promo["max_activations"] - promo["used_count"])
        until = promo["expires_at"][:10] if promo["expires_at"] else "∞"
        is_expired = promo["expires_at"] and promo["expires_at"] < datetime.utcnow().isoformat()
        badge = "🔴" if is_expired or remaining == 0 else "🟢"
        lines.append(f"{badge} <b>{promo['code']}</b> · {_promo_discount_text(promo['discount_value'])} · {remaining}/{promo['max_activations']} · до {until}")
        builder.row(InlineKeyboardButton(text=f"{badge} {promo['code']}", callback_data=f"adm_promo_{promo['id']}"))
    builder.row(InlineKeyboardButton(text="➕ Создать", callback_data="adm_create_promo", style=ButtonStyle.SUCCESS))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm_promo_menu"))
    await call.message.edit_text(
        "📚 <b>Промокоды</b>\n"
        "\n"
        + "\n".join(lines),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.regexp(r"^adm_promo_\d+$"))
async def adm_open_promo(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        promo_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный промокод.", show_alert=True)
        return
    promo = await db.get_promocode_by_id(promo_id)
    if not promo:
        await call.answer("Промокод не найден.", show_alert=True)
        return
    until = promo["expires_at"][:10] if promo["expires_at"] else "∞"
    remaining = max(0, promo["max_activations"] - promo["used_count"])
    is_expired = promo["expires_at"] and promo["expires_at"] < datetime.utcnow().isoformat()
    badge = "🔴 Истёк/исчерпан" if is_expired or remaining == 0 else "🟢 Активен"
    progress = promo["used_count"] / promo["max_activations"] if promo["max_activations"] > 0 else 0
    bar_filled = round(progress * 10)
    bar = f"{'▓' * bar_filled}{'░' * (10 - bar_filled)} {int(progress * 100)}%"

    text = (
        f"🎟 <b>{promo['code']}</b>\n"
        "\n"
        f"Статус: {badge}\n"
        f"💸 Скидка: <b>{_promo_discount_text(promo['discount_value'])}</b>\n"
        f"📅 До: <b>{until}</b>\n\n"
        f"🔢 Использовано: <b>{promo['used_count']}/{promo['max_activations']}</b>\n"
        f"▸ {bar}"
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💸 Скидка", callback_data=f"adm_promo_edit_discount_{promo_id}"),
        InlineKeyboardButton(text="🔢 Лимит", callback_data=f"adm_promo_edit_limit_{promo_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 Срок", callback_data=f"adm_promo_edit_exp_{promo_id}"),
    )
    builder.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"adm_promo_del_{promo_id}", style=ButtonStyle.DANGER))
    builder.row(InlineKeyboardButton(text="◀️ К списку", callback_data="adm_list_promos"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())


@router.message(AdminState.waiting_promo_code)
async def adm_create_promo_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    code = (message.text or "").strip().upper()
    if not code or " " in code or len(code) < 3:
        await message.answer(
            "❌ Без пробелов, минимум 3 символа.\n"
            "Пример: <code>SUMMER50</code>"
        )
        return

    await state.update_data(promo_code=code)
    await message.answer(
        f"✅ Код: <b>{code}</b>\n\n"
        "<b>Шаг 2/4 — Скидка</b>\n\n"
        "Введите размер скидки:\n"
        "• Процент: <code>30</code> → скидка 30%\n"
        "• Фиксированно: <code>-150</code> → скидка 150 ₽"
    )
    await state.set_state(AdminState.waiting_promo_discount)


@router.message(AdminState.waiting_promo_discount)
async def adm_create_promo_discount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    raw = (message.text or "").strip()
    try:
        discount_value = int(raw)
    except ValueError:
        await message.answer("❌ Введите целое число. Пример: <code>30</code> или <code>-150</code>.")
        return

    if discount_value == 0:
        await message.answer("❌ Скидка не может быть 0.")
        return
    if discount_value > 95:
        await message.answer("❌ Процентная скидка: от 1 до 95.")
        return
    if discount_value < -100000:
        await message.answer("❌ Слишком большая фиксированная скидка.")
        return

    await state.update_data(discount_value=discount_value)
    await message.answer(
        f"✅ Скидка: <b>{_promo_discount_text(discount_value)}</b>\n\n"
        "<b>Шаг 3/4 — Лимит активаций</b>\n\n"
        "Сколько раз можно активировать? Например: <code>50</code>"
    )
    await state.set_state(AdminState.waiting_promo_activations)


@router.message(AdminState.waiting_promo_activations)
async def adm_create_promo_activations(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    raw = (message.text or "").strip()
    try:
        max_activations = int(raw)
    except ValueError:
        await message.answer("❌ Введите целое число активаций.")
        return

    if max_activations <= 0:
        await message.answer("❌ Количество активаций должно быть больше 0.")
        return
    if max_activations > 1000000:
        await message.answer("❌ Слишком большое количество активаций.")
        return

    await state.update_data(max_activations=max_activations)
    await message.answer(
        f"✅ Лимит: <b>{max_activations}</b> активаций\n\n"
        "<b>Шаг 4/4 — Срок действия</b>\n\n"
        "Введите дату окончания в формате <code>ДД.ММ.ГГГГ</code>\n"
        "или напишите <code>без срока</code>"
    )
    await state.set_state(AdminState.waiting_promo_expires)


@router.message(AdminState.waiting_promo_expires)
async def adm_create_promo_expires(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    raw = (message.text or "").strip()
    expires_at = None
    expires_label = "без срока"
    if raw.lower() not in {"без срока", "безсрока", "none", "-"}:
        try:
            exp_dt = datetime.strptime(raw, "%d.%m.%Y")
            exp_dt = exp_dt.replace(hour=23, minute=59, second=59)
            expires_at = exp_dt.isoformat()
            expires_label = exp_dt.strftime("%d.%m.%Y")
        except ValueError:
            await message.answer("❌ Формат даты: <code>ДД.ММ.ГГГГ</code> или <code>без срока</code>.")
            return

    data = await state.get_data()
    code = data["promo_code"]
    discount_value = data["discount_value"]
    max_activations = data["max_activations"]

    try:
        promo = await db.create_promocode(
            code=code,
            discount_value=discount_value,
            max_activations=max_activations,
            expires_at=expires_at,
        )
    except aiosqlite.IntegrityError:
        await message.answer(
            f"❌ Промокод <b>{code}</b> уже существует. Используй другое название."
        )
        return

    await message.answer(
        f"✅ <b>Промокод создан!</b>\n\n"
        f"🏷 Код: <code>{promo['code']}</code>\n"
        f"💸 Скидка: <b>{_promo_discount_text(promo['discount_value'])}</b>\n"
        f"🔢 Активаций: <b>{promo['max_activations']}</b>\n"
        f"📅 До: <b>{expires_label}</b>\n\n"
        f"Промокод готов к использованию!",
        reply_markup=kb.admin_menu_kb(),
    )
    await state.clear()


@router.callback_query(F.data.startswith("adm_promo_del_"))
async def adm_delete_promo(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        promo_id = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный промокод.", show_alert=True)
        return
    deleted = await db.delete_promocode(promo_id)
    await call.answer("Удалено." if deleted else "Не найдено.", show_alert=True)
    await adm_list_promos(call)


@router.callback_query(F.data.startswith("adm_promo_edit_discount_"))
async def adm_edit_promo_discount_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promo_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(edit_promo_id=promo_id)
    await state.set_state(AdminState.waiting_edit_promo_discount)
    await call.message.edit_text("Введите новую скидку: <code>30</code> или <code>-150</code>", reply_markup=kb.admin_back_kb())


@router.message(AdminState.waiting_edit_promo_discount)
async def adm_edit_promo_discount_apply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        discount_value = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    promo_id = int((await state.get_data())["edit_promo_id"])
    await db.update_promocode_conditions(promo_id, discount_value=discount_value)
    await state.clear()
    await message.answer("Скидка обновлена.", reply_markup=kb.admin_menu_kb())


@router.callback_query(F.data.startswith("adm_promo_edit_limit_"))
async def adm_edit_promo_limit_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promo_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(edit_promo_id=promo_id)
    await state.set_state(AdminState.waiting_edit_promo_activations)
    await call.message.edit_text("Введите новый лимит активаций.", reply_markup=kb.admin_back_kb())


@router.message(AdminState.waiting_edit_promo_activations)
async def adm_edit_promo_limit_apply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        limit = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите целое число.")
        return
    promo_id = int((await state.get_data())["edit_promo_id"])
    await db.update_promocode_conditions(promo_id, max_activations=limit)
    await state.clear()
    await message.answer("Лимит обновлён.", reply_markup=kb.admin_menu_kb())


@router.callback_query(F.data.startswith("adm_promo_edit_exp_"))
async def adm_edit_promo_exp_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promo_id = int(call.data.rsplit("_", 1)[1])
    await state.update_data(edit_promo_id=promo_id)
    await state.set_state(AdminState.waiting_edit_promo_expires)
    await call.message.edit_text(
        "Введите новую дату <code>ДД.ММ.ГГГГ</code> или <code>без срока</code>.",
        reply_markup=kb.admin_back_kb(),
    )


@router.message(AdminState.waiting_edit_promo_expires)
async def adm_edit_promo_exp_apply(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    expires_at = None
    if raw.lower() not in {"без срока", "безсрока", "none", "-"}:
        try:
            exp_dt = datetime.strptime(raw, "%d.%m.%Y").replace(hour=23, minute=59, second=59)
            expires_at = exp_dt.isoformat()
        except ValueError:
            await message.answer("Неверный формат даты.")
            return
    promo_id = int((await state.get_data())["edit_promo_id"])
    await db.update_promocode_conditions(promo_id, expires_at=expires_at)
    await state.clear()
    await message.answer("Срок обновлён.", reply_markup=kb.admin_menu_kb())


# ── Find user ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_find_user")
async def adm_find_user_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await _render_users_list(call, offset=0)
    await state.set_state(AdminState.waiting_user_query)


async def _render_users_list(target: CallbackQuery, offset: int = 0) -> None:
    total = await db.get_users_count()
    safe_offset = max(0, offset)
    users = await db.get_users_page(offset=safe_offset, limit=USER_LIST_PAGE_SIZE)

    if not users:
        await target.message.edit_text(
            "👥 <b>Пользователи</b>\n\nСписок пуст.",
            reply_markup=kb.admin_back_kb(),
        )
        return

    page = (safe_offset // USER_LIST_PAGE_SIZE) + 1
    pages = max(1, (total + USER_LIST_PAGE_SIZE - 1) // USER_LIST_PAGE_SIZE)
    lines = []
    for user in users:
        username = user.get("username") or "—"
        safe_username = html.escape(username)
        marker = " ⛔" if user.get("is_banned") else ""
        lines.append(
            f"• @{safe_username} <code>{user['tg_id']}</code>{marker}"
        )

    builder = InlineKeyboardBuilder()
    for user in users:
        username = user.get("username") or "—"
        marker = " ⛔" if user.get("is_banned") else ""
        builder.row(
            InlineKeyboardButton(
                text=f"@{username}{marker} · {user['tg_id']}",
                callback_data=f"adm_u_{user['tg_id']}",
            )
        )

    nav_row = []
    if safe_offset > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"adm_find_user_page_{prev_offset}")
        )
    nav_row.append(
        InlineKeyboardButton(text=f"📄 {page}/{pages}", callback_data="adm_find_user_page_0")
    )
    if safe_offset + USER_LIST_PAGE_SIZE < total:
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"adm_find_user_page_{safe_offset + USER_LIST_PAGE_SIZE}")
        )
    if len(nav_row) > 1:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))

    text = (
        "👥 <b>Пользователи</b>\n\n"
        f"Всего: <b>{total}</b> · Стр. <b>{page}/{pages}</b>\n\n"
        + "\n".join(lines)
        + "\n\n💬 Или отправьте для поиска:\nTG ID · @username · имя · реферальный код"
    )
    await target.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("adm_find_user_page_"))
async def adm_find_user_page(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        offset = int(call.data.rsplit("_", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return
    await _render_users_list(call, offset=offset)
    await state.set_state(AdminState.waiting_user_query)


def _user_row_label(user: dict) -> str:
    username = user.get("username") or "—"
    marker = " ⛔" if user.get("is_banned") else ""
    return f"{user['tg_id']} · @{username}{marker}"


def _user_actions_kb(tg_id: int, is_banned: bool):
    action_kb = InlineKeyboardBuilder()
    ban_label = "🔓 Разбанить" if is_banned else "🚫 Забанить"
    ban_value = 0 if is_banned else 1
    ban_style = ButtonStyle.SUCCESS if is_banned else ButtonStyle.DANGER
    action_kb.row(InlineKeyboardButton(text=ban_label, callback_data=f"adm_ban_{tg_id}_{ban_value}", style=ban_style))
    action_kb.row(
        InlineKeyboardButton(text="⚡ Продлить +30 дн.", callback_data=f"adm_ext_{tg_id}_30"),
        InlineKeyboardButton(text="➕ Выдать подписку", callback_data=f"adm_grant_{tg_id}"),
    )
    action_kb.row(InlineKeyboardButton(text="🧊 Отключить все подписки", callback_data=f"adm_deact_{tg_id}", style=ButtonStyle.DANGER))
    action_kb.row(InlineKeyboardButton(text="◀️ К поиску", callback_data="adm_find_user"))
    return action_kb.as_markup()


async def _user_card_text(user: dict) -> str:
    subs = await db.get_all_subscriptions(user["id"])
    ref_stats = await db.get_referral_stats(user["id"])
    paid_before = await db.has_paid_before(user["id"])

    active = []
    now = datetime.utcnow()
    for s in subs:
        if s["is_active"] and datetime.fromisoformat(s["expires_at"]) > now:
            active.append(s)

    joined = datetime.fromisoformat(user["created_at"]).strftime("%d.%m.%Y")
    safe_username = html.escape(user.get("username") or "—")
    safe_full_name = html.escape(user.get("full_name") or "—")

    status = "⛔ Забанен" if user["is_banned"] else "🟢 Активен"
    trial = "✅ Использован" if user.get("trial_used") else "❌ Не использован"
    paid = "✅ Да" if paid_before else "❌ Нет"

    sub_lines = []
    for s in active[:5]:
        exp = datetime.fromisoformat(s["expires_at"])
        days_left = max(0, (exp - now).days)
        badge = "🟢" if days_left > 7 else "🟡" if days_left > 1 else "🔴"
        sub_lines.append(f"  {badge} №{s['id']} · {s['devices']}📱 · {days_left} дн. · до {exp.strftime('%d.%m')}")
    if not sub_lines:
        sub_lines.append("  ⚫ Нет активных")

    return (
        f"👤 <b>{safe_full_name}</b>\n"
        f"@{safe_username}\n"
        "\n"
        f"🆔 <code>{user['tg_id']}</code>\n"
        f"📅 Регистрация: {joined}\n"
        f"Статус: {status}\n\n"
        f"🧪 Триал: {trial}\n"
        f"💳 Оплаты: {paid}\n\n"
        f"📡 <b>Подписки</b> ({len(active)} актив.)\n"
        + "\n".join(sub_lines) + "\n\n"
        f"👥 Рефералов: <b>{ref_stats['invited']}</b>"
    )


async def _show_user_card(target: Message | CallbackQuery, tg_id: int) -> None:
    user = await db.get_user(tg_id)
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("Пользователь не найден.", show_alert=True)
        else:
            await target.answer("❌ Пользователь не найден.")
        return

    text = await _user_card_text(user)
    markup = _user_actions_kb(user["tg_id"], bool(user["is_banned"]))
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(AdminState.waiting_user_query)
async def adm_find_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    query = (message.text or "").strip()
    if not query:
        await message.answer("❌ Пустой запрос.")
        return

    users = await db.search_users(query, limit=20)
    if not users:
        await message.answer("❌ Никого не найдено. Попробуйте другой запрос.")
        return

    if len(users) == 1:
        await _show_user_card(message, int(users[0]["tg_id"]))
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    for user in users:
        username = user.get("username") or "—"
        marker = " ⛔" if user.get("is_banned") else ""
        builder.row(
            InlineKeyboardButton(
                text=f"@{username}{marker} · {user['tg_id']}",
                callback_data=f"adm_u_{user['tg_id']}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))

    await message.answer(
        f"👥 <b>Найдено: {len(users)}</b>\n"
        "\n"
        "Выберите пользователя:",
        reply_markup=builder.as_markup(),
    )
    await state.clear()


@router.callback_query(F.data.startswith("adm_u_"))
async def adm_user_open(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        tg_id = int(call.data.split("_", 2)[2])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная команда.", show_alert=True)
        return
    await _show_user_card(call, tg_id)


@router.callback_query(F.data == "adm_back")
async def adm_back(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.message.edit_text(
        "🛠 <b>VpNLi Admin</b>\n\n"
        "Статистика, рассылки, промокоды, пользователи — всё здесь.",
        reply_markup=kb.admin_menu_kb(),
    )


@router.callback_query(F.data.startswith("adm_ban_"))
async def adm_ban_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        _, _, tg_id_raw, ban_raw = call.data.split("_", 3)
        tg_id = int(tg_id_raw)
        ban_val = bool(int(ban_raw))
    except (TypeError, ValueError):
        await call.answer("Некорректная команда.", show_alert=True)
        return
    if tg_id == call.from_user.id:
        await call.answer("Нельзя заблокировать самого себя.", show_alert=True)
        return
    if is_admin(tg_id):
        await call.answer("Нельзя заблокировать другого администратора.", show_alert=True)
        return

    await db.ban_user(tg_id, ban_val)
    action = "заблокирован ⛔" if ban_val else "разблокирован ✅"
    await call.answer(f"Пользователь {action}.", show_alert=True)
    await _show_user_card(call, tg_id)


@router.callback_query(F.data.startswith("adm_ext_"))
async def adm_extend_user_subscription(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        _, _, tg_id_raw, days_raw = call.data.split("_", 3)
        tg_id = int(tg_id_raw)
        days = int(days_raw)
    except (TypeError, ValueError):
        await call.answer("Некорректная команда.", show_alert=True)
        return

    user = await db.get_user(tg_id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return

    updated = await db.extend_latest_active_subscription(user["id"], days)
    if not updated:
        await call.answer("Нет активной подписки для продления.", show_alert=True)
        return

    await call.answer(f"Добавлено +{days} дней.", show_alert=True)
    await _show_user_card(call, tg_id)


@router.callback_query(F.data.startswith("adm_deact_"))
async def adm_deactivate_all_user_subscriptions(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        tg_id = int(call.data.split("_", 2)[2])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная команда.", show_alert=True)
        return

    user = await db.get_user(tg_id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return

    subs = await db.get_active_subscriptions(user["id"])
    if not subs:
        await call.answer("Активных подписок нет.", show_alert=True)
        return

    for sub in subs:
        if sub.get("xui_client_id"):
            try:
                await xui.delete_client(sub["xui_client_id"], email=sub.get("email"))
            except Exception:
                logger.exception("Failed to delete xui client %s", sub["xui_client_id"])
        clients = await db.get_active_vpn_clients_for_subscription(sub["id"])
        for client in clients:
            if client.get("xui_client_id"):
                try:
                    await xui.delete_client(client["xui_client_id"], email=client.get("email"))
                except Exception:
                    logger.exception("Failed to delete legacy xui client %s", client["xui_client_id"])
                await db.deactivate_vpn_client(client["xui_client_id"])
        await db.deactivate_subscription(sub["id"])

    await call.answer("Все активные подписки отключены.", show_alert=True)
    await _show_user_card(call, tg_id)


@router.callback_query(F.data.startswith("adm_grant_"))
async def adm_grant_subscription_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    try:
        tg_id = int(call.data.split("_", 2)[2])
    except (TypeError, ValueError):
        await call.answer("Некорректная команда.", show_alert=True)
        return

    user = await db.get_user(tg_id)
    username = user.get("username") or "—" if user else "—"

    builder = InlineKeyboardBuilder()
    for devices in sorted(PRICES.keys()):
        row_buttons = []
        for months in sorted(PRICES[devices].keys()):
            price = PRICES[devices][months]
            row_buttons.append(
                InlineKeyboardButton(
                    text=f"{months}м · {devices}📱 · {price}₽",
                    callback_data=f"adm_give_{tg_id}_{months}_{devices}",
                )
            )
            if len(row_buttons) == 2:
                builder.row(*row_buttons)
                row_buttons = []
        if row_buttons:
            builder.row(*row_buttons)
    builder.row(InlineKeyboardButton(text="◀️ К карточке", callback_data=f"adm_u_{tg_id}"))
    await call.message.edit_text(
        f"➕ <b>Выдать подписку</b>\n"
        f"@{html.escape(username)} · <code>{tg_id}</code>\n"
        "\n"
        "Выберите тариф:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("adm_give_"))
async def adm_grant_subscription(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return

    try:
        _, _, tg_id_raw, months_raw, devices_raw = call.data.split("_", 4)
        tg_id = int(tg_id_raw)
        months = int(months_raw)
        devices = int(devices_raw)
    except (TypeError, ValueError):
        await call.answer("Некорректная команда.", show_alert=True)
        return

    user = await db.get_user(tg_id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return

    await call.answer("Создаю подписку...")
    try:
        await xui.set_inbound_remark(XUI_INBOUND_ID, INBOUND_REMARK)
    except Exception:
        logger.exception("Failed to sync inbound remark before admin grant")

    created = await xui.add_client(
        inbound_id=XUI_INBOUND_ID,
        email=xui.build_client_email(),
        devices=devices,
        expire_days=months * 30,
    )
    if not created or not created.get("subscription_url"):
        await call.answer("Не удалось выдать подписку (ошибка XUI).", show_alert=True)
        return

    sub = await db.create_subscription(
        user_id=user["id"],
        xui_client_id=created["client_id"],
        inbound_id=XUI_INBOUND_ID,
        devices=devices,
        months=months,
        vless_key=created.get("vless_key") or "",
        days=months * 30,
        subscription_url=created["subscription_url"],
        subscription_id=created["subscription_id"],
        email=created["email"],
    )

    try:
        expires = datetime.fromisoformat(sub["expires_at"]).strftime("%d.%m.%Y")
        await bot.send_message(
            user["tg_id"],
            (
                "🎁 <b>Вам выдана подписка администратором</b>\n\n"
                f"Тариф: <b>{months} мес.</b> · <b>{devices} устр.</b>\n"
                f"Действует до: <b>{expires}</b>\n\n"
                f"🔗 Ссылка:\n<code>{html.escape(sub.get('subscription_url') or '')}</code>"
            ),
        )
    except Exception:
        logger.exception("Failed to notify user %s about admin grant", user["tg_id"])

    await call.answer("Подписка выдана ✅", show_alert=True)
    await _show_user_card(call, tg_id)
USER_LIST_PAGE_SIZE = 20
