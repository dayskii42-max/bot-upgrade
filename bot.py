#!/usr/bin/env python3
"""
DataLine Store - Telegram Bot (Balance-Only Purchase, $15 min top-up)
Requirements: python-telegram-bot requests
"""

import subprocess, sys
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--root-user-action=ignore", "-q"])
import requests
import time
import asyncio
import config
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
# from topup.api import payments  # Disabled for now
payments = None  # Placeholder

# --- CONFIG ---
# Read token from Railway environment (NOT hardcoded)
import os
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env variable not set")
ADMIN_ID    = 8798542436
STORE_URL   = "https://telegram.me/datalaunch_bot"

WALLETS = {
    "BTC":        "0",
    "ETH":        "0",
    "USDT_TRC20": "TCGjtfZnsWt3JDccm3Y1uk2QvLmvM3Yt2x",
    "LTC":        "Lak56Y1JhwiW26YwcnXdgMSEMDjSUgp7PB",
}

PRICE_PER_LINE  = 5
MIN_TOPUP       = 15  # Minimum top-up amount in USD

# Conversation states
WAITING_BIN, WAITING_COUNTRY = range(2)
TOPUP_CHOOSING_CRYPTO, TOPUP_CHOOSING_AMOUNT, TOPUP_CUSTOM_AMOUNT = range(2, 5)

# In-memory sessions
user_sessions = {}

# --- BACKEND API ---
API_URL = "https://loud-cipher-trade-core.base44.app/api/functions/getBotData"
HEADERS = {"Content-Type": "application/json"}
BOT_SECRET = "ANDRO"

def call_api(payload):
    try:
        payload["secret"] = BOT_SECRET
        resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=10)
        if resp.ok:
            return resp.json()
        print(f"API error {resp.status_code}: {resp.text}")
        return None
    except Exception as e:
        print(f"API error: {e}")
        return None

def get_available_lines(limit=20):
    result = call_api({"action": "get_available", "query": {"limit": limit}})
    return result if isinstance(result, list) else []

def search_lines_by_bin(bin_prefix, limit=20):
    result = call_api({"action": "search_bin", "query": {"bin_prefix": bin_prefix}})
    return result if isinstance(result, list) else []

def search_lines_by_country(country, limit=20):
    result = call_api({"action": "search_country", "query": {"country": country}})
    return result if isinstance(result, list) else []

def search_lines_by_base(base_name):
    result = call_api({"action": "search_base", "query": {"base_name": base_name}})
    return result if isinstance(result, list) else []

def get_bases():
    result = call_api({"action": "get_bases"})
    return result if isinstance(result, list) else []

def get_balance(user_id):
    result = call_api({"action": "get_balance", "telegram_user_id": str(user_id)})
    return result.get("balance_usd", 0) if result else 0

# --- ONECHECK API HELPERS ---
ONECHECK_HEADERS = {"X-API-Key": config.ONECHECK_API_KEY, "Content-Type": "application/json"}

def create_check_task(data):
    """Create a OneCheck verification task and return its id."""
    resp = requests.post(f"{config.ONECHECK_BASE_URL}/check", headers=ONECHECK_HEADERS, json=data, timeout=15)
    resp.raise_for_status()
    return resp.json().get("id")

