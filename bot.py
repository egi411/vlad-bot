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
import db
from voice import transcribe_voice, parse_due_date, detect_task_meta
from reports import build_morning_report, build_evening_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪"}
PRIORITY_NAMES = {4: "Срочно", 3: "Важно", 2: "Обычно", 1: "Условно"}
STATUS_LABELS = {"active": "Активна", "waiting": "Ожидает", "done": "Выполнена"}

ADDING_TASK = 1

MENU_BUTTONS = {
    "📋 Список задач", "📊 По приоритету", "🗂 По проектам", "➕ Добавить задачу",
}

CATEGORY_EMOJI = {"VLAD BYKOV": "👔", "Контент": "🎬", "Restoria": "🏪", "Личное": "👤"}
CATEGORY_DISPLAY = {
    "VLAD BYKOV": "👔 Клиенты",
    "Контент": "🎬 Контент",
    "Restoria": "🏪 Restoria",
    "Личное": "👤 Личное",
}

# Ожидание комментария: chat_id -> {task_id, source}
_pending_comment: dict[int, dict] = {}

KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить задачу"), KeyboardButton("📋 Список задач")],
        [KeyboardButton("📊 По приоритету"), KeyboardButton("🗂 По проектам")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Или напиши задачу текстом..."
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)

PRIORITY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔴 Срочно", callback_data="priority_4"),
        InlineKeyboardButton("🟠 Важно", callback_data="priority_3"),
    ],
    [
        InlineKeyboardButton("🟡 Обычно", callback_data="priority_2"),
        InlineKeyboardButton("⚪ Условно", callback_data="priority_1"),
    ],
])

CATEGORY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("👔 Клиенты", callback_data="cat_VLAD BYKOV"),
        InlineKeyboardButton("🎬 Контент", callback_data="cat_Контент"),
    ],
    [
        InlineKeyboardButton("🏪 Restoria", callback_data="cat_Restoria"),
        InlineKeyboardButton("👤 Личное", callback_data="cat_Личное"),
    ],
])


