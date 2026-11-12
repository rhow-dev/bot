import os
import asyncio
import sqlite3
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN", "9221180526:AAGXly8l7LFMbg1UY4vTfoc-o7EdcZE37sA")
DB_PATH = os.getenv("DB_PATH", "bot.db")  # روی Railway: /data/bot.db (با Volume)
REWARD = 1        # سکه‌ی هر عضویت
PRICE = 2         # هزینه‌ی هر عضو سفارشی (سکه)
WELCOME_COINS = 2
PENALTY = 2           # جریمه‌ی خروج از کانال (سکه)
HOLD_DAYS = 7         # تا چند روز بعد از عضویت، خروج جریمه دارد
CHECK_EVERY = 1800    # هر چند ثانیه چک شود (۳۰ دقیقه)

BTN_EARN = "💰 کسب سکه"
BTN_ORDER = "📣 ثبت سفارش"
BTN_ACCOUNT = "👤 حساب من"
BTN_ORDERS = "📋 سفارش‌های من"
MENU = ReplyKeyboardMarkup(
    [[BTN_EARN, BTN_ORDER], [BTN_ACCOUNT, BTN_ORDERS]], resize_keyboard=True
)


# ---------- دیتابیس ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                username TEXT NOT NULL, title TEXT,
                target INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS joins(
                user_id INTEGER, order_id INTEGER,
                joined_at INTEGER NOT NULL DEFAULT 0,
                penalized INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, order_id));
            """
        )
        # مایگریشن برای دیتابیس‌های قدیمی
        for col in ("joined_at INTEGER NOT NULL DEFAULT 0",
                    "penalized INTEGER NOT NULL DEFAULT 0"):
            try:
                con.execute(f"ALTER TABLE joins ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass


def ensure_user(uid):
    with db() as con:
        row = con.execute("SELECT coins FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            con.execute("INSERT INTO users(id, coins) VALUES(?,?)", (uid, WELCOME_COINS))
            return WELCOME_COINS
        return row["coins"]


def get_coins(uid):
    return ensure_user(uid)


def next_order(uid):
    with db() as con:
        return con.execute(
            """SELECT * FROM orders
               WHERE active=1 AND done<target AND owner_id!=?
               AND id NOT IN (SELECT order_id FROM joins WHERE user_id=?)
               ORDER BY id LIMIT 1""",
            (uid, uid),
        ).fetchone()


# ---------- هندلرها ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text(
        "سلام! 👋\nبا عضو شدن در کانال‌ها سکه بگیر و با سکه‌ها برای کانال خودت ممبر سفارش بده.",
        reply_markup=MENU,
    )


def order_view(order):
    text = (
        f"📢 کانال: {order['title'] or order['username']}\n"
        f"🔗 https://t.me/{order['username'].lstrip('@')}\n\n"
        f"در کانال عضو شو و بعد دکمه‌ی «عضو شدم» را بزن تا {REWARD} سکه بگیری."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 ورود به کانال", url=f"https://t.me/{order['username'].lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data=f"chk:{order['id']}")],
        [InlineKeyboardButton("⏭ بعدی", callback_data="next")],
    ])
    return text, kb


async def earn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    order = next_order(uid)
    if not order:
        await update.message.reply_text("فعلاً کانالی برای عضویت نیست. بعداً سر بزن 🙏")
        return
    text, kb = order_view(order)
    await update.message.reply_text(text, reply_markup=kb)


async def on_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order = next_order(q.from_user.id)
    if not order:
        await q.edit_message_text("فعلاً کانال دیگری نیست. بعداً سر بزن 🙏")
        return
    text, kb = order_view(order)
    await q.edit_message_text(text, reply_markup=kb)


async def on_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    order_id = int(q.data.split(":")[1])
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or not order["active"]:
        await q.answer("این سفارش دیگر فعال نیست.", show_alert=True)
        return
    try:
        m = await context.bot.get_chat_member(order["chat_id"], uid)
        joined = m.status in ("member", "administrator", "creator")
    except Exception:
        joined = False
    if not joined:
        await q.answer("❌ هنوز عضو کانال نشدی!", show_alert=True)
        return

    owner_notify = None
    with db() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO joins(user_id, order_id, joined_at) "
            "VALUES(?,?,strftime('%s','now'))", (uid, order_id)
        )
        if cur.rowcount == 0:
            await q.answer("قبلاً سکه‌اش را گرفتی.", show_alert=True)
            return
        con.execute("UPDATE users SET coins=coins+? WHERE id=?", (REWARD, uid))
        con.execute("UPDATE orders SET done=done+1 WHERE id=?", (order_id,))
        o = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if o["done"] >= o["target"]:
            con.execute("UPDATE orders SET active=0 WHERE id=?", (order_id,))
            owner_notify = o

    await q.answer(f"✅ {REWARD} سکه دریافت کردی!", show_alert=True)
    if owner_notify:
        try:
            await context.bot.send_message(
                owner_notify["owner_id"],
                f"🎉 سفارش کانال {owner_notify['username']} کامل شد ({owner_notify['target']} عضو).",
            )
        except Exception:
            pass
    nxt = next_order(uid)
    if nxt:
        text, kb = order_view(nxt)
        await q.edit_message_text(text, reply_markup=kb)
    else:
        await q.edit_message_text("آفرین! فعلاً کانال دیگری نیست.")


async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coins = get_coins(update.effective_user.id)
    await update.message.reply_text(
        f"💰 موجودی: {coins} سکه\n"
        f"هر عضویت = {REWARD} سکه\nهر عضو سفارشی = {PRICE} سکه\n"
        f"⚠️ خروج از کانال تا {HOLD_DAYS} روز بعد از عضویت = {PENALTY} سکه جریمه"
    )


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows = con.execute(
            "SELECT * FROM orders WHERE owner_id=? ORDER BY id DESC LIMIT 10",
            (update.effective_user.id,),
        ).fetchall()
    if not rows:
        await update.message.reply_text("هنوز سفارشی نداری.")
        return
    lines = [
        f"{r['username']} — {r['done']}/{r['target']} {'🟢' if r['active'] else '✅'}"
        for r in rows
    ]
    await update.message.reply_text("\n".join(lines))


async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coins = get_coins(update.effective_user.id)
    if coins < PRICE:
        await update.message.reply_text(
            f"سکه‌ات کافی نیست (حداقل {PRICE} سکه). اول با «{BTN_EARN}» سکه جمع کن."
        )
        return
    context.user_data["state"] = "channel"
    await update.message.reply_text(
        "ربات را اول در کانالت «ادمین» کن، بعد یوزرنیم کانال را بفرست (مثل @mychannel)."
        "\nبرای لغو: /start"
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    state = context.user_data.get("state")

    if text == BTN_EARN:
        return await earn(update, context)
    if text == BTN_ORDER:
        return await order_start(update, context)
    if text == BTN_ACCOUNT:
        return await account(update, context)
    if text == BTN_ORDERS:
        return await my_orders(update, context)

    if state == "channel":
        if not text.startswith("@"):
            await update.message.reply_text("یوزرنیم باید با @ شروع شود.")
            return
        try:
            chat = await context.bot.get_chat(text)
            me = await context.bot.get_chat_member(chat.id, context.bot.id)
            user_m = await context.bot.get_chat_member(chat.id, uid)
        except Exception:
            await update.message.reply_text("کانال پیدا نشد یا ربات داخلش ادمین نیست.")
            return
        if chat.type != "channel":
            await update.message.reply_text("این یک کانال نیست.")
            return
        if me.status != "administrator":
            await update.message.reply_text("ربات باید در کانال ادمین باشد.")
            return
        if user_m.status not in ("administrator", "creator"):
            await update.message.reply_text("فقط ادمین/مالک کانال می‌تواند سفارش بدهد.")
            return
        context.user_data.update(
            state="qty", chat_id=chat.id, username=text, title=chat.title
        )
        mx = get_coins(uid) // PRICE
        await update.message.reply_text(
            f"چند عضو می‌خواهی؟ (حداکثر {mx})"
        )
        return

    if state == "qty":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("یک عدد معتبر بفرست.")
            return
        qty = int(text)
        cost = qty * PRICE
        with db() as con:
            coins = con.execute("SELECT coins FROM users WHERE id=?", (uid,)).fetchone()["coins"]
            if cost > coins:
                await update.message.reply_text(
                    f"سکه کافی نیست. حداکثر {coins // PRICE} عضو می‌توانی بگیری."
                )
                return
            con.execute("UPDATE users SET coins=coins-? WHERE id=?", (cost, uid))
            con.execute(
                "INSERT INTO orders(owner_id, chat_id, username, title, target) VALUES(?,?,?,?,?)",
                (uid, context.user_data["chat_id"], context.user_data["username"],
                 context.user_data["title"], qty),
            )
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ سفارش ثبت شد: {qty} عضو ({cost} سکه کم شد).", reply_markup=MENU
        )
        return

    await update.message.reply_text("از منوی پایین استفاده کن 👇", reply_markup=MENU)


async def check_leavers(context: ContextTypes.DEFAULT_TYPE):
    """عضویت‌های اخیر را چک می‌کند؛ اگر کسی لفت داده باشد جریمه می‌شود."""
    with db() as con:
        rows = con.execute(
            """SELECT j.user_id, j.order_id, o.chat_id, o.username
               FROM joins j JOIN orders o ON o.id=j.order_id
               WHERE j.penalized=0
               AND j.joined_at > strftime('%s','now') - ?""",
            (HOLD_DAYS * 86400,),
        ).fetchall()

    for r in rows:
        await asyncio.sleep(0.1)  # جلوگیری از محدودیت تلگرام
        try:
            m = await context.bot.get_chat_member(r["chat_id"], r["user_id"])
        except Exception:
            continue  # مثلاً ربات از کانال حذف شده؛ رد می‌شویم
        if m.status in ("member", "administrator", "creator"):
            continue

        with db() as con:
            cur = con.execute(
                "UPDATE joins SET penalized=1 WHERE user_id=? AND order_id=? AND penalized=0",
                (r["user_id"], r["order_id"]),
            )
            if cur.rowcount == 0:
                continue
            con.execute(
                "UPDATE users SET coins=MAX(coins-?,0) WHERE id=?", (PENALTY, r["user_id"])
            )
            # عضو از شمارش سفارش کم می‌شود و سفارش دوباره فعال می‌شود
            con.execute(
                "UPDATE orders SET done=MAX(done-1,0), active=1 WHERE id=?", (r["order_id"],)
            )
        try:
            await context.bot.send_message(
                r["user_id"],
                f"⚠️ از کانال {r['username']} خارج شدی و {PENALTY} سکه جریمه شدی.",
            )
        except Exception:
            pass


def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.job_queue.run_repeating(check_leavers, interval=CHECK_EVERY, first=60)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_check, pattern=r"^chk:\d+$"))
    app.add_handler(CallbackQueryHandler(on_next, pattern=r"^next$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
