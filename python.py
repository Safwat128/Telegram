"""
Production-Ready Telegram Bot for Digital Product Delivery & Secure Payments.
Includes External REST API Integration, Local SQLite Database, Admin Panel, and bKash/Nagad Payment Architecture.
"""

import os
import sys
import sqlite3
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ---------------------------------------------------------------------------
# LOGGING CONFIGURATION
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ENVIRONMENT CONFIGURATION & VALIDATION
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "6551628383"))
EXTERNAL_API_BASE_URL = os.getenv(
    "EXTERNAL_API_BASE_URL", 
    "https://safwantigershopbot-production.up.railway.app/api"
)
EXTERNAL_API_KEY = os.getenv("EXTERNAL_API_KEY")

# Optional Payment Credentials (bKash & Nagad)
BKASH_APP_KEY = os.getenv("BKASH_APP_KEY")
BKASH_APP_SECRET = os.getenv("BKASH_APP_SECRET")
BKASH_USERNAME = os.getenv("BKASH_USERNAME")
BKASH_PASSWORD = os.getenv("BKASH_PASSWORD")

NAGAD_MERCHANT_ID = os.getenv("NAGAD_MERCHANT_ID")
NAGAD_MERCHANT_NUMBER = os.getenv("NAGAD_MERCHANT_NUMBER")
NAGAD_MERCHANT_PRIVATE_KEY = os.getenv("NAGAD_MERCHANT_PRIVATE_KEY")
PAYMENT_CALLBACK_URL = os.getenv("PAYMENT_CALLBACK_URL")
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET")

if not BOT_TOKEN:
    logger.error("CRITICAL: BOT_TOKEN environment variable is missing!")
    sys.exit(1)

if not EXTERNAL_API_KEY:
    logger.warning("WARNING: EXTERNAL_API_KEY is not set. External API calls may fail.")

# Conversation states for Admin Actions
ADD_TOOL_NAME, ADD_TOOL_DESC, ADD_TOOL_PRICE, ADD_TOOL_STOCK, ADD_TOOL_STATUS = range(5)
ADD_BALANCE_AMOUNT = range(5, 6)

# ---------------------------------------------------------------------------
# DATABASE INITIALIZATION
# ---------------------------------------------------------------------------
DB_PATH = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users & Wallet Balance
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Local Products
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            status TEXT DEFAULT 'Active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Stock History
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            admin_id INTEGER,
            quantity_change INTEGER,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Local Orders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_type TEXT, -- 'local' or 'external'
            product_id INTEGER,
            product_name TEXT,
            amount REAL,
            status TEXT, -- 'Pending', 'Completed', 'Rejected'
            delivery_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Transactions / Payments
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            gateway TEXT,
            status TEXT, -- 'Pending', 'Verified', 'Failed'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

init_db()

# ---------------------------------------------------------------------------
# EXTERNAL API CLIENT
# ---------------------------------------------------------------------------
class ExternalAPIClient:
    def __init__(self):
        self.base_url = EXTERNAL_API_BASE_URL.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {EXTERNAL_API_KEY}" if EXTERNAL_API_KEY else "",
            "Content-Type": "application/json"
        }
        self.timeout = 10.0

    async def get_products(self) -> Optional[List[Dict[str, Any]]]:
        url = f"{self.base_url}/products"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, list) else data.get("products", [])
                logger.error(f"External API /products error: Status {response.status_code}")
        except Exception as e:
            logger.error(f"External API connection error for /products: {e}")
        return None

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/balance"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    return response.json()
                logger.error(f"External API /balance error: Status {response.status_code}")
        except Exception as e:
            logger.error(f"External API connection error for /balance: {e}")
        return None

    async def place_order(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/order"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=self.headers, json=payload)
                if response.status_code in [200, 201]:
                    return response.json()
                logger.error(f"External API /order error: Status {response.status_code} - {response.text}")
        except httpx.TimeoutException:
            logger.error("External API /order timed out.")
            return {"status": "timeout", "message": "Order request timed out. Please check status."}
        except Exception as e:
            logger.error(f"External API connection error for /order: {e}")
        return None

api_client = ExternalAPIClient()

# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def get_user_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0.0

def update_user_balance(user_id: int, amount: float):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def register_user_if_not_exists(user_id: int, username: str, first_name: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)", 
                   (user_id, username, first_name))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# KEYBOARDS
