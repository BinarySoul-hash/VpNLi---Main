
from __future__ import annotations

import html
from datetime import datetime

from config import (
    PERIOD_DISCOUNTS,
    PERIOD_LABELS,
    PRICES,
    REFERRAL_BONUS_DAYS_BY_MONTHS,
    TRIAL_DAYS,
    TRIAL_TRAFFIC_GB,
)

CHANNEL_URL = "https://t.me/VpNLi_Hab"
SUPPORT_HANDLE = "@coketRokets"
DIVIDER = " ✦ VpNLi ✦ "


def _sub_no(sub: dict) -> int:
    return int(sub.get("display_no") or sub.get("id") or 0)

def _days_bar(days_left: int, total_days: int) -> str:
    if total_days <= 0:
        return "□□□□□□□□□□ 0%"
    ratio = max(0, min(1, days_left / total_days))
    filled = round(ratio * 10)
    return f"{'■' * filled}{'□' * (10 - filled)} {int(ratio * 100)}%"


def _remaining_days(expires_at: datetime) -> int:
    remaining_seconds = max(0, (expires_at - datetime.utcnow()).total_seconds())
    return int((remaining_seconds + 86399) // 86400)


def _days_label(days_left: int) -> str:
    if days_left % 10 == 1 and days_left % 100 != 11:
        return f"{days_left} день"
    if 2 <= days_left % 10 <= 4 and not (12 <= days_left % 100 <= 14):
        return f"{days_left} дня"
    return f"{days_left} дней"
def _period_price_line(devices: int, months: int) -> str:
    price = PRICES[devices][months]
    per_month = price / months
    discount = PERIOD_DISCOUNTS.get(months)
    suffix = f" · {discount}" if discount else ""
    return f"• <b>{PERIOD_LABELS[months]}</b> — {price} ₽ <i>({per_month:.0f} ₽/мес{suffix})</i>"


def welcome(name: str, is_new: bool) -> str:
    safe_name = html.escape(name or "друг")
    if is_new:
        return (
            f"Привет, <b>{safe_name}</b>.\n\n"
            "🔒 Добро пожаловать в VpNLi — быстрый и безопасный VPN.\n\n"
            "✨ <b>Старт в 4 шага</b>\n"
            "• Нажмите на кнопку «Получить 3 дня бесплатно»\n"
            "• Скопируйте полученную ссылку\n"
            "• Нажмите «Как подключиться», чтобы увидеть инструкцию для вашего устройства.\n"
            "• И выходите в сеть спокойно\n\n"
            "Приятного пользования! И не забывайте, что в случае вопросов всегда можно обратиться в поддержку.\n\n"
            f"📣 Обновления и новости: <a href='{CHANNEL_URL}'>канал VpNLi</a>"
        )

    return (
        f"С возвращением, <b>{safe_name}</b>.\n"
        "Здесь всё собрано так, чтобы до подключения было буквально несколько нажатий."
    )


def welcome_with_status(name: str, days_left: int, devices: int, expires_str: str) -> str:
    safe_name = html.escape(name or "друг")
    if days_left <= 0:
        status = "🔴 <b>Доступ закончился</b>\nПродлите подписку, чтобы снова выйти в защищённый канал."
    elif days_left <= 3:
        status = (
            f"🟡 <b>До окончания осталось {_days_label(days_left)}</b>\n"
            f"Текущая подписка: <b>{devices}</b> устр. · до <b>{expires_str}</b>"
        )
    else:
        status = (
            "🟢 <b>Подписка активна</b>\n"
            f"Текущая подписка: <b>{devices}</b> устр. · до <b>{expires_str}</b>\n"
            f"Активна ещё: <b>{_days_label(days_left)}</b>"
        )

    return (
        f"{DIVIDER}\n\n"
        f"Рад видеть вас снова, <b>{safe_name}</b>.\n\n"
        f"{status}"
    )


def profile(user: dict, subs: list, ref_stats: dict) -> str:
    joined = datetime.fromisoformat(user["created_at"]).strftime("%d.%m.%Y")
    active_subs = [sub for sub in subs if sub["is_active"]]

    if active_subs:
        lines = []
        for sub in active_subs:
            exp = datetime.fromisoformat(sub["expires_at"])
            days_left = _remaining_days(exp)
            if days_left <= 1:
                emoji = "🔴"
            elif days_left <= 7:
                emoji = "🟡"
            else:
                emoji = "🟢"
            lines.append(
                f"{emoji} <b>{sub['devices']}</b> устр. · до <b>{exp.strftime('%d.%m.%Y')}</b> · {_days_label(days_left)}"
            )
        sub_block = "\n".join(lines)
    else:
        sub_block = "⚫️ Пока без активной подписки."

    return (
        "👤 <b>Профиль</b>\n\n"
        f"🆔 Telegram ID: <code>{user['tg_id']}</code>\n"
        f"🗓 С нами с: <b>{joined}</b>\n\n"
        "📡 <b>Активные подписки</b>\n"
        f"{sub_block}\n\n"
        "🔗 <b>Реферальная программа</b>\n"
        f"• Приглашено: <b>{ref_stats['invited']}</b>\n"
        f"• С оплатой: <b>{ref_stats['paid']}</b>"
    )


def buy_hub(active_subs: list[dict]) -> str:
    best = max(active_subs, key=lambda item: item["expires_at"])
    exp = datetime.fromisoformat(best["expires_at"])
    days_left = _remaining_days(exp)
    return (
        "🛒 <b>Купить / продлить VPN</b>\n\n"
        "У вас уже есть активный доступ.\n\n"
        f"🔹 Самая длинная подписка сейчас: <b>{best['devices']}</b> устр. · до <b>{exp.strftime('%d.%m.%Y')}</b>\n"
        f"🔹 Осталось: <b>{_days_label(days_left)}</b>\n\n"
        "• <b>Купить новую подписку</b> — если нужна отдельная новая подписка.\n"
        "• <b>Продлить существующую</b> — если хотите продолжить текущую подписку."
    )


def choose_devices(mode: str = "new", current_devices: int | None = None) -> str:
    title = "🆕 <b>Новая подписка</b>\n" if mode == "new" else "🪄 <b>Смена количества устройств</b>\n"
    subtitle = (
        "Выберите, сколько устройств смогут быть онлайн одновременно."
        if mode == "new"
        else "Выберите новый лимит устройств для продления."
    )

    lines = []
    for devices in (1, 2, 3, 5):
        marker = "✦"
        if current_devices == devices:
            marker = "◉"
        lines.append(f"{marker} <b>{devices}</b> устр. — от {PRICES[devices][1]} ₽")

    return (
        f"{title}\n"
        f"{subtitle}\n\n"
        + "\n".join(lines)
        + "\n\n"
        "⛓ <b>Важно:</b> лимит устройств строгий."+"\n" + "Например:"+"\n"+"Тариф на 2 устройства = максимум 2 одновременных подключения."
    )


def choose_period(
    devices: int,
    *,
    flow: str = "new",
    current_devices: int | None = None,
) -> str:
    if flow == "renew" and current_devices and current_devices != devices:
        lead = (
            f"🛰️ Продление с новым лимитом: было <b>{current_devices}</b> устр., станет <b>{devices}</b> устр."
        )
    elif flow == "renew":
        lead = f"🛰️ Продление текущей подписки на <b>{devices}</b> устр."
    else:
        lead = f"🆕 Новая подписка на <b>{devices}</b> устр."

    return (
        "⏳ <b>Выберите период</b>\n\n"
        f"{lead}\n\n"
        + "\n".join(_period_price_line(devices, months) for months in PERIOD_LABELS)
    )


def confirm_order(
    devices: int,
    months: int,
    final_price: int | None = None,
    promo_label: str | None = None,
    *,
    flow: str = "new",
    renewal_target: dict | None = None,
    current_devices: int | None = None,
) -> str:
    base_price = PRICES[devices][months]
    pay_amount = final_price if final_price is not None else base_price

    if flow == "renew":
        if renewal_target and current_devices and current_devices != devices:
            header = (
                f"🛰️ <b>Продление подписки №{renewal_target['id']}</b>\n"
                f"Было: <b>{current_devices}</b> устр. · станет: <b>{devices}</b> устр."
            )
        elif renewal_target:
            header = f"🛰️ <b>Продление подписки №{renewal_target['id']}</b>\nЛимит устройств останется прежним."
        else:
            header = "🛰️ <b>Продление подписки</b>"
    else:
        header = "🆕 <b>Новая подписка</b>"

    price_block = f"💳 К оплате: <b>{pay_amount} ₽</b>"
    if pay_amount < base_price:
        price_block = (
            "💳 К оплате:\n"
            f"• Было: <s>{base_price} ₽</s>\n"
            f"• Сейчас: <b>{pay_amount} ₽</b>"
        )
    is_tariff_change = flow == "renew" and bool(current_devices) and current_devices != devices
    tariff_warning = ""
    if is_tariff_change:
        tariff_warning = (
            "⚠️ Важно:\n"
            "При смене тарифа (как в большую сторону, так и в меньшую) стоимость <u>списывается в полном размере</u>.\n\n"
            "Текущий остаток по подписке в этом случае <b><u>не компенсируется</u></b>.\n\n"
        )

    promo_block = f"🎟 Промокод: <b>{promo_label}</b>\n" if promo_label else "🎟 Промокод (если есть) можно применить кнопкой ниже.\n"

    return (
        "✨ <b>Подтверждение заказа</b>\n\n"
        f"{header}\n\n"
        f"📱 Устройств: <b>{devices}</b>\n"
        f"🗓 Период: <b>{PERIOD_LABELS[months]}</b>\n"
        f"{promo_block}"
        f"{price_block}\n\n"
        f"{tariff_warning}"
        "Нажимая 'Перейти к оплате', вы подтверждаете оформление заказа.\n\n"
        "После оплаты бот сразу выдаст или обновит ссылку подписки."
    )


def payment_waiting(url: str) -> str:
    del url
    return (
        "💳 <b>Оплата готова</b>\n"
        "Откройте платёж по кнопке ниже.\n\n"
        "Когда банк подтвердит операцию, нажмите <b>«Я оплатил»</b> и бот сразу завершит выдачу.\n\n"
        "⌛ Обычно это занимает меньше минуты."
    )


def payment_success(
    subscription_url: str | None,
    devices: int,
    months: int,
    expires_at: str,
    *,
    flow: str = "new",
    previous_devices: int | None = None,
) -> str:
    expires_str = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
    if flow == "renew" and previous_devices and previous_devices != devices:
        headline = "🛰️ <b>Продление завершено</b>\nЛимит устройств обновлён."
    elif flow == "renew":
        headline = "🛰️ <b>Продление завершено</b>\nТекущая подписка стала длиннее."
    else:
        headline = "✨ <b>Подписка активирована</b>\nВаш защищённый канал готов."

    link_block = (
        f"🔗 Ссылка подписки:\n<code>{html.escape(subscription_url)}</code>\n\n"
        "☝️ Нажмите на ссылку, чтобы скопировать её."
        if subscription_url
        else "📡 Ссылку можно открыть позже в разделе «Мои подписки»."
    )

    return (
        f"{headline}\n"
        f"📱 Устройств: <b>{devices}</b>\n"
        f"🗓 Период: <b>{PERIOD_LABELS.get(months, f'{months} мес.')}</b>\n"
        f"📅 Доступ до: <b>{expires_str}</b>\n\n"
        f"{link_block}\n\n"
        f"📣 Канал VpNLi: <a href='{CHANNEL_URL}'>следить за обновлениями</a>"
    )


def trial_activated(expires_at: str, subscription_url: str | None = None) -> str:
    expires_str = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y")
    link_note = (
        f"🔗 Ссылка подписки:\n<code>{html.escape(subscription_url)}</code>\n\n"
        "☝️ Нажмите на ссылку, чтобы скопировать её."
        if subscription_url
        else "Ссылку можно открыть в разделе «Мои подписки»."
    )
    return (
        "🎁 <b>Пробный доступ открыт</b>\n\n"
        f"📅 До: <b>{expires_str}</b>\n"
        f"📦 Трафик: <b>{TRIAL_TRAFFIC_GB} ГБ</b>\n\n"
        f"{link_note}\n"
        "Дальше всё просто: откройте инструкцию под вашу платформу, импортируйте ссылку и подключайтесь."
    )


def subscriptions_list_intro(subs: list[dict]) -> str:
    active_count = sum(1 for sub in subs if sub["is_active"])
    return (
        "📡 <b>Мои подписки</b>\n\n"
        f"Всего подписок: <b>{len(subs)}</b>\n"
        f"Активных сейчас: <b>{active_count}</b>"
    )


def my_subscriptions_empty() -> str:
    return (
        "📡 <b>Мои подписки</b>\n\n"
        "Здесь пока тихо: активных или архивных подписок ещё нет.\n\n"
        "Нажмите «Купить / продлить VPN», и бот проведёт вас дальше без лишних шагов."
    )


def subscription_info(sub: dict, online_ips: int | None = None, active_clients: int = 0) -> str:
    expires_at = datetime.fromisoformat(sub["expires_at"])
    days_left = _remaining_days(expires_at)

    if not sub["is_active"] or days_left <= 0:
        status = "🔴 Истекла"
    elif days_left <= 3:
        status = "🟡 На грани окончания"
    else:
        status = "🟢 Активна"

    online_line = ""
    if online_ips is not None:
        online_line = f"\n📶 Онлайн сейчас: <b>{online_ips}/{sub['devices']}</b>"

    links_line = (
        "\n🔗 Формат доступа: <b>единая подписочная ссылка</b>"
        if sub.get("subscription_url")
        else f"\n🔗 Активных ссылок: <b>{active_clients}</b>"
    )

    total_days = sub["months"] * 30 if sub["months"] else TRIAL_DAYS
    total_days = max(1, total_days)

    return (
        f"📡 <b>Подписка №{_sub_no(sub)}</b>\n\n"
        f"🛰 Статус: <b>{status}</b>\n\n"
        f"📱 Устройств: <b>{sub['devices']}</b>\n"
        f"📅 До: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n"
        f"⏳ Осталось: <b>{_days_label(days_left)}</b>\n"
        f"▸ {_days_bar(days_left, total_days)}"
        f"{links_line}"
        f"{online_line}"
    )


def renew_subscriptions_intro(subs: list[dict]) -> str:
    lines = []
    for sub in subs:
        exp = datetime.fromisoformat(sub["expires_at"])
        days_left = _remaining_days(exp)
        lines.append(
            f"• <b>№{_sub_no(sub)}</b> · {sub['devices']} устр. · до {exp.strftime('%d.%m.%Y')} · {_days_label(days_left)}"
        )

    return (
        "🛰️ <b>Продлить существующую подписку</b>\n\n"
        "Выберите активную подписку, которую хотите продлить.\n\n"
        + "\n".join(lines)
    )


def renewal_options(sub: dict) -> str:
    expires_at = datetime.fromisoformat(sub["expires_at"])
    days_left = _remaining_days(expires_at)
    return (
        f"🛰️ <b>Продление подписки №{_sub_no(sub)}</b>\n\n"
        f"📱 Сейчас: <b>{sub['devices']}</b> устр.\n"
        f"📅 До: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n"
        f"⏳ Осталось: <b>{_days_label(days_left)}</b>\n\n"
        "Как продлеваем?\n"
        "• <b>Продлить текущий тариф</b> — сохранить тот же лимит устройств\n"
        "• <b>Сменить количество устройств</b> — обновить тариф сразу при продлении"
    )


def referral_info(user: dict, bot_username: str, ref_stats: dict) -> str:
    ref_link = f"https://t.me/{bot_username}?start=ref_{user['referral_code']}"
    bonus_lines = []
    for months, bonus_days in sorted(REFERRAL_BONUS_DAYS_BY_MONTHS.items()):
        bonus_lines.append(f"• {PERIOD_LABELS.get(months, f'{months} мес.')} → <b>+{bonus_days} дней</b> вам и другу")

    return (
        "🔗 <b>Реферальная программа</b>\n\n"
        "Поделитесь личной ссылкой: друг перейдёт в бота, оформит первую оплату, и вам обоим начислятся бонусные дни.\n\n"
        f"🔹 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: <b>{ref_stats['invited']}</b>\n"
        f"💳 С оплатой: <b>{ref_stats['paid']}</b>\n\n"
        "📌 <b>Правила</b>\n"
        "• Рефереру засчитывается максимум <b>5 оплаченных рефералов за 30 дней</b>.\n"
        "• Приглашённый получает бонус только <b>один раз</b> — за первую успешную оплату.\n"
        "• Реферер получает бонус от одного и того же приглашённого тоже только <b>один раз</b>.\n\n"
        "🎁 <b>Размер бонуса</b>\n"
        + "\n".join(bonus_lines)
    )


def howto_general() -> str:
    return (
        "📲 <b>Как подключиться</b>\n\n"
        "VpNLi работает на <b>VLESS + Reality</b> — это быстрый и устойчивый протокол.\n\n"
        "Выберите вашу платформу, а дальше бот проведёт вас за руку."
    )


def howto_platform(platform: str) -> str:
    guides = {
        "android": (
            "🤖 <b>Android</b>\n"
            "1. Установите <b>v2raytun</b>\n"
            "2. Скопируйте ссылку подписки из бота\n"
            "3. Откройте приложение и нажмите <b>+</b>\n"
            "4. Выберите импорт из буфера обмена\n"
            "5. Нажмите ▶️ и подключайтесь\n\n"
            "📥 <a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>Скачать v2raytun</a>"
        ),
        "ios": (
            "🍎 <b>iPhone / iPad</b>\n"
            "1. Установите <b>Streisand</b>\n"
            "2. Скопируйте ссылку подписки из бота\n"
            "3. Откройте приложение и нажмите <b>+</b>\n"
            "4. Выберите импорт из буфера\n"
            "5. Подтвердите создание VPN-конфигурации\n\n"
            "📥 <a href='https://apps.apple.com/app/streisand/id6450534064'>Скачать Streisand</a>"
        ),
        "windows": (
            "🪟 <b>Windows</b>\n"
            "1. Скачайте <b>Hiddify</b>\n"
            "2. Скопируйте ссылку подписки из бота\n"
            "3. Откройте приложение\n"
            "4. Нажмите <b>+</b> и выберите импорт из буфера\n"
            "5. Нажмите <b>Connect</b>\n\n"
            "📥 <a href='https://github.com/hiddify/hiddify-next/releases'>Скачать Hiddify</a>"
        ),
        "macos": (
            "🍏 <b>macOS</b>\n"
            "1. Установите <b>Hiddify</b>\n"
            "2. Скопируйте ссылку подписки из бота\n"
            "3. Откройте приложение\n"
            "4. Нажмите <b>+</b> и выберите импорт из буфера\n"
            "5. Нажмите <b>Connect</b>\n\n"
            "📥 <a href='https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532'>Скачать Hiddify</a>"
        ),
    }
    return guides.get(platform, "Платформа не найдена.")


def howto_routing_v2raytun() -> str:
    return (
        "🔁 <b>Маршрутизация</b>\n\n"
        "1. Откройте настройки приложения.\n\n"
        "2. Найдите пункт <b>Маршрутизация</b>.\n\n"
        "3. Далее в этом пункте <b>Маршрутизация выбранных приложений.</b>\n\n"
        "4. Включите <b>Маршрутизация выбранных приложений</b> и выберите из списка ниже приложения трафик которых <b>будет</b> идти через VPN.\n\n"
        "Вот и всё вы подключили маршрутизацию."
    )


HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "Если нужен быстрый путь:\n"
    "• чтобы подключиться — откройте «Как подключиться»\n"
    "• чтобы забрать ссылку — откройте «Мои подписки»\n"
    "• если что-то пошло не так — напишите в поддержку"
)


