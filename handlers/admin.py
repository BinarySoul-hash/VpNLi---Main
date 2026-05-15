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
    waiting_broadcast_buttons = State()
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
        "🛠 <b>Панель администратора</b>\n\n"
        "Выберите действие: аналитика, рассылка, промокоды или поиск пользователя.",
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

    # Считаем активные подписки эффективнее — через одну сводку
    all_users = await db.get_all_users()
    active_subs = 0
    trial_users = 0
    for u in all_users:
        subs = await db.get_active_subscriptions(u["id"])
        active_subs += len(subs)
        if u.get("trial_used") and not await db.has_paid_before(u["id"]):
            trial_users += 1

    # Новые юзеры за последние 7 дней
    new_week = sum(
        1 for u in all_users
        if (datetime.utcnow() - datetime.fromisoformat(u["created_at"])).days <= 7
    )

    text = (
        "📊 <b>Статистика сервиса</b>\n\n"
        f"👥 Всего пользователей: <b>{users}</b>\n"
        f"🆕 За последние 7 дней: <b>+{new_week}</b>\n\n"
        f"🟢 Активных подписок: <b>{active_subs}</b>\n"
        f"🎁 Только триал (не купили): <b>{trial_users}</b>\n\n"
        f"💰 Доходов всего: <b>{revenue['total']} ₽</b>\n"
        f"🧾 Успешных оплат: <b>{revenue['count']}</b>"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_menu_kb())


# ── Broadcast ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    await call.message.edit_text(
        "📣 <b>Рассылка</b>\n\n"
        "Отправьте сообщение, которое получат все пользователи.\n\n"
        "• Поддерживаются HTML-теги и медиафайлы\n"
        "• Сообщение отправится точно в том виде, как вы его пришлёте\n\n"
        "<i>Для отмены — вернитесь в админ-панель</i>",
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
        "📣 <b>Рассылка с кнопками</b>\n\n"
        "Пришлите одним сообщением шаблон:\n\n"
        "<code>Текст рассылки\n"
        "---\n"
        "Текст кнопки 1 | callback:buy\n"
        "Текст кнопки 2 | url:https://t.me/your_channel</code>\n\n"
        "Правила:\n"
        "• разделитель между текстом и кнопками: <code>---</code>\n"
        "• кнопка: <code>Название | callback:...</code> или <code>Название | url:...</code>\n"
        "• не более 8 кнопок\n\n"
        "<i>Для отмены — вернитесь в админ-панель</i>",
        reply_markup=kb.admin_back_kb(),
    )
    await state.set_state(AdminState.waiting_broadcast_buttons)


def _build_broadcast_keyboard(raw_rows: list[str]):
    builder = InlineKeyboardBuilder()
    parsed = 0
    for row in raw_rows:
        line = row.strip()
        if not line:
            continue
        if "|" not in line:
            raise ValueError(f"Неверный формат строки кнопки: {line}")
        label, action = [part.strip() for part in line.split("|", 1)]
        if not label or not action:
            raise ValueError(f"Неверный формат строки кнопки: {line}")
        if action.startswith("callback:"):
            cb = action.split("callback:", 1)[1].strip()
            if not cb:
                raise ValueError(f"Пустой callback: {line}")
            builder.row(InlineKeyboardButton(text=label, callback_data=cb))
        elif action.startswith("url:"):
            url = action.split("url:", 1)[1].strip()
            if not url.startswith("http://") and not url.startswith("https://"):
                raise ValueError(f"URL должен начинаться с http(s): {line}")
            builder.row(InlineKeyboardButton(text=label, url=url))
        else:
            raise ValueError(f"Действие должно быть callback: или url: {line}")
        parsed += 1
        if parsed > 8:
            raise ValueError("Слишком много кнопок (максимум 8).")
    if parsed == 0:
        raise ValueError("Не найдено ни одной кнопки.")
    return builder.as_markup()