def poll_check_task(task_id, timeout=180):
    """Poll OneCheck task until completed or timeout, returning final result."""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        r = requests.get(f"{config.ONECHECK_BASE_URL}/check/{task_id}", headers=ONECHECK_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "completed":
            return data
    raise TimeoutError("OneCheck task timed out")


def notify_new_user(user_id, username, first_name):
    call_api({"action": "notify_new_user", "telegram_user_id": str(user_id), "telegram_username": username or "", "first_name": first_name or ""})

def create_topup_invoice(user_id, username, crypto, amount):
    return call_api({
        "action": "create_topup",
        "telegram_user_id": str(user_id),
        "telegram_username": username or "",
        "crypto_type": crypto,
        "amount_usd": amount,
    })

def buy_with_balance(user_id, line_id):
    return call_api({
        "action": "buy_with_balance",
        "telegram_user_id": str(user_id),
        "line_id": line_id,
    })

def refund_purchase(user_id, line_id):
    return call_api({
        "action": "refund_purchase",
        "telegram_user_id": str(user_id),
        "line_id": line_id,
    })

def get_my_orders(user_id):
    result = call_api({"action": "my_orders", "telegram_user_id": str(user_id)})
    return result if isinstance(result, list) else []

# --- HELPERS ---
def fmt(x):
    return f"{x:.2f}"

def build_lines_keyboard(lines):
    kb = []
    for line in lines:
        bin6    = line.get("bin", "??????")
        exp     = f"{line.get('exp_month','??')}/{line.get('exp_year','??')}"
        state   = line.get("state", "")
        country = line.get("country", "")
        price   = line.get("price", PRICE_PER_LINE)
        label   = f"💳 {bin6}XXXX  {exp}  {state} {country}  — $" + str(price)
        kb.append([InlineKeyboardButton(label, callback_data=f"buy_{line['id']}")])
    return kb

# --- HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username
    fname = update.effective_user.first_name

    # No API call on /start — the backend has no opportunity to inject
    # promotional content this way. Balance defaults to $0 and will be
    # shown from the payment system or a later API call (e.g. /balance).
    balance = 0  # Will be shown from payment system or other means
    is_new = False  # Don't notify backend on /start
    kb = [
        [InlineKeyboardButton("🛒 Browse Store",      callback_data="browse"),
         InlineKeyboardButton("🔍 Search by BIN",     callback_data="search_bin")],
        [InlineKeyboardButton("🌍 Search by Country", callback_data="search_country"),
         InlineKeyboardButton("📦 Search by Base",    callback_data="search_base")],
        [InlineKeyboardButton("💰 Balance: $" + fmt(balance), callback_data="balance_menu"),
         InlineKeyboardButton("➕ Top Up Balance",    callback_data="topup_start")],
        [InlineKeyboardButton("📋 My Orders",          callback_data="my_orders"),
         InlineKeyboardButton("🆘 Support & Refunds", url="https://t.me/Andro_ccz")],
    ]
    username = update.effective_user.first_name or update.effective_user.username or "there"
    await update.message.reply_text(
        f"👋 Welcome <b>{username}</b> to\n\n"
        "💵 <b>ANDRO'S CVV STORE</b> 💵\n\n"
        "💲 Cheap price, good quality sniffed CVV 🏦\n\n"
        "✅ Refunds on all cards if dead\n"
        "✅ All cards are checked before upload\n"
        "✅ Proof of valid rate uploaded with base\n\n"
        "♻️ All refunds are manual for now via support: @Andro_ccz\n\n"
        "📌 If your BIN is not on the bot, message me — I may have stock not yet uploaded: @Andro_ccz\n\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def balance_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    kb = [
        [InlineKeyboardButton("➕ Top Up Balance", callback_data="topup_start"),
         InlineKeyboardButton("🛒 Browse & Buy",   callback_data="browse")],
        [InlineKeyboardButton("« Back",            callback_data="back_start")],
    ]
    balance_text = await payments.balance_text(uid)
    await query.edit_message_text(
        f"💰 <b>Your Balance</b>\n\n{balance_text}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ── TOP UP FLOW (Auto-confirmation) ──────────────────────────────────────────
async def topup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [
        [InlineKeyboardButton("₮ USDT TRC20",     callback_data="tc_USDT_TRC20"),
         InlineKeyboardButton("Ł Litecoin (LTC)", callback_data="tc_LTC")],
        [InlineKeyboardButton("« Back",           callback_data="back_start")],
    ]
    await query.edit_message_text(
        "➕ <b>Top Up Balance</b>\n\n"
        f"Minimum top-up: <b>${MIN_TOPUP} USD</b>\n\n"
        "✅ <b>Auto-confirmation</b> — just send the exact amount shown and your balance is credited automatically after 3 blockchain confirmations.\n\n"
        "Choose your crypto:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TOPUP_CHOOSING_CRYPTO

async def topup_choose_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    crypto = query.data.replace("tc_", "")
    user_sessions[query.from_user.id] = {"topup_crypto": crypto}
    kb = [
        [InlineKeyboardButton("$15",  callback_data="ta_15"),
         InlineKeyboardButton("$25",  callback_data="ta_25")],
        [InlineKeyboardButton("$50",  callback_data="ta_50"),
         InlineKeyboardButton("$100", callback_data="ta_100")],
        [InlineKeyboardButton("💰 Custom Amount", callback_data="ta_custom")],
        [InlineKeyboardButton("« Back", callback_data="topup_start")],
    ]
    await query.edit_message_text(
        f"➕ <b>Top Up via {crypto}</b>\n\n"
        f"Minimum: <b>${MIN_TOPUP}</b>\n\n"
        "Choose amount:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TOPUP_CHOOSING_AMOUNT

async def topup_custom_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"➕ <b>Enter Custom Amount</b>\n\n"
        f"Minimum: <b>${MIN_TOPUP}</b>\n\n"
        "Type the amount in USD (e.g., 20, 75, 200):",
        parse_mode="HTML"
    )
    return TOPUP_CUSTOM_AMOUNT

async def topup_receive_custom_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username or str(uid)
    try:
        amount = float(update.message.text.strip())
        if amount < MIN_TOPUP:
            await update.message.reply_text(f"⚠️ Minimum amount is <b>${MIN_TOPUP}</b>.", parse_mode="HTML")
            return TOPUP_CUSTOM_AMOUNT
        session = user_sessions.get(uid, {})
        crypto = session.get("topup_crypto", "USDT_TRC20")
        await update.message.reply_text("⏳ Generating payment address...")
        await _send_invoice(update.message.reply_text, uid, uname, crypto, amount)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number (e.g., 20, 50.50).")
        return TOPUP_CUSTOM_AMOUNT

async def topup_show_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = int(query.data.replace("ta_", ""))
    uid = query.from_user.id
    uname = query.from_user.username or str(uid)
    session = user_sessions.get(uid, {})
    crypto = session.get("topup_crypto", "USDT_TRC20")
    await query.edit_message_text("⏳ Generating payment address...")
    info = await payments.create_topup(
        telegram_id=uid,
        username=uname,
        asset_code=crypto,
    )
    if info.get("error"):
        await query.edit_message_text(f"❌ Error: {info['error']}", parse_mode="HTML")
        return ConversationHandler.END
    await query.edit_message_text(info["message"], parse_mode="HTML")
    return ConversationHandler.END

async def _send_invoice(reply_fn, uid, uname, crypto, amount):
    result = create_topup_invoice(uid, uname, crypto, amount)
    if not result or result.get("error"):
        err = result.get("error", "Unknown error") if result else "API unreachable"
        await reply_fn(f"❌ Failed to generate invoice: {err}\nPlease try again.", parse_mode="HTML")
        return
    wallet = result["wallet_address"]
    crypto_amount = result["expected_crypto_amount"]
    ticker = "USDT" if crypto == "USDT_TRC20" else "LTC"
    network = "TRC20" if crypto == "USDT_TRC20" else "Litecoin"
    await reply_fn(
        f"➕ <b>Top Up Invoice — ${amount} USD</b>\n\n"
        f"⚠️ Send <b>EXACTLY</b> this amount:\n"
        f"<code>{crypto_amount}</code> <b>{ticker}</b> ({network})\n\n"
        f"To this wallet:\n<code>{wallet}</code>\n\n"
        "⏳ Your balance will be credited <b>automatically</b> after 3 blockchain confirmations (usually 5-15 min).\n\n"
        "⚠️ <b>Send the exact amount shown</b> — different amounts will not be matched!\n"
        "Use /balance to check when it's credited.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Check Balance", callback_data="balance_menu")]])
    )

