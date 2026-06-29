"""
Payment handlers: create YooKassa payments, verify them, and fulfill orders.
"""
from __future__ import annotations

import logging
import html
import math
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import database as db
import keyboards as kb
import texts
from config import INBOUND_REMARK, PERIOD_LABELS, PRICES, REFERRAL_BONUS_DAYS_BY_MONTHS, XUI_INBOUND_ID
from decorators import inject_db_user
from services.payment import create_payment, get_payment_details, get_payment_status
from services.xui import xui

logger = logging.getLogger(__name__)
router = Router()


def _bonus_days_for_referral(months: int) -> int:
    return REFERRAL_BONUS_DAYS_BY_MONTHS.get(months, 0)


def _promo_discount_label(discount_value: int) -> str:
    if discount_value > 0:
        return f"{discount_value}%"
    return f"{abs(discount_value)} ₽"


def _months_text(months: int) -> str:
    if months % 10 == 1 and months % 100 != 11:
        return f"{months} месяц"
    if 2 <= months % 10 <= 4 and not (12 <= months % 100 <= 14):
        return f"{months} месяца"
    return f"{months} месяцев"


async def _resolve_payment_pricing(user_id: int, devices: int, months: int) -> dict:
    base_amount = PRICES[devices][months]
    open_usage = await db.get_open_promocode_usage(user_id)
    if not open_usage:
        return {
            "base_amount": base_amount,
            "amount": base_amount,
            "promo_code": None,
            "promo_discount": 0,
            "promo_usage_id": None,
            "promo_text": None,
        }

    discount_amount, final_amount = db.calculate_discount(base_amount, open_usage["discount_value"])
    if discount_amount <= 0:
        return {
            "base_amount": base_amount,
            "amount": base_amount,
            "promo_code": None,
            "promo_discount": 0,
            "promo_usage_id": None,
            "promo_text": None,
        }

    return {
        "base_amount": base_amount,
        "amount": final_amount,
        "promo_code": open_usage["code"],
        "promo_discount": discount_amount,
        "promo_usage_id": open_usage["usage_id"],
        "promo_text": f"{open_usage['code']} (−{_promo_discount_label(open_usage['discount_value'])})",
    }


