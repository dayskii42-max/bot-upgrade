#!/usr/bin/env python3
"""
DataLine Store - Telegram Bot (Railway + topup DB only)
Unified single-source-of-truth: topup.api.payments for balance, topup, and purchases
"""

import os
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Add repo root to path so topup/ module is found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from topup.api import payments

# --- CONFIG ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env variable not set")

ADMIN_ID = 8798542436
PRICE_PER_LINE = 5
MIN_TOPUP = 15

# Conversation states
WAITING_BIN, WAITING_COUNTRY = range(2)
TOPUP_CHOOSING_CRYPTO, TOPUP_CHOOSING_AMOUNT, TOPUP_CUSTOM_AMOUNT = range(2, 5)

# In-memory sessions
user_sessions = {}

# --- HELPERS ---
def fmt(x):
    return f"{x:.2f}"

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# --- ADMIN HANDLERS ---

async def admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin panel"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    kb = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("💰 Top Ups", callback_data="admin_topups")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("📈 Balance", callback_data="admin_balance")],
        [InlineKeyboardButton("🔧 Settings", callback_data="admin_settings")],
    ]
    
    await update.message.reply_text(
        "🔐 <b>ADMIN PANEL</b>\n\n"
        "Select an option:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show payment stats"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    # Get stats from payments module
    stats = await payments.get_stats()
    
    text = (
        "📊 <b>Payment Stats</b>\n\n"
        f"Total Deposits: {stats.get('total_deposits', 0)}\n"
        f"Total Amount: ${fmt(float(stats.get('total_amount', 0)))}\n"
        f"Pending: {stats.get('pending_count', 0)}\n"
        f"Confirmed: {stats.get('confirmed_count', 0)}\n"
        f"Active Users: {stats.get('unique_users', 0)}\n"
    )
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="admin_back")]
        ])
    )

async def admin_topups(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show recent top-ups"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    topups = await payments.get_recent_topups(limit=10)
    
    text = "💰 <b>Recent Top Ups</b>\n\n"
    if topups:
        for topup in topups:
            text += (
                f"User: {topup.get('username', 'N/A')}\n"
                f"Amount: ${fmt(float(topup.get('usd_amount', 0)))}\n"
                f"Asset: {topup.get('asset_code', 'N/A')}\n"
                f"Status: {topup.get('status', 'pending')}\n\n"
            )
    else:
        text += "No recent top-ups"
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="admin_back")]
        ])
    )

async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show user stats"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    users = await payments.get_users_summary()
    
    text = "👥 <b>User Summary</b>\n\n"
    if users:
        for user in users[:10]:  # Top 10
            text += (
                f"ID: {user.get('telegram_id', 'N/A')}\n"
                f"Balance: ${fmt(float(user.get('balance', 0)))}\n"
                f"Deposits: {user.get('deposit_count', 0)}\n\n"
            )
    else:
        text += "No users yet"
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="admin_back")]
        ])
    )

async def admin_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show total balance info"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    balance_info = await payments.get_balance_info()
    
    text = (
        "💰 <b>Balance Summary</b>\n\n"
        f"Total User Balance: ${fmt(float(balance_info.get('total_balance', 0)))}\n"
        f"Pending Confirmations: ${fmt(float(balance_info.get('pending_amount', 0)))}\n"
        f"Confirmed: ${fmt(float(balance_info.get('confirmed_amount', 0)))}\n"
    )
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="admin_back")]
        ])
    )

async def admin_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin settings"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    auto_sweep = os.getenv("AUTO_SWEEP", "true").lower() in {"1", "true", "yes", "on"}
    enabled_assets = os.getenv("ENABLED_ASSETS", "").split(",")
    
    text = (
        "🔧 <b>Settings</b>\n\n"
        f"Auto Sweep: {'✅ ON' if auto_sweep else '❌ OFF'}\n"
        f"Enabled Assets: {', '.join(enabled_assets)}\n"
        f"Poll Interval: {os.getenv('POLL_INTERVAL_SECONDS', '20')}s\n"
    )
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="admin_back")]
        ])
    )

