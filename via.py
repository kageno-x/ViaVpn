import asyncio
import os
import math
import logging
import sqlite3
import threading
import html
import uuid
from datetime import datetime
import urllib.parse
import aiohttp
import io
from PIL import Image, ImageDraw

import dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

dotenv.load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
XUI_URL = os.getenv("XUI_URL")
API_KEY = os.getenv("XUI_API_KEY")
INBOUND_IDS_ENV = os.getenv("INBOUND_ID", "")
INBOUND_IDS_FALLBACK = [
    int(x.strip()) for x in INBOUND_IDS_ENV.split(",") if x.strip().lstrip("-").isdigit()
]
DOMAIN = os.getenv("DOMAIN")
WEBSITE_URL = os.getenv("DOMAIN")
if DOMAIN and "://" in DOMAIN:
    _scheme, _rest = DOMAIN.split("://", 1)
    SUB_DOMAIN = f"{_scheme}://sub.{_rest}"
else:
    SUB_DOMAIN = f"sub.{DOMAIN}" if DOMAIN else DOMAIN
DB_FILE = "vpn.db"

_ADMIN_IDS_ENV = os.getenv("ADMIN_ID", "")
ADMIN_IDS = {int(x.strip()) for x in _ADMIN_IDS_ENV.split(",") if x.strip().lstrip("-").isdigit()}

if not TOKEN or not XUI_URL or not API_KEY:
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Проверьте переменные окружения в .env!")
if not ADMIN_IDS:
    logger.warning("ADMIN_ID не задан в .env — админ-команды (/broadcast) будут недоступны никому.")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

session: aiohttp.ClientSession = None

TIMEOUT = aiohttp.ClientTimeout(total=10)

THROTTLE_INTERVAL = 0.7
_last_action_ts: dict[int, float] = {}

class ThrottlingMiddleware:
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None:
            state: FSMContext = data.get("state")
            current_state = await state.get_state() if state else None
            if current_state is None:
                now = asyncio.get_event_loop().time()
                last = _last_action_ts.get(user.id, 0)
                if now - last < THROTTLE_INTERVAL:
                    if isinstance(event, CallbackQuery):
                        try:
                            await event.answer()
                        except Exception:
                            pass
                    return
                _last_action_ts[user.id] = now
        return await handler(event, data)

dp.message.middleware(ThrottlingMiddleware())
dp.callback_query.middleware(ThrottlingMiddleware())

async def _cleanup_throttle_cache():
    while True:
        await asyncio.sleep(3600)
        now = asyncio.get_event_loop().time()
        stale = [uid for uid, ts in _last_action_ts.items() if now - ts > 3600]
        for uid in stale:
            _last_action_ts.pop(uid, None)

class InviteStates(StatesGroup):
    waiting_for_name = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()