def _payment_matches_expected(payment: dict, details: dict | None) -> bool:
    if not details:
        return False
    if details.get("status") != "succeeded":
        return False
    if details.get("amount_currency") != "RUB":
        return False
    try:
        expected_amount = Decimal(str(payment["amount"])).quantize(Decimal("0.01"))
        paid_amount = Decimal(str(details.get("amount_value"))).quantize(Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        return False
    if paid_amount != expected_amount:
        return False
    metadata = details.get("metadata") or {}
    if str(metadata.get("payment_db_id", "")).strip() != str(payment["id"]):
        return False
    if str(metadata.get("user_id", "")).strip() != str(payment["user_id"]):
        return False
    if str(metadata.get("devices", "")).strip() != str(payment["devices"]):
        return False
    if str(metadata.get("months", "")).strip() != str(payment["months"]):
        return False
    return True


async def _maybe_process_existing_pending_payment(
    call: CallbackQuery,
    bot: Bot,
    db_user: dict,
    state: FSMContext,
    *,
    payment_type: str,
    target_sub_id: int | None,
    devices: int,
    months: int,
) -> bool:
    pending = await db.get_latest_pending_payment(db_user["id"])
    if not pending or not pending.get("yookassa_id"):
        return False

    status = await get_payment_status(pending["yookassa_id"])
    if status == "succeeded":
        await call.message.edit_text("⏳ Нашли уже оплаченную заявку. Завершаем выдачу...")
        await _process_successful_payment(bot, db_user, pending["id"])
        await state.clear()
        return True

    if status == "canceled":
        await db.set_payment_status(pending["id"], "canceled")
        return False

    same_flow = (
        pending.get("payment_type", "new") == payment_type
        and pending.get("target_sub_id") == target_sub_id
        and pending.get("devices") == devices
        and pending.get("months") == months
    )
    if status in {"pending", "waiting_for_capture"} and same_flow and pending.get("confirmation_url"):
        await call.message.edit_text(
            texts.payment_waiting(pending["confirmation_url"]),
            reply_markup=kb.payment_link_kb(pending["confirmation_url"], pending["id"]),
        )
        await state.clear()
        return True

    return False


async def _grant_referrer_bonus(referrer: dict, bonus_days: int) -> Optional[dict]:
    updated_sub = await db.extend_latest_active_subscription(referrer["id"], bonus_days)
    if updated_sub:
        return {"mode": "extended", "subscription": updated_sub}
    return None


async def _maybe_apply_referrer_bonus(
    bot: Bot,
    referred_user: dict,
    payment: dict,
    bonus_days: int,
) -> None:
    if bonus_days <= 0:
        return

    referrer_id = referred_user.get("referred_by")
    if not referrer_id:
        return

    already_rewarded = await db.has_referral_reward_for_payment(payment["id"])
    if already_rewarded:
        return

    referrer = await db.get_user_by_id(referrer_id)
    if not referrer:
        logger.warning("Referrer user %s not found for payment %s", referrer_id, payment["id"])
        return

    reward_amount = int(payment["amount"] * 20 / 100)

    result = await _grant_referrer_bonus(referrer, bonus_days)
    if result:
        await db.create_referral_reward(
            referrer_id=referrer["id"],
            referred_id=referred_user["id"],
            payment_id=payment["id"],
            amount=reward_amount,
        )
        try:
            await bot.send_message(
                referrer["tg_id"],
                (
                    "🎁 <b>Реферальный бонус</b>\n\n"
                    f"Ваш приглашённый оплатил подписку!\n"
                    f"На ваш баланс начислено <b>+{bonus_days} дней</b>.\n"
                    f"Спасибо за приглашение!"
                ),
            )
        except Exception:
            logger.exception("Failed to notify referrer %s", referrer["tg_id"])


async def _renew_existing_subscription(payment: dict, bonus_days: int) -> dict | None:
    target_sub = await db.get_subscription_by_id(payment["target_sub_id"])
    if not target_sub or target_sub["user_id"] != payment["user_id"]:
        logger.warning("Renew target %s not found, creating a new subscription instead", payment["target_sub_id"])
        return await _create_new_subscription(payment, bonus_days)
    if target_sub.get("months") == 0:
        logger.warning(
            "Renew target %s is a trial subscription, creating a new subscription instead",
            payment["target_sub_id"],
        )
        return await _create_new_subscription(payment, bonus_days)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_exp = datetime.fromisoformat(target_sub["expires_at"])
    new_devices = payment["devices"]
    old_devices = target_sub["devices"]
    is_tariff_change = new_devices != old_devices
    base_dt = now if is_tariff_change else (current_exp if current_exp > now else now)
    new_expires = base_dt + timedelta(days=payment["months"] * 30 + bonus_days)

    replacement = None
    ready_for_sync = (
        target_sub.get("xui_client_id")
        and target_sub.get("email")
        and target_sub.get("subscription_url")
    )
    if ready_for_sync:
        synced = await xui.sync_subscription_client(
            target_sub["inbound_id"],
            client_id=target_sub["xui_client_id"],
            email=target_sub["email"],
            limit_ip=new_devices,
            expires_at=new_expires.isoformat(),
            enabled=True,
            total_gb=0,
        )
    else:
        synced = False

    if not synced:
        expire_days = max(1, math.ceil(max(0, (new_expires - now).total_seconds()) / 86400))
        email = xui.build_client_email()
        replacement = await xui.add_client(
            inbound_id=target_sub["inbound_id"],
            email=email,
            devices=new_devices,
            expire_days=expire_days,
        )
        if not replacement or not replacement.get("subscription_url"):
            return None

    renewed = await db.renew_subscription(
        target_sub["id"],
        payment["months"],
        devices=new_devices,
        bonus_days=bonus_days,
        reset_remaining=is_tariff_change,
    )
    if not renewed:
        return None

    if replacement:
        renewed = await db.update_subscription_credentials(
            target_sub["id"],
            xui_client_id=replacement["client_id"],
            subscription_id=replacement["subscription_id"],
            subscription_url=replacement["subscription_url"],
            email=replacement["email"],
        )
        if not renewed:
            return None

        old_client_id = target_sub.get("xui_client_id")
        if old_client_id and old_client_id != replacement["client_id"]:
            try:
                await xui.del_client(target_sub["inbound_id"], old_client_id)
            except Exception:
                logger.exception("Failed to delete old client %s after renewal replacement", old_client_id)

    return {
        "subscription": renewed,
        "flow": "renew",
        "previous_devices": old_devices,
        "bonus_days": bonus_days,
        "xui_created": replacement,
    }


async def _notify_success(bot: Bot, db_user: dict, payment: dict, result: dict) -> None:
    sub = result["subscription"]
    expires_at = datetime.fromisoformat(sub["expires_at"])
    days_left = max(0, (expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).days)
    bonus_note = ""
    if result["bonus_days"] > 0:
        bonus_note = (
            "\n\n🎁 <b>Реферальный бонус</b>\n"
            f"К вашему тарифу уже добавлено <b>+{result['bonus_days']} дней</b>."
        )

    await bot.send_message(
        db_user["tg_id"],
        texts.payment_success(
            sub.get("subscription_url"),
            payment["devices"],
            payment["months"],
            sub["expires_at"],
            flow=result["flow"],
            previous_devices=result["previous_devices"],
        )
        + bonus_note,
        reply_markup=kb.main_menu_kb(
            trial_available=False,
            has_active_sub=True,
            days_left=days_left,
        ),
        disable_web_page_preview=True,
    )




async def _process_successful_payment(bot: Bot, db_user: dict, payment_id: int) -> bool:
    payment = await db.get_payment(payment_id)
    if not payment or payment["user_id"] != db_user["id"]:
        return False

    if payment["status"] == "paid":
        return True
    if not payment.get("yookassa_id"):
        logger.warning("Payment %s has no gateway id", payment_id)
        return False

    details = await get_payment_details(payment["yookassa_id"])
    if not _payment_matches_expected(payment, details):
        logger.warning(
            "Gateway verification failed in processing: payment_id=%s yookassa_id=%s details=%s",
            payment["id"],
            payment.get("yookassa_id"),
            details,
        )
        return False

    claimed = await db.claim_payment_for_processing(payment_id)
    if not claimed:
        payment = await db.get_payment(payment_id)
        return bool(payment and payment["status"] == "paid")

    try:
        # Referred user gets referral bonus only once: on the first paid order.
        has_paid_before_now = await db.has_paid_before(db_user["id"])
        bonus_days = (
            _bonus_days_for_referral(payment["months"])
            if db_user.get("referred_by") and not has_paid_before_now
            else 0
        )

        if payment.get("promo_usage_id"):
            await db.attach_promocode_usage_to_payment(payment["promo_usage_id"], payment_id)

        if payment.get("payment_type") == "renew" and payment.get("target_sub_id"):
            result = await _renew_existing_subscription(payment, bonus_days)
        else:
            result = await _create_new_subscription(payment, bonus_days)

        if not result:
            await db.release_payment_processing(payment_id)
            await bot.send_message(
                db_user["tg_id"],
                (
                    "⚠️ <b>Оплата найдена</b>\n"
                    f"{texts.DIVIDER}\n\n"
                    "Но выдать доступ автоматически пока не получилось. Нажмите «Я оплатил» ещё раз через минуту или напишите в поддержку."
                ),
            )
            return False

        await db.confirm_payment(payment_id)
        await _maybe_apply_referrer_bonus(bot, db_user, payment, result["bonus_days"])
        await _notify_success(bot, db_user, payment, result)
        return True
    except Exception:
        logger.exception("Failed to process successful payment %s", payment_id)
        await db.release_payment_processing(payment_id)
        await bot.send_message(
            db_user["tg_id"],
            (
                "⚠️ <b>Оплата подтверждена</b>\n"
                f"{texts.DIVIDER}\n\n"
                "Но на этапе выдачи произошла ошибка. Попробуйте ещё раз нажать «Я оплатил» или напишите в поддержку."
            ),
        )
        return False


@router.callback_query(F.data.startswith("pay_"))
@inject_db_user
async def initiate_payment(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        _, devices_str, months_str = call.data.split("_", 2)
        devices = int(devices_str)
        months = int(months_str)
    except (TypeError, ValueError):
        await call.answer("Некорректный тариф.", show_alert=True)
        return
    if devices not in PRICES or months not in PRICES[devices]:
        await call.answer("Тариф не найден или больше не доступен.", show_alert=True)
        return

    data = await state.get_data()
    payment_type = "renew" if data.get("flow") == "renew" and data.get("target_sub_id") else "new"
    target_sub_id = data.get("target_sub_id") if payment_type == "renew" else None

    if payment_type == "renew":
        target_sub = await db.get_subscription_by_id(target_sub_id)
        if not target_sub or target_sub["user_id"] != db_user["id"]:
            await call.answer("Выбранная подписка больше недоступна.", show_alert=True)
            return
        if target_sub.get("months") == 0:
            logger.warning(
                "Trial subscription %s selected for renewal, switching payment flow to new",
                target_sub_id,
            )
            payment_type = "new"
            target_sub_id = None

    await call.answer()

    if await _maybe_process_existing_pending_payment(
        call,
        bot,
        db_user,
        state,
        payment_type=payment_type,
        target_sub_id=target_sub_id,
        devices=devices,
        months=months,
    ):
        return

    pricing = await _resolve_payment_pricing(db_user["id"], devices, months)
    description = (
        f"Продление подписки VpNLi на {_months_text(months)}"
        if payment_type == "renew"
        else f"Новая подписка VpNLi на {_months_text(months)}"
    )
    if pricing["promo_code"]:
        description += f" · Промокод {pricing['promo_code']}"

    payment_db_id = await db.create_payment(
        user_id=db_user["id"],
        amount=pricing["amount"],
        devices=devices,
        months=months,
        base_amount=pricing["base_amount"],
        promo_code=pricing["promo_code"],
        promo_discount=pricing["promo_discount"],
        promo_usage_id=pricing["promo_usage_id"],
        payment_type=payment_type,
        target_sub_id=target_sub_id,
    )

    result = await create_payment(
        amount=pricing["amount"],
        description=description,
        user_id=db_user["id"],
        payment_db_id=payment_db_id,
        devices=devices,
        months=months,
    )
    if not result:
        await db.set_payment_status(payment_db_id, "canceled")
        await call.message.edit_text(
            "❌ Не удалось создать платёжную ссылку. Попробуйте ещё раз чуть позже.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    await db.set_payment_gateway_data(
        payment_id=payment_db_id,
        yookassa_id=result["payment_id"],
        confirmation_url=result.get("confirmation_url"),
    )
    await state.clear()

    await call.message.edit_text(
        texts.payment_waiting(result["confirmation_url"]),
        reply_markup=kb.payment_link_kb(result["confirmation_url"], payment_db_id),
    )


@router.callback_query(F.data.startswith("cancel_payment_"))
@inject_db_user
async def cancel_payment(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        payment_db_id = int(call.data.split("cancel_payment_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный идентификатор платежа.", show_alert=True)
        return
    payment = await db.get_payment(payment_db_id)
    if not payment or payment["user_id"] != db_user["id"]:
        await call.answer("Платёж не найден.", show_alert=True)
        return

    if payment["status"] == "paid":
        await call.answer("Платёж уже обработан.", show_alert=True)
        return

    if payment["status"] == "processing":
        await call.answer("Платёж уже обрабатывается. Подождите пару секунд.", show_alert=True)
        return

    status = await get_payment_status(payment.get("yookassa_id")) if payment.get("yookassa_id") else None
    if status == "succeeded":
        await call.message.edit_text("⏳ Платёж уже подтверждён. Завершаем выдачу...")
        await _process_successful_payment(bot, db_user, payment_db_id)
        await state.clear()
        return

    if payment.get("promo_usage_id"):
        await db.detach_promocode_usage(payment["promo_usage_id"])

    await db.set_payment_status(payment_db_id, "canceled")
    await state.clear()
    await call.answer("Платёж отменён.")
    await call.message.edit_text(
        "Платёж отменён в боте. Если захотите, можно вернуться к оплате позже по новой заявке.",
        reply_markup=kb.payment_resume_kb(payment_db_id, payment.get("confirmation_url")),
    )


@router.callback_query(F.data.startswith("check_payment_"))
@inject_db_user
async def check_payment(
    call: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    try:
        payment_db_id = int(call.data.split("check_payment_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный идентификатор платежа.", show_alert=True)
        return
    payment = await db.get_payment(payment_db_id)
    if not payment or payment["user_id"] != db_user["id"]:
        await call.answer("Платёж не найден.", show_alert=True)
        return

    if payment["status"] == "paid":
        await call.answer("Платёж уже обработан.", show_alert=True)
        return

    if payment["status"] == "processing":
        await call.answer("Платёж уже обрабатывается. Подождите пару секунд.", show_alert=True)
        return

    if not payment.get("yookassa_id"):
        await call.answer("Не найден идентификатор платежа. Напишите в поддержку.", show_alert=True)
        return

    details = await get_payment_details(payment["yookassa_id"])
    status = (details or {}).get("status")
    if status != "succeeded":
        if status == "canceled":
            await db.set_payment_status(payment_db_id, "canceled")
            await call.answer("Платёж отменён. Создайте новую заявку.", show_alert=True)
            return
        await call.answer("Оплата ещё не подтверждена. Попробуйте снова через несколько секунд.", show_alert=True)
        return
    if not _payment_matches_expected(payment, details):
        logger.warning(
            "Payment mismatch: payment_id=%s yookassa_id=%s details=%s",
            payment["id"],
            payment.get("yookassa_id"),
            details,
        )
        await call.answer("Параметры платежа не совпали. Напишите в поддержку.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text("⏳ Оплата подтверждена. Завершаем выдачу...")
    await _process_successful_payment(bot, db_user, payment_db_id)
    await state.clear()
