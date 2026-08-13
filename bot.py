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
    r"\bдякую\b", r"\bспасибі\b", r"\bдякс\b",
    r"\bспасибо\b", r"\bблагодарю\b",
    r"\bthanks\b", r"\bthank\s+you\b", r"\bthx\b", r"\bty\b",
]
THANK_RE = re.compile("|".join(THANK_PATTERNS), re.IGNORECASE)
POSITIVE_REACTIONS = {"👍", "🔥", "❤️", "❤", "🙏", "👏", "✅", "💯"}

# --- СЛОВНИК ПЕРЕКЛАДІВ ---
TRANSLATIONS = {
    "uk": {
        "welcome": "👋 Привіт, <b>{names}</b>! Вітаємо в групі <b>{chat_name}</b>! 🔑\n\nРозкажіть, який у вас рівень навичок локсміта і де ви знаходитесь?",
        "rep_gain": "🏆 <b>{receiver}</b> отримує +1 до репутації від <b>{giver}</b>\nРепутація: <b>{rep}</b> | Ранг: <i>{rank}</i>",
        "rep_info": "🏆 <b>{target}</b>\nРепутація: <b>{rep}</b> | Ранг: <i>{rank}</i>",
        "top_title": "⭐ <b>Рейтинг групи</b>",
        "top_empty": "⭐ Рейтинг поки порожній.",
        "start": "Я бот репутації для Telegram-груп.\nКоманди: /rep, /top",
    },
    "en": {
        "welcome": "👋 Hello, <b>{names}</b>! Welcome to <b>{chat_name}</b>! 🔑\n\nPlease tell us, what is your locksmith skill level and where are you located?",
        "rep_gain": "🏆 <b>{receiver}</b> gets +1 reputation from <b>{giver}</b>\nReputation: <b>{rep}</b> | Rank: <i>{rank}</i>",
        "rep_info": "🏆 <b>{target}</b>\nReputation: <b>{rep}</b> | Rank: <i>{rank}</i>",
        "top_title": "⭐ <b>Group Leaderboard</b>",
        "top_empty": "⭐ Leaderboard is currently empty.",
        "start": "I am a reputation bot for Telegram groups.\nCommands: /rep, /top",
    },
    "ru": {
        "welcome": "👋 Привет, <b>{names}</b>! Добро пожаловать в группу <b>{chat_name}</b>! 🔑\n\nРасскажите, каков ваш уровень навыков локсмита и где вы находитесь?",
        "rep_gain": "🏆 <b>{receiver}</b> получает +1 к репутации от <b>{giver}</b>\nРепутация: <b>{rep}</b> | Ранг: <i>{rank}</i>",
        "rep_info": "🏆 <b>{target}</b>\nРепутация: <b>{rep}</b> | Ранг: <i>{rank}</i>",
        "top_title": "⭐ <b>Рейтинг группы</b>",
        "top_empty": "⭐ Рейтинг пока пуст.",
        "start": "Я бот репутации для Telegram-групп.\nКоманды: /rep, /top",
    },
}

# 10 чітких рангів (від 0 до 9) на основі балів
RANKS = {
    "uk": [
        (0, "Ранг 0: Новачок 🌱"),
        (11, "Ранг 1: Учень 🛠️"),
        (26, "Ранг 2: Практик ⚙️"),
        (51, "Ранг 3: Слюсар 🔓"),
        (101, "Ранг 4: Спеціаліст 💻"),
        (181, "Ранг 5: Майстер 🔑"),
        (281, "Ранг 6: Експерт ⭐"),
        (401, "Ранг 7: Гуру Локсмітів 🔥"),
        (551, "Ранг 8: Професор Електроніки ⚡"),
        (751, "Ранг 9: Легенда Сервісу 🏆"),
    ],
    "en": [
        (0, "Rank 0: Newbie 🌱"),
        (11, "Rank 1: Apprentice 🛠️"),
        (26, "Rank 2: Practitioner ⚙️"),
        (51, "Rank 3: Locksmith 🔓"),
        (101, "Rank 4: Specialist 💻"),
        (181, "Rank 5: Master 🔑"),
        (281, "Rank 6: Expert ⭐"),
        (401, "Rank 7: Locksmith Guru 🔥"),
        (551, "Rank 8: Electronics Professor ⚡"),
        (751, "Rank 9: Service Legend 🏆"),
    ],
    "ru": [
        (0, "Ранг 0: Новичок 🌱"),
        (11, "Ранг 1: Ученик 🛠️"),
        (26, "Ранг 2: Практик ⚙️"),
        (51, "Ранг 3: Слесарь 🔓"),
        (101, "Ранг 4: Специалист 💻"),
        (181, "Ранг 5: Мастер 🔑"),
        (281, "Ранг 6: Эксперт ⭐"),
        (401, "Ранг 7: Гуру Локсмитов 🔥"),
        (551, "Ранг 8: Профессор Электроники ⚡"),
        (751, "Ранг 9: Легенда Сервиса 🏆"),
    ],
}

def get_lang(update: Update) -> str:
    user = update.effective_user
    if user and user.language_code:
        code = user.language_code.lower()
        if code.startswith("uk"):
            return "uk"
        if code.startswith("ru"):
            return "ru"
    return "en"  # За замовчуванням англійська