SUPPORT_TEXT = (
    "💬 <b>Поддержка VpNLi</b>\n\n"
    f"Напишите: <b>{SUPPORT_HANDLE}</b>\n\n"
    "Чтобы помочь быстрее, сразу укажите:\n"
    "• вашу платформу\n"
    "• что именно не работает\n"
    "• по возможности скриншот"
)


DOCUMENTS_TEXT = (
    "📄 <b>Документы</b>\n\n"
    "Здесь собраны юридические документы сервиса.\n"
    "Выберите нужный пункт ниже."
)


PRIVACY_POLICY_TEXT = (
    "🔒 <b>Политика конфиденциальности</b>\n\n"
    "Дата публикации: <b>14.05.2026</b>\n\n"
    "1. <b>Оператор данных</b>\n"
    "Сервис VpNLi обрабатывает данные пользователей Telegram-бота для предоставления VPN-доступа, техподдержки и исполнения платежей.\n\n"
    "2. <b>Какие данные мы обрабатываем</b>\n"
    "• Telegram ID, username, имя профиля;\n"
    "• технические данные подписок (тариф, срок, статус, лимит устройств);\n"
    "• данные платежей (статус, сумма, идентификатор платежа в платёжном провайдере);\n"
    "• данные по промокодам и рефералам;\n"
    "• сообщения в поддержку, если вы сами их отправляете.\n\n"
    "3. <b>Для чего обрабатываются данные</b>\n"
    "• регистрация и ведение аккаунта;\n"
    "• выдача и продление VPN-подписок;\n"
    "• учёт оплат, скидок, промокодов и реферальных начислений;\n"
    "• предотвращение злоупотреблений и защита от мошенничества;\n"
    "• поддержка пользователей.\n\n"
    "4. <b>Правовые основания</b>\n"
    "Обработка выполняется для исполнения договора (оферты), соблюдения обязательств по учёту платежей и на основании согласия пользователя при использовании сервиса.\n\n"
    "5. <b>Передача данных третьим лицам</b>\n"
    "Мы передаём только необходимые данные:\n"
    "• платёжному провайдеру для приёма и подтверждения оплаты;\n"
    "• инфраструктурным подрядчикам (хостинг, серверы) в объёме, нужном для работы сервиса.\n"
    "Данные не продаются и не передаются в рекламные сети.\n\n"
    "6. <b>Срок хранения</b>\n"
    "Данные хранятся в течение срока использования сервиса и разумного периода после для бухгалтерского учёта, разбора спорных ситуаций и соблюдения требований законодательства.\n\n"
    "7. <b>Безопасность</b>\n"
    "Мы применяем организационные и технические меры защиты, включая разграничение доступа к данным и контроль операций в сервисе.\n\n"
    "8. <b>Права пользователя</b>\n"
    "Вы можете запросить актуализацию, ограничение обработки или удаление данных, если это не противоречит обязательным требованиям закона и учёта платежей.\n\n"
    "9. <b>Контакты</b>\n"
    f"По вопросам обработки данных: <b>{SUPPORT_HANDLE}</b>.\n\n"
    "10. <b>Изменения политики</b>\n"
    "Актуальная редакция политики публикуется в разделе «Документы» этого бота."
)