_db_conn: sqlite3.Connection = None
_DB_LOCK: threading.Lock = None
def init_db():
    global _db_conn, _DB_LOCK
    _DB_LOCK = threading.Lock()

    _db_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    _db_conn.execute("PRAGMA journal_mode=WAL")
    _db_conn.execute("PRAGMA synchronous=NORMAL")

    cursor = _db_conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id TEXT,
            name TEXT,
            email TEXT UNIQUE,
            vpn_link TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referred_id TEXT PRIMARY KEY,
            referrer_id TEXT,
            created_at TEXT
        )
    """)
    _db_conn.commit()
    logger.info("База данных SQLite успешно инициализирована (WAL, persistent connection).")

def close_db():
    if _db_conn:
        _db_conn.close()
        logger.info("Соединение с базой данных закрыто.")

def _add_user_sync(tg_id: int, username: str, display_name: str):
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    with _DB_LOCK:
        _db_conn.execute(
            "INSERT OR REPLACE INTO users (tg_id, username, display_name, created_at) VALUES (?, ?, ?, ?)",
            (tg_id, username, display_name, created_at)
        )
        _db_conn.commit()

def _get_all_users_sync() -> list:
    with _DB_LOCK:
        rows = _db_conn.execute("SELECT tg_id FROM users").fetchall()
    return [r[0] for r in rows]

def _add_invite_sync(inviter_id: int, name: str, email: str, vpn_link: str, created_at: str):
    with _DB_LOCK:
        _db_conn.execute(
            "INSERT OR REPLACE INTO invites (inviter_id, name, email, vpn_link, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(inviter_id), name, email, vpn_link, created_at)
        )
        _db_conn.commit()

def _get_invites_sync(inviter_id: int) -> list:
    with _DB_LOCK:
        rows = _db_conn.execute(
            "SELECT name, email, vpn_link, created_at FROM invites WHERE inviter_id = ?", (str(inviter_id),)
        ).fetchall()
    return [{"name": r[0], "email": r[1], "vpn_link": r[2], "created_at": r[3]} for r in rows]

def _delete_invite_sync(email: str):
    with _DB_LOCK:
        _db_conn.execute("DELETE FROM invites WHERE email = ?", (email,))
        _db_conn.commit()

def _add_referral_sync(referred_id: int, referrer_id: int, created_at: str):
    with _DB_LOCK:
        _db_conn.execute(
            "INSERT OR IGNORE INTO referrals (referred_id, referrer_id, created_at) VALUES (?, ?, ?)",
            (str(referred_id), str(referrer_id), created_at)
        )
        _db_conn.commit()

def _get_referrals_count_sync(referrer_id: int) -> int:
    with _DB_LOCK:
        row = _db_conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (str(referrer_id),)
        ).fetchone()
    return row[0] if row else 0

def _referral_exists_sync(referred_id: int) -> bool:
    with _DB_LOCK:
        row = _db_conn.execute("SELECT 1 FROM referrals WHERE referred_id = ?", (str(referred_id),)).fetchone()
    return row is not None

def _user_exists_sync(tg_id: int) -> bool:
    with _DB_LOCK:
        row = _db_conn.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    return row is not None

async def db_add_user(tg_id: int, username: str, display_name: str):
    await asyncio.to_thread(_add_user_sync, tg_id, username, display_name)

async def db_get_all_users() -> list:
    return await asyncio.to_thread(_get_all_users_sync)

async def db_add_invite(inviter_id: int, name: str, email: str, vpn_link: str, created_at: str):
    await asyncio.to_thread(_add_invite_sync, inviter_id, name, email, vpn_link, created_at)

async def db_get_invites(inviter_id: int) -> list:
    return await asyncio.to_thread(_get_invites_sync, inviter_id)

async def db_delete_invite(email: str):
    await asyncio.to_thread(_delete_invite_sync, email)

async def db_add_referral(referred_id: int, referrer_id: int, created_at: str):
    await asyncio.to_thread(_add_referral_sync, referred_id, referrer_id, created_at)

async def db_get_referrals_count(referrer_id: int) -> int:
    return await asyncio.to_thread(_get_referrals_count_sync, referrer_id)

async def db_referral_exists(referred_id: int) -> bool:
    return await asyncio.to_thread(_referral_exists_sync, referred_id)

async def db_user_exists(tg_id: int) -> bool:
    return await asyncio.to_thread(_user_exists_sync, tg_id)

def get_headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

def format_bytes(b) -> str:
    if not b or b <= 0:
        return "0 B"
    s = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    i = int(math.floor(math.log(b, 1024)))
    i = min(i, len(s) - 1)
    p = math.pow(1024, i)
    r = round(b / p, 2)
    return f"{r} {s[i]}"

def format_expiry(ms: int) -> str:
    if not ms or ms == 0:
        return "Без ограничений"
    try:
        dt = datetime.fromtimestamp(ms / 1000)
        now = datetime.now()
        delta = dt - now
        date_str = dt.strftime("%d.%m.%Y")
        if delta.days > 0:
            return f"{date_str} (Осталось: {delta.days} дн.)"
        elif delta.days == 0:
            return f"{date_str} (Истекает сегодня)"
        else:
            return f"{date_str} (Истек)"
    except Exception:
        return "Ошибка даты"

def get_client_email_by_tg_id(tg_id: int) -> str:
    return f"id_{tg_id}"

def get_qr_url(data: str) -> str:
    encoded_data = urllib.parse.quote(data)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_data}"

def get_user_display_name(from_user) -> str:
    if from_user.username:
        return f"@{html.escape(from_user.username)}"
    first_name = from_user.first_name or "Пользователь"
    return html.escape(first_name)

def make_friend_email(inviter_tg_id: int) -> str:
    return f"inv_{inviter_tg_id}_{uuid.uuid4().hex[:8]}"

async def track_user(event_from_user):
    if event_from_user:
        await db_add_user(
            event_from_user.id,
            event_from_user.username,
            event_from_user.full_name
        )

async def create_custom_qr_image(qr_data_bytes: bytes) -> bytes:
    try:
        qr_img = Image.open(io.BytesIO(qr_data_bytes)).convert("RGBA")
        qr_img = qr_img.resize((260, 260), Image.Resampling.LANCZOS)

        bg_color = (10, 10, 10, 255)
        canvas = Image.new("RGBA", (380, 380), bg_color)
        draw = ImageDraw.Draw(canvas)

        draw.rounded_rectangle([45, 45, 335, 335], radius=15, fill=(255, 255, 255, 255))
        canvas.paste(qr_img, (60, 60), qr_img)

        out_io = io.BytesIO()
        canvas.convert("RGB").save(out_io, format="PNG")
        return out_io.getvalue()
    except Exception as e:
        logger.error(f"Ошибка стилизации QR-кода: {e}")
        return qr_data_bytes

def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подключить VPN", callback_data="btn_connect", icon_custom_emoji_id="5258073068852485953", style="primary")],
            [InlineKeyboardButton(text="Мой Кабинет", callback_data="btn_profile", icon_custom_emoji_id="5257963315258204021")],
            [
                InlineKeyboardButton(text="Пригласить друга", callback_data="btn_invite_menu", icon_custom_emoji_id="5258362837411045098"),
                InlineKeyboardButton(text="Приглашённые", callback_data="btn_invited_list", icon_custom_emoji_id="5258513401784573443")
            ],
            [InlineKeyboardButton(text="Поддержка", callback_data="btn_support", icon_custom_emoji_id="5258215850745275216")]
        ]
    )

def get_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_profile_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обновить данные", callback_data="btn_refresh_profile", icon_custom_emoji_id="5258420634785947640", style="primary")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_qr_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вернуться к ссылке", callback_data="btn_connect", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_connect_keyboard(sub_id: str):
    web_connect_url = f"{SUB_DOMAIN}/{sub_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Настроить в Happ", url=web_connect_url, icon_custom_emoji_id="5258073068852485953")],
            [InlineKeyboardButton(text="Показать QR-код", callback_data="btn_qr_code", icon_custom_emoji_id="5257974976094412956")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_invite_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать доступ вручную", callback_data="btn_invite_manual", icon_custom_emoji_id="5258362837411045098", style="primary")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_no_profile_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подключить VPN", callback_data="btn_connect", icon_custom_emoji_id="5258073068852485953", style="primary")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_invite_form_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить", callback_data="btn_cancel_invite", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

def get_invite_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пригласить ещё", callback_data="btn_invite_menu", icon_custom_emoji_id="5258362837411045098")],
            [InlineKeyboardButton(text="Приглашённые", callback_data="btn_invited_list", icon_custom_emoji_id="5258513401784573443")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ]
    )

clutter_control_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Скрыть", callback_data="btn_delete_msg", icon_custom_emoji_id="5258130763148172425")]
])

_clients_cache: list = []
_clients_cache_ts: float = 0
CLIENTS_CACHE_TTL = 5  # секунд
_clients_cache_lock = asyncio.Lock()

async def get_all_clients(force: bool = False) -> list:
    global _clients_cache, _clients_cache_ts
    now = asyncio.get_event_loop().time()
    if not force and _clients_cache and (now - _clients_cache_ts) < CLIENTS_CACHE_TTL:
        return _clients_cache

    async with _clients_cache_lock:
        now = asyncio.get_event_loop().time()
        if not force and _clients_cache and (now - _clients_cache_ts) < CLIENTS_CACHE_TTL:
            return _clients_cache

        url = f"{XUI_URL}/panel/api/clients/list"
        try:
            async with session.get(url, headers=get_headers(), timeout=TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    logger.error(f"Ошибка получения списка клиентов. Статус: {r.status}")
                    return _clients_cache
                data = await r.json()
                _clients_cache = data.get("clients") or data.get("obj") or []
                _clients_cache_ts = now
                return _clients_cache
        except Exception as e:
            logger.error(f"Исключение при получении общего списка клиентов: {e}")
            return _clients_cache

_inbound_ids_cache: list = []
_inbound_ids_cache_ts: float = 0
INBOUND_CACHE_TTL = 300  # секунд

async def fetch_inbound_ids_from_panel() -> list:
    """Запрашивает у панели 3x-ui список всех настроенных inbound'ов и возвращает их ID."""
    url = f"{XUI_URL}/panel/api/inbounds/list"
    try:
        async with session.get(url, headers=get_headers(), timeout=TIMEOUT, ssl=False) as r:
            if r.status != 200:
                logger.error(f"Не удалось получить список inbounds. Статус: {r.status}")
                return []
            data = await r.json()
            inbounds = data.get("obj") or data.get("inbounds") or []
            ids = [inb.get("id") for inb in inbounds if inb.get("id") is not None]
            return ids
    except Exception as e:
        logger.error(f"Исключение при получении списка inbounds: {e}")
        return []

