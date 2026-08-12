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

POSITIVE_REACTIONS = {"👍", "🔥", "❤️", "❤", "🙏", "👏", "✅"}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
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

async def delete_later(bot, chat_id, message_id, delay=10):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def send_temp(update, context, text, delay=10):
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
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

    conn = db()
    try:
        upsert_user(conn, chat.id, msg.from_user)
        conn.execute(
            "INSERT OR REPLACE INTO messages(chat_id,message_id,author_id) VALUES(?,?,?)",
            (chat.id, msg.message_id, msg.from_user.id),
        )

        text = (msg.text or msg.caption or "").strip()
        reply = msg.reply_to_message

        if text and reply and reply.from_user and not reply.from_user.is_bot and THANK_RE.search(text):
            giver_id = msg.from_user.id
            receiver = reply.from_user
            if giver_id != receiver.id:
                upsert_user(conn, chat.id, receiver)
                try:
                    conn.execute(
                        """INSERT INTO thank_votes(chat_id,message_id,giver_id,receiver_id)
                           VALUES(?,?,?,?)""",
                        (chat.id, msg.message_id, giver_id, receiver.id),
                    )
                    change_rep(conn, chat.id, receiver.id, +1)
                    conn.commit()
                    rep = conn.execute(
                        "SELECT reputation FROM users WHERE chat_id=? AND user_id=?",
                        (chat.id, receiver.id),
                    ).fetchone()[0]
                    await send_temp(
                        update,
                        context,
                        f"🏆 <b>{display_name(receiver)}</b> отримує +1 до репутації.\n"
                        f"Репутація: <b>{rep}</b>",
                    )
                except sqlite3.IntegrityError:
                    pass
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
        row = conn.execute(
            "SELECT author_id FROM messages WHERE chat_id=? AND message_id=?",
            (chat_id, reaction.message_id),
        ).fetchone()
        if not row:
            return

        receiver_id = row[0]
        if receiver_id == giver.id:
            return

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
            except sqlite3.IntegrityError:
                return
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

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.new_chat_members:
        return
    names = [display_name(u) for u in msg.new_chat_members if not u.is_bot]
    if not names:
        return
    text = (
        f"👋 Вітаємо, <b>{', '.join(names)}</b>!\n"
        "Раді бачити вас у групі."
    )
    await send_temp(update, context, text, delay=20)

async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    target = (
        update.effective_message.reply_to_message.from_user
        if update.effective_message.reply_to_message
        and update.effective_message.reply_to_message.from_user
        else update.effective_user
    )
    conn = db()
    try:
        upsert_user(conn, update.effective_chat.id, target)
        conn.commit()
        row = conn.execute(
            "SELECT reputation FROM users WHERE chat_id=? AND user_id=?",
            (update.effective_chat.id, target.id),
        ).fetchone()
        rep = row[0] if row else 0
    finally:
        conn.close()
    await send_temp(update, context, f"🏆 <b>{display_name(target)}</b>\nРепутація: <b>{rep}</b>")

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    conn = db()
    try:
        rows = conn.execute(
            """SELECT name, reputation FROM users
               WHERE chat_id=? AND reputation>0
               ORDER BY reputation DESC, name COLLATE NOCASE ASC
               LIMIT 10""",
            (update.effective_chat.id,),
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
    await send_temp(update, context, text, delay=20)

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
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
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
        # Локальний запуск для тесту
        application.run_polling(allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    main()
