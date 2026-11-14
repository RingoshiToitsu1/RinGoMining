"""aiogram wiring. Command logic lives in commands/*.py as plain async
functions returning a Reply, so it is testable without Telegram."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .commands import control, info
from .format import Reply, e
from .notifications import Notifier

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)
Handler = Callable[["App", list[str], str], Awaitable[Reply]]

COMMANDS: dict[str, tuple[Handler, str]] = {
    "start": (info.start, "Initialize bot"),
    "dashboard": (info.dashboard, "Overall trading dashboard"),
    "scan": (control.scan, "Trigger immediate scan"),
    "opportunities": (info.opportunities, "Show available arbitrage"),
    "positions": (info.positions, "Show active positions"),
    "balance": (info.balance, "Show available balances"),
    "profit": (info.profit, "Realized and unrealized P&L"),
    "history": (info.history, "Historical trades"),
    "settings": (control.settings, "Configure system"),
    "capital": (control.capital, "Configure bankroll allocation"),
    "threshold": (control.threshold, "Dynamic profitability settings"),
    "risk": (info.risk, "Display risk exposure"),
    "autotrade": (control.autotrade, "Enable or disable automated execution"),
    "pause": (control.pause, "Pause new trades"),
    "resume": (control.resume, "Resume trading"),
    "close": (control.close, "Manage open positions"),
    "status": (info.status, "System and exchange health"),
    "mappings": (control.mappings, "Review market matches"),
    "stop": (control.stop, "Emergency stop"),
    "help": (info.help_, "Display commands"),
}


async def dispatch(app: "App", command: str, args: list[str], user: str) -> Reply:
    """Run a command by name. Used by both message and button handlers (and tests)."""
    entry = COMMANDS.get(command)
    if entry is None:
        return Reply("Unknown command. /help")
    if not app.ready and command not in ("start", "help", "status"):
        return Reply("Starting up and reconciling with the exchanges. Try again in a moment.")
    try:
        return await entry[0](app, args, user)
    except Exception as ex:
        log.exception("command /%s failed", command)
        return Reply(f"❌ /{command} failed: {e(ex, 300)}")


def _keyboard(reply: Reply) -> InlineKeyboardMarkup | None:
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data[:64]) for label, data in row] for row in reply.buttons])


def build(app: "App") -> tuple[Bot, Dispatcher, Notifier]:
    settings = app.settings
    admins = settings.admin_ids
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    router = Router()

    def allowed(user_id: int | None) -> bool:
        return user_id is not None and user_id in admins

    async def answer(message: Message, reply: Reply) -> None:
        text = reply.text
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        for i, chunk in enumerate(chunks):
            await message.answer(chunk, reply_markup=_keyboard(reply) if i == len(chunks) - 1 else None,
                                 disable_web_page_preview=True)

    @router.message(Command(*COMMANDS))
    async def on_command(message: Message, command: CommandObject) -> Any:
        uid = message.from_user.id if message.from_user else None
        if not allowed(uid):
            log.warning("rejected command from unauthorised user %s", uid)
            return await message.answer("Not authorised.")
        args = (command.args or "").split()
        name = message.from_user.username or str(uid)
        await answer(message, await dispatch(app, command.command, args, name))

    @router.callback_query(F.data)
    async def on_button(query: CallbackQuery) -> Any:
        if not allowed(query.from_user.id):
            return await query.answer("Not authorised.", show_alert=True)
        parts = (query.data or "").split()
        await query.answer()
        if parts and isinstance(query.message, Message):
            name = query.from_user.username or str(query.from_user.id)
            await answer(query.message, await dispatch(app, parts[0], parts[1:], name))

    dp.include_router(router)

    async def send_all(text: str) -> None:
        for chat_id in admins:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)

    return bot, dp, Notifier(app.bus, send_all)


async def run(app: "App") -> None:
    bot, dp, notifier = build(app)
    await bot.set_my_commands([BotCommand(command=k, description=v[1]) for k, v in COMMANDS.items()])
    cfg = app.cfg
    await notifier.send(f"🤖 Arbitrage bot online | mode {cfg.mode.upper()} | autotrade "
                        f"{'ON' if cfg.autotrade else 'OFF'}"
                        + ("\n" + "\n".join(e(n, 200) for n in app.recovery_notes[:6]) if app.recovery_notes else ""))
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