async def get_inbound_ids() -> list:
    """Возвращает актуальный список ID inbound'ов (с кэшем на INBOUND_CACHE_TTL секунд).
    Если панель недоступна и кэш пуст — используется список из .env (INBOUND_ID) как запасной вариант."""
    global _inbound_ids_cache, _inbound_ids_cache_ts
    now = asyncio.get_event_loop().time()
    if _inbound_ids_cache and (now - _inbound_ids_cache_ts) < INBOUND_CACHE_TTL:
        return _inbound_ids_cache

    fresh_ids = await fetch_inbound_ids_from_panel()
    if fresh_ids:
        _inbound_ids_cache = fresh_ids
        _inbound_ids_cache_ts = now
        return fresh_ids

    if _inbound_ids_cache:
        logger.warning("Не удалось обновить список inbounds, используется предыдущий кэш.")
        return _inbound_ids_cache
    logger.warning("Не удалось получить inbounds с панели, используется запасной список из .env (INBOUND_ID).")
    return INBOUND_IDS_FALLBACK

async def create_client(email: str, tg_id: int, username: str = None) -> bool:
    url = f"{XUI_URL}/panel/api/clients/add"
    comment = f"@{username}" if username else "No Username"
    inbound_ids = await get_inbound_ids()
    payload = {
        "client": {
            "email": email,
            "subId": email,
            "comment": comment,
            "totalGB": 0,
            "expiryTime": 0,
            "tgId": tg_id,
            "limitIp": 0,
            "enable": True
        },
        "inboundIds": inbound_ids
    }
    try:
        async with session.post(url, json=payload, headers=get_headers(), timeout=TIMEOUT, ssl=False) as r:
            if r.status in [200, 201]:
                return True
            response_text = await r.text()
            logger.error(f"Не удалось создать клиента. Код: {r.status}, Ответ: {response_text}")
            return False
    except Exception as e:
        logger.error(f"Исключение при создании клиента {email}: {e}")
        return False

