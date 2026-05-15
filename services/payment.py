"""
YooKassa payment service wrappers.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from yookassa import Configuration, Payment

from config import YOOKASSA_RETURN_URL, YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID

logger = logging.getLogger(__name__)

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


def _create_payment_sync(
    *,
    amount: int,
    description: str,
    user_id: int,
    payment_db_id: int,
    devices: int,
    months: int,
) -> dict:
    payment = Payment.create(
        {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": YOOKASSA_RETURN_URL,
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": user_id,
                "payment_db_id": payment_db_id,
                "devices": devices,
                "months": months,
            },
        },
        str(uuid.uuid4()),
    )
    return {
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url,
    }


async def create_payment(
    amount: int,
    description: str,
    user_id: int,
    payment_db_id: int,
    devices: int,
    months: int,
) -> Optional[dict]:
    try:
        return await asyncio.to_thread(
            _create_payment_sync,
            amount=amount,
            description=description,
            user_id=user_id,
            payment_db_id=payment_db_id,
            devices=devices,
            months=months,
        )
    except Exception as exc:
        logger.error("YooKassa create_payment error: %s", exc)
        return None


def _get_payment_status_sync(yookassa_id: str) -> Optional[str]:
    payment = Payment.find_one(yookassa_id)
    return payment.status


def _get_payment_details_sync(yookassa_id: str) -> Optional[dict]:
    payment = Payment.find_one(yookassa_id)
    amount_value = None
    amount_currency = None
    metadata = {}
    if getattr(payment, "amount", None):
        amount_value = getattr(payment.amount, "value", None)
        amount_currency = getattr(payment.amount, "currency", None)
    if getattr(payment, "metadata", None):
        metadata = dict(payment.metadata)
    return {
        "status": payment.status,
        "amount_value": amount_value,
        "amount_currency": amount_currency,
        "metadata": metadata,
    }


async def get_payment_status(yookassa_id: str) -> Optional[str]:
    if not yookassa_id:
        return None
    try:
        return await asyncio.to_thread(_get_payment_status_sync, yookassa_id)
    except Exception as exc:
        logger.error("YooKassa get_payment_status error: %s", exc)
        return None


async def get_payment_details(yookassa_id: str) -> Optional[dict]:
    if not yookassa_id:
        return None
    try:
        return await asyncio.to_thread(_get_payment_details_sync, yookassa_id)
    except Exception as exc:
        logger.error("YooKassa get_payment_details error: %s", exc)
        return None


async def is_payment_succeeded(yookassa_id: str) -> bool:
    return await get_payment_status(yookassa_id) == "succeeded"
