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

# Todoist priority: 4=p1(срочно), 3=p2(важно), 2=p3(обычно), 1=p4(условно)
PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪"}
PRIORITY_NAMES = {4: "Срочно", 3: "Важно", 2: "Обычно", 1: "Условно"}
WAITING_LABEL = "⏳_Ожидаем_ответ"

ADDING_TASK = 1
CHOOSING_WAITING = 2

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
        InlineKeyboardButton("🔴 Срочно (p1)", callback_data="priority_4"),
        InlineKeyboardButton("🟠 Важно (p2)", callback_data="priority_3"),
    ],
    [
        InlineKeyboardButton("🟡 Обычно (p3)", callback_data="priority_2"),
        InlineKeyboardButton("⚪ Условно (p4)", callback_data="priority_1"),
    ],
])

WAITING_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("⏳ Да, ожидаем ответ", callback_data="waiting_yes"),
        InlineKeyboardButton("✅ Нет", callback_data="waiting_no"),
    ]
])


def priority_emoji(task):
    return PRIORITY_EMOJI.get(task.get("priority", 1), "🟡")


def is_waiting(task):
    return WAITING_LABEL in task.get("labels", [])


def is_vlad(update: Update) -> bool:
    return update.effective_chat.id == get_vlad_id()


def is_victoria(update: Update) -> bool:
    return update.effective_chat.id == get_victoria_id()


async def notify_victoria(bot, task_content: str, priority: int, due: str | None, sender: str = "Влад"):
    victoria_id = get_victoria_id()
    if not victoria_id:
        return
    due_text = f"\n📅 Срок: {due}" if due else ""
    e = PRIORITY_EMOJI.get(priority, "🟡")
    name = PRIORITY_NAMES.get(priority, "Обычно")
    await bot.send_message(
        chat_id=victoria_id,
        text=f"📌 *{sender} добавил задачу:*\n\n{e} [{name}] {task_content}{due_text}",
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

    priority = int(query.data.replace("priority_", ""))
    text = context.user_data.get("pending_task", "")

    if not text:
        await query.edit_message_text("❌ Задача не найдена. Попробуй снова.")
        return

    context.user_data["pending_priority"] = priority
    e = PRIORITY_EMOJI[priority]
    name = PRIORITY_NAMES[priority]

    vlad_id = get_vlad_id()
    is_vlad_user = query.from_user.id == vlad_id

    if is_vlad_user:
        # Влад: спрашиваем про "Ожидаем ответ"
        await query.edit_message_text(
            f"📝 *{text}*\n{e} {name}\n\nОтметить «⏳ Ожидаем ответ» от Виктории?",
            parse_mode="Markdown",
            reply_markup=WAITING_KEYBOARD
        )
    else:
        # Виктория: сразу создаём задачу без вопроса про ожидание
        due = context.user_data.get("pending_due")
        sender = context.user_data.get("pending_sender", "Виктория")
        create_task(content=text, due_string=due, priority=priority)
        due_text = f" (срок: {due})" if due else ""
        await query.edit_message_text(f"✅ Задача добавлена!\n{e} {text}{due_text}")
        await query.message.reply_text("Что дальше?" + VICTORIA_HELP, parse_mode="Markdown", reply_markup=VICTORIA_KEYBOARD)
        await notify_victoria(query.get_bot(), text, str(priority), due, sender=sender)
        await notify_vlad(query.get_bot(), f"📌 *Виктория добавила задачу:*\n\n{e} {text}{due_text}")
        context.user_data.clear()


# ── Выбор "Ожидаем ответ" (только Влад) ──────────────────
async def handle_waiting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = context.user_data.get("pending_task", "")
    due = context.user_data.get("pending_due")
    priority = context.user_data.get("pending_priority", 2)
    waiting = query.data == "waiting_yes"

    labels = [WAITING_LABEL] if waiting else []
    create_task(content=text, due_string=due, priority=priority, labels=labels)

    e = PRIORITY_EMOJI[priority]
    due_text = f" (срок: {due})" if due else ""
    waiting_text = "\n⏳ Отмечено: Ожидаем ответ" if waiting else ""
    await query.edit_message_text(f"✅ Задача добавлена!\n{e} {text}{due_text}{waiting_text}")
    await query.message.reply_text("Что дальше?" + VLAD_HELP, parse_mode="Markdown", reply_markup=VLAD_KEYBOARD)
    await notify_victoria(query.get_bot(), text, str(priority), due, sender="Влад")
    context.user_data.clear()


# ── Построение списка задач ───────────────────────────────
def build_by_priority(tasks):
    sorted_tasks = sorted(tasks, key=lambda t: -t.get("priority", 1))

    lines = ["📊 *По приоритету:*\n"]
    keyboard = []
    for t in sorted_tasks[:20]:
        e = priority_emoji(t)
        waiting = " ⏳" if is_waiting(t) else ""
        lines.append(f"{e} {t['content']}{waiting}")
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
        sorted_items = sorted(items, key=lambda t: -t.get("priority", 1))
        for t in sorted_items[:10]:
            e = priority_emoji(t)
            waiting = " ⏳" if is_waiting(t) else ""
            lines.append(f"{e} {t['content']}{waiting}")
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
            by_project[t.get("project_id", "")].append(t)

        project_names = {p["id"]: p["name"] for p in get_projects()}
        lines = ["📋 *Список задач:*\n"]
        for project_id, items in by_project.items():
            name = project_names.get(project_id, "Без проекта")
            lines.append(f"\n*{name}*")
            for t in sorted(items, key=lambda t: -t.get("priority", 1)):
                e = priority_emoji(t)
                waiting = " ⏳" if is_waiting(t) else ""
                lines.append(f"{e} {t['content']}{waiting}")
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


# ── Ожидают ответ (Влад) ─────────────────────────────────
async def btn_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_all_active_tasks()
    waiting = [t for t in tasks if is_waiting(t)]

    if not waiting:
        await update.message.reply_text("⏳ Нет задач в ожидании ответа от Виктории.")
        return

    lines = ["⏳ *Ожидаем ответ от Виктории:*\n"]
    for t in sorted(waiting, key=lambda t: -t.get("priority", 1)):
        e = priority_emoji(t)
        due = t.get("due") or {}
        due_text = f" — {due['date']}" if due.get("date") else ""
        lines.append(f"{e} {t['content']}{due_text}")

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
    app.add_handler(CallbackQueryHandler(handle_waiting_callback, pattern="^waiting_"))
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