async def create_friend_client(email: str, friend_name: str) -> bool:
    url = f"{XUI_URL}/panel/api/clients/add"
    inbound_ids = await get_inbound_ids()
    payload = {
        "client": {
            "email": email,
            "subId": email,
            "comment": friend_name,
            "totalGB": 0,
            "expiryTime": 0,
            "tgId": 0,
            "limitIp": 0,
            "enable": True
        },
        "inboundIds": inbound_ids
    }
    try:
        async with session.post(url, json=payload, headers=get_headers(), timeout=TIMEOUT, ssl=False) as r:
            if r.status in [200, 201]:
                return True
            response_text = await r.text()
            logger.error(f"Не удалось создать друга. Код: {r.status}, Ответ: {response_text}")
            return False
    except Exception as e:
        logger.error(f"Исключение при создании друга {email}: {e}")
        return False

async def get_client_data(email: str, force: bool = False) -> dict | None:
    clients = await get_all_clients(force=force)
    for c in clients:
        if c.get("email") == email:
            return c
    return None

async def update_client_comment(email: str, tg_id: int, username: str = None, client: dict = None) -> bool:
    if client is None:
        client = await get_client_data(email)
    if not client:
        return False

    comment = f"@{username}" if username else "No Username"
    if client.get("comment") == comment:
        return True

    url = f"{XUI_URL}/panel/api/clients/update/{email}"
    payload = {
        "email": email,
        "subId": client.get("subId", email),
        "comment": comment,
        "totalGB": client.get("totalGB", 0),
        "expiryTime": client.get("expiryTime", 0),
        "tgId": tg_id,
        "limitIp": client.get("limitIp", 0),
        "enable": client.get("enable", True)
    }
    try:
        async with session.post(url, json=payload, headers=get_headers(), timeout=TIMEOUT, ssl=False) as r:
            return r.status in [200, 201]
    except Exception as e:
        logger.error(f"Исключение при обновлении комментария для {email}: {e}")
        return False

async def sync_manual_invites(inviter_tg_id: int) -> list:
    invites = await db_get_invites(inviter_tg_id)
    if not invites:
        return []

    clients = await get_all_clients()
    existing_emails = {c.get("email") for c in clients if c.get("email") and c.get("enable", True)}

    alive = []
    for inv in invites:
        email = inv.get("email", "")
        if email and email in existing_emails:
            alive.append(inv)
        else:
            await db_delete_invite(email)

    return alive

async def send_or_edit_main_menu(target, user_id: int, user_display_name: str, edit: bool = False, client: dict = None):
    if client is None:
        email = get_client_email_by_tg_id(user_id)
        client = await get_client_data(email)

    sub_info = ""
    if client:
        sub_id = client.get("subId") or client.get("id")
        link = f"{SUB_DOMAIN}/{sub_id}"
        sub_info = (
            f"Ваша подписка:\n"
            f"<blockquote><code>{link}</code></blockquote>\n"
        )

    menu_text = (
        f"<b>Главная</b>\n\n"
        f"Via — это когда между вами и сетью не должно быть ничего лишнего.\n\n"
        f"Привет, <b>{user_display_name}</b>!\n\n"
        f"{sub_info}\n"
        f"Что доступно в боте:\n"
        f"• Подключение в одно касание на телефоне, ПК и в браузере\n"
        f"• Личный кабинет со статистикой трафика и сроком действия\n"
        f"• Доступы для друзей — даже без Telegram"
    )

    if edit:
        await target.edit_text(menu_text, reply_markup=get_main_keyboard())
    else:
        await target.answer(menu_text, reply_markup=get_main_keyboard())