def escape_md(text: str) -> str:
    for ch in ['\\', '*', '_', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


def priority_emoji(task):
    return PRIORITY_EMOJI.get(task.get("priority", 2), "🟡")


# ── /start ────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
    db.set_setting("vlad_id", str(chat_id))
    await update.message.reply_text(
        f"✅ *VB Assistant активирован!*\n`Chat ID: {chat_id}`",
        parse_mode="Markdown",
        reply_markup=KEYBOARD
    )


# ── Добавление задачи ─────────────────────────────────────
async def btn_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Напиши задачу:", reply_markup=CANCEL_KEYBOARD)
    return ADDING_TASK


async def receive_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if update.effective_chat.id in _pending_comment:
        await handle_text(update, context)
        return ConversationHandler.END

    if text == "❌ Отмена" or text in MENU_BUTTONS:
        await update.message.reply_text("Отменено.", reply_markup=KEYBOARD)
        return ConversationHandler.END

    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = parse_due_date(text)
    await update.message.reply_text(
        f"📝 *{escape_md(text)}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )
    return ConversationHandler.END


# ── Выбор приоритета ──────────────────────────────────────
async def handle_priority_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    priority = int(query.data.replace("priority_", ""))
    text = context.user_data.get("pending_task", "")
    if not text:
        await query.edit_message_text("❌ Задача не найдена. Попробуй снова.")
        return
    context.user_data["pending_priority"] = priority
    await query.edit_message_text(
        f"📝 *{escape_md(text)}*\n{PRIORITY_EMOJI[priority]} {PRIORITY_NAMES[priority]}\n\nВыбери категорию:",
        parse_mode="Markdown",
        reply_markup=CATEGORY_KEYBOARD
    )


# ── Выбор категории ───────────────────────────────────────
async def handle_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    project = query.data.replace("cat_", "")
    text = context.user_data.get("pending_task", "")
    priority = context.user_data.get("pending_priority", 2)
    due = context.user_data.get("pending_due")

    db.create_task(content=text, priority=priority, project=project, sender="Влад", due=due)

    e = PRIORITY_EMOJI[priority]
    cat = CATEGORY_DISPLAY.get(project, project)
    due_text = f" (срок: {due})" if due else ""
    await query.edit_message_text(f"✅ Задача добавлена!\n{e} {text}{due_text}\n{cat}")
    await query.message.reply_text("Готово!", reply_markup=KEYBOARD)
    context.user_data.clear()


# ── Построение списков ────────────────────────────────────
def _card_buttons_keyboard(tasks_with_index, source: str) -> InlineKeyboardMarkup | None:
    btns = [
        InlineKeyboardButton(f"💬 {i}. {t['content'][:22]}", callback_data=f"card_{t['id']}_{source}")
        for i, t in tasks_with_index
    ]
    rows = [btns[j:j + 2] for j in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows) if rows else None


def build_by_priority(tasks):
    sorted_tasks = sorted(tasks, key=lambda t: -t.get("priority", 1))[:20]
    lines = ["📊 *По приоритету:*\n"]
    indexed = []
    for i, t in enumerate(sorted_tasks, 1):
        e = priority_emoji(t)
        done = " ✅" if t.get("status") == "done" else ""
        lines.append(f"{i}. {e} {escape_md(t['content'])}{done}")
        indexed.append((i, t))
    return "\n".join(lines), _card_buttons_keyboard(indexed, "priority")


def build_by_project(tasks):
    from collections import defaultdict
    by_project = defaultdict(list)
    for t in tasks:
        by_project[t.get("project", "Без проекта")].append(t)
    lines = ["🗂 *По проектам:*\n"]
    indexed = []
    counter = 1
    for project_name, items in by_project.items():
        lines.append(f"\n*{escape_md(project_name)}*")
        for t in sorted(items, key=lambda t: -t.get("priority", 1))[:10]:
            e = priority_emoji(t)
            done = " ✅" if t.get("status") == "done" else ""
            lines.append(f"{counter}. {e} {escape_md(t['content'])}{done}")
            indexed.append((counter, t))
            counter += 1
    return "\n".join(lines), _card_buttons_keyboard(indexed, "project")


def build_task_list(tasks):
    from collections import defaultdict
    by_project = defaultdict(list)
    for t in tasks:
        by_project[t.get("project", "Без проекта")].append(t)
    lines = ["📋 *Список задач:*\n"]
    indexed = []
    counter = 1
    for project_name, items in by_project.items():
        lines.append(f"\n*{escape_md(project_name)}*")
        for t in sorted(items, key=lambda t: -t.get("priority", 1)):
            e = priority_emoji(t)
            done = " ✅" if t.get("status") == "done" else ""
            lines.append(f"{counter}. {e} {escape_md(t['content'])}{done}")
            indexed.append((counter, t))
            counter += 1
    return "\n".join(lines), _card_buttons_keyboard(indexed, "list")


# ── Карточка задачи ───────────────────────────────────────
def _card_text_and_markup(task_id: int, source: str = "list"):
    task = db.get_task(task_id)
    if not task:
        return "❌ Задача не найдена", None

    comments = db.get_comments(task_id)
    e = PRIORITY_EMOJI.get(task["priority"], "🟡")
    pname = PRIORITY_NAMES.get(task["priority"], "Обычно")
    status = STATUS_LABELS.get(task["status"], task["status"])
    cat = CATEGORY_DISPLAY.get(task["project"], escape_md(task.get("project", "?")))

    lines = [
        f"📋 *Карточка задачи*\n",
        f"{e} {escape_md(task['content'])}",
        f"*Статус:* {status}",
        f"*Приоритет:* {pname}",
        f"*Категория:* {cat}",
    ]
    if task.get("due"):
        lines.append(f"*Срок:* {escape_md(task['due'])}")

    if comments:
        lines.append("\n💬 *Комментарии:*")
        for c in comments:
            dt = ""
            if c.get("created_at"):
                try:
                    dt = c["created_at"].strftime("%d.%m %H:%M")
                except Exception:
                    dt = str(c["created_at"])[:16]
            lines.append(f"• [{dt}] {escape_md(c['text'])}")

    rows = []
    if task["status"] != "done":
        rows.append([InlineKeyboardButton("✅ Выполнить", callback_data=f"done_{task_id}_{source}")])
    rows.append([InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment_{task_id}_{source}")])
    rows.append([InlineKeyboardButton("← Назад к списку", callback_data=f"back_{source}")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def handle_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    task_id = int(parts[1])
    source = parts[2] if len(parts) > 2 else "list"
    text, markup = _card_text_and_markup(task_id, source=source)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Выполнить задачу ──────────────────────────────────────
async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    task_id = int(parts[1])
    source = parts[2] if len(parts) > 2 else "list"

    _pending_comment[query.from_user.id] = {"action": "done", "task_id": task_id, "source": source}

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Пропустить", callback_data=f"nocomment_done_{task_id}_{source}")
    ]])
    task = db.get_task(task_id)
    name = escape_md(task["content"][:50]) if task else f"#{task_id}"
    await query.message.reply_text(
        f"💬 Комментарий к выполнению «{name}»\n_или нажми Пропустить_",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Пропустить комментарий ────────────────────────────────
async def handle_no_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # nocomment_done_{task_id}_{source}
    parts = query.data.split("_", 3)
    task_id = int(parts[2])
    source = parts[3] if len(parts) > 3 else "list"

    _pending_comment.pop(query.from_user.id, None)
    db.mark_done(task_id)

    text, markup = _card_text_and_markup(task_id, source=source)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Добавить комментарий к карточке ──────────────────────
async def handle_add_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    task_id = int(parts[1])
    source = parts[2] if len(parts) > 2 else "list"

    _pending_comment[query.from_user.id] = {"action": "card", "task_id": task_id, "source": source}

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Отмена", callback_data=f"card_{task_id}_{source}")
    ]])
    await query.edit_message_text("💬 *Напиши комментарий:*", parse_mode="Markdown", reply_markup=keyboard)


# ── Назад к списку ────────────────────────────────────────
async def handle_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source = query.data.replace("back_", "")
    tasks = db.get_active_tasks()

    if not tasks:
        await query.edit_message_text("Нет активных задач 🎉")
        return

    if source == "priority":
        text, markup = build_by_priority(tasks)
    elif source == "project":
        text, markup = build_by_project(tasks)
    else:
        text, markup = build_task_list(tasks)

    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Кнопки списка ─────────────────────────────────────────
async def btn_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = db.get_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_task_list(tasks)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def btn_by_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = db.get_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_by_priority(tasks)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def btn_by_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = db.get_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_by_project(tasks)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Прямой текст ─────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id

    if chat_id in _pending_comment:
        pending = _pending_comment.pop(chat_id)
        action = pending["action"]
        task_id = pending["task_id"]
        source = pending.get("source", "list")

        db.add_comment(task_id, "Влад", text)

        if action == "done":
            db.mark_done(task_id)

        card_text, card_markup = _card_text_and_markup(task_id, source=source)
        await update.message.reply_text("✅ Готово!")
        await update.message.reply_text(card_text, parse_mode="Markdown", reply_markup=card_markup)
        return

    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = parse_due_date(text)
    await update.message.reply_text(
        f"📝 *{escape_md(text)}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Голос ─────────────────────────────────────────────────
def _voice_confirm_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Создать задачу", callback_data="voice_confirm"),
        InlineKeyboardButton("✏️ Изменить", callback_data="voice_edit"),
    ]])


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎙 Расшифровываю...")
    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name
    try:
        text = transcribe_voice(tmp_path)
        os.unlink(tmp_path)
        await msg.edit_text("🤖 Определяю приоритет и категорию...")
        meta = detect_task_meta(text)
        priority = meta.get("priority", 2)
        category = meta.get("category", "VLAD BYKOV")
        due = parse_due_date(text)

        context.user_data["pending_task"] = text
        context.user_data["pending_due"] = due
        context.user_data["pending_priority"] = priority
        context.user_data["pending_project"] = category

        e = PRIORITY_EMOJI[priority]
        pname = PRIORITY_NAMES[priority]
        cname = CATEGORY_DISPLAY.get(category, category)
        due_line = f"\n📅 {due}" if due else ""

        await msg.edit_text(
            f"🎙 *{escape_md(text)}*\n\n{e} {pname}  •  {cname}{due_line}\n\nСоздать задачу?",
            parse_mode="Markdown",
            reply_markup=_voice_confirm_keyboard()
        )
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await msg.edit_text("❌ Не удалось расшифровать голосовое.")


