"""Telegram channel adapter: text in, agent reply out."""

import asyncio
import logging
from collections import defaultdict, deque
from time import monotonic

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from agent.loop import FRIENDLY_ERROR, Agent

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000
RATE_LIMIT = 10
RATE_WINDOW = 60.0
TYPING_REFRESH = 4.0

START_TEXT = (
    "مرحباً! أنا مساعد كلية علوم الحاسب والمعلومات في جامعة الملك سعود.\n"
    "Hi! I'm the CCIS Support Agent.\n"
    "اسألني عن البرامج والقبول والمواعيد وشؤون الطلبة، بالعربية أو الإنجليزية. /help\n"
    "Ask me about programs, admission, deadlines and advisors."
)

HELP_TEXT = (
    "I can help with:\n"
    "1. Programs and departments\n"
    "2. Admission requirements and steps\n"
    "3. Academic calendar dates and deadlines\n"
    "4. Advisor and faculty contacts\n"
    "5. Free slots on a faculty schedule (never booking)\n"
    "6. Complex cases: contact plus an email template in Arabic or English\n\n"
    "أستطيع المساعدة في: البرامج والأقسام، شروط القبول، المواعيد، بيانات المرشد "
    "وأعضاء هيئة التدريس، الأوقات المتاحة، وتحويل الحالات المعقدة مع قالب بريد.\n"
    "/reset لمسح المحادثة."
)

RESET_TEXT = "Conversation cleared. تم مسح المحادثة."
ONLY_TEXT = "I can only read text messages. أستطيع قراءة الرسائل النصية فقط."
TOO_MANY_TEXT = "One moment please, that's a lot of messages. لحظة من فضلك، أرسلت رسائل كثيرة."


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a reply into pieces under the limit, preferring line breaks."""
    pieces: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


class ChatLimiter:
    """Allow a fixed number of messages per chat within a sliding window."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, chat_id: int) -> bool:
        """Record a message and report whether the chat is still under the limit."""
        now = monotonic()
        hits = self._hits[chat_id]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def build_application(agent: Agent | None = None) -> Application:
    """Create the Telegram application with its handlers."""
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    application.bot_data["agent"] = agent or Agent()
    application.bot_data["limiter"] = ChatLimiter()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(MessageHandler(filters.ALL, on_unsupported))
    return application


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the user briefly in Arabic and English."""
    await update.effective_message.reply_text(START_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List what the agent can do."""
    await update.effective_message.reply_text(HELP_TEXT)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the conversation memory for this chat."""
    context.application.bot_data["agent"].reset(update.effective_chat.id)
    await update.effective_message.reply_text(RESET_TEXT)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pass a text message to the agent and send the answer back."""
    chat_id = update.effective_chat.id
    if not context.application.bot_data["limiter"].allow(chat_id):
        await update.effective_message.reply_text(TOO_MANY_TEXT)
        return
    answer = await _answer(update, context, update.effective_message.text)
    for piece in split_message(answer):
        await update.effective_message.reply_text(piece)


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell the user that only text is supported."""
    await update.effective_message.reply_text(ONLY_TEXT)


async def _answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    """Run the agent in a worker thread, keeping the typing indicator alive."""
    chat_id = update.effective_chat.id
    agent = context.application.bot_data["agent"]
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    task = asyncio.create_task(asyncio.to_thread(agent.reply, chat_id, text))
    while not task.done():
        await asyncio.wait({task}, timeout=TYPING_REFRESH)
        if not task.done():
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    if task.exception() is not None:
        logger.error("agent failed: %s", task.exception())
        return FRIENDLY_ERROR
    return task.result()