PUBLIC_OFFER_TEXT = (
    "📄 <b>Публичная оферта</b>\n\n"
    "Дата публикации: <b>14.05.2026</b>\n\n"
    "1. <b>Общие положения</b>\n"
    "Настоящий документ является публичной офертой сервиса VpNLi на предоставление доступа к цифровой услуге VPN. Оплачивая услугу, пользователь подтверждает акцепт оферты.\n\n"
    "2. <b>Предмет договора</b>\n"
    "Сервис предоставляет пользователю ограниченный по сроку и параметрам тарифный доступ к VPN через Telegram-бота.\n\n"
    "3. <b>Тарифы и оплата</b>\n"
    "• актуальные тарифы указываются в интерфейсе бота;\n"
    "• оплата выполняется через подключённого платёжного провайдера;\n"
    "• услуга считается оказанной с момента успешной активации или продления подписки.\n\n"
    "4. <b>Промокоды и скидки</b>\n"
    "Промокоды имеют ограничение по сроку действия и/или количеству активаций. Недействительный, просроченный или исчерпанный промокод не даёт скидку.\n\n"
    "5. <b>Реферальная программа</b>\n"
    "Условия бонусов по реферальной программе, лимиты и период действия указаны в соответствующем разделе бота и применяются в текущей редакции на момент начисления.\n\n"
    "6. <b>Права и обязанности пользователя</b>\n"
    "Пользователь обязуется использовать сервис законно, не передавать доступ третьим лицам с нарушением условий тарифа и не предпринимать действий, нарушающих работу инфраструктуры.\n\n"
    "7. <b>Ограничение ответственности</b>\n"
    "Сервис предоставляется «как есть». Возможны временные ограничения, связанные с внешней сетью, блокировками операторов связи, работой сторонних платформ и техническими обновлениями.\n\n"
    "8. <b>Возвраты и спорные ситуации</b>\n"
    "Запросы по возвратам и спорным операциям рассматриваются индивидуально через поддержку с учётом факта оказания услуги и статуса платежа.\n\n"
    "9. <b>Срок действия и изменение условий</b>\n"
    "Оферта действует бессрочно до её отзыва или замены новой редакцией. Актуальная версия всегда доступна в разделе «Документы».\n\n"
    "10. <b>Контакты поддержки</b>\n"
    f"По всем вопросам: <b>{SUPPORT_HANDLE}</b>."
)


