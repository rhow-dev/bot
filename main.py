import os
import asyncio
import sqlite3
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, TypeHandler, ApplicationHandlerStop,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN", "9221180526:AAGXly8l7LFMbg1UY4vTfoc-o7EdcZE37sA")
DB_PATH = os.getenv("DB_PATH", "bot.db")

FORCE_CHANNEL = "@NULLVASET"   # کانال اجباری
DEFAULT_ADMINS = [7281188442]  # مدیر اصلی

REWARD = 1        # سکه‌ی هر عضویت
PRICE = 2         # هزینه‌ی هر عضو سفارشی (سکه)
WELCOME_COINS = 2
PENALTY = 7           # جریمه‌ی خروج از کانال (سکه) - بدون محدودیت زمانی
CHECK_EVERY = 600     # هر چند ثانیه یک دسته چک شود (۱۰ دقیقه)
CHECK_BATCH = 200     # در هر نوبت چند عضویت چک شود

SUPER_ADMINS = set(DEFAULT_ADMINS) | {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

BTN_EARN = "💰 کسب سکه"
BTN_ORDER = "📣 ثبت سفارش"
BTN_ACCOUNT = "👤 حساب من"
BTN_ORDERS = "📋 وضعیت سفارش‌های من"
BTN_ADMIN = "🛠 مدیریت"


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
                active INTEGER NOT NULL DEFAULT 1,
                cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS joins(
                user_id INTEGER, order_id INTEGER,
                joined_at INTEGER NOT NULL DEFAULT 0,
                penalized INTEGER NOT NULL DEFAULT 0,
                last_checked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, order_id));
            CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
            """
        )
        # مایگریشن برای دیتابیس‌های قدیمی
        for table, col in (
            ("joins", "joined_at INTEGER NOT NULL DEFAULT 0"),
            ("joins", "penalized INTEGER NOT NULL DEFAULT 0"),
            ("joins", "last_checked INTEGER NOT NULL DEFAULT 0"),
            ("orders", "cancelled INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        # اگر قبلاً موجودی منفی شده بود، صفر شود
        con.execute("UPDATE users SET coins=0 WHERE coins<0")


def ensure_user(uid):
    with db() as con:
        row = con.execute("SELECT coins FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            con.execute("INSERT INTO users(id, coins) VALUES(?,?)", (uid, WELCOME_COINS))
            return WELCOME_COINS
        return row["coins"]


def get_coins(uid):
    return ensure_user(uid)


def is_admin(uid):
    if uid in SUPER_ADMINS:
        return True
    with db() as con:
        return con.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def menu_for(uid):
    rows = [[BTN_EARN, BTN_ORDER], [BTN_ACCOUNT, BTN_ORDERS]]
    if is_admin(uid):
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def next_order(uid):
    with db() as con:
        return con.execute(
            """SELECT * FROM orders
               WHERE active=1 AND done<target AND owner_id!=?
               AND id NOT IN (SELECT order_id FROM joins WHERE user_id=?)
               ORDER BY id LIMIT 1""",
            (uid, uid),
        ).fetchone()


# ---------- عضویت اجباری ----------
async def is_member(bot, uid):
    try:
        m = await bot.get_chat_member(FORCE_CHANNEL, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/NULLVASET")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="fj")],
    ])


async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    q = update.callback_query

    if q and q.data == "fj":
        if await is_member(context.bot, user.id):
            ensure_user(user.id)
            await q.answer("✅ تایید شد!", show_alert=True)
            await q.message.reply_text(
                "حالا می‌توانی از ربات استفاده کنی 👇", reply_markup=menu_for(user.id)
            )
        else:
            await q.answer("❌ هنوز عضو کانال نشدی!", show_alert=True)
        raise ApplicationHandlerStop

    if is_admin(user.id) or await is_member(context.bot, user.id):
        return

    text = "برای استفاده از ربات اول باید در کانال ما عضو شوی 👇"
    if q:
        await q.answer("اول در کانال عضو شو!", show_alert=True)
        await q.message.reply_text(text, reply_markup=join_kb())
    elif update.message:
        await update.message.reply_text(text, reply_markup=join_kb())
    raise ApplicationHandlerStop


# ---------- کاربران ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    context.user_data.clear()
    await update.message.reply_text(
        "سلام! 👋\nبا عضو شدن در کانال‌ها سکه بگیر و با سکه‌ها برای کانال خودت ممبر سفارش بده.",
        reply_markup=menu_for(uid),
    )


def order_view(order):
    text = (
        f"📢 کانال: {order['title'] or order['username']}\n"
        f"🔗 https://t.me/{order['username'].lstrip('@')}\n\n"
        f"در کانال عضو شو و بعد دکمه‌ی «عضو شدم» را بزن تا {REWARD} سکه بگیری.\n"
        f"⚠️ اگر بعداً از کانال خارج شوی، تا {PENALTY} سکه جریمه می‌شوی."
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
                f"🎉 سفارش #{owner_notify['id']} کانال {owner_notify['username']} کامل شد "
                f"({owner_notify['target']} عضو).",
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
        f"💰 موجودی: {coins} سکه\n\n"
        f"هر عضویت = {REWARD} سکه\n"
        f"هر عضو سفارشی = {PRICE} سکه\n"
        f"⚠️ خروج از هر کانالی که با آن سکه گرفته‌ای (حتی بعد از چند روز یا ماه) = "
        f"{PENALTY} سکه جریمه (حداکثر تا صفر شدن موجودی)"
    )


def order_status_text(r):
    pct = int(r["done"] * 100 / r["target"]) if r["target"] else 0
    filled = pct // 10
    bar = "▓" * filled + "░" * (10 - filled)
    if r["cancelled"]:
        st = "❌ لغو شده"
    elif r["active"]:
        st = "🟢 در حال انجام"
    else:
        st = "✅ تکمیل شده"
    return (
        f"🆔 سفارش #{r['id']}\n"
        f"📢 {r['username']}\n"
        f"{bar} {pct}%\n"
        f"👥 {r['done']} از {r['target']} عضو\n"
        f"وضعیت: {st}"
    )


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as con:
        rows = con.execute(
            "SELECT * FROM orders WHERE owner_id=? ORDER BY id DESC LIMIT 10", (uid,)
        ).fetchall()
    if not rows:
        await update.message.reply_text("هنوز سفارشی نداری.")
        return
    texts, buttons = [], []
    for r in rows:
        texts.append(order_status_text(r))
        if r["active"]:
            buttons.append([InlineKeyboardButton(
                f"❌ لغو سفارش #{r['id']}", callback_data=f"cx:{r['id']}")])
    await update.message.reply_text(
        "\n\n".join(texts),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    oid = int(q.data.split(":")[1])
    with db() as con:
        o = con.execute(
            "SELECT * FROM orders WHERE id=? AND owner_id=?", (oid, uid)
        ).fetchone()
        if not o or not o["active"]:
            await q.answer("این سفارش قابل لغو نیست.", show_alert=True)
            return
        refund = max(o["target"] - o["done"], 0) * PRICE
        con.execute("UPDATE orders SET active=0, cancelled=1 WHERE id=?", (oid,))
        con.execute("UPDATE users SET coins=coins+? WHERE id=?", (refund, uid))
    await q.answer(f"سفارش لغو شد و {refund} سکه برگشت.", show_alert=True)
    await q.edit_message_text(f"❌ سفارش #{oid} لغو شد و {refund} سکه به حسابت برگشت.")


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


# ---------- مدیریت ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with db() as con:
        users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        total = con.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        active = con.execute("SELECT COUNT(*) c FROM orders WHERE active=1").fetchone()["c"]
        joins = con.execute("SELECT COUNT(*) c FROM joins").fetchone()["c"]
        penal = con.execute("SELECT COUNT(*) c FROM joins WHERE penalized=1").fetchone()["c"]
    await update.message.reply_text(
        "🛠 پنل مدیریت\n\n"
        f"👥 کاربران: {users}\n"
        f"📦 کل سفارش‌ها: {total}\n"
        f"🟢 سفارش‌های فعال: {active}\n"
        f"✅ کل عضویت‌های انجام‌شده: {joins}\n"
        f"⚠️ جریمه‌شده‌ها (خروج): {penal}\n\n"
        "دستورها:\n"
        "/addcoins آیدی تعداد  ← افزودن/کسر سکه (عدد منفی = کسر)\n"
        "/broadcast متن  ← ارسال پیام به همه‌ی کاربران\n"
        "/addadmin آیدی  ← افزودن مدیر (فقط مدیر اصلی)\n"
        "/deladmin آیدی  ← حذف مدیر (فقط مدیر اصلی)"
    )


async def cmd_addcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("فرمت درست:\n/addcoins آیدی_عددی تعداد")
        return
    ensure_user(target)
    with db() as con:
        con.execute("UPDATE users SET coins=MAX(coins+?,0) WHERE id=?", (amount, target))
    await update.message.reply_text(f"✅ انجام شد. موجودی جدید: {get_coins(target)} سکه")
    try:
        await context.bot.send_message(target, f"💰 مدیریت {amount:+d} سکه به حسابت اضافه/کم کرد.")
    except Exception:
        pass


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("فرمت درست:\n/broadcast متن پیام")
        return
    with db() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM users").fetchall()]
    ok = 0
    for i in ids:
        try:
            await context.bot.send_message(i, msg)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"📨 برای {ok} از {len(ids)} نفر ارسال شد.")


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in SUPER_ADMINS:
        return
    try:
        target = int(context.args[0])
    except Exception:
        await update.message.reply_text("فرمت درست:\n/addadmin آیدی_عددی")
        return
    with db() as con:
        con.execute("INSERT OR IGNORE INTO admins(id) VALUES(?)", (target,))
    await update.message.reply_text(f"✅ {target} مدیر شد.")


async def cmd_deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in SUPER_ADMINS:
        return
    try:
        target = int(context.args[0])
    except Exception:
        await update.message.reply_text("فرمت درست:\n/deladmin آیدی_عددی")
        return
    with db() as con:
        con.execute("DELETE FROM admins WHERE id=?", (target,))
    await update.message.reply_text(f"✅ {target} از مدیران حذف شد.")


# ---------- متن‌ها ----------
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
    if text == BTN_ADMIN:
        return await admin_panel(update, context)

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
        await update.message.reply_text(f"چند عضو می‌خواهی؟ (حداکثر {mx})")
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
            cur = con.execute(
                "INSERT INTO orders(owner_id, chat_id, username, title, target) VALUES(?,?,?,?,?)",
                (uid, context.user_data["chat_id"], context.user_data["username"],
                 context.user_data["title"], qty),
            )
            oid = cur.lastrowid
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ سفارش #{oid} ثبت شد: {qty} عضو ({cost} سکه کم شد).\n"
            f"پیشرفت را از «{BTN_ORDERS}» ببین.",
            reply_markup=menu_for(uid),
        )
        return

    await update.message.reply_text("از منوی پایین استفاده کن 👇", reply_markup=menu_for(uid))


# ---------- جریمه‌ی خروج (بدون محدودیت زمانی) ----------
async def check_leavers(context: ContextTypes.DEFAULT_TYPE):
    # هر بار قدیمی‌ترین چک‌نشده‌ها را برمی‌دارد تا همه‌ی عضویت‌ها نوبتی چک شوند
    with db() as con:
        rows = con.execute(
            """SELECT j.user_id, j.order_id, o.chat_id, o.username
               FROM joins j JOIN orders o ON o.id=j.order_id
               WHERE j.penalized=0
               ORDER BY j.last_checked ASC
               LIMIT ?""",
            (CHECK_BATCH,),
        ).fetchall()

    for r in rows:
        await asyncio.sleep(0.1)  # جلوگیری از محدودیت تلگرام
        with db() as con:
            con.execute(
                "UPDATE joins SET last_checked=strftime('%s','now') "
                "WHERE user_id=? AND order_id=?",
                (r["user_id"], r["order_id"]),
            )
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
