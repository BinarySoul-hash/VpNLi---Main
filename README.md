# 🔒 VPNLi Bot

Полнофункциональный Telegram-бот для продажи VPN-подписок.

## ✨ Возможности

| Функция | Описание |
|---|---|
| 🛒 Продажа подписок | 1/2/3/5 устройств × 1/3 мес. |
| 💳 Оплата | ЮКасса (карты, СБП, ЮMoney) |
| 📄 Публичная оферта | Показывается только при первой покупке |
| 🎁 Пробный период | 3 дня / 1 ГБ для новых пользователей |
| 🔗 Реферальная программа | 20% от первой оплаты реферала на баланс |
| 🔑 Доступ после оплаты | Автоматическая выдача ссылки подписки + VLESS-ключа через 3X-UI |
| 📲 Инструкции | Android / iOS / Windows / macOS |
| ⏰ Уведомления | За 3 дня до истечения подписки |
| 🤖 Автоочистка | Удаляет истёкшие ключи из панели |
| 🛠 Админ-панель | Статистика, рассылка, управление пользователями |

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
git clone <repo>
cd vpn_bot
cp .env.example .env
nano .env      # Заполните все переменные
```

### 2. Запуск через Docker

```bash
docker-compose up -d
docker-compose logs -f
```

### 3. Запуск без Docker

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

---

## ⚙️ Настройка .env

```env
BOT_TOKEN=         # Токен от @BotFather
ADMIN_IDS=         # Ваш Telegram ID (узнать: @userinfobot)
YOOKASSA_SHOP_ID=  # ID магазина из личного кабинета ЮКасса
YOOKASSA_SECRET_KEY= # Секретный ключ ЮКасса
YOOKASSA_RETURN_URL=https://t.me/ВАШ_БОТ
XUI_HOST=https://IP_СЕРВЕРА:2053
XUI_USERNAME=admin
XUI_PASSWORD=ваш_пароль
XUI_INBOUND_ID=1
XUI_VERIFY_SSL=true
XUI_PUBLIC_BASE_URL=
XUI_SUBSCRIPTION_URL_TEMPLATE=
XUI_REQUEST_RETRIES=3
XUI_INBOUNDS_TIMEOUT_SECONDS=45
XUI_CLIENT_IPS_TIMEOUT_SECONDS=25
DB_PATH=data/vpn_bot.db
DB_BACKUP_DIR=data/backups
DB_BACKUP_KEEP=10
TRIAL_LOCK_STALE_SECONDS=900
```

---

## 🖥️ Настройка 3X-UI (пошаговая инструкция)

### Шаг 1: Установка 3X-UI на сервер

```bash
# Подключитесь по SSH к вашему VPN-серверу (Amsterdam)
ssh root@YOUR_SERVER_IP

# Установите 3X-UI одной командой:
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
```

После установки будет показан адрес панели, логин и пароль.

### Шаг 2: Создание inbound (входящего соединения)

1. Откройте браузер: `http://IP_СЕРВЕРА:2053`
2. Войдите под своими учётными данными
3. Нажмите **«Добавить Inbound»**
4. Настройте:

```
Remark:     VpNLi 🔷 | 🇳🇱 Amsterdam
Protocol:   VLESS
Listen IP:  (пусто)
Port:       443
```

5. В разделе **Transmission** выберите:
   - Network: `TCP`
   - Security: `Reality`

6. В разделе **Reality Settings**:
   - Dest (destination): `yahoo.com:443`
   - Server Names: `yahoo.com`
   - Нажмите **«Генерировать»** для Public/Private Key и Short ID

7. Нажмите **«Создать»**

8. Запомните ID inbound (цифра слева от названия) — это `XUI_INBOUND_ID` в `.env`

### Шаг 3: Открыть порт в файрволе

```bash
# На сервере с 3X-UI:
ufw allow 443/tcp
ufw allow 5001/tcp   # Порт панели (для бота)
ufw reload
```

### Шаг 4: Проверка

В `.env` укажите:
```env
XUI_HOST=https://IP_VPN_СЕРВЕРА:2053
XUI_INBOUND_ID=1   # ID вашего inbound
XUI_VERIFY_SSL=true
```

Запустите бота и попробуйте создать пробный ключ — в панели 3X-UI должен появиться новый клиент.