@dp.message(F.text.startswith("/start"))
async def start(m: Message, state: FSMContext):
    await state.clear()

    user_id = m.from_user.id
    email = get_client_email_by_tg_id(user_id)

    already_tracked = await db_user_exists(user_id)
    already_referred = await db_referral_exists(user_id)

    await track_user(m.from_user)

    args = m.text.split()[1] if len(m.text.split()) > 1 else None
    if args and args.startswith("ref_"):
        try:
            inviter_id = int(args.replace("ref_", ""))
            if inviter_id != user_id and not already_referred and not already_tracked:
                now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
                await db_add_referral(user_id, inviter_id, now_str)
                user_mention = m.from_user.full_name
                try:
                    await bot.send_message(
                        chat_id=inviter_id,
                        text=(
                            f"Новый реферал:\n"
                            f"Пользователь <b>{user_mention}</b> подключился по вашей ссылке."
                        ),
                        reply_markup=clutter_control_kb
                    )
                except Exception:
                    pass
        except ValueError:
            pass

    client = await get_client_data(email)
    if client:
        asyncio.create_task(update_client_comment(email, user_id, m.from_user.username, client=client))

    user_name = get_user_display_name(m.from_user)
    await send_or_edit_main_menu(m, user_id, user_name, edit=False, client=client)

@dp.callback_query(F.data == "btn_main_menu")
async def main_menu_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    await state.clear()
    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    client = await get_client_data(email)
    if client:
        asyncio.create_task(update_client_comment(email, user_id, call.from_user.username, client=client))

    user_name = get_user_display_name(call.from_user)
    await send_or_edit_main_menu(call.message, user_id, user_name, edit=True, client=client)
    await call.answer()

@dp.callback_query(F.data == "btn_connect")
async def connect_callback(call: CallbackQuery):
    await track_user(call.from_user)
    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    await call.answer("Проверка настроек...")
    client = await get_client_data(email)

    if not client:
        success = await create_client(email, user_id, call.from_user.username)
        if not success:
            await call.message.edit_text(
                "Ошибка создания профиля.\n\n"
                "Сервер временно недоступен. Повторите запрос позже.",
                reply_markup=get_back_keyboard()
            )
            return
        client = await get_client_data(email, force=True)
    else:
        asyncio.create_task(update_client_comment(email, user_id, call.from_user.username, client=client))

    if not client:
        await call.message.edit_text(
            "Не удалось получить параметры подключения.",
            reply_markup=get_back_keyboard()
        )
        return

    sub_id = client.get("subId") or client.get("id")
    link = f"{SUB_DOMAIN}/{sub_id}"

    connect_text = (
        f"<b>Конфигурация готова</b>\n\n"
        f"Это твоя персональная ссылка-подписка, привязанная к аккаунту:\n"
        f"<blockquote><code>{link}</code></blockquote>\n\n"
        f"Как подключиться:\n"
        f"1. Нажми на «Настроить в Happ» и открой наш веб-сайт.\n"
        f"2. Выбери устройство из представленных.\n"
        f"3. Следуй подсказкам на экране - все просто!\n\n"
        f"Хочешь быстрее? Нажми «Показать QR-код» и отсканируй его камерой телефона."
    )
    if call.message.photo:
        await call.message.delete()
        await call.message.answer(connect_text, reply_markup=get_connect_keyboard(sub_id))
    else:
        await call.message.edit_text(connect_text, reply_markup=get_connect_keyboard(sub_id))

@dp.callback_query(F.data == "btn_qr_code")
async def qr_code_callback(call: CallbackQuery):
    await track_user(call.from_user)
    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    await call.answer("Генерация премиум-стиля...")
    client = await get_client_data(email)

    if not client:
        await call.message.edit_text("Конфигурация не найдена.", reply_markup=get_back_keyboard())
        return

    sub_id = client.get("subId") or client.get("id")
    link = f"{SUB_DOMAIN}/{sub_id}"
    qr_url = get_qr_url(link)

    try:
        async with session.get(qr_url, timeout=TIMEOUT, ssl=False) as resp:
            if resp.status == 200:
                photo_bytes = await resp.read()
                custom_photo_bytes = await create_custom_qr_image(photo_bytes)

                photo = BufferedInputFile(custom_photo_bytes, filename="qrcode.png")
                await call.message.delete()
                await call.message.answer_photo(
                    photo,
                    caption=(
                        f"<b>QR-код подписки</b>\n\n"
                        f"Открой Happ → «Добавить подписку» → камера, наведи на код — ссылка добавится автоматически.\n\n"
                        f"Ссылка:\n<blockquote><code>{link}</code></blockquote>"
                    ),
                    reply_markup=get_qr_back_keyboard()
                )
            else:
                await call.message.edit_text("Ошибка QR. Используйте ссылку.", reply_markup=get_back_keyboard())
    except Exception as e:
        logger.error(f"Исключение при генерации брендированного QR-кода: {e}")
        await call.message.edit_text("Сервер QR недоступен. Скопируйте ссылку.", reply_markup=get_back_keyboard())

