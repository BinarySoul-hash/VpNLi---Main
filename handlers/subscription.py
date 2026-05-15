"""
Handlers: buying, renewing, trial, and subscription management.
"""
from __future__ import annotations

import html
import logging
import math
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as db
import keyboards as kb
import texts
from config import ADMIN_IDS, INBOUND_REMARK, PRICES, TRIAL_DAYS, TRIAL_TRAFFIC_GB, XUI_INBOUND_ID
from decorators import inject_db_user
from services.xui import xui

logger = logging.getLogger(__name__)
router = Router()


class BuyState(StatesGroup):
    waiting_promo_code = State()


def _promo_discount_label(discount_value: int) -> str:
    if discount_value > 0:
        return f"{discount_value}%"
    return f"{abs(discount_value)} ₽"


async def _trial_available(db_user: dict) -> bool:
    return await db.is_trial_available(db_user["id"])


async def _get_order_pricing(user_id: int, devices: int, months: int) -> dict:
    base_price = PRICES[devices][months]
    open_usage = await db.get_open_promocode_usage(user_id)
    if not open_usage:
        return {"base_price": base_price, "final_price": base_price, "promo_label": None}

    discount_amount, final_price = db.calculate_discount(base_price, open_usage["discount_value"])
    if discount_amount <= 0:
        return {"base_price": base_price, "final_price": base_price, "promo_label": None}

    return {
        "base_price": base_price,
        "final_price": final_price,
        "promo_label": f"{open_usage['code']} (−{_promo_discount_label(open_usage['discount_value'])})",
    }


