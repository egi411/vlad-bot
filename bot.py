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

from config import TELEGRAM_TOKEN
from todoist_client import create_task, get_all_active_tasks
from voice import transcribe_voice, parse_due_date
from reports import build_morning_report, build_evening_report
from users import get_vlad_id, get_victoria_id, save_vlad_id, save_victoria_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LABEL_ORDER = {"🔴_Срочно": 0, "🟡_В_работе": 1, "⏳_Ожидаем_ответ": 2}


def detect_label(text: str) -> str:
    lower = text.lower()
    if "срочно" in lower or "срочная" in lower:
        return "🔴_Срочно"
    if "ожидаем" in lower or "ждём" in lower or "ждем" in lower:
        return "⏳_Ожидаем_ответ"
    return "🟡_В_работе"


async def notify_victoria(bot, task_content: str, label: str, due: str | None):
    victoria_id = get_victoria_id()
    if not victoria_id:
        return
    due_text = f"\n📅 Срок: {due}" if due else ""
    label_text = f"\n🏷 {label.replace('_', ' ')}" if label else ""
    await bot.send_message(
        chat_id=victoria_id,
        text=f"📌 *Влад добавил задачу:*\n\n{task_content}{label_text}{due_text}",
        parse_mode="Markdown"
    )


# ── /start ──────────────────────────────────────────────
async def start_vlad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_vlad_id(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Привет, Влад!*\n\n"
        "Отправляй задачи текстом или голосом.\n\n"
        "Команды:\n"
        "• /waiting — задачи где ожидается ответ",
        parse_mode="Markdown"
    )


async def start_victoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_victoria_id(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Привет, Виктория!*\n\n"
        "Я буду уведомлять тебя о каждой новой задаче от Влада.\n\n"
        "Команды:\n"
        "• /tasks — все задачи по срочности и проектам",
        parse_mode="Markdown"
    )


# ── /waiting (для Влада) ─────────────────────────────────
async def cmd_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    waiting = [t for t in tasks if "⏳_Ожидаем_ответ" in t.get("labels", [])]

    if not waiting:
        await update.message.reply_text("⏳ Нет задач в ожидании ответа.")
        return

    lines = ["⏳ *Ожидаем ответ:*\n"]
    for t in waiting:
        due = t.get("due", {})
        due_text = f" — {due['date']}" if due and due.get("date") else ""
        lines.append(f"• {t['content']}{due_text}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /tasks (для Виктории) ────────────────────────────────
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач.")
        return

    # Группируем по проекту, сортируем по срочности
    from collections import defaultdict
    by_project = defaultdict(list)
    for t in tasks:
        labels = t.get("labels", [])
        priority = LABEL_ORDER.get(labels[0], 3) if labels else 3
        by_project[t.get("project_id", "—")].append((priority, t))

    # Получаем названия проектов
    from todoist_client import get_projects
    project_names = {p["id"]: p["name"] for p in get_projects()}

    lines = ["📋 *Все задачи по срочности:*\n"]
    for project_id, items in sorted(by_project.items()):
        project_name = project_names.get(project_id, "Без проекта")
        sorted_items = sorted(items, key=lambda x: x[0])
        lines.append(f"\n*{project_name}*")
        for _, t in sorted_items:
            labels = t.get("labels", [])
            emoji = "🔴" if "🔴_Срочно" in labels else "⏳" if "⏳_Ожидаем_ответ" in labels else "🟡"
            lines.append(f"{emoji} {t['content']}")

    text = "\n".join(lines)
    # Telegram limit 4096 chars
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Обработка текста ─────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    due = parse_due_date(text)
    label = detect_label(text)

    create_task(content=text, due_string=due, label=label)

    due_text = f" (срок: {due})" if due else ""
    await update.message.reply_text(f"✅ Задача добавлена{due_text}")
    await notify_victoria(context.bot, text, label, due)


# ── Обработка голоса ─────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙 Расшифровываю...")

    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        text = transcribe_voice(tmp_path)
        os.unlink(tmp_path)

        due = parse_due_date(text)
        label = detect_label(text)
        create_task(content=text, due_string=due, label=label)

        due_text = f" (срок: {due})" if due else ""
        await update.message.reply_text(
            f"🎙 *Распознано:* {text}\n\n✅ Задача добавлена{due_text}",
            parse_mode="Markdown"
        )
        await notify_victoria(context.bot, text, label, due)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать голосовое.")


# ── Обработка фото ───────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    due = parse_due_date(caption)
    label = detect_label(caption)

    create_task(content=caption, due_string=due, label=label)
    await update.message.reply_text(f"📎 Задача добавлена: {caption}")
    await notify_victoria(context.bot, caption, label, due)


# ── Ежедневные отчёты ────────────────────────────────────
async def send_morning_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_vlad_id()
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=build_morning_report(), parse_mode="Markdown")


async def send_evening_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_vlad_id()
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=build_evening_report(), parse_mode="Markdown")


# ── Запуск ───────────────────────────────────────────────
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_vlad))
    app.add_handler(CommandHandler("victoria", start_victoria))
    app.add_handler(CommandHandler("waiting", cmd_waiting))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    msk = timezone(timedelta(hours=3))
    app.job_queue.run_daily(send_morning_report, time=time(9, 0, tzinfo=msk))
    app.job_queue.run_daily(send_evening_report, time=time(20, 0, tzinfo=msk))

    logger.info("Бот запущен")
    async with app:
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