@dp.callback_query(F.data.in_(["btn_profile", "btn_refresh_profile"]))
async def profile_callback(call: CallbackQuery):
    await track_user(call.from_user)
    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    if call.data == "btn_refresh_profile":
        await call.answer("Обновлено.")
    else:
        await call.answer("Загрузка...")

    client = await get_client_data(email)

    if not client:
        profile_text = (
            f"<b>Личный кабинет</b>\n\n"
            f"Доступ еще не активирован — он создается автоматически при первом подключении.\n\n"
            f"Нажми «Подключить VPN» в главном меню, и через несколько секунд здесь появится твоя статистика."
        )
        await call.message.edit_text(profile_text, reply_markup=get_back_keyboard())
        return

    traffic = client.get("traffic") or {}
    up = traffic.get("up", 0)
    down = traffic.get("down", 0)

    total_bytes = client.get("total", 0)
    if total_bytes is None:
        total_bytes = 0

    if total_bytes == 0:
        total_bytes = client.get("totalGB", 0) or 0

    status = "Активен" if client.get("enable", True) else "Деактивирован"
    expiry = format_expiry(client.get("expiryTime", 0))
    display_name = get_user_display_name(call.from_user)

    profile_text = (
        f"<b>Статистика профиля</b>\n\n"
        f"• Аккаунт: <code>{display_name}</code>\n"
        f"• Статус: <b>{status}</b>\n"
        f"• Срок действия: <b>{expiry}</b>\n\n"
        f"Трафик:\n"
        f"├ Отправлено: <code>{format_bytes(up)}</code>\n"
        f"├ Скачано: <code>{format_bytes(down)}</code>\n"
        f"├ Общий: <code>{format_bytes(up + down)}</code>\n"
        f"└ Лимит: <code>{format_bytes(total_bytes) if total_bytes > 0 else 'Без ограничений'}</code>"
    )

    try:
        await call.message.edit_text(profile_text, reply_markup=get_profile_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в профиле: {e}")

@dp.callback_query(F.data == "btn_support")
async def support_callback(call: CallbackQuery):
    await track_user(call.from_user)
    await call.answer("Статус сети...")

    support_text = (
        f"<b>Техническая поддержка</b>\n\n"
        f"Если что-то не работает:\n"
        f"1. Обнови подписку в приложении Happ.\n"
        f"2. Попробуй переподключиться через 1-2 минуты.\n"
        f"3. Если проблема не уходит — напиши нам, опиши ситуацию и приложи скриншот.\n\n"
        f"По всем вопросам пишите в поддержку:\n"
        f"@via_support_bot"
    )
    await call.message.edit_text(support_text, reply_markup=get_back_keyboard())

@dp.callback_query(F.data == "btn_invite_menu")
async def invite_menu_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    await state.clear()

    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    await call.answer("Загрузка...")

    own_client = await get_client_data(email)
    if not own_client:
        no_profile_text = (
            f"<b>Сначала подключи VPN себе</b>\n\n"
            f"Чтобы приглашать друзей и выдавать им доступ, у тебя сначала должен быть собственный профиль — "
            f"это займёт пару секунд и происходит автоматически при первом подключении.\n\n"
            f"Нажми «Подключить VPN» в главном меню, а после возвращайся в этот раздел — "
            f"тогда появится твоя личная реферальная ссылка и возможность создать доступ другу вручную."
        )
        await call.message.edit_text(no_profile_text, reply_markup=get_no_profile_keyboard())
        return

    bot_info = await bot.get_me()
    bot_username = bot_info.username
    inviter_id = user_id
    ref_url = f"https://t.me/{bot_username}?start=ref_{inviter_id}"

    invite_text = (
        f"<b>Пригласить друга</b>\n\n"
        f"Отправь другу персональную ссылку — как только он запустит бота, реферал засчитается автоматически:\n"
        f"<blockquote><code>{ref_url}</code></blockquote>\n\n"
        f"У друга нет Telegram или ты хочешь выдать доступ напрямую? Создай его вручную — кнопка ниже."
    )
    await call.message.edit_text(invite_text, reply_markup=get_invite_menu_keyboard())

@dp.callback_query(F.data == "btn_invite_manual")
async def invite_manual_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    await call.answer()

    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)
    own_client = await get_client_data(email)
    if not own_client:
        no_profile_text = (
            f"<b>Сначала подключи VPN себе</b>\n\n"
            f"Чтобы выдавать доступ друзьям, у тебя сначала должен быть собственный профиль — "
            f"это займёт пару секунд и происходит автоматически при первом подключении.\n\n"
            f"Нажми «Подключить VPN» в главном меню, а после возвращайся к созданию доступа другу."
        )
        await call.message.edit_text(no_profile_text, reply_markup=get_no_profile_keyboard())
        return

    await state.set_state(InviteStates.waiting_for_name)

    invite_text = (
        f"<b>Новый доступ</b>\n\n"
        f"Как назвать этот доступ? Имя поможет узнать его в списке приглашённых — например, имя друга или название устройства.\n\n"
        f"Введи имя (до 64 символов):"
    )
    await call.message.edit_text(invite_text, reply_markup=get_invite_form_keyboard())

