import os
import asyncio
import logging
import tempfile
from datetime import time, timezone, timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)

from config import TELEGRAM_TOKEN
from todoist_client import create_task, get_all_active_tasks, get_projects, complete_task
from voice import transcribe_voice, parse_due_date
from reports import build_morning_report, build_evening_report
from users import get_vlad_id, get_victoria_id, save_vlad_id, save_victoria_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LABEL_ORDER = {"🔴_Срочно": 0, "🟡_В_работе": 1, "⏳_Ожидаем_ответ": 2}
ADDING_TASK = 1

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
    "📋 По приоритету — задачи от срочных к обычным\n"
    "🗂 По проектам — задачи сгруппированы по проекту\n"
    "✅ Кнопка у задачи — отметить выполненной"
)

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
        [KeyboardButton("📊 По приоритету"), KeyboardButton("🗂 По проектам")],
        [KeyboardButton("🔔 Уведомления включены")],
    ],
    resize_keyboard=True
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True
)

PRIORITY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔴 Срочно", callback_data="priority_🔴_Срочно"),
        InlineKeyboardButton("🟡 В работе", callback_data="priority_🟡_В_работе"),
        InlineKeyboardButton("⏳ Ожидаем", callback_data="priority_⏳_Ожидаем_ответ"),
    ]
])


def label_emoji(labels):
    if "🔴_Срочно" in labels:
        return "🔴"
    if "⏳_Ожидаем_ответ" in labels:
        return "⏳"
    return "🟡"


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


# ── /start ────────────────────────────────────────────────
async def start_vlad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    save_vlad_id(chat_id)
    await update.message.reply_text(
        f"✅ *Система активирована, Влад!*\n\n🆔 Твой chat ID: `{chat_id}`\n\nДобавь в Railway Variables как `VLAD_CHAT_ID`" + VLAD_HELP,
        parse_mode="Markdown",
        reply_markup=VLAD_KEYBOARD
    )


async def start_victoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    save_victoria_id(chat_id)
    await update.message.reply_text(
        f"✅ *Привет, Виктория!*\n\n🆔 Твой chat ID: `{chat_id}`\n\nДобавь в Railway Variables как `VICTORIA_CHAT_ID`" + VICTORIA_HELP,
        parse_mode="Markdown",
        reply_markup=VICTORIA_KEYBOARD
    )


# ── Добавление задачи (кнопка) ───────────────────────────
async def btn_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Напиши задачу:", reply_markup=CANCEL_KEYBOARD)
    return ADDING_TASK


async def receive_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        keyboard = VLAD_KEYBOARD if is_vlad(update) else VICTORIA_KEYBOARD
        await update.message.reply_text("Отменено.", reply_markup=keyboard)
        return ConversationHandler.END

    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = parse_due_date(text)
    context.user_data["pending_sender"] = "Влад" if is_vlad(update) else "Виктория"
    context.user_data["pending_chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        f"📝 *{text}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )
    return ConversationHandler.END


# ── Выбор приоритета (callback) ───────────────────────────
async def handle_priority_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    label = query.data.replace("priority_", "")
    text = context.user_data.get("pending_task", "")
    due = context.user_data.get("pending_due")
    sender = context.user_data.get("pending_sender", "Влад")

    if not text:
        await query.edit_message_text("❌ Задача не найдена. Попробуй снова.")
        return

    create_task(content=text, due_string=due, label=label)

    emoji = label_emoji([label])
    due_text = f" (срок: {due})" if due else ""
    await query.edit_message_text(f"✅ Задача добавлена!\n{emoji} {text}{due_text}")

    vlad_id = get_vlad_id()
    is_vlad_user = query.from_user.id == vlad_id
    keyboard = VLAD_KEYBOARD if is_vlad_user else VICTORIA_KEYBOARD
    help_text = VLAD_HELP if is_vlad_user else VICTORIA_HELP
    await query.message.reply_text("Что дальше?" + help_text, parse_mode="Markdown", reply_markup=keyboard)

    await notify_victoria(query.get_bot(), text, label, due, sender=sender)
    if not is_vlad_user:
        await notify_vlad(query.get_bot(), f"📌 *Виктория добавила задачу:*\n\n{emoji} {text}{due_text}")

    context.user_data.clear()


# ── Построение списка задач ───────────────────────────────
def build_by_priority(tasks):
    priority_order = {"🔴_Срочно": 0, "🟡_В_работе": 1, "⏳_Ожидаем_ответ": 2}
    sorted_tasks = sorted(tasks, key=lambda t: priority_order.get(
        t.get("labels", [""])[0] if t.get("labels") else "", 3
    ))

    lines = ["📊 *По приоритету:*\n"]
    keyboard = []
    for t in sorted_tasks[:20]:
        labels = t.get("labels", [])
        e = label_emoji(labels)
        lines.append(f"{e} {t['content']}")
        keyboard.append([InlineKeyboardButton(
            f"✅ {t['content'][:45]}", callback_data=f"done_{t['id']}"
        )])

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return "\n".join(lines), markup