# ── BROWSE / BUY FLOW (Balance only) ─────────────────────────────────────────
async def browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Loading available lines...")
    lines = get_available_lines(20)
    if not lines:
        kb = [[InlineKeyboardButton("🔄 Refresh", callback_data="browse"), InlineKeyboardButton("« Back", callback_data="back_start")]]
        await query.edit_message_text("❌ No lines available right now. Check back soon!", reply_markup=InlineKeyboardMarkup(kb))
        return
    kb = build_lines_keyboard(lines)
    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="browse")])
    await query.edit_message_text(
        f"🛒 <b>Available Lines</b> ({len(lines)} in stock)\n\nSelect a line to purchase with your balance:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def search_bin_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 <b>BIN Search</b>\n\nType the first 4-6 digits:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="back_start")]])
    )
    return WAITING_BIN

async def receive_bin_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bin_prefix = update.message.text.strip().replace(" ", "")
    if not bin_prefix.isdigit() or len(bin_prefix) < 4:
        await update.message.reply_text("⚠️ Please enter at least 4 digits.")
        return WAITING_BIN
    await update.message.reply_text(f"⏳ Searching for BIN <b>{bin_prefix}...</b>", parse_mode="HTML")
    lines = search_lines_by_bin(bin_prefix)
    if not lines:
        kb = [[InlineKeyboardButton("🔄 Try Again", callback_data="search_bin"), InlineKeyboardButton("🛒 Browse All", callback_data="browse")]]
        await update.message.reply_text(f"❌ No lines found for BIN <b>{bin_prefix}</b>.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        kb = build_lines_keyboard(lines)
        kb.append([InlineKeyboardButton("🔄 New Search", callback_data="search_bin"), InlineKeyboardButton("🛒 Browse All", callback_data="browse")])
        await update.message.reply_text(
            f"✅ <b>{len(lines)} lines found</b> for BIN <b>{bin_prefix}</b>:\n\nSelect a line:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )
    return ConversationHandler.END

async def search_country_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🌍 <b>Country Search</b>\n\nType the country code (e.g. <b>US</b>, <b>UK</b>, <b>CA</b>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="back_start")]])
    )
    return WAITING_COUNTRY

