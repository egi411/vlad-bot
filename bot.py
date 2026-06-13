import os
import asyncio
import logging
import tempfile
from datetime import time, timezone, timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

from config import TELEGRAM_TOKEN
from todoist_client import create_task, get_all_active_tasks, get_projects
from voice import transcribe_voice, parse_due_date
from reports import build_morning_report, build_evening_report
from users import get_vlad_id, get_victoria_id, save_vlad_id, save_victoria_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LABEL_ORDER = {"🔴_Срочно": 0, "🟡_В_работе": 1, "⏳_Ожидаем_ответ": 2}
ADDING_TASK = 1  # conversation state

VLAD_HELP = (
    "\n\n─────────────────\n"
    "📌 *Команды:*\n"
    "➕ Добавить задачу — кнопка или просто напиши текст\n"
    "🎙 Голосовое — автоматически станет задачей\n"
    "📋 Список задач — все активные задачи\n"
    "⏳ Ожидают ответ — задачи на паузе"
)

VICTORIA_HELP = (
    "\n\n─────────────────\n"
    "📌 *Команды:*\n"
    "➕ Добавить задачу — поставить задачу в систему\n"
    "📋 Список задач — все активные задачи по срочности\n"
    "🔔 Уведомления — приходят автоматически при новой задаче от Влада"
)

# ── Клавиатуры ───────────────────────────────────────────
VLAD_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить задачу"), KeyboardButton("📋 Список задач")],
        [KeyboardButton("⏳ Ожидают ответ")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Или напиши задачу текстом..."
)

VICTORIA_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить задачу"), KeyboardButton("📋 Список задач")],
        [KeyboardButton("🔔 Уведомления включены")],
    ],
    resize_keyboard=True
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True
)


def detect_label(text: str) -> str:
    lower = text.lower()
    if "срочно" in lower or "срочная" in lower:
        return "🔴_Срочно"
    if "ожидаем" in lower or "ждём" in lower or "ждем" in lower:
        return "⏳_Ожидаем_ответ"
    return "🟡_В_работе"


def is_vlad(update: Update) -> bool:
    return update.effective_chat.id == get_vlad_id()


def is_victoria(update: Update) -> bool:
    return update.effective_chat.id == get_victoria_id()


async def notify_victoria(bot, task_content: str, label: str, due: str | None, sender: str = "Влад"):
    victoria_id = get_victoria_id()
    if not victoria_id:
        return
    due_text = f"\n📅 Срок: {due}" if due else ""
    emoji = "🔴" if label == "🔴_Срочно" else "⏳" if label == "⏳_Ожидаем_ответ" else "🟡"
    await bot.send_message(
        chat_id=victoria_id,
        text=f"📌 *{sender} добавил задачу:*\n\n{emoji} {task_content}{due_text}",
        parse_mode="Markdown"
    )


async def notify_vlad(bot, text: str):
    vlad_id = get_vlad_id()
    if not vlad_id:
        return
    await bot.send_message(chat_id=vlad_id, text=text, parse_mode="Markdown")


# ── /start для Влада ─────────────────────────────────────
async def start_vlad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_vlad_id(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Система активирована, Влад!*\n\nОтправляй задачи текстом, голосом или через кнопки." + VLAD_HELP,
        parse_mode="Markdown",
        reply_markup=VLAD_KEYBOARD
    )


# ── /victoria для Виктории ───────────────────────────────
async def start_victoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_victoria_id(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Привет, Виктория!*\n\nТы будешь получать уведомления о каждой новой задаче от Влада." + VICTORIA_HELP,
        parse_mode="Markdown",
        reply_markup=VICTORIA_KEYBOARD
    )


# ── Кнопка «Добавить задачу» (начало диалога) ───────────
async def btn_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✏️ Напиши задачу:",
        reply_markup=CANCEL_KEYBOARD
    )
    return ADDING_TASK


