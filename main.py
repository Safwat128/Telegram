"""
Production-Ready Telegram Bot with Supabase PostgreSQL Database,
BDGate Live Payment Gateway Integration, USDT to BDT Pricing, and Admin Controls.
"""

import sys
import logging
from typing import Optional, Dict, Any, List

import httpx
import psycopg2
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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
# CONFIGURATION & CREDENTIALS
# ---------------------------------------------------------------------------
BOT_TOKEN = "8307223176:AAFj-2H86Ma6JmF_Zv1oYIPy0V3O15N-v5c"
ADMIN_USER_ID = 6551628383
EXTERNAL_API_BASE_URL = "https://safwantigershopbot-production.up.railway.app/api"
EXTERNAL_API_KEY = "stapi_ada749893e7a50de084870a8aaed5508390a5b067c0767ec270e740a8fd52c19"

# BDGate Payment Gateway Configuration
BDGATE_BASE_URL = "https://api.bdgate.net/api/v1"
BDGATE_API_KEY = "bd_live_06981e9a4c31b664c75fd4990c68f4010f2b8198"

USDT_TO_BDT = 135.0  # 1 USDT = 135 BDT Conversion Rate

if not BOT_TOKEN:
    logger.error("CRITICAL: BOT_TOKEN is missing!")
    sys.exit(1)

# ---------------------------------------------------------------------------
# DATABASE CONNECTION & INITIALIZATION
# ---------------------------------------------------------------------------
DATABASE_URL = "postgresql://postgres.zrvbpbslnuvnagtpbpsa:KW5DFquJJ5mPregz@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn:
        logger.error("Failed to connect to Supabase database during init.")
        return
    
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_products (
            product_id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            status TEXT DEFAULT 'Active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_history (
            id SERIAL PRIMARY KEY,
            product_id INTEGER,
            admin_id BIGINT,
            quantity_change INTEGER,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            product_type TEXT,
            product_id TEXT,
            product_name TEXT,
            amount REAL,
            status TEXT,
            delivery_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            payment_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount REAL,
            method TEXT,
            trx_id TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("Supabase PostgreSQL Database initialized successfully.")

init_db()

# ---------------------------------------------------------------------------
# EXTERNAL API & BDGATE PAYMENT CLIENT
# ---------------------------------------------------------------------------
class ExternalAPIClient:
    def __init__(self):
        self.base_url = EXTERNAL_API_BASE_URL.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {EXTERNAL_API_KEY}" if EXTERNAL_API_KEY else "",
            "x-api-key": EXTERNAL_API_KEY if EXTERNAL_API_KEY else "",
            "Content-Type": "application/json"
        }
        self.timeout = 12.0

    async def get_products(self) -> Optional[List[Dict[str, Any]]]:
        url = f"{self.base_url}/products"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return data.get("products") or data.get("data") or []
        except Exception as e:
            logger.error(f"External API error for /products: {e}")
        return None

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/balance"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"External API error for /balance: {e}")
        return None

    async def place_order(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/order"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=self.headers, json=payload)
                if response.status_code in [200, 201]:
                    return response.json()
                return {"error": f"API Status {response.status_code}: {response.text}"}
        except Exception as e:
            logger.error(f"External API error for /order: {e}")
            return {"error": str(e)}

async def create_bdgate_checkout(amount: float, order_id: str, user_name: str) -> Optional[Dict[str, Any]]:
    url = f"{BDGATE_BASE_URL}/checkout"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": BDGATE_API_KEY
    }
    payload = {
        "amount": f"{amount:.2f}",
        "customer_name": user_name or "Telegram User",
        "customer_email": "user@telegram.bot",
        "customer_phone": "01700000000",
        "order_id": order_id,
        "success_url": "https://t.me/",
        "fail_url": "https://t.me/",
        "cancel_url": "https://t.me/",
        "webhook_url": "https://safwantigershopbot-production.up.railway.app/api/webhook",
        "description": f"Balance Top-up {order_id}"
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                return response.json()
            logger.error(f"BDGate Checkout Error: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"BDGate connection error: {e}")
    return None

api_client = ExternalAPIClient()

# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def get_user_balance(user_id: int) -> float:
    conn = get_db_connection()
    if not conn:
        return 0.0
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else 0.0

def register_user_if_not_exists(user_id: int, username: str, first_name: str):
    conn = get_db_connection()
    if not conn:
        return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username, first_name) 
        VALUES (%s, %s, %s) 
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username, first_name))
    conn.commit()
    cursor.close()
    conn.close()