Если панель открывается по HTTPS с самоподписанным сертификатом, оставьте `XUI_VERIFY_SSL=false`.
Если пользователю нужно отдавать ссылку на другой домен, укажите `XUI_PUBLIC_BASE_URL=https://vpn.example.com`.
При нестандартном шаблоне подписки используйте `XUI_SUBSCRIPTION_URL_TEMPLATE`, например:
`{base_url}/sub/{sub_id}`.

---

## 💳 Настройка ЮКасса

1. Зарегистрируйтесь на [yookassa.ru](https://yookassa.ru)
2. Пройдите верификацию магазина
3. В разделе **«Интеграция»** → **«HTTP-уведомления»** добавьте URL вебхука (опционально, бот работает и без него — через polling)
4. Скопируйте **Shop ID** и **Секретный ключ** → вставьте в `.env`

> ⚠️ Для тестирования используйте тестовый магазин из личного кабинета ЮКасса.

---

## 📁 Структура проекта

```
vpn_bot/
├── bot.py              # Точка входа
├── config.py           # Все настройки (читает .env)
├── database.py         # Все запросы к SQLite
├── keyboards.py        # Inline-клавиатуры
├── texts.py            # Тексты сообщений
├── scheduler.py        # Фоновые задачи (уведомления, очистка)
├── handlers/
│   ├── start.py        # /start, меню, помощь
│   ├── subscription.py # Выбор тарифа, мои подписки
│   ├── payment.py      # Создание и проверка платежей
│   ├── profile.py      # Профиль, реферальная программа
│   └── admin.py        # Админ-панель
├── services/
│   ├── xui.py          # 3X-UI REST API клиент
│   └── payment.py      # ЮКасса клиент
├── middlewares/
│   └── register.py     # Авторегистрация пользователей
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 💰 Тарифы (настраиваются в config.py)

| Устройства | 1 мес | 3 мес (-16%) |
|---|---|---|
| 1 устройство | 99 ₽ | 249 ₽ |
| 2 устройства | 129 ₽ | 325 ₽ |
| 3 устройства | 159 ₽ | 400 ₽ |
| 5 устройств | 219 ₽ | 555 ₽ |

---

## 🔗 Реферальная программа

- Каждый пользователь получает уникальную реферальную ссылку
- Приглашённый получает **+3 дня** к первой подписке
- Пригласивший получает **20%** от суммы первой оплаты реферала на баланс
- Баланс тратится при следующей покупке (логика оплаты с баланса — добавить по желанию)

---

## 🛠 Команды бота

| Команда | Описание |
|---|---|
| `/start` | Главное меню |
| `/buy` | Купить VPN |
| `/profile` | Мой профиль |
| `/referral` | Реферальная программа |
| `/howto` | Инструкция по подключению |
| `/admin` | Панель администратора (только для ADMIN_IDS) |

---

## 🤝 Поддержка

Если что-то не работает — проверьте:
1. `docker-compose logs vpn_bot` — логи бота
2. Доступность 3X-UI: `curl http://IP:2053/login`
3. Корректность `.env` переменных

---

## 🔧 Решение проблем с сетью

### 3X-UI Connection timeout

**Проблема:** `Connection timeout to host https://...`

**Решение:**
- Увеличьте таймауты в `services/xui.py` (уже установлены на 60s)
- Проверьте доступность панели 3X-UI из контейнера: `docker-compose exec vpn_bot curl -v http://IP_СЕРВЕРА:2053/login`
- Проверьте firewall на сервере 3X-UI: `ufw status`
- Убедитесь, что порт 2053 открыт: `netstat -tlnp | grep 2053`

### Telegram API DNS error

**Проблема:** `Cannot connect to host api.telegram.org:443 ssl:default [Name or service not known]`

**Решение:**
- Проверьте DNS в контейнере: `docker-compose exec vpn_bot cat /etc/resolv.conf`
- Добавьте в `docker-compose.yml` секцию `dns`:
```yaml
services:
  vpn_bot:
    dns:
      - 8.8.8.8
      - 1.1.1.1
```
- Перезагрузитесь: `docker-compose down && docker-compose up -d`

### Повышенное количество ошибок

**Причина:** Плохое сетевое соединение

**Решение:**
- Бот автоматически делает 3 повторных попытки с экспоненциальной задержкой (2s, 4s, 8s)
- Таймауты установлены на 60 секунд для стабильной работы на медленных сетях
- Interval enforcer'а удвоится при ошибке, чтобы избежать наводнения запросов
