import os
import re
import asyncio
import html
from datetime import datetime, timezone

import mysql.connector
from mysql.connector import IntegrityError, Error as MySQLError
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

DB_HOST = os.environ.get("DB_HOST", "db1.ho.ua")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_NAME = os.environ.get("DB_NAME", "ddyautokey1")
DB_USER = os.environ.get("DB_USER", "ddyautokey1")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "ddyautokey1")

if WEBHOOK_BASE_URL:
    BASE_URL = WEBHOOK_BASE_URL.rstrip("/")
elif RENDER_EXTERNAL_HOSTNAME:
    BASE_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}"
else:
    BASE_URL = None

THANK_PATTERNS = [
    r"\bдякую\b", r"\bспасибі\b", r"\bдякс\b",
    r"\bспасибо\b", r"\bблагодарю\b",
    r"\bthanks\b", r"\bthank\s+you\b", r"\bthx\b", r"\bty\b",
]
THANK_RE = re.compile("|".join(THANK_PATTERNS), re.IGNORECASE)
POSITIVE_REACTIONS = {"👍", "🔥", "❤️", "❤", "🙏", "👏", "✅", "💯"}

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

TAG_RANKS = {
    "uk": [(0, ""), (11, "Учень"), (26, "Практик"), (51, "Слюсар"), (101, "Спеціаліст"), (181, "Майстер"), (281, "Експерт"), (401, "Гуру Локсмітів"), (551, "Проф. електроніки"), (751, "Легенда сервісу")],
    "en": [(0, ""), (11, "Apprentice"), (26, "Practitioner"), (51, "Locksmith"), (101, "Specialist"), (181, "Master"), (281, "Expert"), (401, "Locksmith Guru"), (551, "Electronics Prof"), (751, "Service Legend")],
    "ru": [(0, ""), (11, "Ученик"), (26, "Практик"), (51, "Слесарь"), (101, "Специалист"), (181, "Мастер"), (281, "Эксперт"), (401, "Гуру Локсмитов"), (551, "Проф. электроники"), (751, "Легенда сервиса")],
}

RANKS = {
    "uk": [(0, "Ранг 0: Новачок 🌱"), (11, "Ранг 1: Учень 🛠️"), (26, "Ранг 2: Практик ⚙️"), (51, "Ранг 3: Слюсар 🔓"), (101, "Ранг 4: Спеціаліст 💻"), (181, "Ранг 5: Майстер 🔑"), (281, "Ранг 6: Експерт ⭐"), (401, "Ранг 7: Гуру Локсмітів 🔥"), (551, "Ранг 8: Професор Електроніки ⚡"), (751, "Ранг 9: Легенда Сервісу 🏆")],
    "en": [(0, "Rank 0: Newbie 🌱"), (11, "Rank 1: Apprentice 🛠️"), (26, "Rank 2: Practitioner ⚙️"), (51, "Rank 3: Locksmith 🔓"), (101, "Rank 4: Specialist 💻"), (181, "Rank 5: Master 🔑"), (281, "Rank 6: Expert ⭐"), (401, "Rank 7: Locksmith Guru 🔥"), (551, "Rank 8: Electronics Professor ⚡"), (751, "Rank 9: Service Legend 🏆")],
    "ru": [(0, "Ранг 0: Новичок 🌱"), (11, "Ранг 1: Ученик 🛠️"), (26, "Ранг 2: Практик ⚙️"), (51, "Ранг 3: Слесарь 🔓"), (101, "Ранг 4: Специалист 💻"), (181, "Ранг 5: Мастер 🔑"), (281, "Ранг 6: Эксперт ⭐"), (401, "Ранг 7: Гуру Локсмитов 🔥"), (551, "Ранг 8: Профессор Электроники ⚡"), (751, "Ранг 9: Легенда Сервиса 🏆")],
}


def get_lang(update: Update) -> str:
    return get_user_lang(update.effective_user)


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
    current_rank = RANKS.get(lang, RANKS["en"])[0][1]
    for threshold, title in RANKS.get(lang, RANKS["en"]):
        if rep >= threshold:
            current_rank = title
        else:
            break
    return current_rank


def get_rank_tag(rep: int, lang: str) -> str:
    current_tag = ""
    for threshold, tag in TAG_RANKS.get(lang, TAG_RANKS["en"]):
        if rep >= threshold:
            current_tag = tag
        else:
            break
    return current_tag


async def sync_member_tag(context, chat_id: int, user_id: int, rep: int, lang: str):
    try:
        await context.bot.set_chat_member_tag(chat_id=chat_id, user_id=user_id, tag=get_rank_tag(rep, lang))
    except Exception as e:
        print(f"TAG_UPDATE_FAILED chat={chat_id} user={user_id}: {e}")


def db():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
        connection_timeout=10,
    )