@router.message(AdminState.waiting_broadcast_buttons)
async def adm_broadcast_with_buttons_send(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    raw = (message.text or "").strip()
    if not raw or "---" not in raw:
        await message.answer("❌ Формат неверный. Нужен разделитель <code>---</code> между текстом и кнопками.")
        return

    text_part, buttons_part = raw.split("---", 1)
    text_part = text_part.strip()
    button_rows = [line for line in buttons_part.strip().splitlines() if line.strip()]
    if not text_part:
        await message.answer("❌ Текст рассылки пустой.")
        return

    try:
        reply_markup = _build_broadcast_keyboard(button_rows)
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return

    users = await db.get_all_users()
    sent = 0
    failed = 0
    status_msg = await message.answer(f"📤 Рассылка с кнопками: 0 / {len(users)}...")

    for i, user in enumerate(users):
        try:
            await bot.send_message(
                chat_id=user["tg_id"],
                text=text_part,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 20 == 0:
            try:
                await status_msg.edit_text(
                    f"📤 Рассылка с кнопками: {i + 1} / {len(users)}... ({sent} ✅ {failed} ❌)"
                )
            except Exception:
                logger.debug("Failed to update broadcast-with-buttons progress message", exc_info=True)
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📤 Отправлено: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
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
        "✨ <b>Создание промокода</b>\n\n"
        "<b>Шаг 1/4 — Название</b>\n\n"
        "Введите код (без пробелов, минимум 3 символа).\n"
        "Например: <code>SUMMER50</code>, <code>VPN30</code>",
        reply_markup=kb.admin_back_kb(),
    )
    await state.set_state(AdminState.waiting_promo_code)


@router.callback_query(F.data == "adm_promo_menu")
async def adm_promo_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm_create_promo"))
    builder.row(InlineKeyboardButton(text="📚 Существующие промокоды", callback_data="adm_list_promos"))
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))
    await call.message.edit_text("🎟 <b>Промокоды</b>\n\nВыберите действие:", reply_markup=builder.as_markup())