def build_by_project(tasks, project_names):
    from collections import defaultdict
    by_project = defaultdict(list)
    for t in tasks:
        by_project[t.get("project_id", "")].append(t)

    lines = ["🗂 *По проектам:*\n"]
    keyboard = []
    for project_id, items in by_project.items():
        name = project_names.get(project_id, "Без проекта")
        lines.append(f"\n*{name}*")
        for t in items[:10]:
            labels = t.get("labels", [])
            e = label_emoji(labels)
            lines.append(f"{e} {t['content']}")
            keyboard.append([InlineKeyboardButton(
                f"✅ {t['content'][:45]}", callback_data=f"done_{t['id']}"
            )])

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    return "\n".join(lines), markup


# ── Кнопки списка задач ───────────────────────────────────
async def btn_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    logger.info(f"Tasks fetched: {len(tasks)}")

    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return

    if is_victoria(update):
        text, markup = build_by_priority(tasks)
    else:
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
                e = label_emoji(t.get("labels", []))
                lines.append(f"{e} {t['content']}")
        text = "\n".join(lines)
        markup = None

    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def btn_view_by_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_by_priority(tasks)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def btn_view_by_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    project_names = {p["id"]: p["name"] for p in get_projects()}
    text, markup = build_by_project(tasks, project_names)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Отметить выполненной (callback) ──────────────────────
async def handle_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Выполнено!")

    task_id = query.data.replace("done_", "")
    try:
        complete_task(task_id)
        tasks = get_all_active_tasks()
        if tasks:
            text, markup = build_by_priority(tasks)
            if len(text) > 4000:
                text = text[:4000] + "\n..."
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await query.edit_message_text("🎉 Все задачи выполнены!")

        await notify_vlad(query.get_bot(), "✅ *Виктория отметила задачу выполненной*")
    except Exception as e:
        logger.error(f"Complete task error: {e}")
        await query.answer("❌ Ошибка при выполнении задачи", show_alert=True)


# ── Ожидают ответ ─────────────────────────────────────────
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


# ── Прямой текст от Влада ────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    due = parse_due_date(text)

    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = due
    context.user_data["pending_sender"] = "Влад"
    context.user_data["pending_chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        f"📝 *{text}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


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

        context.user_data["pending_task"] = text
        context.user_data["pending_due"] = due
        context.user_data["pending_sender"] = "Влад"

        await update.message.reply_text(
            f"🎙 *Распознано:* {text}\n\nВыбери приоритет:",
            parse_mode="Markdown",
            reply_markup=PRIORITY_KEYBOARD
        )
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Не удалось расшифровать голосовое.")


# ── Фото ─────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    due = parse_due_date(caption)

    context.user_data["pending_task"] = caption
    context.user_data["pending_due"] = due
    context.user_data["pending_sender"] = "Влад"

    await update.message.reply_text(
        f"📎 *{caption}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Уведомления (заглушка) ────────────────────────────────
async def btn_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔔 Уведомления активны — ты получаешь сообщения о каждой новой задаче от Влада.",
        reply_markup=VICTORIA_KEYBOARD
    )


# ── Отчёты ───────────────────────────────────────────────
async def send_morning_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_victoria_id()
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=build_morning_report(), parse_mode="Markdown")


async def send_evening_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_victoria_id()
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=build_evening_report(), parse_mode="Markdown")


# ── Запуск ───────────────────────────────────────────────
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить задачу$"), btn_add_task)],
        states={
            ADDING_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_task_text)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", start_vlad))
    app.add_handler(CommandHandler("victoria", start_victoria))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Список задач$"), btn_task_list))
    app.add_handler(MessageHandler(filters.Regex("^📊 По приоритету$"), btn_view_by_priority))
    app.add_handler(MessageHandler(filters.Regex("^🗂 По проектам$"), btn_view_by_project))
    app.add_handler(MessageHandler(filters.Regex("^⏳ Ожидают ответ$"), btn_waiting))
    app.add_handler(MessageHandler(filters.Regex("^🔔 Уведомления включены$"), btn_notifications))
    app.add_handler(CallbackQueryHandler(handle_priority_callback, pattern="^priority_"))
    app.add_handler(CallbackQueryHandler(handle_complete_callback, pattern="^done_"))
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
        await _broadcast_startup(app.bot)
        await asyncio.Event().wait()


async def _broadcast_startup(bot):
    vlad_id = get_vlad_id()
    victoria_id = get_victoria_id()
    if vlad_id:
        try:
            await bot.send_message(
                chat_id=vlad_id,
                text="👋 *VB Assistant на связи!*" + VLAD_HELP,
                parse_mode="Markdown",
                reply_markup=VLAD_KEYBOARD
            )
        except Exception as e:
            logger.error(f"Broadcast to Vlad failed: {e}")
    if victoria_id:
        try:
            await bot.send_message(
                chat_id=victoria_id,
                text="👋 *VB Assistant на связи!*" + VICTORIA_HELP,
                parse_mode="Markdown",
                reply_markup=VICTORIA_KEYBOARD
            )
        except Exception as e:
            logger.error(f"Broadcast to Victoria failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
