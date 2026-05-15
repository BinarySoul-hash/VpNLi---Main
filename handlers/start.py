"""
Handlers: start screen, main menu, help, and connection guides.
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

import database as db
import keyboards as kb
import texts
from config import OFFER_PDF_PATH, PRIVACY_PDF_PATH
from decorators import inject_db_user

router = Router()
logger = logging.getLogger(__name__)


async def _home_payload(db_user: dict, first_name: str) -> tuple[str, object]:
    trial_available = await db.is_trial_available(db_user["id"])
    active_subs = await db.get_active_subscriptions(db_user["id"])

    has_active_sub = bool(active_subs)
    days_left = None
    if has_active_sub:
        best = max(active_subs, key=lambda item: item["expires_at"])
        expires_at = datetime.fromisoformat(best["expires_at"])
        days_left = max(0, (expires_at - datetime.utcnow()).days)
        text = texts.welcome_with_status(
            first_name,
            days_left,
            best["devices"],
            expires_at.strftime("%d.%m.%Y"),
        )
    else:
        is_new = trial_available
        text = texts.welcome(first_name, is_new)

    markup = kb.main_menu_kb(
        trial_available=trial_available,
        has_active_sub=has_active_sub,
        days_left=days_left,
    )
    return text, markup


@router.message(CommandStart())
@inject_db_user
async def cmd_start(message: Message, db_user: dict | None = None) -> None:
    if not db_user:
        logger.error("db_user missing for /start from %s", message.from_user.id)
        await message.answer("❌ Не удалось открыть меню. Попробуйте ещё раз позже.")
        return

    text, markup = await _home_payload(db_user, message.from_user.first_name or "друг")
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "main_menu")
@inject_db_user
async def cb_main_menu(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        logger.error("db_user missing for main_menu from %s", call.from_user.id)
        await call.answer("❌ Не удалось открыть меню.", show_alert=True)
        return

    text, markup = await _home_payload(db_user, call.from_user.first_name or "друг")
    await call.answer()
    try:
        await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        await call.message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(texts.HELP_TEXT, reply_markup=kb.help_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP_TEXT, reply_markup=kb.help_kb())


@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(texts.SUPPORT_TEXT, reply_markup=kb.back_to_menu_kb())


@router.callback_query(F.data == "docs")
async def cb_documents(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(texts.DOCUMENTS_TEXT, reply_markup=kb.documents_kb())


@router.callback_query(F.data == "doc_privacy")
async def cb_privacy_document(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.answer_document(
            FSInputFile(PRIVACY_PDF_PATH),
            caption="🔒 <b>Политика конфиденциальности</b>",
            reply_markup=kb.documents_kb(),
        )
    except Exception:
        logger.exception("Failed to send privacy PDF from %s", PRIVACY_PDF_PATH)
        await call.message.answer(
            "❌ Не удалось отправить файл политики конфиденциальности. Попробуйте позже.",
            reply_markup=kb.documents_kb(),
        )


@router.callback_query(F.data == "doc_offer")
async def cb_offer_document(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.answer_document(
            FSInputFile(OFFER_PDF_PATH),
            caption="📄 <b>Публичная оферта</b>",
            reply_markup=kb.documents_kb(),
        )
    except Exception:
        logger.exception("Failed to send offer PDF from %s", OFFER_PDF_PATH)
        await call.message.answer(
            "❌ Не удалось отправить файл оферты. Попробуйте позже.",
            reply_markup=kb.documents_kb(),
        )


@router.callback_query(F.data == "howto")
@router.message(Command("howto"))
async def cb_howto(event: CallbackQuery | Message) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(texts.howto_general(), reply_markup=kb.howto_kb())
    else:
        await event.answer(texts.howto_general(), reply_markup=kb.howto_kb())


@router.callback_query(F.data.startswith("howto_sub_"))
async def cb_howto_for_subscription(call: CallbackQuery) -> None:
    try:
        sub_id = int(call.data.split("howto_sub_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Не удалось открыть инструкцию.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text(
        texts.howto_general(),
        reply_markup=kb.howto_kb(back_callback=f"install_sub_{sub_id}"),
    )


@router.callback_query(F.data.startswith("howto_"))
@inject_db_user
async def cb_howto_platform(call: CallbackQuery, db_user: dict | None = None) -> None:
    if not db_user:
        await call.answer("❌ Не удалось открыть инструкцию.", show_alert=True)
        return

    platform = call.data.split("howto_", 1)[1]
    active_subs = await db.get_active_subscriptions(db_user["id"])
    await call.answer()
    await call.message.edit_text(
        texts.howto_platform(platform),
        reply_markup=kb.after_howto_platform_kb(sub_available=bool(active_subs)),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "install")
async def cb_install(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "📲 <b>Подключение</b>\n\n"
        "Выберите платформу для пошаговой инструкции или настройте маршрутизацию для приложений.",
        reply_markup=kb.install_kb(routing_callback="routing_v2raytun"),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("install_sub_"))
async def cb_install_for_subscription(call: CallbackQuery) -> None:
    try:
        sub_id = int(call.data.split("install_sub_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Не удалось открыть подключение.", show_alert=True)
        return

    await call.answer()
    await call.message.edit_text(
        "📲 <b>Подключение</b>\n\n"
        "Выберите платформу для пошаговой инструкции или настройте маршрутизацию для приложений.",
        reply_markup=kb.install_kb(
            howto_callback=f"howto_sub_{sub_id}",
            routing_callback=f"routing_sub_{sub_id}",
            back_callback=f"sub_{sub_id}",
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "routing_v2raytun")
async def cb_routing_v2raytun(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        texts.howto_routing_v2raytun(),
        reply_markup=kb.back_kb("install"),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("routing_sub_"))
async def cb_routing_for_subscription(call: CallbackQuery) -> None:
    try:
        sub_id = int(call.data.split("routing_sub_", 1)[1])
    except (TypeError, ValueError):
        await call.answer("Не удалось открыть маршрутизацию.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        texts.howto_routing_v2raytun(),
        reply_markup=kb.back_kb(f"install_sub_{sub_id}"),
        disable_web_page_preview=True,
    )