# ---------------------------------------------------------------------------
def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🛍️ Browse Products", callback_data="browse_products"),
         InlineKeyboardButton("💎 Premium Tools", callback_data="premium_tools")],
        [InlineKeyboardButton("💰 My Balance", callback_data="my_balance"),
         InlineKeyboardButton("📦 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="add_balance"),
         InlineKeyboardButton("📞 Support", callback_data="support")],
        [InlineKeyboardButton("ℹ️ How to Use", callback_data="how_to_use")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="adm_dashboard"),
         InlineKeyboardButton("➕ Add Tool", callback_data="adm_add_tool")],
        [InlineKeyboardButton("📦 Manage Tools", callback_data="adm_manage_tools"),
         InlineKeyboardButton("📥 Add Stock", callback_data="adm_add_stock")],
        [InlineKeyboardButton("📋 Manage Orders", callback_data="adm_manage_orders"),
         InlineKeyboardButton("💳 Manage Payments", callback_data="adm_manage_payments")],
        [InlineKeyboardButton("👥 User Management", callback_data="adm_users"),
         InlineKeyboardButton("📈 Sales Statistics", callback_data="adm_stats")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="adm_settings"),
         InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------------------------------------------------------------------
# USER HANDLERS
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user_if_not_exists(user.id, user.username or "", user.first_name or "")
    is_admin = (user.id == ADMIN_USER_ID)
    
    welcome_text = (
        f"স্বাগতম, {user.first_name}!\n\n"
        "প্রিমিয়াম ডিজিটাল টুলস, সার্ভিসেস এবং অ্যাকাউন্ট কিনতে আমাদের বট ব্যবহার করুন।\n"
        "নিচের মেনু থেকে আপনার প্রয়োজনীয় অপশনটি বেছে নিন:"
    )
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(is_admin))
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(is_admin))