def link_reveal_info(sub: dict, reveals_now: int, online_ips: int) -> str:
    return (
        f"🔗 <b>Ссылка подписки №{_sub_no(sub)}</b>\n"
        f"📱 Лимит устройств: <b>{sub['devices']}</b>\n"
        f"📶 Онлайн сейчас: <b>{online_ips}/{sub['devices']}</b>\n"
        f"🧾 Выдач ссылки: <b>{reveals_now}</b>\n\n"
        "Скопируйте ссылку ниже и импортируйте её в приложение."
    )


def link_limit_reached(devices: int, reveals_now: int, online_ips: int) -> str:
    return (
        "⛔ <b>Лимит устройства достигнут</b>\n\n"
        f"Ваш тариф рассчитан на <b>{devices}</b> одновременных подключений.\n"
        f"• Выдач ссылки: <b>{reveals_now}</b>\n"
        f"• Онлайн сейчас: <b>{online_ips}</b>\n\n"
        "Можно перевыпустить ссылку или перейти на тариф с большим числом устройств."
    )


def subscription_reissued(sub: dict) -> str:
    return (
        "♻️ <b>Ссылка перевыпущена</b>\n"
        "Старая ссылка и старые подключения больше неактивны.\n"
        "Импортируйте новый адрес в приложение и подключайтесь заново.\n\n"
        f"{subscription_info(sub)}"
    )