async def _show_text(
    message: Message,
    text: str,
    markup,
    *,
    edit: bool = True,
    disable_web_page_preview: bool = True,
) -> None:
    if edit:
        try:
            await message.edit_text(
                text,
                reply_markup=markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
        except TelegramBadRequest:
            logger.debug("Message edit failed, falling back to send_new_message", exc_info=True)

    await message.answer(
        text,
        reply_markup=markup,
        disable_web_page_preview=disable_web_page_preview,
    )


async def _show_buy_root(
    target: CallbackQuery | Message,
    state: FSMContext,
    db_user: dict,
    *,
    force_new_message: bool = False,
) -> None:
    await state.clear()
    active_subs = await db.get_active_subscriptions(db_user["id"])
    trial_available = await _trial_available(db_user)

    if active_subs:
        text = texts.buy_hub(active_subs)
        markup = kb.buy_hub_kb(trial_available=trial_available)
        edit = isinstance(target, CallbackQuery) and not force_new_message
        await _show_text(
            target.message if isinstance(target, CallbackQuery) else target,
            text,
            markup,
            edit=edit,
        )
        return

    await state.update_data(
        flow="new",
        target_sub_id=None,
        current_devices=None,
        devices=None,
        months=None,
        period_back_callback="main_menu",
    )
    text = texts.choose_devices(mode="new")
    markup = kb.devices_kb(trial_available=trial_available, back_callback="main_menu")
    edit = isinstance(target, CallbackQuery) and not force_new_message
    await _show_text(
        target.message if isinstance(target, CallbackQuery) else target,
        text,
        markup,
        edit=edit,
    )


async def _show_new_purchase_flow(
    message: Message,
    state: FSMContext,
    db_user: dict,
    *,
    back_callback: str,
    edit: bool = True,
) -> None:
    await state.clear()
    await state.update_data(
        flow="new",
        target_sub_id=None,
        current_devices=None,
        devices=None,
        months=None,
        period_back_callback=back_callback,
    )
    await _show_text(
        message,
        texts.choose_devices(mode="new"),
        kb.devices_kb(
            trial_available=await _trial_available(db_user),
            back_callback=back_callback,
        ),
        edit=edit,
    )


async def _show_periods(
    message: Message,
    state: FSMContext,
    *,
    devices: int,
    flow: str,
    current_devices: int | None = None,
    back_callback: str,
    edit: bool = True,
) -> None:
    await state.update_data(
        devices=devices,
        months=None,
        period_back_callback=back_callback,
    )
    await _show_text(
        message,
        texts.choose_period(
            devices,
            flow=flow,
            current_devices=current_devices,
        ),
        kb.period_kb(devices, back_callback="back_to_devices"),
        edit=edit,
    )


async def _render_order_confirmation(
    message: Message,
    state: FSMContext,
    db_user: dict,
    *,
    edit: bool = True,
) -> None:
    data = await state.get_data()
    devices = data.get("devices")
    months = data.get("months")
    if not devices or not months:
        return

    pricing = await _get_order_pricing(db_user["id"], devices, months)
    flow = data.get("flow", "new")
    target_sub = None
    if flow == "renew" and data.get("target_sub_id"):
        target_sub = await db.get_subscription_by_id(data["target_sub_id"])

    text = texts.confirm_order(
        devices,
        months,
        final_price=pricing["final_price"],
        promo_label=pricing["promo_label"],
        flow=flow,
        renewal_target=target_sub,
        current_devices=data.get("current_devices"),
    )
    markup = kb.confirm_payment_kb(
        devices,
        months,
        price_override=pricing["final_price"],
        has_active_promo=bool(pricing["promo_label"]),
        back_callback="back_to_periods",
    )
    await _show_text(message, text, markup, edit=edit)


async def _ensure_subscription_link(sub: dict) -> dict | None:
    if not await xui.login():
        return None

    try:
        await xui.set_inbound_remark(sub["inbound_id"], INBOUND_REMARK)
    except Exception:
        logger.exception("Failed to set inbound remark for sub %s", sub["id"])

    if sub.get("subscription_url") and sub.get("email") and sub.get("xui_client_id"):
        synced = await xui.sync_subscription_client(
            sub["inbound_id"],
            client_id=sub["xui_client_id"],
            email=sub["email"],
            limit_ip=sub["devices"],
            expires_at=sub["expires_at"],
            enabled=True,
        )
        if synced:
            return await db.get_subscription_by_id(sub["id"]) or sub

    expires_at = datetime.fromisoformat(sub["expires_at"])
    remaining_seconds = max(0, (expires_at - datetime.utcnow()).total_seconds())
    expire_days = max(1, math.ceil(remaining_seconds / 86400))
    new_email = xui.build_client_email()
    created = await xui.add_client(
        inbound_id=sub["inbound_id"],
        email=new_email,
        devices=sub["devices"],
        expire_days=expire_days,
    )
    if not created or not created.get("subscription_url"):
        return None

    updated_sub = await db.update_subscription_credentials(
        sub["id"],
        xui_client_id=created["client_id"],
        subscription_id=created["subscription_id"],
        subscription_url=created["subscription_url"],
        email=created["email"],
    )
    if not updated_sub:
        return None

    old_client_id = sub.get("xui_client_id")
    if old_client_id and old_client_id != created["client_id"]:
        try:
            await xui.del_client(sub["inbound_id"], old_client_id)
        except Exception:
            logger.exception("Failed to delete old client %s during migration", old_client_id)

    try:
        old_clients = await db.get_active_vpn_clients_for_subscription(sub["id"])
        for client in old_clients:
            cid = client.get("xui_client_id")
            if cid:
                await xui.delete_client(cid)
                await db.deactivate_vpn_client(cid)
    except Exception:
        logger.exception("Failed to cleanup legacy vpn_clients for sub %s", sub["id"])

    return updated_sub


@router.callback_query(F.data == "buy")
@router.message(Command("buy"))
@inject_db_user
async def start_buy(
    event: CallbackQuery | Message,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    if isinstance(event, CallbackQuery):
        await event.answer()
    await _show_buy_root(event, state, db_user)


@router.callback_query(F.data == "trial")
@inject_db_user
async def activate_trial(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    if not await db.is_trial_available(db_user["id"]):
        await call.answer("Пробный период уже использован.", show_alert=True)
        return
    claimed = await db.claim_trial_slot(db_user["id"])
    if not claimed:
        await call.answer("Пробный период уже использован.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text("⏳ Открываем пробный доступ...")

    if not await xui.login():
        await db.finalize_trial_slot(db_user["id"], success=False)
        await call.message.edit_text(
            "❌ Не удалось связаться с VPN-панелью. Попробуйте чуть позже.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    try:
        await xui.set_inbound_remark(XUI_INBOUND_ID, INBOUND_REMARK)
    except Exception:
        logger.exception("Failed to update inbound remark for trial")

    email = xui.build_client_email()
    created = await xui.add_client(
        inbound_id=XUI_INBOUND_ID,
        email=email,
        devices=1,
        expire_days=TRIAL_DAYS,
        traffic_gb=TRIAL_TRAFFIC_GB,
    )
    if not created or not created.get("subscription_url"):
        await db.finalize_trial_slot(db_user["id"], success=False)
        await call.message.edit_text(
            "❌ Не удалось создать тестовый доступ. Попробуйте позже или напишите в поддержку.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    try:
        sub = await db.create_subscription(
            user_id=db_user["id"],
            xui_client_id=created["client_id"],
            inbound_id=XUI_INBOUND_ID,
            devices=1,
            months=0,
            vless_key=created.get("vless_key") or "",
            days=TRIAL_DAYS,
            subscription_url=created.get("subscription_url"),
            subscription_id=created.get("subscription_id"),
            email=created.get("email") or email,
        )
    except Exception:
        await db.finalize_trial_slot(db_user["id"], success=False)
        raise

    await db.finalize_trial_slot(db_user["id"], success=True)
    await state.clear()

    await call.message.edit_text(
        texts.trial_activated(sub["expires_at"], sub.get("subscription_url")),
        reply_markup=kb.trial_result_kb(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "buy_new")
@inject_db_user
async def buy_new(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    await call.answer()
    await state.update_data(
        flow="new",
        target_sub_id=None,
        current_devices=None,
        devices=None,
        months=None,
        period_back_callback="buy",
    )
    await _show_text(
        call.message,
        texts.choose_devices(mode="new"),
        kb.devices_kb(
            trial_available=await _trial_available(db_user),
            back_callback="buy",
        ),
    )


@router.callback_query(F.data == "buy_renew_list")
@inject_db_user
async def buy_renew_list(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    active_subs = await db.get_active_subscriptions(db_user["id"])
    active_subs = await _attach_display_numbers(db_user["id"], active_subs)
    renewable_subs = [sub for sub in active_subs if sub.get("months", 0) > 0]
    if not renewable_subs:
        message = (
            "Пробный доступ продлить нельзя. Покажу оформление новой подписки."
            if active_subs
            else "Активных подписок сейчас нет. Покажу новую покупку."
        )
        await call.answer(message, show_alert=True)
        await _show_new_purchase_flow(
            call.message,
            state,
            db_user,
            back_callback="buy",
        )
        return

    await call.answer()
    await call.message.edit_text(
        texts.renew_subscriptions_intro(renewable_subs),
        reply_markup=kb.renew_subscriptions_kb(renewable_subs),
    )


@router.callback_query(F.data.startswith("renew_pick_"))
@router.callback_query(F.data.startswith("renew_options_"))
@inject_db_user
async def renew_pick(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        parts = call.data.split("_")
        sub_id = int(parts[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"] or not sub["is_active"]:
        await call.answer("Подписка не найдена или уже неактивна.", show_alert=True)
        return
    sub = await _attach_display_number(db_user["id"], sub)
    if sub.get("months") == 0:
        await call.answer("Пробный доступ продлить нельзя. Оформите новую подписку.", show_alert=True)
        await _show_new_purchase_flow(
            call.message,
            state,
            db_user,
            back_callback="buy",
        )
        return

    await call.answer()
    await call.message.edit_text(
        texts.renewal_options(sub),
        reply_markup=kb.renewal_options_kb(sub_id),
    )


async def _start_renew_same_flow(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict,
    *,
    source_prefix: str,
    back_callback_factory,
) -> None:
    try:
        sub_id = int(call.data.split(source_prefix, 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"] or not sub["is_active"]:
        await call.answer("Подписка не найдена или уже неактивна.", show_alert=True)
        return
    sub = await _attach_display_number(db_user["id"], sub)
    if sub.get("months") == 0:
        await call.answer("Пробный доступ продлить нельзя. Оформите новую подписку.", show_alert=True)
        await _show_new_purchase_flow(
            call.message,
            state,
            db_user,
            back_callback="buy",
        )
        return

    back_callback = back_callback_factory(sub_id)

    await call.answer()
    await state.update_data(
        flow="renew",
        renewal_flow="same",
        target_sub_id=sub_id,
        current_devices=sub["devices"],
        devices=sub["devices"],
        months=None,
        period_back_callback=back_callback,
    )
    await _show_periods(
        call.message,
        state,
        devices=sub["devices"],
        flow="renew",
        current_devices=sub["devices"],
        back_callback=back_callback,
    )


@router.callback_query(F.data.startswith("renew_same_") & ~F.data.startswith("renew_same_sub_"))
@inject_db_user
async def renew_same(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    await _start_renew_same_flow(
        call,
        state,
        db_user,
        source_prefix="renew_same_",
        back_callback_factory=lambda sub_id: f"renew_options_{sub_id}",
    )


@router.callback_query(F.data.startswith("renew_same_sub_"))
@inject_db_user
async def renew_same_from_subscription(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    await _start_renew_same_flow(
        call,
        state,
        db_user,
        source_prefix="renew_same_sub_",
        back_callback_factory=lambda sub_id: f"sub_{sub_id}",
    )


@router.callback_query(F.data.startswith("renew_change_"))
@inject_db_user
async def renew_change(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        sub_id = int(call.data.split("renew_change_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"] or not sub["is_active"]:
        await call.answer("Подписка не найдена или уже неактивна.", show_alert=True)
        return
    if sub.get("months") == 0:
        await call.answer("Пробный доступ продлить нельзя. Оформите новую подписку.", show_alert=True)
        await _show_new_purchase_flow(
            call.message,
            state,
            db_user,
            back_callback="buy",
        )
        return

    await call.answer()
    await state.update_data(
        flow="renew",
        renewal_flow="change",
        target_sub_id=sub_id,
        current_devices=sub["devices"],
        devices=None,
        months=None,
        period_back_callback=f"renew_options_{sub_id}",
    )
    await _show_text(
        call.message,
        texts.choose_devices(mode="renew", current_devices=sub["devices"]),
        kb.devices_kb(trial_available=False, back_callback=f"renew_options_{sub_id}"),
    )


@router.callback_query(F.data == "back_to_devices")
@inject_db_user
async def back_to_devices(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    data = await state.get_data()
    flow = data.get("flow", "new")
    renewal_flow = data.get("renewal_flow")
    current_devices = data.get("current_devices")
    target_sub_id = data.get("target_sub_id")
    back_cb = data.get("period_back_callback") or "buy"
    trial_available = await _trial_available(db_user)

    await call.answer()
    if flow == "renew" and renewal_flow == "same" and target_sub_id:
        sub = await db.get_subscription_by_id(int(target_sub_id))
        if sub and sub["user_id"] == db_user["id"] and sub["is_active"]:
            sub = await _attach_display_number(db_user["id"], sub)
            await _show_text(
                call.message,
                texts.renewal_options(sub),
                kb.renewal_options_kb(sub["id"]),
            )
            return

    if flow == "renew" and current_devices:
        await _show_text(
            call.message,
            texts.choose_devices(mode="renew", current_devices=current_devices),
            kb.devices_kb(trial_available=False, back_callback=back_cb),
        )
    else:
        await _show_text(
            call.message,
            texts.choose_devices(mode="new"),
            kb.devices_kb(trial_available=trial_available, back_callback=back_cb),
        )


@router.callback_query(F.data.startswith("dev_"))
@inject_db_user
async def choose_devices(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        devices = int(call.data.split("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный тариф.", show_alert=True)
        return
    data = await state.get_data()
    flow = data.get("flow", "new")
    current_devices = data.get("current_devices")
    back_callback = data.get("period_back_callback") or "buy"

    await call.answer()
    await _show_periods(
        call.message,
        state,
        devices=devices,
        flow=flow,
        current_devices=current_devices,
        back_callback=back_callback,
    )


@router.callback_query(F.data.startswith("period_"))
@inject_db_user
async def choose_period(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        months = int(call.data.split("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный период.", show_alert=True)
        return
    data = await state.get_data()
    devices = data.get("devices")
    if not devices or months not in PRICES.get(devices, {}):
        await call.answer("Этот тариф больше недоступен. Выберите другой.", show_alert=True)
        return

    await state.update_data(months=months)
    await call.answer()
    await _render_order_confirmation(call.message, state, db_user)


@router.callback_query(F.data == "back_to_periods")
@inject_db_user
async def back_to_periods(
    call: CallbackQuery,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    del db_user
    data = await state.get_data()
    devices = data.get("devices")
    if not devices:
        await call.answer()
        await call.message.edit_text(
            "Сессия выбора тарифа завершилась. Показываю начало потока заново.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    await call.answer()
    await _show_periods(
        call.message,
        state,
        devices=devices,
        flow=data.get("flow", "new"),
        current_devices=data.get("current_devices"),
        back_callback=data.get("period_back_callback") or "buy",
    )


@router.callback_query(F.data == "enter_promo")
@router.message(Command("promo"))
@inject_db_user
async def enter_promo(
    event: CallbackQuery | Message,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return
    canceled_history = await db.get_canceled_promocode_history(db_user["id"])
    canceled_line = ""
    if canceled_history:
        canceled_codes = ", ".join(item["promo_code"] for item in canceled_history[:5])
        canceled_line = f"\n\n🗂 Отменённые вами промокоды: <code>{html.escape(canceled_codes)}</code>"

    prompt = (
        "🎟 <b>Промокод</b>\n"
        "Отправьте промокод одним сообщением.\n"
        "Если он подойдёт, скидка применится на этапе оплаты."
        f"{canceled_line}"
    )

    data = await state.get_data()
    back_callback = "back_to_periods" if data.get("devices") and data.get("months") else "buy"

    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(prompt, reply_markup=kb.back_kb(back_callback))
    else:
        await event.answer(prompt, reply_markup=kb.back_kb(back_callback))

    await state.set_state(BuyState.waiting_promo_code)


@router.message(BuyState.waiting_promo_code)
@inject_db_user
async def apply_promo_code(
    message: Message,
    state: FSMContext,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        await state.clear()
        command = raw_text.split()[0].split("@")[0].lower()
        if command == "/admin" and message.from_user.id in ADMIN_IDS:
            await message.answer(
                "🛠 <b>Панель администратора</b>\n\n"
                "Выберите действие: аналитика, рассылка, промокоды или поиск пользователя.",
                reply_markup=kb.admin_menu_kb(),
            )
            return
        await message.answer(
            "Ввод промокода отменён. Команда обработана в обычном режиме, повторите её ещё раз.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    if await db.is_promocode_rate_limited(db_user["id"]):
        await message.answer(
            "⏱ Слишком много попыток ввода промокода. Подождите 5 минут и попробуйте снова."
        )
        return

    code = (message.text or "").strip().upper()
    result = await db.consume_promocode(code, db_user["id"])
    await db.log_promocode_attempt(db_user["id"], success=bool(result.get("ok")))

    if not result.get("ok"):
        error = result.get("error")
        if error == "already_applied" and code and code != (result.get("code") or "").upper():
            canceled = await db.cancel_open_promocode_usage(db_user["id"])
            if canceled.get("ok"):
                result = await db.consume_promocode(code, db_user["id"])
                if result.get("ok"):
                    usage = result["usage"]
                    discount_label = _promo_discount_label(usage["discount_value"])
                    remaining = result.get("remaining", 0)
                    await message.answer(
                        "🔄 <b>Промокод заменён</b>\n"
                        f"❌ Отменён: <b>{canceled['code']}</b>\n"
                        f"✅ Новый: <b>{usage['code']}</b>\n"
                        f"💸 Скидка: <b>{discount_label}</b>\n"
                        f"🔢 Осталось активаций: <b>{remaining}</b>"
                    )
                    data = await state.get_data()
                    await state.set_state(None)
                    if data.get("devices") and data.get("months"):
                        await _render_order_confirmation(message, state, db_user, edit=False)
                    else:
                        await message.answer(
                            "Теперь просто выберите тариф, и скидка автоматически применится.",
                            reply_markup=kb.back_to_menu_kb(),
                        )
                    return
            error = result.get("error")

        error_map = {
            "empty": "⚠️ Отправьте промокод текстом, например <code>VPN30</code>.",
            "not_found": "❌ Такой промокод не найден.",
            "already_used": "⚠️ Этот промокод уже был использован с вашего аккаунта.",
            "expired": "⌛ Промокод исчерпан: срок действия уже закончился.",
            "exhausted": "😔 Промокод исчерпан: активации закончились.",
            "invalid_expiry": "❌ С промокодом что-то не так. Напишите в поддержку.",
        }
        if error == "already_applied":
            await message.answer(
                f"⚠️ У вас уже активирован промокод <b>{result.get('code')}</b>.",
                reply_markup=kb.back_to_menu_kb(),
            )
        else:
            await message.answer(error_map.get(error, "❌ Не удалось применить промокод."))
        return

    usage = result["usage"]
    discount_label = _promo_discount_label(usage["discount_value"])
    remaining = result.get("remaining", 0)

    await message.answer(
        "✨ <b>Промокод принят</b>\n"
        f"🏷 Код: <b>{usage['code']}</b>\n"
        f"💸 Скидка: <b>{discount_label}</b>\n"
        f"🔢 Осталось активаций: <b>{remaining}</b>"
    )

    data = await state.get_data()
    if data.get("devices") and data.get("months"):
        await state.set_state(None)
        await _render_order_confirmation(message, state, db_user, edit=False)
        return

    await state.set_state(None)
    await message.answer(
        "Теперь просто выберите тариф, и скидка автоматически применится.",
        reply_markup=kb.back_to_menu_kb(),
    )


@router.callback_query(F.data == "my_subs")
@inject_db_user
async def my_subscriptions(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    subs = await db.get_all_subscriptions(db_user["id"])
    subs = await _attach_display_numbers(db_user["id"], subs)
    await call.answer()
    if not subs:
        await call.message.edit_text(
            texts.my_subscriptions_empty(),
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    await call.message.edit_text(
        texts.subscriptions_list_intro(subs),
        reply_markup=kb.subs_list_kb(subs),
    )


@router.callback_query(F.data.startswith("sub_"))
@inject_db_user
async def subscription_detail(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    try:
        sub_id = int(call.data.split("_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"]:
        await call.answer("Подписка не найдена.", show_alert=True)
        return
    sub = await _attach_display_number(db_user["id"], sub)

    online_ips = None
    if sub["is_active"] and sub.get("email"):
        try:
            online_ips = await xui.get_online_ips_count(sub["email"])
        except Exception:
            logger.exception("Failed to get online count for subscription %s", sub_id)

    active_clients = await db.get_active_vpn_clients_for_subscription(sub_id)
    expires_at = datetime.fromisoformat(sub["expires_at"])
    is_expired = (expires_at <= datetime.utcnow()) or (not sub["is_active"])

    await call.answer()
    await call.message.edit_text(
        texts.subscription_info(sub, online_ips=online_ips, active_clients=len(active_clients)),
        reply_markup=kb.subscription_detail_kb(sub_id, bool(sub["is_active"]), is_expired=is_expired),
    )


@router.callback_query(F.data == "cancel_open_promo")
@inject_db_user
async def cancel_open_promo(call: CallbackQuery, state: FSMContext, db_user: dict | None = None) -> None:
    if not db_user:
        return
    result = await db.cancel_open_promocode_usage(db_user["id"])
    await call.answer()
    if not result.get("ok"):
        await call.message.answer("Сейчас нет активного промокода для отмены.")
        return
    await call.message.answer(f"❌ Промокод <b>{result['code']}</b> отменён. Можно ввести другой.")
    await _render_order_confirmation(call.message, state, db_user)


@router.callback_query(F.data.startswith("get_link_"))
@inject_db_user
async def get_link(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    try:
        sub_id = int(call.data.split("get_link_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"]:
        await call.answer("Подписка не найдена.", show_alert=True)
        return
    if not sub["is_active"]:
        await call.answer("Эта подписка уже неактивна.", show_alert=True)
        return

    await call.answer()
    ready_sub = await _ensure_subscription_link(sub)
    if not ready_sub or not ready_sub.get("subscription_url"):
        await call.message.edit_text(
            "❌ Не удалось подготовить ссылку. Попробуйте позже или напишите в поддержку.",
            reply_markup=kb.back_to_sub_kb(sub_id),
        )
        return
    ready_sub = await _attach_display_number(db_user["id"], ready_sub)

    online_ips = 0
    if ready_sub.get("email"):
        online_ips = await xui.get_online_ips_count(ready_sub["email"])

    await call.message.edit_text(
        "🔗 <b>Ссылка готова</b>\n\n"
        f"📡 Подписка №<b>{ready_sub['display_no']}</b>\n"
        f"📶 Онлайн сейчас: <b>{online_ips}/{ready_sub['devices']}</b>\n"
        "Ссылка:\n"
        f"<code>{html.escape(ready_sub['subscription_url'])}</code>\n\n"
        "Нажмите на ссылку, чтобы её скопировать.",
        reply_markup=kb.back_to_sub_kb(sub_id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("manage_links_"))
@inject_db_user
async def manage_links(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    try:
        sub_id = int(call.data.split("manage_links_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"]:
        await call.answer("Подписка не найдена.", show_alert=True)
        return

    active_clients = await db.get_active_vpn_clients_for_subscription(sub_id)
    await call.answer()
    if not active_clients:
        await call.message.edit_text(
            "У этой подписки нет отдельных активных device-ссылок.",
            reply_markup=kb.back_to_sub_kb(sub_id),
        )
        return

    lines = [
        f"{index}. {datetime.fromisoformat(client['created_at']).strftime('%d.%m.%Y %H:%M')}"
        for index, client in enumerate(active_clients, start=1)
    ]
    await call.message.edit_text(
        "📋 <b>Управление ссылками</b>\n"
        "Выберите ссылку, которую нужно отключить:\n\n"
        + "\n".join(lines),
        reply_markup=kb.manage_links_kb(sub_id, active_clients),
    )


@router.callback_query(F.data.startswith("deactivate_client_"))
@inject_db_user
async def deactivate_client(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    try:
        _, _, sub_id_str, client_row_id = call.data.split("_", 3)
        sub_id = int(sub_id_str)
    except (TypeError, ValueError):
        await call.answer("Некорректная ссылка.", show_alert=True)
        return
    active_clients = await db.get_active_vpn_clients_for_subscription(sub_id)
    client = next((item for item in active_clients if str(item["id"]) == client_row_id), None)
    if not client:
        await call.answer("Ссылка не найдена.", show_alert=True)
        return

    await db.deactivate_vpn_client(client["xui_client_id"])
    if await xui.login():
        await xui.delete_client(client["xui_client_id"])

    await call.answer("Ссылка отключена.")
    await call.message.edit_text(
        "Ссылка отключена. При необходимости можно выпустить новую.",
        reply_markup=kb.back_to_sub_kb(sub_id),
    )


@router.callback_query(F.data.startswith("reset_reveals_"))
@inject_db_user
async def reset_reveals(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    try:
        sub_id = int(call.data.split("reset_reveals_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректная подписка.", show_alert=True)
        return
    sub = await db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != db_user["id"]:
        await call.answer("Подписка не найдена.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text("⏳ Перевыпускаем ссылку и отключаем старые подключения...")

    if not await xui.login():
        await call.message.edit_text(
            "❌ Не удалось подключиться к VPN-панели. Старые данные пока не изменены.",
            reply_markup=kb.subscription_detail_kb(sub_id, bool(sub["is_active"])),
        )
        return

    try:
        await xui.set_inbound_remark(sub["inbound_id"], INBOUND_REMARK)
    except Exception:
        logger.exception("Failed to update inbound remark before reissue")

    rotated = await xui.reissue_subscription_client(sub)
    if not rotated:
        await call.message.edit_text(
            "❌ Перевыпустить ссылку не удалось. Попробуйте позже.",
            reply_markup=kb.subscription_detail_kb(sub_id, bool(sub["is_active"])),
        )
        return

    updated_sub = await db.update_subscription_credentials(
        sub_id,
        xui_client_id=rotated["client_id"],
        subscription_id=rotated["subscription_id"],
        subscription_url=rotated["subscription_url"],
        email=rotated["email"],
    )
    if not updated_sub:
        await call.message.edit_text(
            "❌ Новая ссылка создалась, но не сохранилась в базе. Напишите в поддержку.",
            reply_markup=kb.subscription_detail_kb(sub_id, bool(sub["is_active"])),
        )
        return

    await call.message.edit_text(
        texts.subscription_reissued(updated_sub),
        reply_markup=kb.subscription_detail_kb(sub_id, bool(updated_sub["is_active"])),
    )


@router.callback_query(F.data.startswith("renew_"))
@inject_db_user
async def legacy_renew_alias(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        return

    parts = call.data.split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return

    sub = await db.get_subscription_by_id(int(parts[1]))
    if not sub or sub["user_id"] != db_user["id"] or not sub["is_active"]:
        await call.answer("Подписка не найдена или уже неактивна.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text(
        texts.renewal_options(sub),
        reply_markup=kb.renewal_options_kb(sub["id"]),
    )
async def _attach_display_numbers(user_id: int, subs: list[dict]) -> list[dict]:
    number_map = await db.get_subscription_display_map(user_id)
    result: list[dict] = []
    for sub in subs:
        item = dict(sub)
        item["display_no"] = number_map.get(int(sub["id"]), int(sub["id"]))
        result.append(item)
    return result


async def _attach_display_number(user_id: int, sub: dict) -> dict:
    number_map = await db.get_subscription_display_map(user_id)
    item = dict(sub)
    item["display_no"] = number_map.get(int(sub["id"]), int(sub["id"]))
    return item


    sub = await _attach_display_number(db_user["id"], sub)