@dp.callback_query(F.data == "btn_cancel_invite")
async def cancel_invite_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    await state.clear()
    await call.answer("Отменено.")

    user_id = call.from_user.id
    user_name = get_user_display_name(call.from_user)
    await send_or_edit_main_menu(call.message, user_id, user_name, edit=True)

@dp.message(InviteStates.waiting_for_name)
async def process_invite_name(m: Message, state: FSMContext):
    await track_user(m.from_user)
    friend_name = html.escape(m.text.strip()) if m.text else ""

    if not friend_name or len(friend_name) > 64:
        await m.answer(
            "Некорректное значение. Введите имя еще раз:",
            reply_markup=get_invite_form_keyboard()
        )
        return

    await state.clear()
    inviter_id = m.from_user.id

    await bot.send_chat_action(m.chat.id, "typing")
    friend_email = make_friend_email(inviter_id)

    success = await create_friend_client(friend_email, friend_name)
    if not success:
        await m.answer(
            "Ошибка соединения с сервером. Повторите попытку позже.",
            reply_markup=get_invite_result_keyboard()
        )
        return

    friend_client = await get_client_data(friend_email, force=True)
    if not friend_client:
        await asyncio.sleep(1)
        friend_client = await get_client_data(friend_email, force=True)

    sub_id = (friend_client.get("subId") or friend_client.get("id")) if friend_client else friend_email
    vpn_link = f"{SUB_DOMAIN}/{sub_id}"
    friend_connect_url = f"{SUB_DOMAIN}/{sub_id}"

    created_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    await db_add_invite(inviter_id, friend_name, friend_email, vpn_link, created_str)

    if not friend_client:
        await m.answer(
            "<b>Доступ другу создан</b>\n\n"
            f"• Имя: <b>{friend_name}</b>\n\n"
            "Ссылка появится в разделе «Приглашённые» через несколько секунд — "
            "панели нужно немного времени, чтобы синхронизироваться.",
            reply_markup=get_invite_result_keyboard()
        )
        return

    result_text = (
        f"<b>Доступ другу создан</b>\n\n"
        f"• Имя: <b>{friend_name}</b>\n\n"
        f"Передай другу любую из ссылок ниже — подойдёт что удобнее:\n\n"
        f"Ссылка подписки (для Happ и других клиентов):\n"
        f"<blockquote><code>{vpn_link}</code></blockquote>\n"
        f"Ссылка на сайт (откроет инструкцию по подключению):\n"
        f"<blockquote><code>{friend_connect_url}</code></blockquote>"
    )
    await m.answer(result_text, reply_markup=get_invite_result_keyboard())

