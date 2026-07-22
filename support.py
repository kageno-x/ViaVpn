import asyncio
import os
import logging
import sqlite3
import threading
import html
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
DB_FILE = "support_database.db"

VIA_DB_FILE = os.getenv("VPN_DB_FILE", "vpn_database.db")

_ADMIN_IDS_ENV = os.getenv("SUPPORT_ADMIN_ID", "")
ADMIN_IDS = {int(x.strip()) for x in _ADMIN_IDS_ENV.split(",") if x.strip().lstrip("-").isdigit()}

if not TOKEN:
    logger.critical("Ошибка: Проверьте SUPPORT_BOT_TOKEN в .env!")
if not ADMIN_IDS:
    logger.warning("SUPPORT_ADMIN_ID не задан в .env — тикеты некому будет обрабатывать.")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

@dp.update.outer_middleware()
async def log_every_update(handler, event, data):
    logger.info(f"RAW UPDATE получен: update_id={getattr(event, 'update_id', '?')}")
    return await handler(event, data)

THROTTLE_INTERVAL = 0.7
_last_action_ts: dict[int, float] = {}

@dp.message.middleware()
async def throttling_middleware(handler, event, data):
    user = event.from_user
    if user is not None and not is_admin(user.id):
        now = asyncio.get_event_loop().time()
        last = _last_action_ts.get(user.id, 0)
        if now - last < THROTTLE_INTERVAL:
            return
        _last_action_ts[user.id] = now
    return await handler(event, data)