async def receive_task_from_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        keyboard = VLAD_KEYBOARD if is_vlad(update) else VICTORIA_KEYBOARD
        await update.message.reply_text("Отменено.", reply_markup=keyboard)
        return ConversationHandler.END

    due = parse_due_date(text)
    label = detect_label(text)
    create_task(content=text, due_string=due, label=label)

    due_text = f" (срок: {due})" if due else ""
    keyboard = VLAD_KEYBOARD if is_vlad(update) else VICTORIA_KEYBOARD
    sender = "Влад" if is_vlad(update) else "Виктория"

    help_text = VLAD_HELP if is_vlad(update) else VICTORIA_HELP
    await update.message.reply_text(f"✅ Задача добавлена{due_text}" + help_text, parse_mode="Markdown", reply_markup=keyboard)
    await notify_victoria(update.get_bot(), text, label, due, sender=sender)

    if is_victoria(update):
        await notify_vlad(update.get_bot(), f"📌 *Виктория добавила задачу:*\n\n{text}{due_text}")

    return ConversationHandler.END


# ── Кнопка «Список задач» ────────────────────────────────
async def btn_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач.")
        return

    from collections import defaultdict
    by_project = defaultdict(list)
    for t in tasks:
        labels = t.get("labels", [])
        priority = LABEL_ORDER.get(labels[0], 3) if labels else 3
        by_project[t.get("project_id", "")].append((priority, t))

    project_names = {p["id"]: p["name"] for p in get_projects()}

    lines = ["📋 *Список задач:*\n"]
    for project_id, items in sorted(by_project.items()):
        name = project_names.get(project_id, "Без проекта")
        lines.append(f"\n*{name}*")
        for _, t in sorted(items, key=lambda x: x[0]):
            labels = t.get("labels", [])
            e = "🔴" if "🔴_Срочно" in labels else "⏳" if "⏳_Ожидаем_ответ" in labels else "🟡"
            lines.append(f"{e} {t['content']}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Кнопка «Ожидают ответ» (только Влад) ────────────────
async def btn_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    waiting = [t for t in tasks if "⏳_Ожидаем_ответ" in t.get("labels", [])]

    if not waiting:
        await update.message.reply_text("⏳ Нет задач в ожидании ответа.")
        return

    lines = ["⏳ *Ожидаем ответ:*\n"]
    for t in waiting:
        due = t.get("due") or {}
        due_text = f" — {due['date']}" if due.get("date") else ""
        lines.append(f"• {t['content']}{due_text}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Прямой текст (без кнопки) — только для Влада ────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    due = parse_due_date(text)
    label = detect_label(text)

    create_task(content=text, due_string=due, label=label)
    due_text = f" (срок: {due})" if due else ""
    await update.message.reply_text(f"✅ Задача добавлена{due_text}" + VLAD_HELP, parse_mode="Markdown", reply_markup=VLAD_KEYBOARD)
    await notify_victoria(context.bot, text, label, due)


# ── Голос ────────────────────────────────────────────────
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
            f"🎙 *Распознано:* {text}\n\n✅ Задача добавлена{due_text}" + VLAD_HELP,
            parse_mode="Markdown", reply_markup=VLAD_KEYBOARD
        )
        await notify_victoria(context.bot, text, label, due)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать голосовое.")


# ── Фото ─────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    due = parse_due_date(caption)
    label = detect_label(caption)
    create_task(content=caption, due_string=due, label=label)
    await update.message.reply_text(f"📎 Задача добавлена: {caption}" + VLAD_HELP, parse_mode="Markdown", reply_markup=VLAD_KEYBOARD)
    await notify_victoria(context.bot, caption, label, due)


# ── Отчёты ───────────────────────────────────────────────
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

    # ConversationHandler для кнопки «Добавить задачу»
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Добавить задачу$"), btn_add_task)
        ],
        states={
            ADDING_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_task_from_button)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", start_vlad))
    app.add_handler(CommandHandler("victoria", start_victoria))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Список задач$"), btn_task_list))
    app.add_handler(MessageHandler(filters.Regex("^⏳ Ожидают ответ$"), btn_waiting))
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
