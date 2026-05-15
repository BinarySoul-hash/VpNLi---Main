"""
Handlers: profile and referral program.
"""
from __future__ import annotations

from aiogram import F, Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import database as db
import keyboards as kb
import texts
from decorators import inject_db_user

router = Router()


@router.callback_query(F.data == "profile")
@router.message(Command("profile"))
@inject_db_user
async def show_profile(
    event: CallbackQuery | Message,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    subs = await db.get_active_subscriptions(db_user["id"])
    ref_stats = await db.get_referral_stats(db_user["id"])
    text = texts.profile(db_user, subs, ref_stats)
    markup = kb.profile_kb(bool(subs))

    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


@router.callback_query(F.data == "referral")
@router.message(Command("referral"))
@inject_db_user
async def show_referral(
    event: CallbackQuery | Message,
    bot: Bot,
    db_user: dict | None = None,
) -> None:
    if not db_user:
        return

    me = await bot.get_me()
    ref_stats = await db.get_referral_stats(db_user["id"])
    text = texts.referral_info(db_user, me.username, ref_stats)
    markup = kb.referral_kb(me.username, db_user["referral_code"])

    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)