async def _cleanup_throttle_cache():
    while True:
        await asyncio.sleep(3600)
        now = asyncio.get_event_loop().time()
        stale = [uid for uid, ts in _last_action_ts.items() if now - ts > 3600]
        for uid in stale:
            _last_action_ts.pop(uid, None)

        stale_locks = [uid for uid, lock in _ticket_creation_locks.items() if not lock.locked()]
        for uid in stale_locks:
            _ticket_creation_locks.pop(uid, None)

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
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            last_admin_id INTEGER,
            last_admin_name TEXT,
            created_at TEXT,
            updated_at TEXT,
            closed_at TEXT,
            closed_by TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            sender_id INTEGER,
            text TEXT,
            forwarded_msg_id INTEGER,
            admin_chat_id INTEGER,
            created_at TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_ticket_cards (
            ticket_id INTEGER NOT NULL,
            admin_chat_id INTEGER NOT NULL,
            card_msg_id INTEGER,
            PRIMARY KEY (ticket_id, admin_chat_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ticket_messages_forward
        ON ticket_messages (admin_chat_id, forwarded_msg_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tickets_user_status
        ON tickets (user_id, status)
    """)
    _db_conn.commit()
    logger.info("База данных поддержки SQLite успешно инициализирована (WAL, persistent connection).")

def close_db():
    if _db_conn:
        _db_conn.close()
        logger.info("Соединение с базой данных поддержки закрыто.")

def _create_ticket_sync(user_id: int, username: str, display_name: str, created_at: str) -> int:
    with _DB_LOCK:
        cur = _db_conn.execute(
            "INSERT INTO tickets (user_id, username, display_name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'open', ?, ?)",
            (user_id, username, display_name, created_at, created_at)
        )
        _db_conn.commit()
        return cur.lastrowid

def _get_open_ticket_sync(user_id: int) -> dict | None:
    with _DB_LOCK:
        row = _db_conn.execute(
            "SELECT id, user_id, username, display_name, status, last_admin_id, last_admin_name, "
            "created_at, updated_at, closed_at, closed_by FROM tickets WHERE user_id = ? AND status = 'open' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
    if not row:
        return None
    return _ticket_row_to_dict(row)

def _get_ticket_by_id_sync(ticket_id: int) -> dict | None:
    with _DB_LOCK:
        row = _db_conn.execute(
            "SELECT id, user_id, username, display_name, status, last_admin_id, last_admin_name, "
            "created_at, updated_at, closed_at, closed_by FROM tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()
    if not row:
        return None
    return _ticket_row_to_dict(row)

def _ticket_row_to_dict(row) -> dict:
    return {
        "id": row[0], "user_id": row[1], "username": row[2], "display_name": row[3],
        "status": row[4], "last_admin_id": row[5], "last_admin_name": row[6],
        "created_at": row[7], "updated_at": row[8], "closed_at": row[9], "closed_by": row[10],
    }

def _get_user_closed_tickets_sync(user_id: int, limit: int = 10) -> list:
    with _DB_LOCK:
        rows = _db_conn.execute(
            "SELECT id, status, created_at, closed_at FROM tickets "
            "WHERE user_id = ? AND status = 'closed' ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [{"id": r[0], "status": r[1], "created_at": r[2], "closed_at": r[3]} for r in rows]

def _add_ticket_message_sync(ticket_id: int, sender: str, sender_id: int, text: str,
                              forwarded_msg_id: int, admin_chat_id: int, created_at: str):
    with _DB_LOCK:
        _db_conn.execute(
            "INSERT INTO ticket_messages (ticket_id, sender, sender_id, text, forwarded_msg_id, admin_chat_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, sender, sender_id, text, forwarded_msg_id, admin_chat_id, created_at)
        )
        _db_conn.commit()

def _touch_ticket_sync(ticket_id: int, updated_at: str, admin_id: int = None, admin_name: str = None):
    with _DB_LOCK:
        if admin_id is not None:
            _db_conn.execute(
                "UPDATE tickets SET updated_at = ?, last_admin_id = ?, last_admin_name = ? WHERE id = ?",
                (updated_at, admin_id, admin_name, ticket_id)
            )
        else:
            _db_conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (updated_at, ticket_id))
        _db_conn.commit()

def _close_ticket_sync(ticket_id: int, closed_at: str, closed_by: str):
    with _DB_LOCK:
        _db_conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? WHERE id = ?",
            (closed_at, closed_by, ticket_id)
        )
        _db_conn.commit()

def _find_ticket_by_forward_sync(admin_chat_id: int, forwarded_msg_id: int) -> int | None:
    with _DB_LOCK:
        row = _db_conn.execute(
            "SELECT ticket_id FROM ticket_messages WHERE admin_chat_id = ? AND forwarded_msg_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (admin_chat_id, forwarded_msg_id)
        ).fetchone()
    return row[0] if row else None

def _get_admin_card_msg_sync(ticket_id: int, admin_chat_id: int) -> int | None:
    with _DB_LOCK:
        row = _db_conn.execute(
            "SELECT card_msg_id FROM admin_ticket_cards WHERE ticket_id = ? AND admin_chat_id = ?",
            (ticket_id, admin_chat_id)
        ).fetchone()
    return row[0] if row else None

def _mark_admin_card_sent_sync(ticket_id: int, admin_chat_id: int, card_msg_id: int):
    with _DB_LOCK:
        _db_conn.execute(
            "INSERT OR REPLACE INTO admin_ticket_cards (ticket_id, admin_chat_id, card_msg_id) VALUES (?, ?, ?)",
            (ticket_id, admin_chat_id, card_msg_id)
        )
        _db_conn.commit()

async def create_ticket(user_id: int, username: str, display_name: str, created_at: str) -> int:
    return await asyncio.to_thread(_create_ticket_sync, user_id, username, display_name, created_at)

async def get_open_ticket(user_id: int) -> dict | None:
    return await asyncio.to_thread(_get_open_ticket_sync, user_id)

async def get_ticket_by_id(ticket_id: int) -> dict | None:
    return await asyncio.to_thread(_get_ticket_by_id_sync, ticket_id)

async def get_user_closed_tickets(user_id: int, limit: int = 10) -> list:
    return await asyncio.to_thread(_get_user_closed_tickets_sync, user_id, limit)

async def add_ticket_message(ticket_id: int, sender: str, sender_id: int, text: str,
                              forwarded_msg_id: int = None, admin_chat_id: int = None,
                              created_at: str = None):
    created_at = created_at or datetime.now().strftime("%d.%m.%Y %H:%M")
    await asyncio.to_thread(
        _add_ticket_message_sync, ticket_id, sender, sender_id, text, forwarded_msg_id, admin_chat_id, created_at
    )

async def touch_ticket(ticket_id: int, admin_id: int = None, admin_name: str = None):
    updated_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    await asyncio.to_thread(_touch_ticket_sync, ticket_id, updated_at, admin_id, admin_name)

async def close_ticket(ticket_id: int, closed_by: str):
    closed_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    await asyncio.to_thread(_close_ticket_sync, ticket_id, closed_at, closed_by)

async def find_ticket_by_forward(admin_chat_id: int, forwarded_msg_id: int) -> int | None:
    return await asyncio.to_thread(_find_ticket_by_forward_sync, admin_chat_id, forwarded_msg_id)

async def get_admin_card_msg(ticket_id: int, admin_chat_id: int) -> int | None:
    return await asyncio.to_thread(_get_admin_card_msg_sync, ticket_id, admin_chat_id)

async def mark_admin_card_sent(ticket_id: int, admin_chat_id: int, card_msg_id: int):
    await asyncio.to_thread(_mark_admin_card_sent_sync, ticket_id, admin_chat_id, card_msg_id)

_via_db_conn: sqlite3.Connection = None
_VIA_DB_LOCK: threading.Lock = None
_via_db_available = False

def init_via_db():
    global _via_db_conn, _VIA_DB_LOCK, _via_db_available
    _VIA_DB_LOCK = threading.Lock()
    try:
        if not os.path.exists(VIA_DB_FILE):
            logger.warning(f"Файл базы основного бота не найден: {VIA_DB_FILE}. Контекст недоступен.")
            return
        uri = f"file:{VIA_DB_FILE}?mode=ro"
        _via_db_conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        _via_db_available = True
        logger.info("Read-only подключение к базе основного бота (via_database.db) установлено.")
    except Exception as e:
        logger.error(f"Не удалось открыть базу основного бота в режиме read-only: {e}")
        _via_db_available = False

def close_via_db():
    if _via_db_conn:
        _via_db_conn.close()

def _get_via_user_info_sync(tg_id: int) -> dict | None:
    if not _via_db_available:
        return None
    try:
        with _VIA_DB_LOCK:
            user_row = _via_db_conn.execute(
                "SELECT username, display_name, created_at FROM users WHERE tg_id = ?", (tg_id,)
            ).fetchone()
            ref_row = _via_db_conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (str(tg_id),)
            ).fetchone()
        if not user_row:
            return None
        return {
            "username": user_row[0],
            "display_name": user_row[1],
            "registered_at": user_row[2],
            "referrals_count": ref_row[0] if ref_row else 0,
        }
    except Exception as e:
        logger.error(f"Ошибка чтения контекста пользователя из via_database.db: {e}")
        return None

async def get_via_user_info(tg_id: int) -> dict | None:
    return await asyncio.to_thread(_get_via_user_info_sync, tg_id)

def get_user_display_name(from_user) -> str:
    if from_user.username:
        return f"@{html.escape(from_user.username)}"
    first_name = from_user.first_name or "Пользователь"
    return html.escape(first_name)

async def notify_admins_new_message(ticket: dict, m: Message):
    if not ADMIN_IDS:
        return

    for admin_id in ADMIN_IDS:
        try:
            already_has_card = (await get_admin_card_msg(ticket["id"], admin_id)) is not None
            if not already_has_card:
                via_info = await get_via_user_info(ticket["user_id"])
                if via_info:
                    sub_line = (
                        f"Зарегистрирован в VPN-боте: <b>{html.escape(via_info.get('registered_at') or '—')}</b>\n"
                        f"Рефералов приглашено: <b>{via_info.get('referrals_count', 0)}</b>"
                    )
                else:
                    sub_line = "Нет данных о профиле в основном VPN-боте."

                card_text = (
                    f"<b>Новый тикет №{ticket['id']}</b>\n\n"
                    f"Пользователь: <b>{ticket['display_name']}</b>\n"
                    f"Username: {('@' + ticket['username']) if ticket['username'] else '—'}\n"
                    f"User ID: <code>{ticket['user_id']}</code>\n\n"
                    f"{sub_line}\n\n"
                    f"Отвечайте Reply на сообщения ниже. Команда /close {ticket['id']} закроет тикет."
                )
                card_msg = await bot.send_message(admin_id, card_text)
                await mark_admin_card_sent(ticket["id"], admin_id, card_msg.message_id)

            fwd = await bot.forward_message(chat_id=admin_id, from_chat_id=m.chat.id, message_id=m.message_id)
            await add_ticket_message(
                ticket_id=ticket["id"], sender="user", sender_id=ticket["user_id"], text=m.text or m.caption or "",
                forwarded_msg_id=fwd.message_id, admin_chat_id=admin_id
            )
        except Exception as e:
            logger.error(f"Не удалось переслать сообщение тикета №{ticket['id']} админу {admin_id}: {e}")

@dp.message(F.text.startswith("/start"))
async def start(m: Message, state: FSMContext):
    logger.info(f"/start от user_id={m.from_user.id}")
    await state.clear()
    ticket = await get_open_ticket(m.from_user.id)
    if ticket:
        await m.answer(
            f"У тебя уже открыто обращение №{ticket['id']} — просто напиши сюда, поддержка читает это же чат."
        )
        return

    await m.answer(
        "Опиши свой вопрос или проблему — следующее сообщение создаст обращение в поддержку. "
        "Если вопрос решится сам — позже можно написать /close."
    )

@dp.message(F.text.in_({"/close", "/done"}), ~F.from_user.id.in_(ADMIN_IDS))
async def user_close_ticket(m: Message, state: FSMContext):
    await state.clear()
    ticket = await get_open_ticket(m.from_user.id)
    if not ticket:
        await m.answer("У тебя нет открытого обращения.")
        return

    await close_ticket(ticket["id"], closed_by="user")
    await m.answer("Обращение закрыто. Если вопрос возникнет снова — просто напиши сюда.")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"Пользователь закрыл тикет №{ticket['id']}.")
        except Exception:
            pass

@dp.message(F.text.regexp(r"^/close(?:@\w+)?(?:\s+(\d+))?\s*$"), F.from_user.id.in_(ADMIN_IDS))
async def admin_close_ticket(m: Message):
    match = m.text.split()
    explicit_ticket_id = int(match[1]) if len(match) > 1 and match[1].isdigit() else None

    if explicit_ticket_id is not None:
        ticket = await get_ticket_by_id(explicit_ticket_id)
        if not ticket:
            await m.reply(f"Тикет №{explicit_ticket_id} не найден.")
            return
        if ticket["status"] != "open":
            await m.reply(f"Тикет №{explicit_ticket_id} уже закрыт.")
            return
    else:
        if not m.reply_to_message:
            await m.reply("Уточни номер тикета: /close 123 — либо ответь командой Reply на сообщение из нужного тикета.")
            return
        ticket_id = await find_ticket_by_forward(m.from_user.id, m.reply_to_message.message_id)
        if ticket_id is None:
            await m.reply("Не удалось определить тикет. Используй /close 123 с номером тикета.")
            return
        ticket = await get_ticket_by_id(ticket_id)
        if not ticket or ticket["status"] != "open":
            await m.reply("Тикет уже закрыт или не найден.")
            return

    await close_ticket(ticket["id"], closed_by="admin")

    try:
        await bot.send_message(ticket["user_id"], "Обращение в поддержку закрыто. Если вопрос остался — просто напиши снова.")
    except Exception:
        pass

    await m.reply(f"Тикет №{ticket['id']} закрыт.")


def is_admin_reply(message: Message) -> bool:
    if message.text and message.text.startswith("/close"):
        return False
    return bool(message.reply_to_message) and is_admin(message.from_user.id)

@dp.message(is_admin_reply)
async def process_admin_reply(m: Message):
    admin_chat_id = m.from_user.id
    replied_msg_id = m.reply_to_message.message_id

    ticket_id = await find_ticket_by_forward(admin_chat_id, replied_msg_id)
    if ticket_id is None:
        return
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or ticket["status"] != "open":
        return

    if not (m.text or m.caption):
        return
    
    admin_label = get_user_display_name(m.from_user)

    try:
        if m.photo or m.document:
            caption = html.escape(m.caption) if m.caption else None
            await m.copy_to(chat_id=ticket["user_id"], caption=caption)
        else:
            await bot.send_message(ticket["user_id"], html.escape(m.text or ""))
    except Exception as e:
        logger.error(f"Не удалось отправить ответ пользователю {ticket['user_id']} по тикету №{ticket_id}: {e}")
        return

    await add_ticket_message(
        ticket_id=ticket_id, sender="admin", sender_id=admin_chat_id, text=m.text or m.caption or ""
    )
    await touch_ticket(ticket_id, admin_id=admin_chat_id, admin_name=admin_label)

def is_user_content_message(message: Message) -> bool:
    if message.text and message.text.startswith("/"):
        return False
    return bool(message.text or message.photo or message.document)

_ticket_creation_locks: dict[int, asyncio.Lock] = {}

def _get_ticket_lock(user_id: int) -> asyncio.Lock:
    lock = _ticket_creation_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _ticket_creation_locks[user_id] = lock
    return lock

@dp.message(is_user_content_message)
async def process_user_message(m: Message, state: FSMContext):
    if is_admin(m.from_user.id):
        return

    async with _get_ticket_lock(m.from_user.id):
        ticket = await get_open_ticket(m.from_user.id)
        is_new = ticket is None
        if is_new:
            user_id = m.from_user.id
            username = m.from_user.username
            display_name = get_user_display_name(m.from_user)
            created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
            ticket_id = await create_ticket(user_id, username, display_name, created_at)
            ticket = await get_ticket_by_id(ticket_id)

    await notify_admins_new_message(ticket, m)
    if not is_new:
        await touch_ticket(ticket["id"])

@dp.errors()
async def global_error_handler(event):
    logger.critical(f"НЕОБРАБОТАННАЯ ОШИБКА при обработке апдейта: {event.exception}", exc_info=True)
    return True

async def main():
    init_db()
    init_via_db()

    me = await bot.get_me()
    logger.info(f"Бот поддержки запущен как @{me.username} (id={me.id}). Жду сообщений...")
    asyncio.create_task(_cleanup_throttle_cache())

    try:
        await dp.start_polling(bot)
    finally:
        close_db()
        close_via_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот поддержки остановлен.")