async def handle_voice_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("pending_task", "")
    priority = context.user_data.get("pending_priority", 2)
    category = context.user_data.get("pending_project", "VLAD BYKOV")
    due = context.user_data.get("pending_due")

    db.create_task(content=text, priority=priority, project=category, sender="Влад", due=due)

    e = PRIORITY_EMOJI[priority]
    cname = CATEGORY_DISPLAY.get(category, category)
    due_text = f" (срок: {due})" if due else ""
    await query.edit_message_text(f"✅ Задача создана!\n{e} {text}{due_text}\n{cname}")
    await query.message.reply_text("Готово!", reply_markup=KEYBOARD)
    context.user_data.clear()


async def handle_voice_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("pending_task", "")
    await query.edit_message_text(
        f"📝 *{escape_md(text)}*\n\nВыбери приоритет вручную:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Фото ──────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    context.user_data["pending_task"] = caption
    context.user_data["pending_due"] = parse_due_date(caption)
    await update.message.reply_text(
        f"📎 *{escape_md(caption)}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Отчёты ────────────────────────────────────────────────
async def send_morning_report(context: ContextTypes.DEFAULT_TYPE):
    vlad_id = db.get_setting("vlad_id")
    if vlad_id:
        await context.bot.send_message(chat_id=int(vlad_id), text=build_morning_report(), parse_mode="Markdown")


async def send_evening_report(context: ContextTypes.DEFAULT_TYPE):
    vlad_id = db.get_setting("vlad_id")
    if vlad_id:
        await context.bot.send_message(chat_id=int(vlad_id), text=build_evening_report(), parse_mode="Markdown")


# ── Запуск ────────────────────────────────────────────────
async def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить задачу$"), btn_add_task)],
        states={ADDING_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_task_text)]},
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Список задач$"), btn_task_list))
    app.add_handler(MessageHandler(filters.Regex("^📊 По приоритету$"), btn_by_priority))
    app.add_handler(MessageHandler(filters.Regex("^🗂 По проектам$"), btn_by_project))
    app.add_handler(CallbackQueryHandler(handle_voice_confirm, pattern="^voice_confirm$"))
    app.add_handler(CallbackQueryHandler(handle_voice_edit, pattern="^voice_edit$"))
    app.add_handler(CallbackQueryHandler(handle_priority_callback, pattern="^priority_"))
    app.add_handler(CallbackQueryHandler(handle_category_callback, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_done_callback, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(handle_no_comment_callback, pattern="^nocomment_"))
    app.add_handler(CallbackQueryHandler(handle_card_callback, pattern="^card_"))
    app.add_handler(CallbackQueryHandler(handle_add_comment_callback, pattern="^comment_"))
    app.add_handler(CallbackQueryHandler(handle_back_callback, pattern="^back_"))
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
