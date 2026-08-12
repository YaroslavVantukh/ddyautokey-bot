import os
import re
import sqlite3
import asyncio
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", "10000"))
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "ddyautokey-secret")

if WEBHOOK_BASE_URL:
    BASE_URL = WEBHOOK_BASE_URL.rstrip("/")
elif RENDER_EXTERNAL_HOSTNAME:
    BASE_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}"
else:
    BASE_URL = None

DB_PATH = os.environ.get("DB_PATH", "reputation.db")

THANK_PATTERNS = [
    r"\bдякую\b",
    r"\bспасибі\b",
    r"\bдякс\b",
    r"\bспасибо\b",
    r"\bблагодарю\b",
    r"\bthanks\b",
    r"\bthank\s+you\b",
    r"\bthx\b",
    r"\bty\b",
]
THANK_RE = re.compile("|".join(THANK_PATTERNS), re.IGNORECASE)

POSITIVE_REACTIONS = {"👍", "🔥", "❤️", "❤", "🙏", "👏", "✅", "💯"}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Таблиця користувачів
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            username TEXT,
            reputation INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    
    # Таблиця повідомлень (з додаванням message_thread_id)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            message_thread_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    # Намагаємося додати колонку, якщо база стара, щоб уникнути помилок після оновлення
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN message_thread_id INTEGER")
    except sqlite3.OperationalError:
        pass 
        
    # Таблиці для обліку голосів (щоб уникнути накруток)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thank_votes (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            giver_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id, giver_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reaction_votes (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            giver_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id, giver_id)
        )
    """)
    conn.commit()
    return conn

def display_name(user):
    name = (user.full_name or user.username or str(user.id)).strip()
    return name[:120]

def upsert_user(conn, chat_id, user):
    conn.execute(
        """INSERT INTO users(chat_id,user_id,name,username,reputation)
           VALUES(?,?,?,?,0)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
             name=excluded.name, username=excluded.username""",
        (chat_id, user.id, display_name(user), user.username),
    )

def change_rep(conn, chat_id, user_id, delta):
    conn.execute(
        "UPDATE users SET reputation = MAX(0, reputation + ?) WHERE chat_id=? AND user_id=?",
        (delta, chat_id, user_id),
    )

async def delete_later(bot, chat_id, message_id, delay):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # Ігноруємо помилки, якщо повідомлення вже видалено

async def send_temp(context, chat_id, text, thread_id=None, delay=10):
    """Надсилає повідомлення у конкретну гілку та видаляє його через delay секунд"""
    msg = await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    asyncio.create_task(delete_later(context.bot, msg.chat_id, msg.message_id, delay))
    return msg

async def remember_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.from_user or msg.from_user.is_bot:
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return

    thread_id = msg.message_thread_id
    conn = db()
    try:
        upsert_user(conn, chat.id, msg.from_user)
        # Запам'ятовуємо повідомлення, його автора та ГІЛКУ
        conn.execute(
            "INSERT OR REPLACE INTO messages(chat_id,message_id,author_id,message_thread_id) VALUES(?,?,?,?)",
            (chat.id, msg.message_id, msg.from_user.id, thread_id),
        )

        text = (msg.text or msg.caption or "").strip()
        reply = msg.reply_to_message

        # Обробка текстових подяк
        if text and reply and reply.from_user and not reply.from_user.is_bot and THANK_RE.search(text):
            giver = msg.from_user
            receiver = reply.from_user
            
            if giver.id != receiver.id:
                upsert_user(conn, chat.id, receiver)
                try:
                    conn.execute(
                        """INSERT INTO thank_votes(chat_id,message_id,giver_id,receiver_id)
                           VALUES(?,?,?,?)""",
                        (chat.id, reply.message_id, giver.id, receiver.id),
                    )
                    change_rep(conn, chat.id, receiver.id, +1)
                    conn.commit()
                    
                    rep = conn.execute(
                        "SELECT reputation FROM users WHERE chat_id=? AND user_id=?",
                        (chat.id, receiver.id),
                    ).fetchone()[0]
                    
                    # Відповідь бота в гілку + автовидалення через 1 годину (3600 сек)
                    await send_temp(
                        context,
                        chat.id,
                        f"🏆 <b>{display_name(receiver)}</b> отримує +1 до репутації від <b>{display_name(giver)}</b>\n"
                        f"Репутація: <b>{rep}</b>",
                        thread_id=thread_id,
                        delay=3600
                    )
                except sqlite3.IntegrityError:
                    pass # Вже дякував за це повідомлення
        conn.commit()
    finally:
        conn.close()

async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction or not reaction.user or reaction.user.is_bot:
        return

    chat_id = reaction.chat.id
    giver = reaction.user

    def emojis(items):
        result = set()
        for item in items or []:
            emoji = getattr(item, "emoji", None)
            if emoji:
                result.add(emoji)
        return result

    old_positive = bool(emojis(reaction.old_reaction) & POSITIVE_REACTIONS)
    new_positive = bool(emojis(reaction.new_reaction) & POSITIVE_REACTIONS)

    if old_positive == new_positive:
        return

    conn = db()
    try:
        # Шукаємо автора і гілку
        row = conn.execute(
            "SELECT author_id, message_thread_id FROM messages WHERE chat_id=? AND message_id=?",
            (chat_id, reaction.message_id),
        ).fetchone()
        
        if not row:
            return

        receiver_id, thread_id = row[0], row[1]
        
        if receiver_id == giver.id:
            return # Самому собі +1 давати не можна

        upsert_user(conn, chat_id, giver)

        if new_positive and not old_positive:
            try:
                conn.execute(
                    """INSERT INTO reaction_votes(chat_id,message_id,giver_id,receiver_id)
                       VALUES(?,?,?,?)""",
                    (chat_id, reaction.message_id, giver.id, receiver_id),
                )
                change_rep(conn, chat_id, receiver_id, +1)
                conn.commit()
                
                # Отримуємо нові дані для сповіщення
                receiver_name_row = conn.execute(
                    "SELECT name, reputation FROM users WHERE chat_id=? AND user_id=?", 
                    (chat_id, receiver_id)
                ).fetchone()
                
                if receiver_name_row:
                    receiver_name, rep = receiver_name_row[0], receiver_name_row[1]
                    
                    # Відповідь бота в гілку + автовидалення через 1 годину (3600 сек)
                    await send_temp(
                        context,
                        chat_id,
                        f"🏆 <b>{receiver_name}</b> отримує +1 до репутації від <b>{display_name(giver)}</b>\n"
                        f"Репутація: <b>{rep}</b>",
                        thread_id=thread_id,
                        delay=3600
                    )
            except sqlite3.IntegrityError:
                return # Вже ставив реакцію на це повідомлення
                
        elif old_positive and not new_positive:
            cur = conn.execute(
                """DELETE FROM reaction_votes
                   WHERE chat_id=? AND message_id=? AND giver_id=?""",
                (chat_id, reaction.message_id, giver.id),
            )
            if cur.rowcount:
                change_rep(conn, chat_id, receiver_id, -1)
                conn.commit()
    finally:
        conn.close()

async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
        
    thread_id = msg.message_thread_id
    target = (
        msg.reply_to_message.from_user
        if msg.reply_to_message and msg.reply_to_message.from_user
        else update.effective_user
    )
    
    conn = db()
    try:
        upsert_user(conn, chat.id, target)
        conn.commit()
        row = conn.execute(
            "SELECT reputation FROM users WHERE chat_id=? AND user_id=?",
            (chat.id, target.id),
        ).fetchone()
        rep = row[0] if row else 0
    finally:
        conn.close()
        
    # Видаляємо команду користувача через 60 секунд
    asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=60))
    # Відповідаємо в гілку і видаляємо відповідь бота через 60 секунд
    await send_temp(
        context, 
        chat.id, 
        f"🏆 <b>{display_name(target)}</b>\nРепутація: <b>{rep}</b>", 
        thread_id=thread_id, 
        delay=60
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
        
    thread_id = msg.message_thread_id
    conn = db()
    try:
        rows = conn.execute(
            """SELECT name, reputation FROM users
               WHERE chat_id=? AND reputation>0
               ORDER BY reputation DESC, name COLLATE NOCASE ASC
               LIMIT 10""",
            (chat.id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        text = "⭐ Рейтинг поки порожній."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["⭐ <b>Рейтинг групи</b>"]
        for i, (name, rep) in enumerate(rows, 1):
            prefix = medals[i-1] if i <= 3 else f"{i}."
            lines.append(f"{prefix} {name} — <b>{rep}</b>")
        text = "\n".join(lines)
        
    # Видаляємо команду користувача через 60 секунд
    asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=60))
    # Відповідаємо в гілку і видаляємо відповідь бота через 60 секунд
    await send_temp(
        context, 
        chat.id, 
        text, 
        thread_id=thread_id, 
        delay=60
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Я бот репутації для Telegram-груп.\n"
        "Команди: /rep, /top"
    )

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("rep", rep_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(MessageReactionHandler(on_reaction))
    
    # Використовуємо фільтр ALL, щоб перехоплювати фото, відео, документи та інше (ігноруємо команди)
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            remember_message,
        )
    )

    if BASE_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="telegram",
            webhook_url=f"{BASE_URL}/telegram",
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "message_reaction"],
        )
    else:
        application.run_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    main()