async def receive_country_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    country = update.message.text.strip().upper()
    if len(country) < 2:
        await update.message.reply_text("⚠️ Please enter a valid country code (e.g. US, UK, CA).")
        return WAITING_COUNTRY
    await update.message.reply_text(f"⏳ Searching lines in <b>{country}...</b>", parse_mode="HTML")
    lines = search_lines_by_country(country)
    if not lines:
        kb = [[InlineKeyboardButton("🔄 Try Again", callback_data="search_country"), InlineKeyboardButton("🛒 Browse All", callback_data="browse")]]
        await update.message.reply_text(f"❌ No lines found for <b>{country}</b>.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        kb = build_lines_keyboard(lines)
        kb.append([InlineKeyboardButton("🔄 New Search", callback_data="search_country"), InlineKeyboardButton("🛒 Browse All", callback_data="browse")])
        await update.message.reply_text(
            f"✅ <b>{len(lines)} lines found</b> in <b>{country}</b>:\n\nSelect a line:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )
    return ConversationHandler.END

async def search_base_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Loading bases...")
    bases = get_bases()
    if not bases:
        await query.edit_message_text(
            "❌ No bases available.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="back_start")]])
        )
        return
    kb = [[InlineKeyboardButton(f"📦 {b}", callback_data=f"base_{b}")] for b in bases]
    kb.append([InlineKeyboardButton("« Back", callback_data="back_start")])
    await query.edit_message_text(
        "📦 <b>Select a Base</b>\n\nChoose a base to browse its available lines:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def browse_base(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    base_name = query.data.replace("base_", "", 1)
    await query.edit_message_text(f"⏳ Loading lines from <b>{base_name}</b>...", parse_mode="HTML")
    lines = search_lines_by_base(base_name)
    if not lines:
        kb = [[InlineKeyboardButton("📦 Back to Bases", callback_data="search_base"), InlineKeyboardButton("« Main Menu", callback_data="back_start")]]
        await query.edit_message_text(f"❌ No lines available in <b>{base_name}</b>.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return
    kb = build_lines_keyboard(lines)
    kb.append([InlineKeyboardButton("📦 Back to Bases", callback_data="search_base"), InlineKeyboardButton("« Main Menu", callback_data="back_start")])
    await query.edit_message_text(
        f"📦 <b>{base_name}</b> — {len(lines)} lines available\n\nSelect a line to purchase:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def buy_line(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    line_id = query.data.replace("buy_", "")
    uid = query.from_user.id
    balance = get_balance(uid)

    if balance >= PRICE_PER_LINE:
        kb = [
            [InlineKeyboardButton("⚡ Buy Now ($" + fmt(balance) + " balance)", callback_data=f"bal_{line_id}")],
            [InlineKeyboardButton("« Back", callback_data="browse")],
        ]
        await query.edit_message_text(
            f"💳 <b>Confirm Purchase</b>\n\n"
            f"Price: <b>${PRICE_PER_LINE}</b>\n"
            "Your balance: <b>$" + fmt(balance) + "</b>\n\n"
            "Tap below to buy instantly:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        needed = PRICE_PER_LINE - balance
        kb = [
            [InlineKeyboardButton("➕ Top Up Balance", callback_data="topup_start"),
             InlineKeyboardButton("« Back",            callback_data="browse")],
        ]
        await query.edit_message_text(
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"Price: <b>${PRICE_PER_LINE}</b>\n"
            "Your balance: <b>$" + fmt(balance) + "</b>\n"
            "Needed: <b>$" + fmt(needed) + " more</b>\n\n"
            f"Top up your balance (min <b>${MIN_TOPUP}</b>) to purchase.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def buy_with_balance_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    line_id = query.data.replace("bal_", "")
    uid = query.from_user.id
    await query.edit_message_text("⏳ Processing purchase...")
    result = buy_with_balance(uid, line_id)
    if not result:
        await query.edit_message_text("❌ Purchase failed. Please try again or contact support.")
        return
    if result.get("error"):
        err = result["error"]
        if err == "Insufficient balance":
            bal = result.get("balance", 0)
            price = result.get("price", PRICE_PER_LINE)
            await query.edit_message_text(
                "❌ Insufficient balance ($" + fmt(bal) + " / $" + str(price) + ")\nTop up and try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Top Up", callback_data="topup_start")]])
            )
        else:
            await query.edit_message_text(f"❌ Error: {err}")
        return
    raw_line = result.get("raw_line", "")
    new_balance = result.get("new_balance", 0)

    # Stash purchase details so the check handler can retrieve them later
    session = user_sessions.setdefault(uid, {})
    purchases = session.setdefault("purchases", {})
    purchases[line_id] = {"raw_line": raw_line, "new_balance": new_balance}

    # Show check button for 1 minute (+ buffer)
    kb = [[InlineKeyboardButton("✅ Check Card (1 min)", callback_data=f"check_{line_id}")]]
    await query.edit_message_text(
        "✅ <b>Purchase Complete!</b>\n\n"
        f"<code>{raw_line}</code>\n\n"
        "Remaining balance: <b>$" + fmt(new_balance) + "</b>\n\n"
        "⏱️ You have 1 minute to check this card. Dead cards are refunded automatically.\n\n"
        "Keep this safe. Do not share.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

    # Schedule check button removal after 120 seconds (1 min window + buffer)
    ctx.job_queue.run_once(
        lambda ctx: asyncio.create_task(_remove_check_button(query, line_id)),
        120,
        name=f"check_timeout_{line_id}"
    )

async def balance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = [[InlineKeyboardButton("➕ Top Up", callback_data="topup_start"), InlineKeyboardButton("🛒 Browse", callback_data="browse")]]
    balance_text = await payments.balance_text(uid)
    await update.message.reply_text(
        f"💰 <b>Your Balance</b>\n\n{balance_text}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def howto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📖 <b>How to Buy</b>\n\n"
        "This store uses a <b>balance system</b> — no direct payment at checkout.\n\n"
        "<b>Step 1 — Top Up:</b>\n"
        f"Send crypto to top up your balance (min <b>${MIN_TOPUP}</b>).\n"
        "Your balance is credited automatically after blockchain confirmation.\n\n"
        "<b>Step 2 — Browse:</b>\n"
        "Browse available datalines and select one.\n\n"
        "<b>Step 3 — Buy Instantly:</b>\n"
        "Click ⚡ Buy Now — delivered immediately from your balance.\n\n"
        "✅ No waiting, no manual steps.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Top Up Now", callback_data="topup_start"), InlineKeyboardButton("« Back", callback_data="back_start")]])
    )