@router.callback_query(F.data == "adm_list_promos")
async def adm_list_promos(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔", show_alert=True)
        return
    promos = await db.list_promocodes(limit=100)
    if not promos:
        await call.message.edit_text("📚 Промокодов пока нет.", reply_markup=kb.admin_back_kb())
        return
    builder = InlineKeyboardBuilder()
    lines = []
    for promo in promos[:30]:
        remaining = max(0, promo["max_activations"] - promo["used_count"])
        until = promo["expires_at"][:10] if promo["expires_at"] else "без срока"
        lines.append(f"• <b>{promo['code']}</b> · {_promo_discount_text(promo['discount_value'])} · {remaining}/{promo['max_activations']} · до {until}")
        builder.row(InlineKeyboardButton(text=f"✏️ {promo['code']}", callback_data=f"adm_promo_{promo['id']}"))
    builder.row(InlineKeyboardButton(text="◀️ К промокодам", callback_data="adm_promo_menu"))
    await call.message.edit_text("📚 <b>Существующие промокоды</b>\n\n" + "\n".join(lines), reply_markup=builder.as_markup())


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
    until = promo["expires_at"][:10] if promo["expires_at"] else "без срока"
    text = (
        "🎟 <b>Промокод</b>\n\n"
        f"Код: <b>{promo['code']}</b>\n"
        f"Скидка: <b>{_promo_discount_text(promo['discount_value'])}</b>\n"
        f"Лимит: <b>{promo['max_activations']}</b>\n"
        f"Использовано: <b>{promo['used_count']}</b>\n"
        f"До: <b>{until}</b>"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ Изменить скидку", callback_data=f"adm_promo_edit_discount_{promo_id}"))
    builder.row(InlineKeyboardButton(text="✏️ Изменить лимит", callback_data=f"adm_promo_edit_limit_{promo_id}"))
    builder.row(InlineKeyboardButton(text="✏️ Изменить срок", callback_data=f"adm_promo_edit_exp_{promo_id}"))
    builder.row(InlineKeyboardButton(text="🗑 Удалить промокод", callback_data=f"adm_promo_del_{promo_id}"))
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
        lines.append(
            f"• DB <code>{user['id']}</code> · TG <code>{user['tg_id']}</code> · @{safe_username}"
        )

    builder = InlineKeyboardBuilder()
    for user in users:
        builder.row(
            InlineKeyboardButton(
                text=f"Открыть DB {user['id']} · TG {user['tg_id']}",
                callback_data=f"adm_u_{user['tg_id']}",
            )
        )

    prev_offset = max(0, safe_offset - USER_LIST_PAGE_SIZE)
    next_offset = safe_offset + USER_LIST_PAGE_SIZE
    nav_row = []
    if safe_offset > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_find_user_page_{prev_offset}")
        )
    if next_offset < total:
        nav_row.append(
            InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"adm_find_user_page_{next_offset}")
        )
    if nav_row:
        builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))

    text = (
        "🔍 <b>Пользователи</b>\n\n"
        f"Страница <b>{page}/{pages}</b> · всего <b>{total}</b>\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Можно отправить сообщение для фильтра:\n"
        "• Telegram ID\n"
        "• @username или username\n"
        "• имя/фамилию\n"
        "• реферальный код"
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
    action_kb.row(InlineKeyboardButton(text=ban_label, callback_data=f"adm_ban_{tg_id}_{ban_value}"))
    action_kb.row(InlineKeyboardButton(text="⚡ Продлить +30 дней", callback_data=f"adm_ext_{tg_id}_30"))
    action_kb.row(InlineKeyboardButton(text="➕ Выдать новую подписку", callback_data=f"adm_grant_{tg_id}"))
    action_kb.row(InlineKeyboardButton(text="🧊 Отключить все подписки", callback_data=f"adm_deact_{tg_id}"))
    action_kb.row(InlineKeyboardButton(text="🔍 К поиску", callback_data="adm_find_user"))
    action_kb.row(InlineKeyboardButton(text="🏠 Панель", callback_data="adm_back"))
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

    sub_lines = []
    for s in active[:8]:
        exp = datetime.fromisoformat(s["expires_at"])
        days_left = max(0, (exp - now).days)
        sub_lines.append(f"• #{s['id']} · {s['devices']}📱 · до {exp.strftime('%d.%m.%Y')} ({days_left} дн.)")
    if not sub_lines:
        sub_lines.append("• нет активных")

    return (
        "👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 TG ID: <code>{user['tg_id']}</code>\n"
        f"🗂 DB ID: <code>{user['id']}</code>\n"
        f"Username: @{safe_username}\n"
        f"Имя: {safe_full_name}\n"
        f"📅 Регистрация: {joined}\n"
        f"🧪 Триал использован: {'Да' if user.get('trial_used') else 'Нет'}\n"
        f"💳 Были оплаты: {'Да' if paid_before else 'Нет'}\n"
        f"🚫 Бан: {'Да ⛔' if user['is_banned'] else 'Нет'}\n\n"
        f"📡 Подписок: всего <b>{len(subs)}</b> · активных <b>{len(active)}</b>\n"
        + "\n".join(sub_lines)
        + f"\n\n👥 Рефералов приглашено: {ref_stats['invited']}"
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
        builder.row(
            InlineKeyboardButton(
                text=_user_row_label(user),
                callback_data=f"adm_u_{user['tg_id']}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ В админ-панель", callback_data="adm_back"))

    await message.answer(
        f"👥 Найдено: <b>{len(users)}</b>\nВыберите пользователя:",
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
        "🛠 <b>Панель администратора</b>\n\n"
        "Выберите действие: аналитика, рассылка, промокоды или поиск пользователя.",
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
                await xui.delete_client(sub["xui_client_id"])
            except Exception:
                logger.exception("Failed to delete xui client %s", sub["xui_client_id"])
        clients = await db.get_active_vpn_clients_for_subscription(sub["id"])
        for client in clients:
            if client.get("xui_client_id"):
                try:
                    await xui.delete_client(client["xui_client_id"])
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
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректная команда.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for devices in sorted(PRICES.keys()):
        for months in sorted(PRICES[devices].keys()):
            builder.row(
                InlineKeyboardButton(
                    text=f"{months} мес · {devices} устр",
                    callback_data=f"adm_give_{tg_id}_{months}_{devices}",
                )
            )
    builder.row(InlineKeyboardButton(text="◀️ К карточке", callback_data=f"adm_u_{tg_id}"))
    await call.message.edit_text(
        "➕ <b>Выдача новой подписки</b>\n\nВыберите готовый тариф:",
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
