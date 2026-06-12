import os
import asyncio
import logging
import tempfile
from datetime import time, timezone, timedelta

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

from config import TELEGRAM_TOKEN, MORNING_REPORT_HOUR, EVENING_REPORT_HOUR
from todoist_client import create_task
from voice import transcribe_voice, parse_due_date
from reports import build_morning_report, build_evening_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VLAD_CHAT_ID_FILE = "vlad_chat_id.txt"


def get_vlad_chat_id():
    if os.path.exists(VLAD_CHAT_ID_FILE):
        with open(VLAD_CHAT_ID_FILE) as f:
            val = f.read().strip()
            return int(val) if val else None
    return None


def save_vlad_chat_id(chat_id: int):
    with open(VLAD_CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    save_vlad_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Привет, Влад!\n\n"
        "Система активирована. Отправляй задачи текстом или голосом — "
        "всё автоматически попадёт в Todoist.\n\n"
        "Примеры:\n"
        "• «Позвонить Jetex завтра»\n"
        "• «Подготовить оффер для клиники»\n"
        "• «Проверить производство мюлей срочно»"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    due = parse_due_date(text)

    label = None
    lower = text.lower()
    if "срочно" in lower or "срочная" in lower:
        label = "🔴_Срочно"
    elif "ожидаем" in lower or "ждём" in lower or "ждем" in lower:
        label = "⏳_Ожидаем_ответ"
    else:
        label = "🟡_В_работе"

    task = create_task(content=text, due_string=due, label=label)

    due_text = f" (срок: {due})" if due else ""
    await update.message.reply_text(f"✅ Задача добавлена в Todoist{due_text}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙 Расшифровываю...")

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        text = transcribe_voice(tmp_path)
        os.unlink(tmp_path)

        due = parse_due_date(text)
        label = "🟡_В_работе"
        lower = text.lower()
        if "срочно" in lower:
            label = "🔴_Срочно"
        elif "ждём" in lower or "ждем" in lower or "ожидаем" in lower:
            label = "⏳_Ожидаем_ответ"

        create_task(content=text, due_string=due, label=label)

        due_text = f" (срок: {due})" if due else ""
        await update.message.reply_text(
            f"🎙 *Распознано:* {text}\n\n✅ Задача добавлена в Todoist{due_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать голосовое. Попробуй ещё раз.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    due = parse_due_date(caption)

    create_task(content=caption, due_string=due, label="🟡_В_работе")
    await update.message.reply_text(f"📎 Фото и задача добавлены: {caption}")


async def send_morning_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_vlad_chat_id()
    if not chat_id:
        return
    report = build_morning_report()
    await context.bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown")


async def send_evening_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_vlad_chat_id()
    if not chat_id:
        return
    report = build_evening_report()
    await context.bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown")


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    msk = timezone(timedelta(hours=3))
    job_queue = app.job_queue
    job_queue.run_daily(send_morning_report, time=time(9, 0, tzinfo=msk))
    job_queue.run_daily(send_evening_report, time=time(20, 0, tzinfo=msk))

    logger.info("Бот запущен")
    async with app:
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