async def back_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    balance = get_balance(uid)
    kb = [
        [InlineKeyboardButton("🛒 Browse Store",      callback_data="browse"),
         InlineKeyboardButton("🔍 Search by BIN",     callback_data="search_bin")],
        [InlineKeyboardButton("🌍 Search by Country", callback_data="search_country"),
         InlineKeyboardButton("📦 Search by Base",    callback_data="search_base")],
        [InlineKeyboardButton("💰 Balance: $" + fmt(balance), callback_data="balance_menu"),
         InlineKeyboardButton("➕ Top Up Balance",    callback_data="topup_start")],
        [InlineKeyboardButton("📋 My Orders",          callback_data="my_orders"),
         InlineKeyboardButton("🆘 Support & Refunds", url="https://t.me/Andro_ccz")],
    ]
    await query.edit_message_text(
        "💵 <b>ANDRO'S CVV STORE</b> 💵\n\n"
        f"💲 <b>${PRICE_PER_LINE} per line</b> — Cheap price, good quality\n"
        "⚡ Balance purchases only",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def my_orders_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    await query.edit_message_text("⏳ Loading your orders...")
    orders = get_my_orders(uid)
    if not orders:
        await query.edit_message_text(
            "📋 <b>My Orders</b>\n\nYou have no past purchases yet.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Browse Store", callback_data="browse"), InlineKeyboardButton("« Back", callback_data="back_start")]]),
        )
        return
    text = "📋 <b>My Orders</b> (last 20)\n\n"
    for i, o in enumerate(orders, 1):
        date_str = o.get("created_date", "")[:10] if o.get("created_date") else "?"
        bin6 = o.get("bin", "??????")
        raw = o.get("raw_line", "")
        text += f"<b>#{i}</b> — {date_str} — BIN <code>{bin6}</code>\n"
        text += f"<code>{raw}</code>\n\n"
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="back_start")]]),
    )

