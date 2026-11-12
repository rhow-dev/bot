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

# توکن را از متغیر محیطی بخوان (توکن قبلی لو رفته، حتماً از BotFather عوضش کن)
TOKEN = os.getenv("BOT_TOKEN", "9221180526:AAGXly8l7LFMbg1UY4vTfoc-o7EdcZE37sA")
DB_PATH = os.getenv("DB_PATH", "bot.db")

FORCE_CHANNEL = "@NULLVASET"        # کانال اجباری
DEFAULT_ADMINS = [7281188442]       # مدیر اصلی
REWARD = 1                          # سکهی هر عضویت
PRICE = 2                           # هزینهی هر عضو سفارشی (سکه)
WELCOME_COINS = 20                  # سکهی هدیه برای هر کاربر جدید
REFERRAL_REWARD = 10                # سکهی دعوت برای کسی که یک نفر را دعوت می‌کند
PENALTY = 7                         # جریمهی خروج از کانال (سکه)
CHECK_EVERY = 600                   # هر چند ثانیه یک دسته چک شود
CHECK_BATCH = 200                   # در هر نوبت چند عضویت چک شود

# ---------- تنظیمات خرید سکه ----------
CARD_NUMBER = "5859-8312-0689-7943"
CARD_OWNER = "غفوری"
SUPPORT_ID = "@VERATHB"

# (تعداد سکه، قیمت به تومان)
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
        # مایگریشن برای دیتابیس‌های قدیمی
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
    """کاربر جدید را با سکه‌ی هدیه ثبت می‌کند. اگر با لینک دعوت آمده، به دعوت‌کننده پاداش می‌دهد.
    آیدی دعوت‌کننده را برمی‌گرداند (برای اطلاع‌رسانی) یا None."""
    with db() as con:
        if con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            return None  # قبلاً ثبت شده؛ پاداشی داده نمی‌شود
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
        [InlineKeyboardButton("عضو شدم ✅", callback_data="fj")],
    ])


async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    q = update.callback_query
    # ذخیره‌ی لینک دعوت تا بعد از عضویت در کانال اجباری از دست نرود
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


# ---------- کاربران ----------
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
    context.user_data.clear()
    context.user_data.update(state="receipt", pkg=idx)
    await q.message.reply_text(
        f"🪙 بسته‌ی انتخابی: {coins} سکه\n"
        f"💵 مبلغ: {price:,} تومان\n\n"
        f"💳 شماره کارت:\n{CARD_NUMBER}\n"
        f"👤 به نام: {CARD_OWNER}\n\n"
        "بعد از واریز، «عکس رسید» را همینجا بفرست.\n"
        f"🆘 مشکل داشتی: {SUPPORT_ID}\n"
        "برای لغو: /start"
    )


async def on_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username
    msg = update.message
    if context.user_data.get("state") != "receipt":
        await msg.reply_text(
            f"برای خرید سکه از «{BTN_BUY}» استفاده کن.", reply_markup=menu_for(uid)
        )
        return
    if msg.photo:
        file_id, is_photo = msg.photo[-1].file_id, 1
    elif msg.document:
        file_id, is_photo = msg.document.file_id, 0
    else:
        return
    idx = context.user_data.get("pkg", 0)
    coins, price = PACKAGES[idx]
    with db() as con:
        cur = con.execute(
            "INSERT INTO payments(user_id, username, coins, price, file_id, is_photo, status, created_at) "
            "VALUES(?,?,?,?,?,?,'pending',strftime('%s','now'))",
            (uid, uname, coins, price, file_id, is_photo),
        )
        pid = cur.lastrowid
    context.user_data.clear()
    await msg.reply_text(
        "✅ رسیدت ثبت شد و برای مدیر ارسال شد.\n"
        "بعد از تایید، سکه‌ها به حسابت اضافه می‌شود و خبرت می‌کنم.",
        reply_markup=menu_for(uid),
    )
    await send_payment_to(context, list(all_admin_ids()), pid)


def payment_caption(p):
    uname = p["username"]
    user_line = f"@{uname}" if uname else f"(بدون یوزرنیم) {p['user_id']}"
    return (
        f"💳 درخواست خرید #{p['id']}\n"
        f"👤 کاربر: {user_line}\n"
        f"🪙 {p['coins']} سکه\n"
        f"💵 {p['price']:,} تومان"
    )


async def send_payment_to(context, admin_ids, pid):
    with db() as con:
        p = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not p:
        return 0
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("تایید ✅", callback_data=f"pay_ok:{pid}"),
        InlineKeyboardButton("رد ❌", callback_data=f"pay_no:{pid}"),
    ]])
    sent = 0
    for aid in admin_ids:
        try:
            if p["is_photo"]:
                await context.bot.send_photo(
                    aid, p["file_id"], caption=payment_caption(p), reply_markup=kb
                )
            else:
                await context.bot.send_document(
                    aid, p["file_id"], caption=payment_caption(p), reply_markup=kb
                )
            sent += 1
        except Exception:
            pass
    return sent