async def admin_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Back to admin menu"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    kb = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("💰 Top Ups", callback_data="admin_topups")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("📈 Balance", callback_data="admin_balance")],
        [InlineKeyboardButton("🔧 Settings", callback_data="admin_settings")],
    ]
    
    await query.edit_message_text(
        "🔐 <b>ADMIN PANEL</b>\n\nSelect an option:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- USER HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Main menu"""
    uid = update.effective_user.id
    fname = update.effective_user.first_name or "there"
    
    balance_text = await payments.balance_text(uid)
    
    kb = [
        [InlineKeyboardButton("➕ Top Up", callback_data="topup_start"),
         InlineKeyboardButton("🛒 Browse Store", callback_data="browse")],
        [InlineKeyboardButton("🔍 Search by BIN", callback_data="search_bin"),
         InlineKeyboardButton("🌍 Search Country", callback_data="search_country")],
        [InlineKeyboardButton("📦 Search Base", callback_data="search_base"),
         InlineKeyboardButton("📋 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("🆘 Support", url="https://t.me/Andro_ccz")],
    ]
    
    # Add admin button if user is admin
    if is_admin(uid):
        kb.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin_menu")])
    
    text = (
        f"👋 Welcome <b>{fname}</b> to\n\n"
        "💵 <b>ANDRO'S CVV STORE</b> 💵\n\n"
        "💲 Cheap price, good quality sniffed CVV 🏦\n\n"
        "✅ Refunds on all cards if dead\n"
        "✅ All cards checked before upload\n"
        "✅ Proof of valid rate with base\n\n"
        f"{balance_text}\n\n"
        "📌 Not seeing your BIN? Message @Andro_ccz\n"
    )
    
    # Handle both /start (message) and back button (callback_query)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show admin menu from button"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    kb = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
         InlineKeyboardButton("💰 Top Ups", callback_data="admin_topups")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("📈 Balance", callback_data="admin_balance")],
        [InlineKeyboardButton("🔧 Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ]
    
    await query.edit_message_text(
        "🔐 <b>ADMIN PANEL</b>\n\n"
        "Select an option:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def topup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Start top-up: choose crypto"""
    query = update.callback_query
    await query.answer()
    
    kb = [
        [InlineKeyboardButton("₿ Bitcoin (BTC)", callback_data="tc_BTC"),
         InlineKeyboardButton("Ł Litecoin (LTC)", callback_data="tc_LTC")],
        [InlineKeyboardButton("Ξ Ethereum (ETH)", callback_data="tc_ETH"),
         InlineKeyboardButton("₮ USDT TRC20", callback_data="tc_USDT_TRC20")],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ]
    
    await query.edit_message_text(
        f"➕ <b>Top Up Balance</b>\n\n"
        f"Minimum: <b>${MIN_TOPUP} USD</b>\n\n"
        "✅ <b>Auto-confirmation</b> — send the exact amount and your balance credits after confirmations.\n\n"
        "Choose crypto:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TOPUP_CHOOSING_CRYPTO

async def topup_choose_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Choose top-up amount"""
    query = update.callback_query
    await query.answer()
    crypto = query.data.replace("tc_", "")
    user_sessions[query.from_user.id] = {"topup_crypto": crypto}
    
    kb = [
        [InlineKeyboardButton("$15", callback_data="ta_15"),
         InlineKeyboardButton("$25", callback_data="ta_25")],
        [InlineKeyboardButton("$50", callback_data="ta_50"),
         InlineKeyboardButton("$100", callback_data="ta_100")],
        [InlineKeyboardButton("💰 Custom", callback_data="ta_custom")],
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
    """Prompt for custom amount"""
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
    """Receive custom amount and generate invoice"""
    uid = update.effective_user.id
    uname = update.effective_user.username or str(uid)
    
    try:
        amount = float(update.message.text.strip())
        if amount < MIN_TOPUP:
            await update.message.reply_text(
                f"⚠️ Minimum amount is <b>${MIN_TOPUP}</b>.",
                parse_mode="HTML"
            )
            return TOPUP_CUSTOM_AMOUNT
        
        session = user_sessions.get(uid, {})
        crypto = session.get("topup_crypto", "USDT_TRC20")
        
        await update.message.reply_text("⏳ Generating payment address...")
        info = await payments.create_topup(
            telegram_id=uid,
            username=uname,
            asset_code=crypto,
        )
        
        if info.get("error"):
            await update.message.reply_text(f"❌ Error: {info['error']}", parse_mode="HTML")
        else:
            await update.message.reply_text(info["message"], parse_mode="HTML")
        
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number (e.g., 20, 50.50).")
        return TOPUP_CUSTOM_AMOUNT

async def topup_show_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show invoice for preset amount"""
    query = update.callback_query
    await query.answer()
    
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
    else:
        await query.edit_message_text(info["message"], parse_mode="HTML")
    
    return ConversationHandler.END

async def browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Placeholder: browse store"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🛒 <b>Store Coming Soon</b>\n\n"
        "Catalog integration pending.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="back_start")]
        ])
    )

async def search_bin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Placeholder: search by BIN"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔍 <b>BIN Search Coming Soon</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="back_start")]
        ])
    )