async def _remove_check_button(query, line_id):
    try:
        await query.edit_message_text(
            query.message.text + "\n\n⏰ <i>Check window closed.</i>",
            parse_mode="HTML"
        )
    except:
        pass

def _parse_raw_line(raw_line):
    """Parse a raw card line (number|month|year|cvv) into OneCheck fields."""
    parts = [p.strip() for p in raw_line.replace("/", "|").split("|") if p.strip() != ""]
    if len(parts) < 4:
        # Fallback: try whitespace-separated
        parts = raw_line.split()
    if len(parts) < 4:
        return None
    number, month, year, cvv = parts[0], parts[1], parts[2], parts[3]
    if len(year) == 4:
        year = year[2:]
    return {
        "number": number,
        "month": month.zfill(2),
        "year": year,
        "cvv": cvv,
    }

async def check_card_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    line_id = query.data.replace("check_", "")
    uid = query.from_user.id

    session = user_sessions.get(uid, {})
    purchase = session.get("purchases", {}).get(line_id)
    if not purchase:
        await query.edit_message_text(
            "❌ Could not find this purchase to check. Please contact support.",
            parse_mode="HTML"
        )
        return

    raw_line = purchase.get("raw_line", "")
    card = _parse_raw_line(raw_line)
    if not card:
        await query.edit_message_text(
            "❌ Could not parse card details for checking. Please contact support.",
            parse_mode="HTML"
        )
        return

    await query.edit_message_text(
        "🔎 <b>Checking card...</b>\n\nThis may take up to a few minutes. Please wait.",
        parse_mode="HTML"
    )

    try:
        task_id = create_check_task(card)
        if not task_id:
            raise ValueError("No task id returned from OneCheck")
        result = await asyncio.to_thread(poll_check_task, task_id, 180)
    except Exception as e:
        print(f"OneCheck error: {e}")
        await query.edit_message_text(
            "⚠️ <b>Check Failed</b>\n\n"
            "We could not verify this card right now. Please contact support if you believe it is dead.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support", url="https://t.me/Andro_ccz")]])
        )
        return

    status = (result.get("status_result") or result.get("result") or "").lower()

    if status == "dead":
        refund_result = refund_purchase(uid, line_id)
        if refund_result and not refund_result.get("error"):
            new_balance = refund_result.get("new_balance", 0)
            await query.edit_message_text(
                "💀 <b>Card is Dead</b>\n\n"
                "You have been refunded automatically.\n\n"
                "New balance: <b>$" + fmt(new_balance) + "</b>",
                parse_mode="HTML"
            )
        else:
            err = refund_result.get("error") if refund_result else "Unknown error"
            await query.edit_message_text(
                "💀 <b>Card is Dead</b>\n\n"
                f"Automatic refund failed ({err}). Please contact support for a manual refund.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support", url="https://t.me/Andro_ccz")]])
            )
    elif status == "live":
        await query.edit_message_text(
            "✅ <b>Card is Live!</b>\n\n"
            "No refund necessary. Enjoy!",
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            "❓ <b>Unable to Determine Status</b>\n\n"
            f"OneCheck returned: <code>{status or 'unknown'}</code>\n\n"
            "Please contact support if you believe this card is dead.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Support", url="https://t.me/Andro_ccz")]])
        )

