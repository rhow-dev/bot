
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
TOKEN = os.getenv("BOT_TOKEN", "PUT_NEW_TOKEN_HERE")
DB_PATH = os.getenv("DB_PATH", "bot.db")

FORCE_CHANNEL = "@NULLVASET"        # کانال اجباری
DEFAULT_ADMINS = [7281188442]       # مدیر اصلی
REWARD = 1                          # سکهی هر عضویت
PRICE = 2                           # هزینهی هر عضو سفارشی (سکه)
WELCOME_COINS = 2
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
BTN_ADMIN = "🛠 مدیریت"
MENU_BUTTONS = {BTN_EARN, BTN_ORDER, BTN_ACCOUNT, BTN_ORDERS, BTN_BUY, BTN_SUPPORT, BTN_ADMIN}


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
    rows = [[BTN_EARN, BTN_ORDER], [BTN_ACCOUNT, BTN_ORDERS], [BTN_BUY, BTN_SUPPORT]]
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
TOKEN = os.getenv("BOT_TOKEN", "PUT_NEW_TOKEN_HERE")
DB_PATH = os.getenv("DB_PATH", "bot.db")

FORCE_CHANNEL = "@NULLVASET"        # کانال اجباری
DEFAULT_ADMINS = [7281188442]       # مدیر اصلی
REWARD = 1                          # سکهی هر عضویت
PRICE = 2                           # هزینهی هر عضو سفارشی (سکه)
WELCOME_COINS = 2
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
BTN_ADMIN = "🛠 مدیریت"
MENU_BUTTONS = {BTN_EARN, BTN_ORDER, BTN_ACCOUNT, BTN_ORDERS, BTN_BUY, BTN_SUPPORT, BTN_ADMIN}


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
    rows = [[BTN_EARN, BTN_ORDER], [BTN_ACCOUNT, BTN_ORDERS], [BTN_BUY, BTN_SUPPORT]]
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
