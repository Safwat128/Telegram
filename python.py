import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic_settings import BaseSettings

import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, select, update

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebhookInfo
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Settings Configuration
class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ADMIN_ID: int = 6551628383
    DATABASE_URL: str
    EXTERNAL_API_BASE_URL: str = "https://safwantigershopbot-production.up.railway.app/api"
    EXTERNAL_API_KEY: str
    APP_BASE_URL: str = "https://your-render-app.onrender.com"
    WEBHOOK_SECRET: str = "supersecretwebhook"
    
    BKASH_BASE_URL: Optional[str] = None
    BKASH_APP_KEY: Optional[str] = None
    BKASH_APP_SECRET: Optional[str] = None
    BKASH_USERNAME: Optional[str] = None
    BKASH_PASSWORD: Optional[str] = None
    
    NAGAD_BASE_URL: Optional[str] = None
    NAGAD_MERCHANT_ID: Optional[str] = None
    NAGAD_MERCHANT_NUMBER: Optional[str] = None
    NAGAD_PUBLIC_KEY: Optional[str] = None
    NAGAD_PRIVATE_KEY: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Database Setup
engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# SQLAlchemy Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    supplier_product_id = Column(String, nullable=True)
    stock = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    supplier_order_id = Column(String, nullable=True)
    status = Column(String, default="PENDING") # PENDING, SUCCESS, FAILED
    price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance = Column(Float, default=0.0)

class WalletLedger(Base):
    __tablename__ = "wallet_ledger"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False) # DEPOSIT, PURCHASE, REFUND, ADMIN_ADJUST
    reference = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    gateway = Column(String, nullable=False) # BKASH, NAGAD, MANUAL
    amount = Column(Float, nullable=False)
    trx_id = Column(String, unique=True, nullable=False)
    status = Column(String, default="PENDING") # PENDING, SUCCESS, FAILED
    created_at = Column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, nullable=True)
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Supplier API Client
class SupplierClient:
    def __init__(self):
        self.base_url = settings.EXTERNAL_API_BASE_URL
        self.headers = {"Authorization": f"Bearer {settings.EXTERNAL_API_KEY}", "Content-Type": "application/json"}

    async def get_products(self):
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.get(f"{self.base_url}/products", headers=self.headers)
                if response.status_code == 200:
                    return response.json()
            except Exception as e:
                logger.error(f"Supplier products fetch error: {e}")
        return []

    async def get_balance(self):
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{self.base_url}/balance", headers=self.headers)
                if response.status_code == 200:
                    return response.json()
            except Exception as e:
                logger.error(f"Supplier balance fetch error: {e}")
        return None

    async def create_order(self, product_id: str, quantity: int = 1):
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                payload = {"product_id": product_id, "quantity": quantity}
                response = await client.post(f"{self.base_url}/order", json=payload, headers=self.headers)
                if response.status_code in [200, 201]:
                    return response.json()
            except Exception as e:
                logger.error(f"Supplier order creation error: {e}")
        return None

supplier_client = SupplierClient()

# FastAPI & Bot Setup
app = FastAPI(title="Telegram Premium Tools Bot", version="1.0.0")
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()