async def admin_deliver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    parts = update.message.text.split(" ", 3)
    if len(parts) < 4:
        await update.message.reply_text("Usage: /deliver <order_id> <user_id> <raw_line>")
        return
    _, order_id, user_id, raw_line = parts
    await ctx.bot.send_message(
        int(user_id),
        f"✅ <b>Your DataLine is Ready!</b>\n\n"
        f"Order: <code>{order_id}</code>\n\n"
        f"<code>{raw_line}</code>\n\n"
        "Keep this safe. Do not share.",
        parse_mode="HTML"
    )
    await update.message.reply_text("✅ Delivered!")

# --- MAIN ---
def main():
    print("🤖 Starting bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    # Top-up conversation
    topup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(topup_start, pattern="^topup_start$")],
        states={
            TOPUP_CHOOSING_CRYPTO: [CallbackQueryHandler(topup_choose_amount, pattern=r"^tc_")],
            TOPUP_CHOOSING_AMOUNT: [CallbackQueryHandler(topup_show_invoice, pattern=r"^ta_(?!custom)"), CallbackQueryHandler(topup_custom_amount, pattern=r"^ta_custom$")],
            TOPUP_CUSTOM_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_receive_custom_amount)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        per_chat=True,
    )

    bin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_bin_prompt, pattern="^search_bin$")],
        states={WAITING_BIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bin_search)]},
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        per_chat=True,
    )

    country_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_country_prompt, pattern="^search_country$")],
        states={WAITING_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country_search)]},
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
        per_chat=True,
    )

    # bin/country convs first to capture text replies, topup last (it has broad callback patterns)
    app.add_handler(bin_conv)
    app.add_handler(country_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("deliver", admin_deliver))
    app.add_handler(CallbackQueryHandler(browse,                  pattern="^browse$"))
    app.add_handler(CallbackQueryHandler(buy_line,                pattern=r"^buy_"))
    app.add_handler(CallbackQueryHandler(buy_with_balance_handler,pattern=r"^bal_"))
    app.add_handler(CallbackQueryHandler(balance_menu,            pattern="^balance_menu$"))
    app.add_handler(CallbackQueryHandler(howto,                   pattern="^howto$"))
    app.add_handler(CallbackQueryHandler(back_start,              pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(my_orders_handler,       pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(search_base_prompt,      pattern="^search_base$"))
    app.add_handler(CallbackQueryHandler(browse_base,             pattern=r"^base_"))
    app.add_handler(CallbackQueryHandler(check_card_handler,      pattern=r"^check_"))
    app.add_handler(topup_conv)

    async def on_startup(application):
        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🟢 <b>Bot is now online!</b>\n\nDataLine Store bot started.",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Note: Could not notify admin: {e}")
        # await payments.start(application.bot)  # Disabled

    async def on_shutdown(application):
        # await payments.stop()  # Disabled

    app.post_init = on_startup
    app.post_stop = on_shutdown
    print("🤖 Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