def device_limit_exceeded(sub: dict, online_ips: int, cooldown_seconds: int) -> str:
    return (
        "⛔ <b>Лимит устройств превышен</b>\n"
        f"Подписка <b>№{sub['id']}</b> рассчитана на <b>{sub['devices']}</b> одновременных подключения.\n"
        f"Сейчас система увидела <b>{online_ips}</b> активных IP.\n\n"
        "Чтобы защитить ваш доступ, мы временно остановили эту подписку.\n\n"
        "Что сделать сейчас:\n"
        "• отключить VPN на лишнем устройстве\n"
        "• подождать и подключиться снова\n"
        "• или продлить тариф с большим числом устройств\n\n"
        f"⏱ Повторная попытка обычно доступна примерно через <b>{cooldown_seconds}</b> сек."
    )


EXPIRING_SOON_NOTIFICATION = (
    "🪐 <b>До окончания подписки совсем немного</b>\n"
    "Ваш текущая подписка закончится через <b>{days}</b> ({date}).\n"
    "Продлите ее заранее, чтобы канал не прервался в неподходящий момент."
)


EXPIRED_NOTIFICATION = (
    "🌘 <b>Подписка завершилась</b>\n"
    "Срок текущего доступа закончился.\n"
    "Откройте покупку снова, если хотите сразу вернуть защищённый канал."
)
