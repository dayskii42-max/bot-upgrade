#!/usr/bin/env python3
"""
DataLine Store - Telegram Bot (Railway + topup DB only)
Unified single-source-of-truth: topup.api.payments for balance, topup, and purchases
"""

import asyncio
import os
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Add repo root to path so topup/ module is found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

# --- HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Main menu"""
    uid = update.effective_user.id
    fname = update.effective_user.first_name or "there"
    
    kb = [
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance_menu"),
         InlineKeyboardButton("➕ Top Up", callback_data="topup_start")],
        [InlineKeyboardButton("🛒 Browse Store", callback_data="browse"),
         InlineKeyboardButton("🔍 Search by BIN", callback_data="search_bin")],
        [InlineKeyboardButton("🌍 Search Country", callback_data="search_country"),
         InlineKeyboardButton("📦 Search Base", callback_data="search_base")],
        [InlineKeyboardButton("📋 My Orders", callback_data="my_orders"),
         InlineKeyboardButton("🆘 Support", url="https://t.me/Andro_ccz")],
    ]
    
    await update.message.reply_text(
        f"👋 Welcome <b>{fname}</b> to\n\n"
        "💵 <b>ANDRO'S CVV STORE</b> 💵\n\n"
        "💲 Cheap price, good quality sniffed CVV 🏦\n\n"
        "✅ Refunds on all cards if dead\n"
        "✅ All cards checked before upload\n"
        "✅ Proof of valid rate with base\n\n"
        "💰 Your balance: $0.00\n\n"
        "📌 Not seeing your BIN? Message @Andro_ccz\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def balance_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show balance from topup DB"""
    query = update.callback_query
    await query.answer()
    
    kb = [
        [InlineKeyboardButton("➕ Top Up", callback_data="topup_start"),
         InlineKeyboardButton("🛒 Browse", callback_data="browse")],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ]
    
    await query.edit_message_text(
        "💰 <b>Your Balance</b>\n\n"
        "Your balance: $0.00",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def topup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Start top-up: choose crypto"""
    query = update.callback_query
    await query.answer()
    
    kb = [
        [InlineKeyboardButton("₮ USDT TRC20", callback_data="tc_USDT_TRC20"),
         InlineKeyboardButton("Ł Litecoin (LTC)", callback_data="tc_LTC")],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ]
    
    await query.edit_message_text(
        f"➕ <b>Top Up Balance</b>\n\n"
        f"Minimum: <b>${MIN_TOPUP} USD</b>\n\n"
        "✅ <b>Auto-confirmation</b> — send the exact amount and your balance credits after 3 blockchain confirmations (5-15 min).\n\n"
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
    try:
        amount = float(update.message.text.strip())
        if amount < MIN_TOPUP:
            await update.message.reply_text(
                f"⚠️ Minimum amount is <b>${MIN_TOPUP}</b>.",
                parse_mode="HTML"
            )
            return TOPUP_CUSTOM_AMOUNT
        
        await update.message.reply_text("✅ Payment address would be generated here.")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number (e.g., 20, 50.50).")
        return TOPUP_CUSTOM_AMOUNT

async def topup_show_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show invoice for preset amount"""
    query = update.callback_query
    await query.answer()
    
    session = user_sessions.get(query.from_user.id, {})
    crypto = session.get("topup_crypto", "USDT_TRC20")
    
    await query.edit_message_text(f"✅ Payment address would be generated for {crypto}")
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
    await query.delete_message()
    await start(update, ctx)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    print(f"Update {update} caused error {context.error}")

async def main():
    """Run the bot"""
    print("🤖 Starting bot...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", start))
    
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
    app.add_handler(CallbackQueryHandler(balance_menu, pattern="^balance_menu$"))
    app.add_handler(CallbackQueryHandler(browse, pattern="^browse$"))
    app.add_handler(CallbackQueryHandler(search_bin, pattern="^search_bin$"))
    app.add_handler(CallbackQueryHandler(search_country, pattern="^search_country$"))
    app.add_handler(CallbackQueryHandler(search_base, pattern="^search_base$"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_start$"))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Run bot
    print("🤖 Bot is running!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())