async def search_country(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Placeholder: search by country"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🌍 <b>Country Search Coming Soon</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="back_start")]
        ])
    )

async def search_base(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Placeholder: search by base"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📦 <b>Base Search Coming Soon</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="back_start")]
        ])
    )

async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Placeholder: my orders"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📋 <b>My Orders Coming Soon</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("« Back", callback_data="back_start")]
        ])
    )

async def back_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Go back to /start"""
    query = update.callback_query
    await query.answer()
    await start(update, ctx)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    print(f"Update {update} caused error {context.error}")

async def on_startup(application: Application) -> None:
    """Initialize payments module on startup"""
    print("💰 Starting payment poller...")
    await payments.start(application.bot, autostart_poller=True)
    print("💰 Payment poller ready")

async def on_shutdown(application: Application) -> None:
    """Cleanup on shutdown"""
    print("💰 Stopping payment poller...")
    await payments.stop()
    print("💰 Payment poller stopped")

def main():
    """Run the bot"""
    print("🤖 Starting bot...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Admin command
    app.add_handler(CommandHandler("admin", admin_start))
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    
    # Conversation handler for top-up
    topup_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(topup_start, pattern="^topup_start$")],
        states={
            TOPUP_CHOOSING_CRYPTO: [
                CallbackQueryHandler(topup_choose_amount, pattern="^tc_")
            ],
            TOPUP_CHOOSING_AMOUNT: [
                CallbackQueryHandler(topup_show_invoice, pattern="^ta_[0-9]+$"),
                CallbackQueryHandler(topup_custom_amount, pattern="^ta_custom$"),
            ],
            TOPUP_CUSTOM_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topup_receive_custom_amount)
            ],
        },
        fallbacks=[CallbackQueryHandler(back_to_start, pattern="^back_start$")],
    )
    
    app.add_handler(topup_handler)
    
    # Button handlers
    app.add_handler(CallbackQueryHandler(browse, pattern="^browse$"))
    app.add_handler(CallbackQueryHandler(search_bin, pattern="^search_bin$"))
    app.add_handler(CallbackQueryHandler(search_country, pattern="^search_country$"))
    app.add_handler(CallbackQueryHandler(search_base, pattern="^search_base$"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_start$"))
    
    # Admin handlers
    app.add_handler(CallbackQueryHandler(admin_menu, pattern="^admin_menu$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_topups, pattern="^admin_topups$"))
    app.add_handler(CallbackQueryHandler(admin_users, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_balance, pattern="^admin_balance$"))
    app.add_handler(CallbackQueryHandler(admin_settings, pattern="^admin_settings$"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^admin_back$"))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Startup/shutdown hooks for payments
    app.post_init = on_startup
    app.post_stop = on_shutdown
    
    # Run bot
    print("🤖 Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