def init_db():
    conn = db()
    cur = conn.cursor()
    statements = [
        """CREATE TABLE IF NOT EXISTS telegram_users (
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            name VARCHAR(255) NOT NULL,
            username VARCHAR(255) NULL,
            language_code VARCHAR(32) NULL,
            reputation INT NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id),
            INDEX idx_tg_users_rep (chat_id, reputation)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS telegram_messages (
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            author_id BIGINT NOT NULL,
            message_thread_id BIGINT NULL,
            PRIMARY KEY (chat_id, message_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS telegram_thank_votes (
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            giver_id BIGINT NOT NULL,
            receiver_id BIGINT NOT NULL,
            PRIMARY KEY (chat_id, message_id, giver_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS telegram_reaction_votes (
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            giver_id BIGINT NOT NULL,
            receiver_id BIGINT NOT NULL,
            PRIMARY KEY (chat_id, message_id, giver_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        """CREATE TABLE IF NOT EXISTS telegram_pending_deletions (
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            delete_at DATETIME NOT NULL,
            PRIMARY KEY (chat_id, message_id),
            INDEX idx_tg_delete_at (delete_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    ]
    try:
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def display_name(user):
    name = (user.full_name or user.username or str(user.id)).strip()
    return name[:120]


def safe_name(user):
    return html.escape(display_name(user))


def upsert_user(cur, chat_id, user):
    cur.execute(
        """INSERT INTO telegram_users(chat_id,user_id,name,username,language_code,reputation)
           VALUES(%s,%s,%s,%s,%s,0)
           ON DUPLICATE KEY UPDATE
             name=VALUES(name), username=VALUES(username), language_code=VALUES(language_code)""",
        (chat_id, user.id, display_name(user), user.username, user.language_code),
    )


def change_rep(cur, chat_id, user_id, delta):
    cur.execute(
        "UPDATE telegram_users SET reputation = GREATEST(0, reputation + %s) WHERE chat_id=%s AND user_id=%s",
        (delta, chat_id, user_id),
    )


def schedule_delete(chat_id: int, message_id: int, delay: int):
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO telegram_pending_deletions(chat_id,message_id,delete_at)
               VALUES(%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))
               ON DUPLICATE KEY UPDATE delete_at=VALUES(delete_at)""",
            (chat_id, message_id, delay),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


async def delete_worker(application: Application):
    while True:
        try:
            conn = db()
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT chat_id, message_id FROM telegram_pending_deletions WHERE delete_at <= UTC_TIMESTAMP() ORDER BY delete_at LIMIT 100"
                )
                rows = cur.fetchall()
            finally:
                cur.close()
                conn.close()

            for chat_id, message_id in rows:
                try:
                    await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception as e:
                    print(f"DELETE_FAILED chat={chat_id} message={message_id}: {e}")
                finally:
                    conn = db()
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            "DELETE FROM telegram_pending_deletions WHERE chat_id=%s AND message_id=%s",
                            (chat_id, message_id),
                        )
                        conn.commit()
                    finally:
                        cur.close()
                        conn.close()
        except Exception as e:
            print(f"DELETE_WORKER_ERROR: {e}")

        await asyncio.sleep(10)


async def post_init(application: Application):
    init_db()
    application.create_task(delete_worker(application))
    print(f"DB connected: {DB_HOST}:{DB_PORT}/{DB_NAME}")


async def send_temp(context, chat_id, text, thread_id=None, delay=10):
    msg = await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    schedule_delete(msg.chat_id, msg.message_id, delay)
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
    cur = conn.cursor()
    try:
        upsert_user(cur, chat.id, msg.from_user)
        cur.execute(
            """INSERT INTO telegram_messages(chat_id,message_id,author_id,message_thread_id)
               VALUES(%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE author_id=VALUES(author_id), message_thread_id=VALUES(message_thread_id)""",
            (chat.id, msg.message_id, msg.from_user.id, thread_id),
        )

        text = (msg.text or msg.caption or "").strip()
        reply = msg.reply_to_message

        if text and reply and reply.from_user and not reply.from_user.is_bot and THANK_RE.search(text):
            giver = msg.from_user
            receiver = reply.from_user
            if giver.id != receiver.id:
                upsert_user(cur, chat.id, receiver)
                try:
                    cur.execute(
                        "INSERT INTO telegram_thank_votes(chat_id,message_id,giver_id,receiver_id) VALUES(%s,%s,%s,%s)",
                        (chat.id, reply.message_id, giver.id, receiver.id),
                    )
                    change_rep(cur, chat.id, receiver.id, +1)
                    conn.commit()

                    cur.execute("SELECT reputation FROM telegram_users WHERE chat_id=%s AND user_id=%s", (chat.id, receiver.id))
                    row = cur.fetchone()
                    rep = row[0] if row else 0
                    receiver_lang = get_user_lang(receiver)
                    rank = get_rank_name(rep, receiver_lang)
                    await sync_member_tag(context, chat.id, receiver.id, rep, receiver_lang)
                    txt = TRANSLATIONS[receiver_lang]["rep_gain"].format(
                        receiver=safe_name(receiver), giver=safe_name(giver), rep=rep, rank=rank
                    )
                    await send_temp(context, chat.id, txt, thread_id=thread_id, delay=3600)
                except IntegrityError:
                    conn.rollback()
        conn.commit()
    finally:
        cur.close()
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
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT author_id, message_thread_id FROM telegram_messages WHERE chat_id=%s AND message_id=%s",
            (chat_id, reaction.message_id),
        )
        row = cur.fetchone()
        if not row:
            return

        receiver_id, thread_id = row
        if receiver_id == giver.id:
            return

        upsert_user(cur, chat_id, giver)

        if new_positive and not old_positive:
            try:
                cur.execute(
                    "INSERT INTO telegram_reaction_votes(chat_id,message_id,giver_id,receiver_id) VALUES(%s,%s,%s,%s)",
                    (chat_id, reaction.message_id, giver.id, receiver_id),
                )
                change_rep(cur, chat_id, receiver_id, +1)
                conn.commit()

                cur.execute(
                    "SELECT name, reputation, language_code FROM telegram_users WHERE chat_id=%s AND user_id=%s",
                    (chat_id, receiver_id),
                )
                receiver_row = cur.fetchone()
                if receiver_row:
                    receiver_name, rep, receiver_language_code = receiver_row
                    receiver_lang = get_lang_from_code(receiver_language_code)
                    rank = get_rank_name(rep, receiver_lang)
                    await sync_member_tag(context, chat_id, receiver_id, rep, receiver_lang)
                    txt = TRANSLATIONS[receiver_lang]["rep_gain"].format(
                        receiver=html.escape(receiver_name), giver=safe_name(giver), rep=rep, rank=rank
                    )
                    await send_temp(context, chat_id, txt, thread_id=thread_id, delay=3600)
            except IntegrityError:
                conn.rollback()
                return

        elif old_positive and not new_positive:
            cur.execute(
                "DELETE FROM telegram_reaction_votes WHERE chat_id=%s AND message_id=%s AND giver_id=%s",
                (chat_id, reaction.message_id, giver.id),
            )
            if cur.rowcount:
                change_rep(cur, chat_id, receiver_id, -1)
                conn.commit()
                cur.execute(
                    "SELECT reputation, language_code FROM telegram_users WHERE chat_id=%s AND user_id=%s",
                    (chat_id, receiver_id),
                )
                receiver_row = cur.fetchone()
                if receiver_row:
                    rep, receiver_language_code = receiver_row
                    await sync_member_tag(context, chat_id, receiver_id, rep, get_lang_from_code(receiver_language_code))
    finally:
        cur.close()
        conn.close()


async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    target = msg.from_user
    lang = get_user_lang(target)
    conn = db()
    cur = conn.cursor()
    try:
        upsert_user(cur, chat.id, target)
        conn.commit()
        cur.execute("SELECT reputation FROM telegram_users WHERE chat_id=%s AND user_id=%s", (chat.id, target.id))
        row = cur.fetchone()
        rep = row[0] if row else 0
    finally:
        cur.close()
        conn.close()

    rank = get_rank_name(rep, lang)
    await sync_member_tag(context, chat.id, target.id, rep, lang)
    txt = TRANSLATIONS[lang]["rep_info"].format(target=safe_name(target), rep=rep, rank=rank)
    schedule_delete(chat.id, msg.message_id, 60)
    await send_temp(context, chat.id, txt, thread_id=msg.message_thread_id, delay=60)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    lang = get_lang(update)
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT name, reputation FROM telegram_users
               WHERE chat_id=%s AND reputation>0
               ORDER BY reputation DESC, name ASC LIMIT 10""",
            (chat.id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    t_set = TRANSLATIONS[lang]
    if not rows:
        text = t_set["top_empty"]
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = [t_set["top_title"]]
        for i, (name, rep) in enumerate(rows, 1):
            prefix = medals[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{prefix} <b>{html.escape(name)}</b> — <b>{rep}</b> (<i>{get_rank_name(rep, lang)}</i>)")
        text = "\n".join(lines)

    schedule_delete(chat.id, msg.message_id, 60)
    await send_temp(context, chat.id, text, thread_id=msg.message_thread_id, delay=60)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(TRANSLATIONS[get_lang(update)]["start"])


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    schedule_delete(chat.id, msg.message_id, 3600)

    if not msg.new_chat_members:
        return
    members = [u for u in msg.new_chat_members if not u.is_bot]
    if not members:
        return

    text = TRANSLATIONS[get_lang(update)]["welcome"].format(
        names=", ".join(safe_name(u) for u in members),
        chat_name=html.escape(chat.title or "Group"),
    )
    await send_temp(context, chat.id, text, thread_id=msg.message_thread_id, delay=3600)


async def clean_system_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if msg and chat:
        schedule_delete(chat.id, msg.message_id, 60)


def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("rep", rep_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(MessageReactionHandler(on_reaction))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL & ~filters.StatusUpdate.NEW_CHAT_MEMBERS, clean_system_messages))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL, remember_message))

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
