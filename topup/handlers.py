"""
DEMO TELEGRAM BUTTONS ONLY.

Do not copy this file into your current bot.
Your bot already has its own menus. Use topup.api instead.
See ADD_TO_YOUR_BOT.md
"""
from __future__ import annotations

from io import BytesIO

import qrcode
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from topup.config import (
    ADMIN_TELEGRAM_IDS,
    ASSETS,
    AUTO_SWEEP,
    ENABLED_ASSETS,
    SWEEP_BTC_ADDRESS,
    SWEEP_ETH_ADDRESS,
    SWEEP_LTC_ADDRESS,
    SWEEP_TRON_ADDRESS,
)
from topup.database import Database
from topup.poller import DepositPoller
from topup.sweep import Sweeper
from topup.wallets import HDWalletEngine

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["Top up", "Balance"]],
    resize_keyboard=True,
)


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _wallets(context: ContextTypes.DEFAULT_TYPE) -> HDWalletEngine:
    return context.application.bot_data["wallets"]


def _poller(context: ContextTypes.DEFAULT_TYPE) -> DepositPoller:
    return context.application.bot_data["poller"]


def _sweeper(context: ContextTypes.DEFAULT_TYPE) -> Sweeper:
    return context.application.bot_data["sweeper"]


def _coin_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in ENABLED_ASSETS:
        row.append(InlineKeyboardButton(ASSETS[code].name, callback_data=f"topup:{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _qr_png(payload: str) -> BytesIO:
    image = qrcode.make(payload)
    bio = BytesIO()
    image.save(bio, format="PNG")
    bio.seek(0)
    return bio


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await _db(context).upsert_user(user.id, user.username)
    await update.effective_message.reply_text(
        "Welcome. This store uses crypto top-ups only.\n\n"
        "Tap Top up to get a one-time deposit address. "
        "Whatever amount you send is converted to USD and credited to your balance.",
        reply_markup=MAIN_KEYBOARD,
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    account = await _db(context).upsert_user(user.id, user.username)
    await update.effective_message.reply_text(
        f"Your balance: ${account.balance_usd:.2f}",
        reply_markup=MAIN_KEYBOARD,
    )


async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Choose the coin you want to send. A unique address is generated for this top-up.",
        reply_markup=_coin_keyboard(),
    )


async def topup_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    asset_code = query.data.split(":", 1)[1]
    if asset_code not in ENABLED_ASSETS:
        await query.edit_message_text("That coin is not enabled.")
        return

    user = query.from_user
    db = _db(context)
    await db.upsert_user(user.id, user.username)
    index = await db.next_index(asset_code)
    derived = _wallets(context).derive(asset_code, index)
    deposit = await db.create_deposit(
        telegram_id=user.id,
        asset=asset_code,
        address=derived.address,
        derivation_index=derived.index,
        path=derived.path,
    )
    asset = ASSETS[asset_code]
    caption = (
        f"Send any amount of {asset.name} to this one-time address.\n\n"
        f"<code>{derived.address}</code>\n\n"
        f"There is no set invoice amount. The exact amount that arrives "
        f"is converted to USD and credited after {asset.confirmations} confirmation(s).\n\n"
        "Keep this chat open. You will get a message when the payment is credited."
    )
    try:
        await query.message.reply_photo(
            photo=InputFile(_qr_png(derived.address), filename="deposit.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("I sent it — check now", callback_data=f"check:{deposit.id}")]]
            ),
        )
    except Exception:
        await query.message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("I sent it — check now", callback_data=f"check:{deposit.id}")]]
            ),
        )
    await query.edit_message_text(f"{asset.name} deposit address created.")


async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Checking the blockchain...")
    deposit_id = int(query.data.split(":", 1)[1])
    deposit = await _db(context).get_deposit(deposit_id)
    if deposit is None or deposit.telegram_id != query.from_user.id:
        await query.message.reply_text("That deposit was not found.")
        return
    await _poller(context).poll(context.bot)
    if await _db(context).has_credit(deposit.id):
        await query.message.reply_text("Payment found and credited.")
    else:
        await query.message.reply_text(
            "No confirmed payment yet. Keep the address. "
            "The bot keeps watching and will credit you automatically."
        )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id not in ADMIN_TELEGRAM_IDS:
        return
    users, deposits, total = await _db(context).admin_stats()
    wallets = _wallets(context)
    fee_eth = wallets.fee_wallet("ETH").address
    fee_usdt = wallets.fee_wallet("USDT_ERC20").address
    fee_tron = wallets.fee_wallet("USDT_TRC20").address
    await update.effective_message.reply_text(
        f"Users: {users}\n"
        f"Deposit addresses: {deposits}\n"
        f"Total credited: ${total:.2f}\n"
        f"Auto-sweep: {'on' if AUTO_SWEEP else 'off'}\n\n"
        f"Treasury BTC: {SWEEP_BTC_ADDRESS or 'not set'}\n"
        f"Treasury LTC: {SWEEP_LTC_ADDRESS or 'not set'}\n"
        f"Treasury ETH/USDT: {SWEEP_ETH_ADDRESS or 'not set'}\n"
        f"Treasury TRON/USDT: {SWEEP_TRON_ADDRESS or 'not set'}\n\n"
        f"Fee wallet ETH: {fee_eth}\n"
        f"Fee wallet USDT ERC20: {fee_usdt}\n"
        f"Fee wallet TRON: {fee_tron}\n\n"
        "Fund the TRON and ETH fee wallets so token sweeps can pay gas."
    )


async def sweep_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id not in ADMIN_TELEGRAM_IDS:
        return
    msg = await update.effective_message.reply_text("Sweeping credited deposits to treasury...")
    results = await _sweeper(context).sweep_unswept(limit=40)
    if not results:
        await msg.edit_text("Nothing to sweep. Credited deposits are already consolidated.")
        return
    lines = []
    for item in results:
        status = "ok" if item.ok else "failed"
        tx = f" {item.txids[0]}" if item.txids else ""
        lines.append(f"{item.asset} #{item.deposit_id}: {status} — {item.detail}{tx}")
    text = "Sweep results:\n" + "\n".join(lines)
    await msg.edit_text(text[:4000])


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip().lower()
    if text in {"top up", "topup", "/topup"}:
        await topup_menu(update, context)
        return
    if text in {"balance", "/balance"}:
        await balance(update, context)