def get_user_lang(user) -> str:
    if user and getattr(user, "language_code", None):
        code = user.language_code.lower()
        if code.startswith("uk"):
            return "uk"
        if code.startswith("ru"):
            return "ru"
    return "en"

def get_lang_from_code(language_code) -> str:
    if language_code:
        code = language_code.lower()
        if code.startswith("uk"):
            return "uk"
        if code.startswith("ru"):
            return "ru"
    return "en"

def get_rank_name(rep: int, lang: str) -> str:
    lang_ranks = RANKS.get(lang, RANKS["en"])
    current_rank = lang_ranks[0][1]
    for threshold, title in lang_ranks:
        if rep >= threshold:
            current_rank = title
        else:
            break
    return current_rank

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            username TEXT,
            language_code TEXT,
            reputation INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN language_code TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            message_thread_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN message_thread_id INTEGER")
    except sqlite3.OperationalError:
        pass 
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
        """INSERT INTO users(chat_id,user_id,name,username,language_code,reputation)
           VALUES(?,?,?,?,?,0)
           ON CONFLICT(chat_id,user_id) DO UPDATE SET
             name=excluded.name,
             username=excluded.username,
             language_code=excluded.language_code""",
        (chat_id, user.id, display_name(user), user.username, user.language_code),
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
        pass  

async def send_temp(context, chat_id, text, thread_id=None, delay=10):
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
    lang = get_lang(update)
    conn = db()
    try:
        upsert_user(conn, chat.id, msg.from_user)
        conn.execute(
            "INSERT OR REPLACE INTO messages(chat_id,message_id,author_id,message_thread_id) VALUES(?,?,?,?)",
            (chat.id, msg.message_id, msg.from_user.id, thread_id),
        )

        text = (msg.text or msg.caption or "").strip()
        reply = msg.reply_to_message

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

                    row = conn.execute(
                        "SELECT reputation FROM users WHERE chat_id=? AND user_id=?",
                        (chat.id, receiver.id),
                    ).fetchone()
                    rep = row[0] if row else 0
                    receiver_lang = get_user_lang(receiver)
                    rank = get_rank_name(rep, receiver_lang)

                    txt = TRANSLATIONS[receiver_lang]["rep_gain"].format(
                        receiver=display_name(receiver),
                        giver=display_name(giver),
                        rep=rep,
                        rank=rank
                    )
                    await send_temp(context, chat.id, txt, thread_id=thread_id, delay=3600)
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
    lang = get_lang(update)

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
            "SELECT author_id, message_thread_id FROM messages WHERE chat_id=? AND message_id=?",
            (chat_id, reaction.message_id),
        ).fetchone()

        if not row:
            return

        receiver_id, thread_id = row[0], row[1]
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

                receiver_row = conn.execute(
                    "SELECT name, reputation, language_code FROM users WHERE chat_id=? AND user_id=?", 
                    (chat_id, receiver_id)
                ).fetchone()

                if receiver_row:
                    receiver_name, rep, receiver_language_code = receiver_row
                    receiver_lang = get_lang_from_code(receiver_language_code)
                    rank = get_rank_name(rep, receiver_lang)

                    txt = TRANSLATIONS[receiver_lang]["rep_gain"].format(
                        receiver=receiver_name,
                        giver=display_name(giver),
                        rep=rep,
                        rank=rank
                    )
                    await send_temp(context, chat_id, txt, thread_id=thread_id, delay=3600)
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

async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    thread_id = msg.message_thread_id
    target = msg.from_user
    lang = get_user_lang(target)

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

    rank = get_rank_name(rep, lang)
    txt = TRANSLATIONS[lang]["rep_info"].format(
        target=display_name(target),
        rep=rep,
        rank=rank
    )

    asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=60))
    await send_temp(context, chat.id, txt, thread_id=thread_id, delay=60)

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    thread_id = msg.message_thread_id
    lang = get_lang(update)
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

    t_set = TRANSLATIONS[lang]
    if not rows:
        text = t_set["top_empty"]
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = [t_set["top_title"]]
        for i, (name, rep) in enumerate(rows, 1):
            prefix = medals[i-1] if i <= 3 else f"{i}."
            rank = get_rank_name(rep, lang)
            lines.append(f"{prefix} <b>{name}</b> — <b>{rep}</b> (<i>{rank}</i>)")
        text = "\n".join(lines)

    asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=60))
    await send_temp(context, chat.id, text, thread_id=thread_id, delay=60)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    await update.effective_message.reply_text(TRANSLATIONS[lang]["start"])

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat

    asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=3600))

    if not msg.new_chat_members:
        return

    names = [display_name(u) for u in msg.new_chat_members if not u.is_bot]
    if not names:
        return

    thread_id = msg.message_thread_id
    chat_name = chat.title or "Group"
    lang = get_lang(update)

    text = TRANSLATIONS[lang]["welcome"].format(
        names=", ".join(names),
        chat_name=chat_name
    )
    await send_temp(context, chat.id, text, thread_id=thread_id, delay=3600)

async def clean_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if msg and chat:
        asyncio.create_task(delete_later(context.bot, chat.id, msg.message_id, delay=60))

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("rep", rep_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(MessageReactionHandler(on_reaction))

    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, clean_system_messages))
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
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