@dp.callback_query(F.data == "btn_invited_list")
async def invited_list_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    await state.clear()
    await call.answer("Загрузка списка...")

    user_id = call.from_user.id
    email = get_client_email_by_tg_id(user_id)

    own_client = await get_client_data(email)
    if not own_client:
        no_profile_text = (
            f"<b>Сначала подключи VPN себе</b>\n\n"
            f"Чтобы приглашать друзей и выдавать им доступ, у тебя сначала должен быть собственный профиль — "
            f"это займёт пару секунд и происходит автоматически при первом подключении.\n\n"
            f"Нажми «Подключить VPN» в главном меню, а после возвращайся в этот раздел — "
            f"тогда здесь появится список твоих приглашённых друзей."
        )
        await call.message.edit_text(no_profile_text, reply_markup=get_no_profile_keyboard())
        return

    invites = await sync_manual_invites(call.from_user.id)
    ref_count = await db_get_referrals_count(call.from_user.id)

    if not invites and ref_count == 0:
        list_text = (
            f"<b>Приглашённые друзья</b>\n\n"
            f"Рефералы: <b>0</b>\n"
            f"Создано вручную: <b>0</b>\n\n"
            f"Пока никого нет — поделись своей ссылкой или создай доступ другу прямо здесь."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Создать VPN другу", callback_data="btn_invite_manual", icon_custom_emoji_id="5258362837411045098", style="primary")],
            [InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")]
        ])
    else:
        list_text = (
            f"<b>Приглашённые пользователи</b>\n\n"
            f"Рефералы: <b>{ref_count}</b>\n"
            f"Создано вручную: <b>{len(invites)}</b>\n\n"
            f"Нажми на имя, чтобы посмотреть статистику и ссылки доступа:"
        )

        buttons = []
        row = []
        for inv in invites:
            name = inv.get("name", "Друг")
            email = inv.get("email", "")
            short_name = name[:10] + ".." if len(name) > 12 else name

            row.append(InlineKeyboardButton(text=short_name, callback_data=f"manage_friend:{email}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton(text="Добавить друга", callback_data="btn_invite_manual", icon_custom_emoji_id="5258362837411045098", style="primary")])
        buttons.append([InlineKeyboardButton(text="Назад в меню", callback_data="btn_main_menu", icon_custom_emoji_id="5258236805890710909")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await call.message.edit_text(list_text, reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка вывода списка друзей: {e}")

@dp.callback_query(F.data.startswith("manage_friend:"))
async def manage_friend_callback(call: CallbackQuery):
    await track_user(call.from_user)
    params = call.data.split(":")
    email = params[1]

    await call.answer("Загрузка...")

    client = await get_client_data(email)
    if not client:
        await call.message.edit_text(
            "Профиль не обнаружен или удален.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="btn_invited_list", icon_custom_emoji_id="5258236805890710909")]])
        )
        return

    inviter_id = call.from_user.id
    friends_list = await db_get_invites(inviter_id)
    friend_local = next((f for f in friends_list if f["email"] == email), {})
    friend_name = friend_local.get("name", client.get("comment", "Друг"))
    vpn_link = friend_local.get("vpn_link", f"{SUB_DOMAIN}/{client.get('subId', email)}")

    traffic = client.get("traffic") or {}
    up = traffic.get("up", 0)
    down = traffic.get("down", 0)
    total_used = up + down

    sub_id = client.get("subId") or client.get("id") or email
    friend_connect_url = f"{SUB_DOMAIN}/{sub_id}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сайт для друга", url=friend_connect_url, icon_custom_emoji_id="5258073068852485953")],
        [InlineKeyboardButton(text="Вернуться к списку", callback_data="btn_invited_list", icon_custom_emoji_id="5258236805890710909")]
    ])

    manage_text = (
        f"<b>Профиль друга</b>\n\n"
        f"• Имя: <b>{friend_name}</b>\n\n"
        f"Статистика трафика:\n"
        f"├ Отправлено: <code>{format_bytes(up)}</code>\n"
        f"├ Скачано: <code>{format_bytes(down)}</code>\n"
        f"└ Всего: <code>{format_bytes(total_used)}</code>\n\n"
        f"Ссылка подписки:\n"
        f"<blockquote><code>{vpn_link}</code></blockquote>\n"
        f"Ссылка на сайт:\n"
        f"<blockquote><code>{friend_connect_url}</code></blockquote>"
    )
    await call.message.edit_text(manage_text, reply_markup=kb)

@dp.callback_query(F.data == "btn_delete_msg")
async def delete_msg_callback(call: CallbackQuery):
    try:
        await call.message.delete()
        await call.answer()
    except Exception:
        await call.answer("Сообщение уже удалено или устарело.")

@dp.message(F.text == "/say")
async def cmd_broadcast_init(m: Message, state: FSMContext):
    await track_user(m.from_user)
    if not is_admin(m.from_user.id):
        return

    await state.set_state(AdminStates.waiting_for_broadcast)
    await m.answer(
        "<b>Админ-панель: Рассылка</b>\n\n"
        "Отправьте сообщение для рассылки всем пользователям. К нему будет добавлена кнопка «Скрыть».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_broadcast")]])
    )

@dp.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast_callback(call: CallbackQuery, state: FSMContext):
    await track_user(call.from_user)
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.answer("Отменено.")
    await call.message.edit_text("Действие отменено.", reply_markup=clutter_control_kb)

async def run_broadcast(m: Message, targets: list):
    success_count = 0
    fail_count = 0

    for user_id in targets:
        try:
            if m.text:
                await bot.send_message(chat_id=user_id, text=m.text, reply_markup=clutter_control_kb)
            else:
                await m.copy_to(chat_id=user_id, reply_markup=clutter_control_kb)
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Ошибка доставки пользователю {user_id}: {e}")
            fail_count += 1

    admin_clean_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Очистить отчёт", callback_data="btn_delete_msg", icon_custom_emoji_id="5258130763148172425")]
    ])

    await m.answer(
        f"<b>Рассылка завершена</b>\n\n"
        f"├ Всего в базе: <code>{len(targets)}</code>\n"
        f"├ Успешно доставлено: <code>{success_count}</code>\n"
        f"└ Ошибок: <code>{fail_count}</code>",
        reply_markup=admin_clean_kb
    )

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast_message(m: Message, state: FSMContext):
    await track_user(m.from_user)
    if not is_admin(m.from_user.id):
        return

    await state.clear()

    targets = await db_get_all_users()
    if not targets:
        await m.answer("Список рассылки пуст.")
        return

    asyncio.create_task(run_broadcast(m, targets))

async def main():
    global session
    init_db()

    session = aiohttp.ClientSession()
    logger.info("Глобальная HTTP-сессия успешно создана.")
    asyncio.create_task(_cleanup_throttle_cache())

    try:
        await dp.start_polling(bot)
    finally:
        await session.close()
        logger.info("Глобальная HTTP-сессия закрыта.")
        close_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