async def on_pay_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    admin_id = q.from_user.id
    if not is_admin(admin_id):
        await q.answer("فقط مدیر می‌تواند.", show_alert=True)
        return
    action, pid = q.data.split(":")
    pid = int(pid)
    approve = action == "pay_ok"
    with db() as con:
        p = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not p:
        await q.answer("پیدا نشد.", show_alert=True)
        return
    ensure_user(p["user_id"])
    new_status = "approved" if approve else "rejected"
    with db() as con:
        cur = con.execute(
            "UPDATE payments SET status=? WHERE id=? AND status='pending'", (new_status, pid)
        )
        if cur.rowcount == 0:
            await q.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
            return
        if approve:
            con.execute(
                "UPDATE users SET coins=coins+? WHERE id=?", (p["coins"], p["user_id"])
            )
    label = "✅ تایید شد" if approve else "❌ رد شد"
    await q.answer(label, show_alert=True)
    try:
        await q.edit_message_caption(
            caption=f"{payment_caption(p)}\n\n{label} (مدیر: {admin_id})"
        )
    except Exception:
        pass
    try:
        if approve:
            await context.bot.send_message(
                p["user_id"],
                f"🎉 خریدت تایید شد!\n{p['coins']} سکه به حسابت اضافه شد.\n"
                f"💰 موجودی فعلی: {get_coins(p['user_id'])} سکه",
            )
        else:
            await context.bot.send_message(
                p["user_id"],
                "❌ رسیدت تایید نشد. اگر اشتباه شده، دوباره از «خرید سکه» رسید درست بفرست "
                f"یا با پشتیبانی تماس بگیر: {SUPPORT_ID}",
            )
    except Exception:
        pass


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    with db() as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM payments WHERE status='pending' ORDER BY id").fetchall()]
    if not ids:
        await update.message.reply_text("رسید در انتظاری نیست ✅")
        return
    for pid in ids:
        await send_payment_to(context, [uid], pid)


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
        pend = con.execute("SELECT COUNT(*) c FROM payments WHERE status='pending'").fetchone()["c"]
        sold = con.execute(
            "SELECT COALESCE(SUM(price),0) s FROM payments WHERE status='approved'"
        ).fetchone()["s"]
    await update.message.reply_text(
        "🛠 پنل مدیریت\n\n"
        f"👥 کاربران: {users}\n"
        f"📦 کل سفارش‌ها: {total}\n"
        f"🟢 سفارش‌های فعال: {active}\n"
        f"✅ کل عضویت‌های انجام‌شده: {joins}\n"
        f"⚠️ جریمه‌شده‌ها (خروج): {penal}\n"
        f"🧾 رسیدهای در انتظار: {pend}\n"
        f"💰 مجموع فروش تاییدشده: {sold:,} تومان\n\n"
        "دستورها:\n"
        "/pending ← نمایش رسیدهای در انتظار تایید\n"
        "/addcoins آیدی_عددی تعداد ← افزودن/کسر سکه (عدد منفی = کسر)\n"
        "/broadcast متن ← ارسال پیام به همه‌ی کاربران\n"
        "/addadmin آیدی_عددی ← افزودن مدیر (فقط مدیر اصلی)\n"
        "/deladmin آیدی_عددی ← حذف مدیر (فقط مدیر اصلی)"
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

    if text in MENU_BUTTONS:
        context.user_data.clear()
        if text == BTN_EARN:
            return await earn(update, context)
        if text == BTN_ORDER:
            return await order_start(update, context)
        if text == BTN_ACCOUNT:
            return await account(update, context)
        if text == BTN_ORDERS:
            return await my_orders(update, context)
        if text == BTN_BUY:
            return await buy_menu(update, context)
        if text == BTN_SUPPORT:
            return await support(update, context)
        if text == BTN_REF:
            return await referral(update, context)
        if text == BTN_ADMIN:
            return await admin_panel(update, context)

    state = context.user_data.get("state")

    if state == "receipt":
        await update.message.reply_text("لطفاً «عکس رسید» را بفرست (نه متن). برای لغو: /start")
        return

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
        await update.message.reply_text(f"عضو چند نفر می‌خواهی؟ (حداکثر {mx})")
        return

    if state == "qty":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("یک عدد معتبر بفرست.")
            return
        qty = int(text)
        cost = qty * PRICE
        with db() as con:
            coins = con.execute(
                "SELECT coins FROM users WHERE id=?", (uid,)
            ).fetchone()["coins"]
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
            f"✅ سفارش #{oid} ثبت شد: {qty} عضو.\n({cost} سکه کم شد)\n"
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
            )
            if cur.rowcount == 0:
                continue
            row = con.execute(
                "SELECT coins FROM users WHERE id=?", (r["user_id"],)
            ).fetchone()
            have = max(row["coins"], 0) if row else 0
            deducted = min(have, PENALTY)  # موجودی هیچ‌وقت منفی نمی‌شود
            con.execute(
                "UPDATE users SET coins=coins-? WHERE id=?", (deducted, r["user_id"])
            )
            # عضو از شمارش کم می‌شود؛ سفارش لغوشده دوباره فعال نمی‌شود
            con.execute(
                "UPDATE orders SET done=MAX(done-1,0), "
                "active=CASE WHEN cancelled=1 THEN 0 ELSE 1 END WHERE id=?",
                (r["order_id"],),
            )
            balance = have - deducted
        try:
            await context.bot.send_message(
                r["user_id"],
                f"⚠️ از کانال {r['username']} خارج شدی و {deducted} سکه جریمه شدی.\n"
                f"💰 موجودی فعلی: {balance} سکه",
            )
        except Exception:
            pass


def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(TypeHandler(Update, force_join), group=-1)
    app.job_queue.run_repeating(check_leavers, interval=CHECK_EVERY, first=60)

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