# ---------------------------------------------------------------------------
# KEYBOARDS
# ---------------------------------------------------------------------------
def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🛍️ Browse Products", callback_data="browse_products"),
         InlineKeyboardButton("💎 Premium Tools (API)", callback_data="premium_tools")],
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
        [InlineKeyboardButton("💵 Update Price", callback_data="adm_update_price"),
         InlineKeyboardButton("📋 Manage Orders", callback_data="adm_manage_orders")],
        [InlineKeyboardButton("💳 Manage Payments", callback_data="adm_manage_payments"),
         InlineKeyboardButton("👥 User Management", callback_data="adm_users")],
        [InlineKeyboardButton("📈 Sales Statistics", callback_data="adm_stats"),
         InlineKeyboardButton("⚙️ Bot Settings", callback_data="adm_settings")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------------------------------------------------------------------
# START & UNIFIED CALLBACK HANDLER
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    register_user_if_not_exists(user.id, user.username or "", user.first_name or "")
    is_admin = (user.id == ADMIN_USER_ID)
    
    welcome_text = (
        f"স্বাগতম, {user.first_name}!\n\n"
        "প্রিমিয়াম ডিজিটাল টুলস এবং সার্ভিসেস কিনতে নিচের মেনু থেকে আপনার প্রয়োজনীয় অপশনটি বেছে নিন:"
    )
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(is_admin))
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(welcome_text, reply_markup=main_menu_keyboard(is_admin))

