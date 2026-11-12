import os
import asyncio
import sqlite3
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, TypeHandler, ApplicationHandlerStop,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("9221180526:AAGXly8l7LFMbg1UY4vTfoc-o7EdcZE37sA")
DB_PATH = os.getenv("DB_PATH", "bot.db")

FORCE_CHANNEL = "@NULLVASET"
DEFAULT_ADMINS = [7281188442]
REWARD = 1
PRICE = 2
WELCOME_COINS = 20
REFERRAL_REWARD = 10
PENALTY = 7
CHECK_EVERY = 600
CHECK_BATCH = 200

CARD_NUMBER = "5859-8312-0689-7943"
CARD_OWNER = "غفوری"
SUPPORT_ID = "@VERATHB"

PACKAGES = [
    (100, 50000),
    (500, 100000),
    (1000, 200000),
]

SUPER_ADMINS = set(DEFAULT_ADMINS) | {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

BTN_EARN = "کسب سکه 💰"
BTN_ORDER = "ثبت سفارش 📦"
BTN_ACCOUNT = "حساب من 👤"
BTN_ORDERS = "وضعیت سفارش‌های من 📊"
BTN_BUY = "خرید سکه 💳"
BTN_SUPPORT = "پشتیبانی 🆘"
BTN_REF = "زیرمجموعه‌گیری 👥"
BTN_ADMIN = "🛠 مدیریت"
MENU_BUTTONS = {BTN_EARN, BTN_ORDER, BTN_ACCOUNT, BTN_ORDERS, BTN_BUY, BTN_SUPPORT, BTN_REF, BTN_ADMIN}


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
                id INTEGER PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0,
                referred_by INTEGER);
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
            CREATE TABLE IF NOT EXISTS payments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                coins INTEGER NOT NULL,
                price INTEGER NOT NULL,
                file_id TEXT,
                is_photo INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL DEFAULT 0);
            """
        )
        for table, col in (
            ("joins", "joined_at INTEGER NOT NULL DEFAULT 0"),
            ("joins", "penalized INTEGER NOT NULL DEFAULT 0"),
            ("joins", "last_checked INTEGER NOT NULL DEFAULT 0"),
            ("orders", "cancelled INTEGER NOT NULL DEFAULT 0"),
            ("payments", "username TEXT"),
            ("users", "referred_by INTEGER"),
        ):
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
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


def register_user(uid, ref=None):
    """ثبت کاربر جدید و پاداش دعوت."""
    with db() as con:
        if con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            return None
        inviter = None
        if ref and ref != uid and con.execute("SELECT 1 FROM users WHERE id=?", (ref,)).fetchone():
            inviter = ref
        con.execute(
            "INSERT INTO users(id, coins, referred_by) VALUES(?,?,?)",
            (uid, WELCOME_COINS, inviter),
        )
        if inviter:
            con.execute("UPDATE users SET coins=coins+? WHERE id=?", (REFERRAL_REWARD, inviter))
        return inviter


def parse_ref(args):
    if args and args[0].startswith("ref_") and args[0][4:].isdigit():
        return int(args[0][4:])
    return None


async def notify_inviter(context, inviter):
    if not inviter:
        return
    try:
        await context.bot.send_message(
            inviter, f"🎉 یک نفر با لینک تو وارد ربات شد و {REFERRAL_REWARD} سکه گرفتی!"
        )
    except Exception:
        pass


def is_admin(uid):
    if uid in SUPER_ADMINS:
        return True
    with db() as con:
        return con.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def all_admin_ids():
    ids = set(SUPER_ADMINS)
    with db() as con:
        for r in con.execute("SELECT id FROM admins").fetchall():
            ids.add(r["id"])
    return ids


def menu_for(uid):
    rows = [[BTN_EARN, BTN_ORDER], [BTN_ACCOUNT, BTN_ORDERS], [BTN_BUY, BTN_REF], [BTN_SUPPORT]]
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


async def is_member(bot, uid):
    try:
        m = await bot.get_chat_member(FORCE_CHANNEL, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/NULLVASET")],
        [InlineKeyboardButton("عضو شدم ✅", callback_data="fj")],
    ])


async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    q = update.callback_query
    if update.message and update.message.text and update.message.text.startswith("/start"):
        r = parse_ref(update.message.text.split()[1:])
        if r:
            context.user_data["ref"] = r
    if q and q.data == "fj":
        if await is_member(context.bot, user.id):
            await notify_inviter(context, register_user(user.id, context.user_data.get("ref")))
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
    text = "📢 برای استفاده از ربات اول باید در کانال ما عضو شوی."
    if q:
        await q.answer("اول در کانال عضو شو!", show_alert=True)
        await q.message.reply_text(text, reply_markup=join_kb())
    elif update.message:
        await update.message.reply_text(text, reply_markup=join_kb())
    raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ref = parse_ref(context.args) or context.user_data.get("ref")
    await notify_inviter(context, register_user(uid, ref))
    ensure_user(uid)
    context.user_data.clear()
    await update.message.reply_text(
        "سلام! 👋\nبا عضو شدن در کانال‌ها سکه بگیر و با سکه‌ها برای کانال خودت ممبر سفارش بده.",
        reply_markup=menu_for(uid),
    )


def order_view(order):
    uname = order["username"].lstrip("@")
    name = order["title"] or order["username"]
    text = (
        f"📢 کانال: {name}\n"
        f"🔗 https://t.me/{uname}\n\n"
        f"در کانال عضو شو و بعد دکمه‌ی «عضو شدم» را بزن تا {REWARD} سکه بگیری.\n"
        f"⚠️ اگر بعداً از کانال خارج شوی، {PENALTY} سکه جریمه می‌شوی."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 ورود به کانال", url=f"https://t.me/{uname}")],
        [InlineKeyboardButton("عضو شدم ✅", callback_data=f"chk:{order['id']}")],
        [InlineKeyboardButton("بعدی ⏭", callback_data="next")],
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
        await q.edit_message_text("الان کانال دیگری نیست. بعداً سر بزن 🙏")
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
                f"🎉 سفارش #{owner_notify['id']} کانال {owner_notify['username']} "
                f"کامل شد ({owner_notify['target']} عضو).",
            )
        except Exception:
            pass
    nxt = next_order(uid)
    if nxt:
        text, kb = order_view(nxt)
        await q.edit_message_text(text, reply_markup=kb)
    else:
        await q.edit_message_text("الان کانال دیگری نیست. آفرین! 🙏")


async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coins = get_coins(update.effective_user.id)
    await update.message.reply_text(
        f"💰 موجودی: {coins} سکه\n\n"
        f"هر عضویت = {REWARD} سکه\n"
        f"هر عضو سفارشی = {PRICE} سکه\n"
        f"⚠️ خروج از هر کانالی که با آن سکه گرفته‌ای (حتی بعد از چند روز یا ماه) = "
        f"{PENALTY} سکه جریمه (حداکثر تا صفر شدن موجودی)\n\n"
        f"برای خرید سکه از «{BTN_BUY}» استفاده کن."
    )


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆘 پشتیبانی\n\nبرای هر مشکل یا سوالی به این آیدی پیام بده:\n{SUPPORT_ID}"
    )


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    with db() as con:
        cnt = con.execute("SELECT COUNT(*) c FROM users WHERE referred_by=?", (uid,)).fetchone()["c"]
    link = f"https://t.me/{context.bot.username}?start=ref_{uid}"
    await update.message.reply_text(
        "👥 زیرمجموعه‌گیری\n\n"
        f"به ازای هر نفری که با لینک تو وارد ربات شود، {REFERRAL_REWARD} سکه می‌گیری "
        f"و دوستت هم {WELCOME_COINS} سکه هدیه می‌گیرد.\n\n"
        f"🔗 لینک اختصاصی تو:\n{link}\n\n"
        f"📊 تعداد دعوت‌های موفق: {cnt}\n"
        f"💰 درآمد از دعوت: {cnt * REFERRAL_REWARD} سکه"
    )


def order_status_text(r):
    pct = int(r["done"] * 100 / r["target"]) if r["target"] else 0
    filled = pct // 10
    bar = "▓" * filled + "░" * (10 - filled)
    if r["cancelled"]:
        st = "لغو شده ❌"
    elif r["active"]:
        st = "در حال انجام 🔄"
    else:
        st = "تکمیل شده ✅"
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
            f"سکه‌ات کافی نیست (حداقل {PRICE} سکه). "
            f"اول با «{BTN_EARN}» سکه جمع کن یا از «{BTN_BUY}» سکه بخر."
        )
        return
    context.user_data["state"] = "channel"
    await update.message.reply_text(
        "ربات را اول در کانالت «ادمین» کن، بعد یوزرنیم کانال را بفرست (مثل @mychannel).\n"
        "برای لغو: /start"
    )
    # ---------- خرید سکه (کارت به کارت) ----------
async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [
        [InlineKeyboardButton(f"🪙 {c} سکه — {p:,} تومان", callback_data=f"buy:{i}")]
        for i, (c, p) in enumerate(PACKAGES)
    ]
    await update.message.reply_text(
        "💳 خرید سکه\nیکی از بسته‌ها را انتخاب کن:\n\n"
        f"🆘 پشتیبانی: {SUPPORT_ID}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    with db() as con:
        pend = con.execute(
            "SELECT COUNT(*) c FROM payments WHERE user_id=? AND status='pending'", (uid,)
        ).fetchone()["c"]
    if pend:
        await q.message.reply_text(
            "⏳ یک رسید در انتظار تایید داری. بعد از بررسی مدیر می‌توانی خرید جدید بزنی."
        )
        return
    idx = int(q.data.split(":")[1])
    if idx < 0 or idx >= len(PACKAGES):
        return
    coins, price = PACKAGES[idx]
    context.user_data["buy_package"] = (coins, price)
    await q.message.reply_text(
        f"💳 بسته انتخابی: {coins} سکه\n"
        f"مبلغ: {price:,} تومان\n\n"
        f"شماره کارت:\n{CARD_NUMBER}\n"
        f"به نام: {CARD_OWNER}\n\n"
        "پس از پرداخت، تصویر رسید را همین‌جا ارسال کن."
    )


async def on_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    package = context.user_data.get("buy_package")
    if not package:
        return
    coins, price = package
    photo = update.message.photo[-1].file_id if update.message.photo else None
    document = update.message.document.file_id if update.message.document else None
    receipt = photo or document
    if not receipt:
        return
    with db() as con:
        cur = con.execute(
            "INSERT INTO payments(user_id, coins, price, receipt, status) "
            "VALUES(?, ?, ?, ?, 'pending')",
            (uid, coins, price, receipt),
        )
        payment_id = cur.lastrowid
    context.user_data.pop("buy_package", None)
    await update.message.reply_text(
        "✅ رسیدت ثبت شد. بعد از بررسی مدیر، نتیجه بهت اطلاع داده می‌شه."
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"pay_ok:{payment_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"pay_no:{payment_id}"),
        ]
    ])
    for admin_id in get_admins():
        try:
            if photo:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo,
                    caption=f"🧾 رسید پرداخت جدید\nشناسه: {payment_id}\n"
                            f"کاربر: {uid}\nسکه: {coins}\nمبلغ: {price:,} تومان",
                    reply_markup=keyboard,
                )
            else:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=document,
                    caption=f"🧾 رسید پرداخت جدید\nشناسه: {payment_id}\n"
                            f"کاربر: {uid}\nسکه: {coins}\nمبلغ: {price:,} تومان",
                    reply_markup=keyboard,
                )
        except Exception:
            pass


async def on_pay_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("⛔ دسترسی نداری.")
        return
    action, payment_id = q.data.split(":")
    payment_id = int(payment_id)
    with db() as con:
        payment = con.execute(
            "SELECT * FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        if not payment:
            await q.message.reply_text("❌ پرداخت پیدا نشد.")
            return
        if payment["status"] != "pending":
            await q.message.reply_text("این پرداخت قبلاً بررسی شده است.")
            return
        if action == "pay_ok":
            con.execute(
                "UPDATE payments SET status='approved' WHERE id=?", (payment_id,)
            )
            con.execute(
                "UPDATE users SET coins=coins+? WHERE user_id=?",
                (payment["coins"], payment["user_id"]),
            )
        else:
            con.execute(
                "UPDATE payments SET status='rejected' WHERE id=?", (payment_id,)
            )
    if action == "pay_ok":
        try:
            await context.bot.send_message(
                payment["user_id"],
                f"🎉 پرداختت تایید شد و {payment['coins']} سکه به حسابت اضافه شد."
            )
        except Exception:
            pass
        await q.message.reply_text("✅ پرداخت تایید شد.")
    else:
        try:
            await context.bot.send_message(
                payment["user_id"],
                "❌ رسید پرداختت تایید نشد. برای پیگیری با پشتیبانی تماس بگیر."
            )
        except Exception:
            pass
        await q.message.reply_text("❌ پرداخت رد شد.")


# ---------- پنل مدیریت ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return
    await update.message.reply_text(
        "🛠 پنل مدیریت\n\n"
        "/pending - پرداخت‌های در انتظار\n"
        "/addcoins - افزودن سکه\n"
        "/broadcast - ارسال پیام همگانی\n"
        "/addadmin - افزودن مدیر\n"
        "/deladmin - حذف مدیر"
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with db() as con:
        rows = con.execute(
            "SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await update.message.reply_text("پرداخت در انتظاری وجود ندارد.")
        return
    for row in rows:
        await update.message.reply_text(
            f"🧾 پرداخت شماره {row['id']}\n"
            f"کاربر: {row['user_id']}\n"
            f"سکه: {row['coins']}\n"
            f"مبلغ: {row['price']:,} تومان"
        )


async def cmd_addcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "فرمت دستور:\n/addcoins USER_ID AMOUNT"
        )
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ اطلاعات واردشده معتبر نیست.")
        return
    with db() as con:
        con.execute(
            "UPDATE users SET coins=coins+? WHERE user_id=?",
            (amount, user_id),
        )
    await update.message.reply_text("✅ سکه‌ها اضافه شدند.")
    try:
        await context.bot.send_message(
            user_id, f"🎁 {amount} سکه به حسابت اضافه شد."
        )
    except Exception:
        pass


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text(
            "متن پیام را بعد از دستور بنویس.\n/broadcast متن پیام"
        )
        return
    with db() as con:
        users = con.execute("SELECT user_id FROM users").fetchall()
    sent = 0
    for row in users:
        try:
            await context.bot.send_message(row["user_id"], message)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ پیام برای {sent} کاربر ارسال شد.")


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.message.reply_text("/addadmin USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شناسه کاربر معتبر نیست.")
        return
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (user_id,)
        )
    await update.message.reply_text("✅ مدیر اضافه شد.")


async def cmd_deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.message.reply_text("/deladmin USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شناسه کاربر معتبر نیست.")
        return
    if user_id == update.effective_user.id:
        await update.message.reply_text("نمی‌توانی خودت را حذف کنی.")
        return
    with db() as con:
        con.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    await update.message.reply_text("✅ مدیر حذف شد.")


# ---------- پیام‌های متنی ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🪙 خرید سکه":
        await buy_menu(update, context)
        return

    if text == "👤 حساب کاربری":
        with db() as con:
            user = con.execute(
                "SELECT * FROM users WHERE user_id=?", (uid,)
            ).fetchone()
        if user:
            await update.message.reply_text(
                f"👤 حساب کاربری\n"
                f"شناسه: {uid}\n"
                f"🪙 موجودی سکه: {user['coins']}"
            )
        return

    if text == "🆘 پشتیبانی":
        await update.message.reply_text(
            f"برای ارتباط با پشتیبانی:\n{SUPPORT_ID}"
        )
        return

    await update.message.reply_text(
        "از دکمه‌های منو استفاده کن."
    )


# ---------- بررسی کاربران خارج‌شده ----------
async def check_leavers(context: ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows = con.execute(
            "SELECT user_id FROM users WHERE active=1"
        ).fetchall()
    for row in rows:
        try:
            member = await context.bot.get_chat_member(
                CHANNEL_ID, row["user_id"]
            )
            if member.status in ("left", "kicked"):
                with db() as con:
                    con.execute(
                        "UPDATE users SET active=0 WHERE user_id=?",
                        (row["user_id"],),
                    )
        except Exception:
            pass


# ---------- اجرای ربات ----------
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("addcoins", cmd_addcoins))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("addadmin", cmd_addadmin))
    app.add_handler(CommandHandler("deladmin", cmd_deladmin))

    app.add_handler(CallbackQueryHandler(on_check, pattern=r"^chk:\d+$"))
    app.add_handler(CallbackQueryHandler(on_next, pattern=r"^next$"))
    app.add_handler(CallbackQueryHandler(on_cancel, pattern=r"^cx:\d+$"))
    app.add_handler(CallbackQueryHandler(on_buy, pattern=r"^buy:\d+$"))
    app.add_handler(CallbackQueryHandler(on_pay_review, pattern=r"^pay_(ok|no):\d+$"))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_receipt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