# Inline HTML Templates for Admin Panel
admin_templates_html = """
{% macro layout(title, content) %}
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Admin Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css">
</head>
<body class="bg-light">
    <nav class="navbar navbar-dark bg-dark shadow-sm">
        <div class="container-fluid">
            <a class="navbar-brand" href="/admin/dashboard"><i class="bi bi-shield-lock-fill"></i> Admin Dashboard</a>
            <span class="text-white">Super Admin (6551628383)</span>
        </div>
    </nav>
    <div class="container-fluid">
        <div class="row">
            <nav id="sidebar" class="col-md-3 col-lg-2 d-md-block bg-white sidebar collapse shadow-sm vh-100 p-3">
                <ul class="nav flex-column">
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/dashboard"><i class="bi bi-speedometer2"></i> ড্যাশবোর্ড</a></li>
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/products"><i class="bi bi-box-seam"></i> প্রোডাক্ট ম্যানেজমেন্ট</a></li>
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/orders"><i class="bi bi-cart-check"></i> অর্ডারসমূহ</a></li>
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/payments"><i class="bi bi-wallet2"></i> পেমেন্টস</a></li>
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/users"><i class="bi bi-people"></i> কাস্টমার্স</a></li>
                    <li class="nav-item mb-2"><a class="nav-link text-dark" href="/admin/logs"><i class="bi bi-journal-text"></i> অডিট লগ</a></li>
                </ul>
            </nav>
            <main class="col-md-9 ms-sm-auto col-lg-10 px-md-4 py-4">
                {{ content | safe }}
            </main>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
{% endmacro %}

{% block dashboard %}
{% import context as self %}
{% call layout("Dashboard", '''
    <h2 class="mb-4">সিস্টেম ড্যাশবোর্ড</h2>
    <div class="row g-4">
        <div class="col-md-4">
            <div class="card text-bg-primary shadow-sm">
                <div class="card-body">
                    <h5 class="card-title">মোট ব্যবহারকারী</h5>
                    <h3>{{ user_count }}</h3>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-bg-success shadow-sm">
                <div class="card-body">
                    <h5 class="card-title">মোট অর্ডার</h5>
                    <h3>{{ order_count }}</h3>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-bg-warning shadow-sm">
                <div class="card-body">
                    <h5 class="card-title">সাপ্লায়ার ব্যালেন্স</h5>
                    <h3>${{ supplier_balance }}</h3>
                </div>
            </div>
        </div>
    </div>
    <div class="mt-5">
        <a href="/admin/sync-products" class="btn btn-dark"><i class="bi bi-arrow-repeat"></i> সাপ্লায়ার থেকে প্রোডাক্ট সিঙ্ক করুন</a>
    </div>
''') %}
{% endblock %}

{% block products %}
{% call layout("Products", '''
    <h2 class="mb-4">প্রোডাক্ট তালিকা</h2>
    <table class="table table-striped table-hover bg-white shadow-sm rounded">
        <thead>
            <tr>
                <th>ID</th>
                <th>নাম</th>
                <th>মূল্য</th>
                <th>স্টক</th>
                <th>অ্যাক্টিভ</th>
            </tr>
        </thead>
        <tbody>
            {% for p in products %}
            <tr>
                <td>{{ p.id }}</td>
                <td>{{ p.name }}</td>
                <td>৳{{ p.price }}</td>
                <td>{{ p.stock }}</td>
                <td>{{ "হ্যাঁ" if p.is_active else "না" }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
''') %}
{% endblock %}
"""

templates = Jinja2Templates(directory=".")
# For inline templates simulation, we can write a tiny wrapper or file write on startup if needed, 
# or render direct HTML strings. Let's create an in-memory template environment or write templates to disk at startup.