async def handle_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_USER_ID)

    context.user_data['state'] = None

    if data == "main_menu":
        await query.edit_message_text("🏠 মূল মেনুতে স্বাগতম:", reply_markup=main_menu_keyboard(is_admin))
        return

    elif data == "browse_products":
        conn = get_db_connection()
        if not conn:
            await query.edit_message_text("Database error.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]))
            return
        cursor = conn.cursor()
        cursor.execute("SELECT product_id, name, price, stock FROM local_products WHERE status = 'Active'")
        products = cursor.fetchall()
        cursor.close()
        conn.close()

        if not products:
            await query.edit_message_text(
                "বর্তমানে কোনো লোকাল প্রোডাক্ট উপলব্ধ নেই।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]])
            )
            return

        keyboard = []
        for p_id, name, price, stock in products:
            keyboard.append([InlineKeyboardButton(f"{name} - ৳{price} (Stock: {stock})", callback_data=f"buy_local_{p_id}")])
        keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")])
        
        await query.edit_message_text("🛍️ লোকাল প্রোডাক্ট ক্যাটালগ:\nপছন্দের প্রোডাক্টটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "premium_tools":
        products = await api_client.get_products()
        if not products:
            await query.edit_message_text(
                "এক্সটার্নাল API থেকে প্রোডাক্ট লোড করা সম্ভব হয়নি অথবা বর্তমানে কোনো প্রোডাক্ট নেই।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]])
            )
            return

        context.user_data['ext_products'] = products
        keyboard = []
        for idx, prod in enumerate(products[:20]):
            name = prod.get("name") or prod.get("title") or f"Tool #{idx+1}"
            raw_price = float(prod.get("price") or prod.get("rate") or 0)
            bdt_price = raw_price * USDT_TO_BDT
            keyboard.append([InlineKeyboardButton(f"{name} - ৳{bdt_price:.2f}", callback_data=f"buy_ext_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")])

        await query.edit_message_text("💎 প্রিমিয়াম টুলস (External API):\n(রেট: ১ USDT = ১৩৫ টাকা)\nপছন্দের সার্ভিসটি সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "my_balance":
        balance = get_user_balance(user_id)
        text = f"💰 আপনার ওয়ালেট ব্যালেন্স\n\nবর্তমান ব্যালেন্স: ৳{balance:.2f}"
        keyboard = [[InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন", callback_data="add_balance")],
                    [InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "my_orders":
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, product_name, amount, status, created_at FROM orders WHERE user_id = %s ORDER BY order_id DESC LIMIT 8", (user_id,))
        orders = cursor.fetchall()
        cursor.close()
        conn.close()

        if not orders:
            text = "📦 আপনার কোনো অর্ডার হিস্ট্রি নেই।"
        else:
            text = "📦 আপনার সাম্প্রতিক অর্ডারসমূহ:\n\n"
            for o in orders:
                text += f"🆔 #ORD-{o[0]} | {o[1]} | ৳{o[2]} | স্ট্যাটাস: {o[3]}\nতারিখ: {o[4]}\n\n"

        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "add_balance":
        context.user_data['state'] = 'WAIT_DEPOSIT_AMOUNT'
        text = (
            "➕ ব্যালেন্স যোগ করুন (Online Payment)\n\n"
            "আপনি কত টাকা ডিপোজিট করতে চান তা ইংরেজিতে সংখ্যায় লিখুন (যেমন: 500):"
        )
        keyboard = [[InlineKeyboardButton("🔙 বাতিল করুন", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "support":
        text = "📞 কাস্টমার সাপোর্ট\n\nযেকোনো সমস্যায় আমাদের এডমিনের সাথে যোগাযোগ করুন।"
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "how_to_use":
        text = (
            "ℹ️ কীভাবে বট ব্যবহার করবেন:\n\n"
            "১. 'Add Balance' থেকে আপনার ওয়ালেটে টাকা যোগ করুন।\n"
            "২. 'Browse Products' বা 'Premium Tools' থেকে প্রোডাক্ট কিনুন।\n"
            "৩. 'My Orders' থেকে আপনার অর্ডারের বিস্তারিত দেখুন।"
        )
        keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # --- LOCAL PRODUCT PURCHASE ---
    elif data.startswith("buy_local_"):
        product_id = int(data.split("_")[2])
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()
        cursor.execute("SELECT name, description, price, stock FROM local_products WHERE product_id = %s AND status = 'Active'", (product_id,))
        prod = cursor.fetchone()
        cursor.close()
        conn.close()

        if not prod:
            await query.edit_message_text("❌ প্রোডাক্টটি পাওয়া যায়নি।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]]))
            return

        name, desc, price, stock = prod
        text = f"🛍️ প্রোডাক্ট: {name}\n📝 বিবরণ: {desc}\n💵 মূল্য: ৳{price}\n📦 স্টক: {stock}\n\nআপনি কি এটি কিনতে চান?"
        keyboard = [
            [InlineKeyboardButton("✅ হ্যাঁ, কনফার্ম করুন", callback_data=f"confirm_local_{product_id}")],
            [InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data.startswith("confirm_local_"):
        product_id = int(data.split("_")[2])
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, stock FROM local_products WHERE product_id = %s AND status = 'Active'", (product_id,))
        prod = cursor.fetchone()

        if not prod or prod[2] <= 0:
            cursor.close()
            conn.close()
            await query.edit_message_text("❌ দুঃখিত, প্রোডাক্টটি স্টক আউট!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]]))
            return

        name, price, stock = prod
        user_balance = get_user_balance(user_id)
        if user_balance < price:
            cursor.close()
            conn.close()
            await query.edit_message_text(
                f"❌ পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳{price} | আপনার আছে: ৳{user_balance:.2f}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন", callback_data="add_balance")],
                                                   [InlineKeyboardButton("🔙 ফিরে যান", callback_data="browse_products")]])
            )
            return

        success = False
        try:
            cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (price, user_id))
            cursor.execute("UPDATE local_products SET stock = stock - 1 WHERE product_id = %s AND stock > 0", (product_id,))
            cursor.execute("""
                INSERT INTO orders (user_id, product_type, product_id, product_name, amount, status, delivery_info) 
                VALUES (%s, 'local', %s, %s, %s, 'Completed', 'Delivered')
            """, (user_id, str(product_id), name, price))
            conn.commit()
            success = True
        except Exception as e:
            conn.rollback()
            logger.error(f"Order error: {e}")
        finally:
            cursor.close()
            conn.close()

        if success:
            await query.edit_message_text(f"✅ অর্ডার সফল হয়েছে!\nপ্রোডাক্ট: {name}\nমূল্য: ৳{price}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
        else:
            await query.edit_message_text("❌ অর্ডার সম্পন্ন করা যায়নি।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
        return

    # --- EXTERNAL PRODUCT PURCHASE (USDT to BDT) ---
    elif data.startswith("buy_ext_"):
        idx = int(data.split("_")[2])
        products = context.user_data.get('ext_products', [])
        if idx >= len(products):
            await query.edit_message_text("❌ সেশন এক্সপায়ার্ড। আবার চেষ্টা করুন।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="premium_tools")]]))
            return

        prod = products[idx]
        name = prod.get("name") or prod.get("title") or f"Tool #{idx+1}"
        raw_price = float(prod.get("price") or prod.get("rate") or 0)
        price = raw_price * USDT_TO_BDT

        text = f"💎 API প্রোডাক্ট: {name}\n💵 মূল্য: ৳{price:.2f} ({raw_price} USDT)\n\nআপনি কি অর্ডার কনফার্ম করতে চান?"
        keyboard = [
            [InlineKeyboardButton("✅ কনফার্ম অর্ডার", callback_data=f"confirm_ext_{idx}")],
            [InlineKeyboardButton("🔙 ফিরে যান", callback_data="premium_tools")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data.startswith("confirm_ext_"):
        idx = int(data.split("_")[2])
        products = context.user_data.get('ext_products', [])
        if idx >= len(products):
            await query.edit_message_text("❌ প্রোডাক্ট পাওয়া যায়নি।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="premium_tools")]]))
            return

        prod = products[idx]
        name = prod.get("name") or prod.get("title") or f"Tool #{idx+1}"
        raw_price = float(prod.get("price") or prod.get("rate") or 0)
        price = raw_price * USDT_TO_BDT
        p_id = prod.get("id") or prod.get("product_id") or idx

        user_balance = get_user_balance(user_id)
        if user_balance < price:
            await query.edit_message_text(f"❌ পর্যাপ্ত ব্যালেন্স নেই! প্রয়োজন: ৳{price:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ব্যালেন্স যোগ করুন", callback_data="add_balance")]]))
            return

        res = await api_client.place_order({"product_id": p_id, "quantity": 1})
        if res and not res.get("error"):
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (price, user_id))
                cursor.execute("""
                    INSERT INTO orders (user_id, product_type, product_id, product_name, amount, status, delivery_info) 
                    VALUES (%s, 'external', %s, %s, %s, 'Completed', %s)
                """, (user_id, str(p_id), name, price, str(res)))
                conn.commit()
                cursor.close()
                conn.close()
            await query.edit_message_text(f"✅ API অর্ডার সফল হয়েছে!\nপ্রোডাক্ট: {name}\nমূল্য: ৳{price:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
        else:
            err_msg = res.get("error", "API Rejected Order") if res else "API Connection Failed"
            await query.edit_message_text(f"❌ API অর্ডার ব্যর্থ হয়েছে:\n{err_msg}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
        return

    # =========================================================================
    # ADMIN PANEL SECTION
    # =========================================================================
    if data == "admin_panel" or data.startswith("adm_"):
        if not is_admin:
            await query.answer("❌ অননুমোদিত অ্যাক্সেস!", show_alert=True)
            return

        if data == "admin_panel":
            await query.edit_message_text("👑 এডমিন কন্ট্রোল প্যানেল:", reply_markup=admin_menu_keyboard())

        elif data == "adm_dashboard":
            conn = get_db_connection()
            if not conn:
                return
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
            cursor.close()
            conn.close()

            ext_balance_data = await api_client.get_balance()
            ext_balance = ext_balance_data.get("balance", "N/A") if isinstance(ext_balance_data, dict) else "Unavailable"

            text = (
                "📊 এডমিন ড্যাশবোর্ড (Supabase)\n\n"
                f"👥 মোট ইউজার: {total_users}\n"
                f"📦 মোট লোকাল প্রোডাক্ট: {total_prods}\n"
                f"📥 মোট লোকাল স্টক: {total_stock}\n"
                f"⏳ পেন্ডিং অর্ডার: {pending_orders}\n"
                f"✅ সম্পন্ন অর্ডার: {completed_orders}\n"
                f"💵 মোট লোকাল সেলস: ৳{total_sales:.2f}\n"
                f"🌐 এক্সটার্নাল API ব্যালেন্স: {ext_balance}"
            )
            keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "adm_add_tool":
            context.user_data['state'] = 'ADM_ADD_TOOL_NAME'
            await query.edit_message_text(
                "➕ নতুন টুল যোগ করতে প্রোডাক্টের নাম চ্যাট বক্সে লিখে পাঠান:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="admin_panel")]])
            )

        elif data == "adm_manage_tools":
            conn = get_db_connection()
            if not conn:
                return
            cursor = conn.cursor()
            cursor.execute("SELECT product_id, name, price, stock, status FROM local_products")
            prods = cursor.fetchall()
            cursor.close()
            conn.close()

            if not prods:
                text = "📦 কোনো লোকাল প্রোডাক্ট পাওয়া যায়নি।"
                keyboard = [[InlineKeyboardButton("➕ Add Tool", callback_data="adm_add_tool")],
                            [InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
            else:
                text = "📦 লোকাল টুলস ম্যানেজমেন্ট:\n\n"
                keyboard = []
                for p in prods:
                    text += f"ID: {p[0]} | {p[1]} | ৳{p[2]} | Stock: {p[3]} | [{p[4]}]\n"
                    toggle_Label = "🔴 Disable" if p[4] == 'Active' else "🟢 Enable"
                    keyboard.append([
                        InlineKeyboardButton(f"{toggle_Label} ID:{p[0]}", callback_data=f"adm_toggle_{p[0]}"),
                        InlineKeyboardButton(f"🗑️ Delete ID:{p[0]}", callback_data=f"adm_del_{p[0]}")
                    ])
                keyboard.append([InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")])

            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("adm_toggle_"):
            p_id = int(data.split("_")[2])
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM local_products WHERE product_id = %s", (p_id,))
                row = cursor.fetchone()
                if row:
                    new_status = 'Inactive' if row[0] == 'Active' else 'Active'
                    cursor.execute("UPDATE local_products SET status = %s WHERE product_id = %s", (new_status, p_id))
                    conn.commit()
                cursor.close()
                conn.close()
            await query.edit_message_text(f"✅ প্রোডাক্ট ID {p_id} এর স্ট্যাটাস আপডেট হয়েছে!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_manage_tools")]]))

        elif data.startswith("adm_del_"):
            p_id = int(data.split("_")[2])
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM local_products WHERE product_id = %s", (p_id,))
                conn.commit()
                cursor.close()
                conn.close()
            await query.edit_message_text(f"🗑️ প্রোডাক্ট ID {p_id} ডিলিট করা হয়েছে!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_manage_tools")]]))

        elif data == "adm_add_stock":
            context.user_data['state'] = 'ADM_STOCK_ID'
            await query.edit_message_text(
                "📥 স্টক বাড়াতে বা কমাতে প্রোডাক্টের ID লিখে পাঠান:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="admin_panel")]])
            )

        elif data == "adm_update_price":
            context.user_data['state'] = 'ADM_PRICE_ID'
            await query.edit_message_text(
                "💵 প্রোডাক্টের দাম পরিবর্তন করতে প্রোডাক্টের ID লিখে পাঠান:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="admin_panel")]])
            )

        elif data == "adm_manage_orders":
            conn = get_db_connection()
            if not conn:
                return
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, user_id, product_name, amount, status FROM orders ORDER BY order_id DESC LIMIT 8")
            orders = cursor.fetchall()
            cursor.close()
            conn.close()

            text = "📋 সাম্প্রতিক অর্ডারসমূহ:\n(ইউজারকে মেসেজ পাঠাতে নিচের বাটনে ক্লিক করুন)\n\n"
            keyboard = []
            if not orders:
                text += "কোনো অর্ডার পাওয়া যায়নি।"
            else:
                for o in orders:
                    o_id, u_id, p_name, amt, status = o
                    text += f"#{o_id} | User: `{u_id}` | {p_name} | ৳{amt}\n"
                    keyboard.append([
                        InlineKeyboardButton(f"✉️ Message User #{o_id} (ID: {u_id})", callback_data=f"adm_msg_user_{u_id}")
                    ])
            keyboard.append([InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        elif data.startswith("adm_msg_user_"):
            target_user_id = int(data.split("_")[3])
            context.user_data['msg_target_user'] = target_user_id
            context.user_data['state'] = 'ADM_SEND_USER_MSG'
            await query.edit_message_text(
                f"✉️ ইউজার (`{target_user_id}`)-কে পাঠানোর জন্য আপনার মেসেজটি চ্যাট বক্সে লিখে পাঠান:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="adm_manage_orders")]]),
                parse_mode="Markdown"
            )

        elif data == "adm_manage_payments":
            conn = get_db_connection()
            if not conn:
                return
            cursor = conn.cursor()
            cursor.execute("SELECT payment_id, user_id, amount, trx_id, status FROM payments WHERE status = 'Pending' ORDER BY payment_id DESC LIMIT 10")
            payments = cursor.fetchall()
            cursor.close()
            conn.close()

            text = "💳 পেন্ডিং পেমেন্ট রিকোয়েস্ট:\n\n"
            keyboard = []
            if not payments:
                text += "কোনো পেন্ডিং পেমেন্ট রিকোয়েস্ট নেই।"
            else:
                for pay in payments:
                    p_id, u_id, amt, trx, st = pay
                    text += f"ID: {p_id} | User: {u_id} | ৳{amt} | TrxID: {trx}\n"
                    keyboard.append([
                        InlineKeyboardButton(f"✅ Approve #{p_id} (৳{amt})", callback_data=f"adm_pay_app_{p_id}"),
                        InlineKeyboardButton(f"❌ Reject #{p_id}", callback_data=f"adm_pay_rej_{p_id}")
                    ])
            keyboard.append([InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("adm_pay_app_"):
            p_id = int(data.split("_")[3])
            conn = get_db_connection()
            msg = "Database error."
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, amount, status FROM payments WHERE payment_id = %s", (p_id,))
                row = cursor.fetchone()
                if row and row[2] == 'Pending':
                    u_id, amt, _ = row
                    cursor.execute("UPDATE payments SET status = 'Approved' WHERE payment_id = %s", (p_id,))
                    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amt, u_id))
                    conn.commit()
                    msg = f"✅ পেমেন্ট #{p_id} অ্যাপ্রুভ করা হয়েছে এবং ইউজার {u_id}-এর ব্যালেন্সে ৳{amt} যোগ হয়েছে!"
                    try:
                        await context.bot.send_message(chat_id=u_id, text=f"🎉 আপনার ৳{amt} ডিপোজিট সফলভাবে অ্যাপ্রুভ করা হয়েছে!")
                    except Exception:
                        pass
                else:
                    msg = "❌ পেমেন্টটি ইতিমধ্যে প্রসেস করা হয়েছে।"
                cursor.close()
                conn.close()
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_manage_payments")]]))

        elif data.startswith("adm_pay_rej_"):
            p_id = int(data.split("_")[3])
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE payments SET status = 'Rejected' WHERE payment_id = %s", (p_id,))
                conn.commit()
                cursor.close()
                conn.close()
            await query.edit_message_text(f"❌ পেমেন্ট #{p_id} রিজেক্ট করা হয়েছে।", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ফিরে যান", callback_data="adm_manage_payments")]]))

        elif data == "adm_users":
            conn = get_db_connection()
            if not conn:
                return
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, first_name, balance FROM users ORDER BY created_at DESC LIMIT 10")
            users = cursor.fetchall()
            cursor.close()
            conn.close()

            text = "👥 রেজিস্টার্ড ইউজার লিস্ট:\n\n"
            for u in users:
                text += f"🆔 {u[0]} | {u[1]} | ৳{u[2]:.2f}\n"

            keyboard = [
                [InlineKeyboardButton("➕ ইউজারকে ব্যালেন্স দিন / কাটুন", callback_data="adm_edit_user_bal")],
                [InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "adm_edit_user_bal":
            context.user_data['state'] = 'ADM_USER_BAL_ID'
            await query.edit_message_text(
                "👤 যে ইউজারের ব্যালেন্স পরিবর্তন করতে চান তার Telegram User ID লিখে পাঠান:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 বাতিল", callback_data="adm_users")]])
            )

        elif data == "adm_stats":
            conn = get_db_connection()
            if not conn:
                return
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), SUM(amount) FROM orders WHERE status = 'Completed'")
            ord_stats = cursor.fetchone()
            cursor.execute("SELECT COUNT(*), SUM(amount) FROM payments WHERE status = 'Approved'")
            pay_stats = cursor.fetchone()
            cursor.close()
            conn.close()

            text = (
                "📈 সেলস এবং পেমেন্ট স্ট্যাটিস্টিক্স:\n\n"
                f"🛒 মোট সফল অর্ডার: {ord_stats[0] or 0} টি\n"
                f"💵 মোট অর্ডার বিক্রি: ৳{(ord_stats[1] or 0.0):.2f}\n\n"
                f"💳 মোট সফল ডিপোজিট: {pay_stats[0] or 0} টি\n"
                f"💰 মোট ডিপোজিট কালেকশন: ৳{(pay_stats[1] or 0.0):.2f}"
            )
            keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "adm_settings":
            ext_status = "✅ সংযুক্ত" if EXTERNAL_API_KEY else "❌ কী নেই"
            text = (
                "⚙️ বট কনফিগারেশন ও সেটিংস:\n\n"
                f"👑 Admin User ID: {ADMIN_USER_ID}\n"
                f"🌐 External API URL: {EXTERNAL_API_BASE_URL}\n"
                f"🔑 External API Status: {ext_status}\n"
                f"🗄️ Database: Supabase PostgreSQL (Active)"
            )
            keyboard = [[InlineKeyboardButton("🔙 এডমিন মেনু", callback_data="admin_panel")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ---------------------------------------------------------------------------
# UNIFIED TEXT INPUT HANDLER (Includes BDGate Payment Generation)
# ---------------------------------------------------------------------------
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    state = context.user_data.get('state')

    if not state:
        return

    # --- USER DEPOSIT FLOW (BDGate Live Integration) ---
    if state == 'WAIT_DEPOSIT_AMOUNT':
        try:
            amount = float(text)
            if amount < 10 or amount > 50000:
                await update.message.reply_text("দয়া করে ১০ থেকে ৫০,০০০ টাকার মধ্যে অ্যামাউন্ট লিখুন:")
                return
            
            # Generate unique order/invoice id for payment
            order_id = f"TOPUP-{user_id}-{int(asyncio.get_event_loop().time())}" if 'asyncio' in globals() else f"TOPUP-{user_id}"
            
            # Call BDGate Checkout API
            wait_msg = await update.message.reply_text("⏳ পেমেন্ট গেটওয়ে লিংক তৈরি হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
            
            res = await create_bdgate_checkout(amount, order_id, user_name)
            
            # Delete waiting message
            try:
                await wait_msg.delete()
            except Exception:
                pass

            if res and ("payment_url" in res or "url" in res or "gateway_url" in res):
                pay_url = res.get("payment_url") or res.get("url") or res.get("gateway_url")
                
                # Save pending transaction in database
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO payments (user_id, amount, method, trx_id, status) 
                        VALUES (%s, %s, 'BDGate', %s, 'Pending')
                    """, (user_id, amount, order_id))
                    conn.commit()
                    cursor.close()
                    conn.close()

                context.user_data['state'] = None
                
                keyboard = [[InlineKeyboardButton("💳 পেমেন্ট সম্পূর্ণ করুন", url=pay_url)],
                            [InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]
                
                await update.message.reply_text(
                    f"✅ পেমেন্ট ইনভয়েস তৈরি হয়েছে!\n\nঅ্যামাউন্ট: ৳{amount:.2f}\nঅর্ডার আইডি: `{order_id}`\n\nনিচের লিংকে ক্লিক করে আপনার পেমেন্ট সম্পন্ন করুন:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                err_desc = res.get("error", "Gateway response error") if res else "API timeout or failure"
                await update.message.reply_text(
                    f"❌ পেমেন্ট লিংক তৈরি করতে ব্যর্থ হয়েছে।\nকারণ: {err_desc}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মূল মেনু", callback_data="main_menu")]]))
            
        except ValueError:
            await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন (যেমন: 500):")

    # --- ADMIN FLOWS ---
    elif user_id == ADMIN_USER_ID:
        if state == 'ADM_SEND_USER_MSG':
            target_u_id = context.user_data.get('msg_target_user')
            context.user_data['state'] = None
            try:
                await context.bot.send_message(
                    chat_id=target_u_id,
                    text=f"📢 এডমিন থেকে মেসেজ:\n\n{text}"
                )
                await update.message.reply_text(
                    f"✅ মেসেজটি সফলভাবে ইউজার (`{target_u_id}`)-কে পাঠানো হয়েছে!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]]),
                    parse_mode="Markdown"
                )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ মেসেজ পাঠানো যায়নি। ইউজার হয়তো বট ব্লক করে রেখেছেন বা চ্যাট শুরু করেননি。\nএরর: {e}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]]))

        elif state == 'ADM_ADD_TOOL_NAME':
            context.user_data['new_name'] = text
            context.user_data['state'] = 'ADM_ADD_TOOL_DESC'
            await update.message.reply_text("📝 প্রোডাক্টের বিবরণ (Description) লিখুন:")

        elif state == 'ADM_ADD_TOOL_DESC':
            context.user_data['new_desc'] = text
            context.user_data['state'] = 'ADM_ADD_TOOL_PRICE'
            await update.message.reply_text("💵 প্রোডাক্টের দাম (Price) সংখ্যায় লিখুন (যেমন: 200):")

        elif state == 'ADM_ADD_TOOL_PRICE':
            try:
                context.user_data['new_price'] = float(text)
                context.user_data['state'] = 'ADM_ADD_TOOL_STOCK'
                await update.message.reply_text("📦 প্রাথমিক স্টক পরিমাণ (Stock Quantity) পূর্ণসংখ্যায় লিখুন:")
            except ValueError:
                await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যায় দাম লিখুন:")

        elif state == 'ADM_ADD_TOOL_STOCK':
            try:
                stock = int(text)
                name = context.user_data['new_name']
                desc = context.user_data['new_desc']
                price = context.user_data['new_price']

                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO local_products (name, description, price, stock, status) 
                        VALUES (%s, %s, %s, %s, 'Active')
                    """, (name, desc, price, stock))
                    conn.commit()
                    cursor.close()
                    conn.close()
                context.user_data['state'] = None

                await update.message.reply_text(
                    f"✅ নতুন প্রোডাক্ট সফলভাবে যোগ হয়েছে!\nনাম: {name}\nমূল্য: ৳{price}\nস্টক: {stock}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]])
                )
            except ValueError:
                await update.message.reply_text("❌ স্টক পূর্ণসংখ্যায় লিখুন (যেমন: 10):")

        elif state == 'ADM_STOCK_ID':
            try:
                p_id = int(text)
                conn = get_db_connection()
                if not conn:
                    return
                cursor = conn.cursor()
                cursor.execute("SELECT name, stock FROM local_products WHERE product_id = %s", (p_id,))
                prod = cursor.fetchone()
                cursor.close()
                conn.close()
                if not prod:
                    await update.message.reply_text("❌ এই ID-তে কোনো প্রোডাক্ট নেই। সঠিক ID দিন:")
                    return
                context.user_data['edit_prod_id'] = p_id
                context.user_data['state'] = 'ADM_STOCK_QTY'
                await update.message.reply_text(f"প্রোডাক্ট: {prod[0]} (বর্তমান স্টক: {prod[1]})\nকত স্টক যোগ করতে চান তা সংখ্যায় লিখুন:")
            except ValueError:
                await update.message.reply_text("❌ সঠিক প্রোডাক্ট ID সংখ্যায় লিখুন:")

        elif state == 'ADM_STOCK_QTY':
            try:
                qty = int(text)
                p_id = context.user_data['edit_prod_id']
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE local_products SET stock = stock + %s WHERE product_id = %s", (qty, p_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
                context.user_data['state'] = None
                await update.message.reply_text(
                    f"✅ প্রোডাক্ট #{p_id}-এ {qty} টি স্টক যোগ করা হয়েছে!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]])
                )
            except ValueError:
                await update.message.reply_text("❌ সঠিক পূর্ণসংখ্যা লিখুন:")

        elif state == 'ADM_PRICE_ID':
            try:
                p_id = int(text)
                context.user_data['edit_prod_id'] = p_id
                context.user_data['state'] = 'ADM_PRICE_VAL'
                await update.message.reply_text(f"প্রোডাক্ট #{p_id}-এর নতুন দাম (Price) লিখুন:")
            except ValueError:
                await update.message.reply_text("❌ সঠিক প্রোডাক্ট ID লিখুন:")

        elif state == 'ADM_PRICE_VAL':
            try:
                new_price = float(text)
                p_id = context.user_data['edit_prod_id']
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE local_products SET price = %s WHERE product_id = %s", (new_price, p_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
                context.user_data['state'] = None
                await update.message.reply_text(
                    f"✅ প্রোডাক্ট #{p_id}-এর নতুন দাম ৳{new_price} সেট করা হয়েছে!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]])
                )
            except ValueError:
                await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন:")

        elif state == 'ADM_USER_BAL_ID':
            try:
                target_id = int(text)
                context.user_data['target_user_id'] = target_id
                context.user_data['state'] = 'ADM_USER_BAL_AMT'
                await update.message.reply_text(f"ইউজার {target_id}-এর ব্যালেন্সে কত টাকা যোগ বা বিয়োগ করতে চান লিখুন (যেমন: 500 বা -200):")
            except ValueError:
                await update.message.reply_text("❌ সঠিক User ID সংখ্যায় লিখুন:")

        elif state == 'ADM_USER_BAL_AMT':
            try:
                amt = float(text)
                target_id = context.user_data['target_user_id']
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amt, target_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
                context.user_data['state'] = None
                await update.message.reply_text(
                    f"✅ ইউজার {target_id}-এর ব্যালেন্স আপডেট করা হয়েছে!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 এডমিন প্যানেল", callback_data="admin_panel")]])
                )
            except ValueError:
                await update.message.reply_text("❌ সঠিক অ্যামাউন্ট সংখ্যায় লিখুন:")

# ---------------------------------------------------------------------------
# MAIN APPLICATION ROUTINE
# ---------------------------------------------------------------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_all_callbacks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    logger.info("Bot is starting and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