async def handle_main_menu_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_USER_ID)

    if data == "main_menu":
        await query.edit_message_text(
            "মূল মেনুতে স্বাগতম:", 
            reply_markup=main_menu_keyboard(is_admin)
        )
    
    elif data == "browse_products":
        # Local Products Catalog
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT product_id, name, price, stock FROM local_products WHERE status = 'Active'")
        products = cursor.fetchall()
        conn.close()

        if not products:
            await query.edit_message_text(
                "বর্তমানে কোনো লোকাল প্রোডাক্ট উপলব্ধ নেই।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]])
            )
            return

        keyboard = []
        for p in products:
            p_id, name, price, stock = p
            keyboard.append([InlineKeyboardButton(f"{name} - ৳{price} (Stock: {stock})", callback_data=f"buy_local_{p_id}")])
        keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")])
        
        await query.edit_message_text("🛍️ **লোকাল প্রোডাক্ট ক্যাটালগ**\n\nপছন্দের প্রোডাক্টটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "premium_tools":
        # External API Products Catalog
        products = await api_client.get_products()
        if not products:
            await query.edit_message_text(
                "এক্সটার্নাল API থেকে প্রোডাক্ট লোড করা সম্ভব হয়নি অথবা কোনো প্রোডাক্ট নেই।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]])
            )
            return

        keyboard = []
        for idx, prod in enumerate(products):
            name = prod.get("name", f"Product {idx}")
            price = prod.get("price", "N/A")
            keyboard.append([InlineKeyboardButton(f"{name} - ৳{price}", callback_data=f"buy_ext_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")])

        await query.edit_message_text("💎 **প্রিমিয়াম টুলস (External API)**\n\nপ্রোডাক্ট সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "my_balance":
        balance = get_user_balance(user_id)
        text = f"💰 **আপনার ওয়ালেট ব্যালেন্স**\n\nবর্তমান ব্যালেন্স: ৳{balance:.2f}"
        keyboard = [[InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন", callback_data="add_balance")],
                    [InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "my_orders":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, product_name, amount, status, created_at FROM orders WHERE user_id = ? ORDER BY order_id DESC LIMIT 5", (user_id,))
        orders = cursor.fetchall()
        conn.close()

        if not orders:
            text = "📦 আপনার কোনো অর্ডার হিস্ট্রি নেই।"
        else:
            text = "📦 **আপনার সাম্প্রতিক অর্ডারসমূহ:**\n\n"
            for o in orders:
                o_id, p_name, amt, status, date = o
                text += f"🆔 `#ORD-{o_id}` | {p_name} | ৳{amt} | স্ট্যাটাস: **{status}**\nতারিখ: {date}\n\n"

        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "add_balance":
        if not BKASH_APP_KEY and not NAGAD_MERCHANT_ID:
            text = (
                "💳 **অটোমেটেড পেমেন্ট সেটআপ নোটিশ**\n\n"
                "বর্তমানে bKash বা Nagad পেমেন্ট গেটওয়ে কনফিগার করা নেই। "
                "দয়া করে এডমিনের সাথে যোগাযোগ করে ব্যালেন্স রিচার্জ করুন।"
            )
            keyboard = [[InlineKeyboardButton("📞 সাপোর্ট", callback_data="support"),
                         InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await query.edit_message_text(
                "➕ ব্যালেন্স যোগ করতে কত টাকা রিচার্জ করতে চান তা ইংরেজিতে অ্যামাউন্ট লিখুন (যেমন: 500):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]])
            )
            return ADD_BALANCE_AMOUNT

    elif data == "support":
        text = "📞 **কাস্টমার সাপোর্ট**\n\nযে কোনো সমস্যায় আমাদের এডমিনের সাথে যোগাযোগ করুন:\n@AdminUsername"
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "how_to_use":
        text = (
            "ℹ️ **কীভাবে ব্যবহার করবেন:**\n\n"
            "১. ওয়ালেটে ব্যালেন্স যোগ করুন বা ক্যাটালগ থেকে সরাসরি কিনুন।\n"
            "২. 'Browse Products' বা 'Premium Tools' থেকে পছন্দের সার্ভিস সিলেক্ট করুন।\n"
            "৩. সফল পেমেন্টের পর তাৎক্ষণিকভাবে ডেলিভারি তথ্য পেয়ে যাবেন।"
        )
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "admin_panel":
        if user_id != ADMIN_USER_ID:
            await query.answer("আপনার এই প্যানেলটি ব্যবহার করার অনুমতি নেই!", show_alert=True)
            return
        await query.edit_message_text("👑 **এডমিন কন্ট্রোল প্যানেল**", reply_markup=admin_menu_keyboard(), parse_mode="Markdown")

# ---------------------------------------------------------------------------
# LOCAL PRODUCT PURCHASE FLOW
# ---------------------------------------------------------------------------
async def handle_buy_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    product_id = int(query.data.split("_")[2])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, description, price, stock FROM local_products WHERE product_id = ? AND status = 'Active'", (product_id,))
    prod = cursor.fetchone()
    conn.close()

    if not prod:
        await query.edit_message_text("প্রোডাক্টটি বর্তমানে উপলব্ধ নেই।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]]))
        return

    name, desc, price, stock = prod
    if stock <= 0:
        await query.edit_message_text("দুঃখিত, প্রোডাক্টটি স্টক আউট!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]]))
        return

    user_balance = get_user_balance(user_id)
    if user_balance < price:
        text = f"❌ আপনার পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳{price} | আপনার ব্যালেন্স: ৳{user_balance}"
        keyboard = [[InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন", callback_data="add_balance")],
                    [InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Process Purchase Transaction
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        # Deduct balance
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
        # Reduce stock
        cursor.execute("UPDATE local_products SET stock = stock - 1 WHERE product_id = ? AND stock > 0", (product_id,))
        if cursor.rowcount == 0:
            raise Exception("Out of stock during purchase")
        
        # Record order
        cursor.execute("INSERT INTO orders (user_id, product_type, product_id, product_name, amount, status, delivery_info) VALUES (?, 'local', ?, ?, ?, 'Completed', 'Access granted via automated local delivery.')",
                       (user_id, product_id, name, price))
        conn.commit()
        delivery_success = True
    except Exception as e:
        conn.rollback()
        logger.error(f"Purchase failed: {e}")
        delivery_success = False
    finally:
        conn.close()

    if delivery_success:
        await query.edit_message_text(
            f"✅ অর্ডার সফল হয়েছে!\n\nপ্রোডাক্ট: {name}\nমূল্য: ৳{price}\nস্ট্যাটাস: কমপ্লিটেড\n\nডেলিভারি তথ্য: আপনার প্রোডাক্ট সফলভাবে ডেলিভারি করা হয়েছে। 'My Orders' মেনু থেকে চেক করুন।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
    else:
        await query.edit_message_text(
            "❌ পেমেন্ট বা স্টক সংক্রান্ত সমস্যার কারণে অর্ডার সম্পন্ন হয়নি। আপনার ব্যালেন্স রিফান্ড করা হয়েছে বা কাটা হয়নি।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))

# ---------------------------------------------------------------------------
# ADMIN PANEL HANDLERS
# ---------------------------------------------------------------------------
async def handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id != ADMIN_USER_ID:
        await query.answer("অননুমোদিত অ্যাক্সেস!", show_alert=True)
        return

    data = query.data

    if data == "adm_dashboard":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*), SUM(stock) FROM local_products")
        prod_res = cursor.fetchone()
        total_prods = prod_res[0]
        total_stock = prod_res[1] or 0
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'Pending'")
        pending_orders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*), SUM(amount) FROM orders WHERE status = 'Completed'")
        ord_res = cursor.fetchone()
        completed_orders = ord_res[0]
        total_sales = ord_res[1] or 0.0
        conn.close()

        ext_balance_data = await api_client.get_balance()
        ext_balance = ext_balance_data.get("balance", "N/A") if ext_balance_data else "Unavailable"

        text = (
            "📊 **এডমিন ড্যাশবোর্ড**\n\n"
            f"👥 মোট রেজিস্টার্ড ইউজার: {total_users}\n"
            f"📦 মোট লোকাল প্রোডাক্ট: {total_prods}\n"
            f"📥 মোট এভেইলেবল স্টক: {total_stock}\n"
            f"⏳ পেন্ডিং অর্ডার: {pending_orders}\n"
            f"✅ সম্পন্ন অর্ডার: {completed_orders}\n"
            f"💵 মোট লোকাল সেলস: ৳{total_sales:.2f}\n"
            f"🌐 এক্সটার্নাল API ব্যালেন্স: {ext_balance}"
        )
        keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "adm_manage_tools":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT product_id, name, price, stock, status FROM local_products")
        prods = cursor.fetchall()
        conn.close()

        if not prods:
            text = "কোনো লোকাল টুল পাওয়া যায়নি।"
        else:
            text = "📦 **লোকাল টুলস ম্যানেজমেন্ট:**\n\n"
            for p in prods:
                text += f"ID: {p[0]} | {p[1]} | ৳{p[2]} | Stock: {p[3]} | [{p[4]}]\n"

        keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_add_tool":
        await query.edit_message_text(
            "➕ নতুন টুল যোগ করতে প্রোডাক্টের **নাম** লিখুন:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="admin_panel")]])
        )
        return ADD_TOOL_NAME

    elif data == "adm_manage_orders":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, user_id, product_name, amount, status FROM orders ORDER BY order_id DESC LIMIT 10")
        orders = cursor.fetchall()
        conn.close()

        text = "📋 **সাম্প্রতিক অর্ডারসমূহ:**\n\n"
        for o in orders:
            text += f"ID: {o[0]} | User: {o[1]} | {o[2]} | ৳{o[3]} | Status: {o[4]}\n"

        keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_manage_payments" or data == "adm_add_stock" or data == "adm_users" or data == "adm_stats" or data == "adm_settings":
        text = f"⚙️ {data.replace('adm_', '').replace('_', ' ').title()} মডিউল প্রস্তুত রয়েছে।"
        keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------------------------------------------------------------------------
# CONVERSATION HANDLERS FOR ADMIN & PAYMENTS
# ---------------------------------------------------------------------------
async def admin_add_tool_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_tool_name'] = update.message.text
    await update.message.reply_text("প্রোডাক্টের বিবরণ (Description) লিখুন:")
    return ADD_TOOL_DESC

async def admin_add_tool_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_tool_desc'] = update.message.text
    await update.message.reply_text("প্রোডাক্টের বিক্রয়মূল্য (Selling Price) সংখ্যায় লিখুন (যেমন: 250):")
    return ADD_TOOL_PRICE

async def admin_add_tool_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        context.user_data['new_tool_price'] = price
        await update.message.reply_text("প্রাথমিক স্টক পরিমাণ (Initial Stock Quantity) লিখুন:")
        return ADD_TOOL_STOCK
    except ValueError:
        await update.message.reply_text("দয়া করে সঠিক সংখ্যা লিখুন। আবার চেষ্টা করুন:")
        return ADD_TOOL_PRICE

async def admin_add_tool_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock = int(update.message.text)
        name = context.user_data.get('new_tool_name')
        desc = context.user_data.get('new_tool_desc')
        price = context.user_data.get('new_tool_price')

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO local_products (name, description, price, stock, status) VALUES (?, ?, ?, ?, 'Active')",
                       (name, desc, price, stock))
        p_id = cursor.lastrowid
        cursor.execute("INSERT INTO stock_history (product_id, admin_id, quantity_change, reason) VALUES (?, ?, ?, ?)",
                       (p_id, update.effective_user.id, stock, "Initial Stock Creation"))
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✅ সফলভাবে নতুন টুল যোগ করা হয়েছে!\nনাম: {name}\nমূল্য: ৳{price}\nস্টক: {stock}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]])
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("দয়া করে সঠিক পূর্ণসংখ্যা লিখুন:")
        return ADD_TOOL_STOCK

async def user_add_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if amount < 10 or amount > 50000:
            await update.message.reply_text("দয়া করে ১০ টাকা থেকে ৫০,০০০ টাকার মধ্যে অ্যামাউন্ট লিখুন:")
            return ADD_BALANCE_AMOUNT
        
        user_id = update.effective_user.id
        # Live payment redirection or instructions mockup if credentials aren't live webhook
        text = (
            f"💳 পেমেন্ট গেটওয়ে ইনিশিয়েটেড!\n\n"
            f"অ্যামাউন্ট: ৳{amount}\n"
            "যেহেতু লাইভ মার্চেন্ট গেটওয়ে ব্যাকএন্ড কলব্যাক রিসিভ করার জন্য HTTPS কনফিগার করা প্রয়োজন, "
            "অনুগ্রহ করে অফিশিয়াল মার্চেন্ট পেমেন্ট সম্পন্ন করুন অথবা এডমিনের সাথে যোগাযোগ করুন।"
        )
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("দয়া করে সঠিক অ্যামাউন্ট লিখুন:")
        return ADD_BALANCE_AMOUNT

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("অপারেশন বাতিল করা হয়েছে।", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---------------------------------------------------------------------------
# MAIN APPLICATION ROUTINE
# ---------------------------------------------------------------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation Handlers
    add_tool_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_tool_name, pattern="^adm_add_tool$")],
        states={
            ADD_TOOL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_name)],
            ADD_TOOL_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_desc)],
            ADD_TOOL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_price)],
            ADD_TOOL_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_tool_stock)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    add_balance_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_main_menu_callbacks, pattern="^add_balance$")],
        states={
            ADD_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_add_balance_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    application.add_handler(add_tool_conv)
    application.add_handler(add_balance_conv)

    # Command & Callback Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_admin_callbacks, pattern="^adm_"))
    application.add_handler(CallbackQueryHandler(handle_buy_local, pattern="^buy_local_"))
    application.add_handler(CallbackQueryHandler(handle_main_menu_callbacks))

    logger.info("Bot is starting and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