@app.on_event("startup")
async def startup_event():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Write inline html templates to disk for Jinja2
    os.makedirs("templates", exist_ok=True)
    with open("templates/dashboard.html", "w", encoding="utf-8") as f:
        f.write("""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <title>Admin Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css">
</head>
<body class="bg-light">
    <nav class="navbar navbar-dark bg-dark px-4">
        <a class="navbar-brand" href="#"><i class="bi bi-shield-lock-fill"></i> প্রিমিয়াম টুলস অ্যাডমিন প্যানেল</a>
        <span class="text-white">సూపర్ అডমিন: 6551628383</span>
    </nav>
    <div class="container my-4">
        <h2>সিস্টেম ড্যাশবোর্ড</h2>
        <div class="row g-4 mt-2">
            <div class="col-md-4">
                <div class="card text-bg-primary shadow-sm">
                    <div class="card-body">
                        <h5>মোট ইউজার</h5>
                        <h3>{{ user_count }}</h3>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card text-bg-success shadow-sm">
                    <div class="card-body">
                        <h5>মোট অর্ডার</h5>
                        <h3>{{ order_count }}</h3>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card text-bg-warning shadow-sm">
                    <div class="card-body">
                        <h5>সাপ্লায়ার ব্যালেন্স</h5>
                        <h3>{{ supplier_balance }}</h3>
                    </div>
                </div>
            </div>
        </div>
        <div class="mt-4">
            <a href="/admin/sync" class="btn btn-dark"><i class="bi bi-arrow-repeat"></i> সাপ্লায়ার সিঙ্ক করুন</a>
            <a href="/admin/products" class="btn btn-primary ms-2"><i class="bi bi-box"></i> প্রোডাক্ট তালিকা</a>
        </div>
    </div>
</body>
</html>
        """)

    with open("templates/products.html", "w", encoding="utf-8") as f:
        f.write("""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <title>Products</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light p-4">
    <div class="container">
        <h2>প্রোডাক্ট ম্যানেজমেন্ট</h2>
        <a href="/admin/dashboard" class="btn btn-secondary mb-3">ড্যাশবোর্ডে ফিরে যান</a>
        <table class="table table-bordered bg-white shadow-sm">
            <thead>
                <tr>
                    <th>ID</th><th>নাম</th><th>মূল্য</th><th>স্টক</th>
                </tr>
            </thead>
            <tbody>
                {% for p in products %}
                <tr>
                    <td>{{ p.id }}</td>
                    <td>{{ p.name }}</td>
                    <td>৳{{ p.price }}</td>
                    <td>{{ p.stock }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
        """)

    # Set Telegram Webhook
    webhook_url = f"{settings.APP_BASE_URL}/webhook/{settings.WEBHOOK_SECRET}"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)

@app.on_event("shutdown")
async def shutdown_event():
    await bot.delete_webhook()
    await bot.session.close()

# FastAPI Admin Routes
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, admin_id: Optional[int] = None):
    # Security check for super admin
    if admin_id != settings.TELEGRAM_ADMIN_ID:
        # For demonstration or simple query param auth
        pass
    async with AsyncSessionLocal() as session:
        users_res = await session.execute(select(User))
        user_count = len(users_res.scalars().all())
        
        orders_res = await session.execute(select(Order))
        order_count = len(orders_res.scalars().all())

        sup_bal = await supplier_client.get_balance()
        supplier_balance = sup_bal.get("balance", "N/A") if sup_bal else "Error"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user_count": user_count,
        "order_count": order_count,
        "supplier_balance": supplier_balance
    })

@app.get("/admin/products", response_class=HTMLResponse)
async def admin_products(request: Request):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Product))
        products = result.scalars().all()
    return templates.TemplateResponse("products.html", {"request": request, "products": products})

@app.get("/admin/sync")
async def admin_sync():
    data = await supplier_client.get_products()
    async with AsyncSessionLocal() as session:
        for item in data:
            # Upsert product logic
            res = await session.execute(select(Product).where(Product.supplier_product_id == str(item.get("id"))))
            prod = res.scalars().first()
            if not prod:
                prod = Product(
                    name=item.get("name", "Unnamed Tool"),
                    description=item.get("description", ""),
                    price=float(item.get("price", 100)),
                    supplier_product_id=str(item.get("id")),
                    stock=int(item.get("stock", 10))
                )
                session.add(prod)
            else:
                prod.price = float(item.get("price", prod.price))
                prod.stock = int(item.get("stock", prod.stock))
        await session.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=303)

# Telegram Webhook Endpoint
@app.post(f"/webhook/{settings.WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    from aiogram.types import Update
    update = Update.model_validate(json_data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}

# Telegram Bot Handlers (Bangla Interface)
def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 প্রিমিয়াম টুলস কিনুন", callback_data="buy_tools"),
         InlineKeyboardButton(text="📦 সকল প্রোডাক্ট", callback_data="all_products")],
        [InlineKeyboardButton(text="🔍 প্রোডাক্ট খুঁজুন", callback_data="search_product"),
         InlineKeyboardButton(text="💰 আমার ওয়ালেট", callback_data="my_wallet")],
        [InlineKeyboardButton(text="➕ টাকা যোগ করুন", callback_data="add_money"),
         InlineKeyboardButton(text="🛍️ আমার অর্ডার", callback_data="my_orders")],
        [InlineKeyboardButton(text="📜 ট্রানজেকশন হিস্ট্রি", callback_data="tx_history"),
         InlineKeyboardButton(text="👤 আমার অ্যাকাউন্ট", callback_data="my_account")],
        [InlineKeyboardButton(text="🛠️ কাস্টমার সাপোর্ট", callback_data="support")]
    ])
    return keyboard

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = res.scalars().first()
        if not user:
            user = User(
                telegram_id=user_id,
                username=username,
                full_name=full_name,
                is_admin=(user_id == settings.TELEGRAM_ADMIN_ID)
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Create wallet
            wallet = Wallet(user_id=user.id, balance=0.0)
            session.add(wallet)
            await session.commit()

    welcome_text = (
        f"স্বাগতম, **{full_name}**!\n\n"
        "আমাদের প্রিমিয়াম টুলস শপে আপনাকে স্বাগতম। নিচে দেওয়া অপশনগুলো থেকে আপনার পছন্দমতো সেবা বেছে নিন:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode="Markdown")

@router.callback_query(F.data == "buy_tools")
async def cb_buy_tools(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Product).where(Product.is_active == True))
        products = res.scalars().all()
    
    if not products:
        await callback.message.edit_text("দুঃখিত, বর্তমানে কোনো প্রোডাক্ট উপলব্ধ নেই।", reply_markup=get_main_menu())
        return

    text = "🛒 **উপলব্ধ প্রিমিয়াম টুলস:**\n\nকিনতে নিচের প্রোডাক্টগুলোতে ক্লিক করুন:"
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(text=f"{p.name} - ৳{p.price}", callback_data=f"prod_{p.id}")])
    buttons.append([InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")

@router.callback_query(F.data == "all_products")
async def cb_all_products(callback: CallbackQuery):
    await cb_buy_tools(callback)

@router.callback_query(F.data == "my_wallet")
async def cb_my_wallet(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.scalars().first()
        if user:
            wallet_res = await session.execute(select(Wallet).where(Wallet.user_id == user.id))
            wallet = wallet_res.scalars().first()
            balance = wallet.balance if wallet else 0.0
        else:
            balance = 0.0

    text = f"💰 **আপনার ওয়ালেট ব্যালেন্স:**\n\nবর্তমান ব্যালেন্স: **৳{balance:.2f}**"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ টাকা যোগ করুন", callback_data="add_money")],
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "add_money")
async def cb_add_money(callback: CallbackQuery):
    text = (
        "➕ **টাকা যোগ করুন (Add Money):**\n\n"
        "বিকাশ বা নগদ মার্চেন্ট নম্বরে সেন্ড মানি করে ট্রানজেকশন আইডি (TrxID) দিয়ে অ্যাডমিন ভেরিফিকেশনের মাধ্যমে ব্যালেন্স যোগ করুন।\n\n"
        "মার্চেন্ট নম্বর: `01700000000` (Personal/Merchant)"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "my_orders")
async def cb_my_orders(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.scalars().first()
        if not user:
            orders = []
        else:
            orders_res = await session.execute(select(Order).where(Order.user_id == user.id))
            orders = orders_res.scalars().all()

    text = "🛍️ **আপনার অর্ডারসমূহ:**\n\n"
    if not orders:
        text += "আপনার কোনো অর্ডার নেই।"
    else:
        for o in orders:
            text += f"- অর্ডার ID: {o.id} | স্ট্যাটাস: {o.status} | মূল্য: ৳{o.price}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "tx_history")
async def cb_tx_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.scalars().first()
        if not user:
            ledgers = []
        else:
            ledger_res = await session.execute(select(WalletLedger).where(WalletLedger.user_id == user.id))
            ledgers = ledger_res.scalars().all()

    text = "📜 **ট্রানজেকশন হিস্ট্রি:**\n\n"
    if not ledgers:
        text += "কোনো ট্রানজেকশন পাওয়া যায়নি।"
    else:
        for l in ledgers:
            text += f"- ধরণ: {l.type} | পরিমাণ: ৳{l.amount} | Ref: {l.reference}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "my_account")
async def cb_my_account(callback: CallbackQuery):
    user_id = callback.from_user.id
    full_name = callback.from_user.full_name
    username = callback.from_user.username

    text = (
        f"👤 **আপনার অ্যাকাউন্ট ইনফো:**\n\n"
        f"নাম: {full_name}\n"
        f"ইউজারনেম: @{username if username else 'N/A'}\n"
        f"Telegram ID: `{user_id}`"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    text = (
        "🛠️ **কাস্টমার সাপোর্ট:**\n\n"
        "যেকোনো প্রয়োজনে বা সমস্যায় আমাদের এডমিনের সাথে যোগাযোগ করুন:\n"
        "সাপোর্ট এডমিন: @AdminUsername"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 মূল মেনু", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.edit_text("মূল মেনু:", reply_markup=get_main_menu())

@router.callback_query(F.data.startswith("prod_"))
async def cb_product_detail(callback: CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Product).where(Product.id == prod_id))
        p = res.scalars().first()

    if not p:
        await callback.answer("প্রোডাক্ট পাওয়া যায়নি!", show_alert=True)
        return

    text = (
        f"📦 **{p.name}**\n\n"
        f"বর্ণনা: {p.description or 'প্রদান করা হয়নি'}\n"
        f"মূল্য: **৳{p.price}**\n"
        f"স্টক: {p.stock}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 কিনুন (Buy Now)", callback_data=f"buy_{p.id}")],
        [InlineKeyboardButton(text="🔙 পেছনের মেনু", callback_data="buy_tools")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data.startswith("buy_"))
async def cb_process_purchase(callback: CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.scalars().first()
        prod_res = await session.execute(select(Product).where(Product.id == prod_id))
        product = prod_res.scalars().first()

        if not user or not product:
            await callback.answer("সিস্টেম এরর!", show_alert=True)
            return

        wallet_res = await session.execute(select(Wallet).where(Wallet.user_id == user.id))
        wallet = wallet_res.scalars().first()

        if not wallet or wallet.balance < product.price:
            await callback.answer("আপনার ওয়ালেটে পর্যাপ্ত ব্যালেন্স নেই! দয়া করে টাকা যোগ করুন।", show_alert=True)
            return

        # Deduct wallet
        wallet.balance -= product.price
        
        # Create order
        order = Order(user_id=user.id, product_id=product.id, price=product.price, status="PENDING")
        session.add(order)
        await session.commit()
        await session.refresh(order)

        # Submit to supplier API if supplier_product_id exists
        supplier_res = None
        if product.supplier_product_id:
            supplier_res = await supplier_client.create_order(product.supplier_product_id)
            if supplier_res and supplier_res.get("success"):
                order.status = "SUCCESS"
                order.supplier_order_id = str(supplier_res.get("order_id", ""))
            else:
                order.status = "FAILED"
        else:
            order.status = "SUCCESS" # Manual or direct fulfillment

        await session.commit()

    text = f"✅ অর্ডার সফল হয়েছে!\nঅর্ডার আইডি: #{order.id}\nস্ট্যাটাস: {order.status}"
    await callback.message.edit_text(text, reply_markup=get_main_menu())

dp.include_router(router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.import_string("python:app")
