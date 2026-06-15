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
from users import get_vlad_id, get_victoria_id, save_vlad_id, save_victoria_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪"}
PRIORITY_NAMES = {4: "Срочно", 3: "Важно", 2: "Обычно", 1: "Условно"}

ADDING_TASK = 1

MENU_BUTTONS = {
    "📋 Список задач", "📊 По приоритету", "🗂 По проектам",
    "Ожидают ответ", "🔔 Уведомления включены", "🔕 Уведомления отключены",
    "➕ Добавить задачу",
}

notifications_on: set[int] = set()

VLAD_HELP = (
    "\n\n─────────────────\n"
    "📌 *Команды:*\n"
    "➕ Добавить задачу — кнопка или просто напиши текст\n"
    "🎙 Голосовое — автоматически станет задачей\n"
    "📋 Список задач — все активные задачи\n"
    "Ожидают ответ — задачи, которые Виктория ждёт от тебя"
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
        [KeyboardButton("Ожидают ответ")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Или напиши задачу текстом..."
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

CATEGORY_EMOJI = {
    "VLAD BYKOV": "👔",
    "Контент": "🎬",
    "Restoria": "🏪",
    "Личное": "👤",
}

CATEGORY_DISPLAY = {
    "VLAD BYKOV": "👔 Клиенты",
    "Контент": "🎬 Контент",
    "Restoria": "🏪 Restoria",
    "Личное": "👤 Личное",
}

STATUS_LABELS = {
    "active": "Активна",
    "waiting": "Ожидает ответа",
    "done": "Выполнена",
}


def _victoria_keyboard(chat_id: int) -> ReplyKeyboardMarkup:
    notif_btn = "🔔 Уведомления включены" if chat_id in notifications_on else "🔕 Уведомления отключены"
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Добавить задачу"), KeyboardButton("📋 Список задач")],
            [KeyboardButton("📊 По приоритету"), KeyboardButton("🗂 По проектам")],
            [KeyboardButton(notif_btn)],
        ],
        resize_keyboard=True
    )


def priority_emoji(task):
    return PRIORITY_EMOJI.get(task.get("priority", 2), "🟡")


def escape_md(text: str) -> str:
    for ch in ['\\', '*', '_', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


def is_vlad(update: Update) -> bool:
    return update.effective_chat.id == get_vlad_id()


def is_victoria(update: Update) -> bool:
    return update.effective_chat.id == get_victoria_id()


async def notify_victoria(bot, task_content: str, priority: int, due: str | None, sender: str = "Влад"):
    victoria_id = get_victoria_id()
    if not victoria_id:
        return
    if victoria_id not in notifications_on:
        return
    due_text = f"\n📅 Срок: {due}" if due else ""
    e = PRIORITY_EMOJI.get(priority, "🟡")
    name = PRIORITY_NAMES.get(priority, "Обычно")
    await bot.send_message(
        chat_id=victoria_id,
        text=f"📌 *{sender} добавил задачу:*\n\n{e} [{name}] {escape_md(task_content)}{due_text}",
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
        "✅ *Система активирована, Влад!*" + VLAD_HELP,
        parse_mode="Markdown",
        reply_markup=VLAD_KEYBOARD
    )


async def start_victoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    save_victoria_id(chat_id)
    await update.message.reply_text(
        "✅ *Привет, Виктория!*" + VICTORIA_HELP,
        parse_mode="Markdown",
        reply_markup=_victoria_keyboard(update.effective_chat.id)
    )


# ── Добавление задачи (кнопка) ───────────────────────────
async def btn_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Напиши задачу:", reply_markup=CANCEL_KEYBOARD)
    return ADDING_TASK


async def receive_task_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена" or text in MENU_BUTTONS:
        keyboard = _victoria_keyboard(update.effective_chat.id) if is_victoria(update) else VLAD_KEYBOARD
        await update.message.reply_text("Отменено.", reply_markup=keyboard)
        return ConversationHandler.END

    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = parse_due_date(text)
    context.user_data["pending_sender"] = "Виктория" if is_victoria(update) else "Влад"
    context.user_data["pending_chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        f"📝 *{escape_md(text)}*\n\nВыбери приоритет:",
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

    await query.edit_message_text(
        f"📝 *{escape_md(text)}*\n{e} {name}\n\nВыбери категорию:",
        parse_mode="Markdown",
        reply_markup=CATEGORY_KEYBOARD
    )


# ── Выбор категории (callback) ───────────────────────────
async def handle_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    project_name = query.data.replace("cat_", "")
    context.user_data["pending_project"] = project_name

    text = context.user_data.get("pending_task", "")
    priority = context.user_data.get("pending_priority", 2)
    sender = context.user_data.get("pending_sender", "Влад")
    due = context.user_data.get("pending_due")

    e = PRIORITY_EMOJI[priority]
    cat_e = CATEGORY_EMOJI.get(project_name, "📁")
    display_name = "Клиенты" if project_name == "VLAD BYKOV" else project_name

    db.create_task(content=text, priority=priority, project=project_name, sender=sender, due=due)
    due_text = f" (срок: {due})" if due else ""
    await query.edit_message_text(f"✅ Задача добавлена!\n{e} {text}{due_text}\n{cat_e} {display_name}")

    if sender == "Влад":
        await query.message.reply_text("Что дальше?" + VLAD_HELP, parse_mode="Markdown", reply_markup=VLAD_KEYBOARD)
        await notify_victoria(query.get_bot(), text, priority, due, sender="Влад")
    else:
        await query.message.reply_text("Что дальше?" + VICTORIA_HELP, parse_mode="Markdown", reply_markup=_victoria_keyboard(query.from_user.id))
        await notify_victoria(query.get_bot(), text, priority, due, sender="Виктория")
        await notify_vlad(query.get_bot(), f"📌 *Виктория добавила задачу:*\n\n{e} {escape_md(text)}{due_text}\n{cat_e} {display_name}")
    context.user_data.clear()


# ── Построение списка задач ───────────────────────────────
def _build_keyboard(tasks_with_index, source: str) -> InlineKeyboardMarkup | None:
    """Card buttons only, 2 per row. Source encoded so back button returns to correct list."""
    card_buttons = [
        InlineKeyboardButton(f"💬 {i}. {t['content'][:22]}", callback_data=f"card_{t['id']}_{source}")
        for i, t in tasks_with_index
    ]
    keyboard = [card_buttons[j:j + 2] for j in range(0, len(card_buttons), 2)]
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def build_by_priority(tasks, victoria_view: bool = False):
    sorted_tasks = sorted(tasks, key=lambda t: -t.get("priority", 1))[:20]

    lines = ["📊 *По приоритету:*\n"]
    indexed = []
    for i, t in enumerate(sorted_tasks, 1):
        e = priority_emoji(t)
        waiting = " ⏳" if t.get("status") == "waiting" else ""
        lines.append(f"{i}. {e} {escape_md(t['content'])}{waiting}")
        indexed.append((i, t))

    markup = _build_keyboard(indexed, source="priority")
    return "\n".join(lines), markup


def build_by_project(tasks, victoria_view: bool = False):
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
            waiting = " ⏳" if t.get("status") == "waiting" else ""
            lines.append(f"{counter}. {e} {escape_md(t['content'])}{waiting}")
            indexed.append((counter, t))
            counter += 1

    markup = _build_keyboard(indexed, source="project")
    return "\n".join(lines), markup


# ── Карточка задачи ───────────────────────────────────────
def _card_text_and_markup(task_id: int, victoria_view: bool = False, source: str = "priority"):
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
        f"*Добавил:* {escape_md(task['sender'])}",
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
            lines.append(f"• _{escape_md(c['author'])} [{dt}]:_ {escape_md(c['text'])}")

    rows = []
    if victoria_view:
        if task["status"] != "done":
            action_row = []
            if task["status"] != "waiting":
                action_row.append(InlineKeyboardButton("⏳ Жду ответа", callback_data=f"wait_{task_id}"))
            action_row.append(InlineKeyboardButton("✅ Выполнить", callback_data=f"done_{task_id}"))
            rows.append(action_row)
        rows.append([InlineKeyboardButton("💬 Добавить комментарий / процесс", callback_data=f"comment_{task_id}")])
    rows.append([InlineKeyboardButton("← Назад к списку", callback_data=f"back_{source}")])

    keyboard = InlineKeyboardMarkup(rows)
    return "\n".join(lines), keyboard


async def handle_back_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # data: back_priority / back_project / back_list / back_waiting
    source = query.data.replace("back_", "")
    victoria_view = query.from_user.id == get_victoria_id()

    tasks = db.get_active_tasks()

    if source == "waiting":
        waiting = db.get_waiting_tasks()
        if not waiting:
            await query.edit_message_text("⏳ Нет задач, ожидающих ответа.")
            return
        lines = ["⏳ *Ожидают ответа:*\n"]
        indexed = []
        for i, t in enumerate(sorted(waiting, key=lambda t: -t.get("priority", 1)), 1):
            e = priority_emoji(t)
            due_text = f" — {t['due']}" if t.get("due") else ""
            lines.append(f"{i}. {e} {escape_md(t['content'])}{due_text}")
            indexed.append((i, t))
        text = "\n".join(lines)
        markup = _build_keyboard(indexed, source="waiting")
    elif source == "project":
        if not tasks:
            await query.edit_message_text("Нет активных задач 🎉")
            return
        text, markup = build_by_project(tasks, victoria_view=victoria_view)
    elif source == "list":
        if not tasks:
            await query.edit_message_text("Нет активных задач 🎉")
            return
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
                waiting = " ⏳" if t.get("status") == "waiting" else ""
                lines.append(f"{counter}. {e} {escape_md(t['content'])}{waiting}")
                indexed.append((counter, t))
                counter += 1
        text = "\n".join(lines)
        markup = _build_keyboard(indexed, source="list")
    else:  # priority (default)
        if not tasks:
            await query.edit_message_text("Нет активных задач 🎉")
            return
        text, markup = build_by_priority(tasks, victoria_view=victoria_view)

    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


async def handle_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # data: card_{id}_{source}
    parts = query.data.split("_", 2)  # ["card", "123", "priority"]
    task_id = int(parts[1])
    source = parts[2] if len(parts) > 2 else "priority"
    victoria_view = query.from_user.id == get_victoria_id()
    text, markup = _card_text_and_markup(task_id, victoria_view=victoria_view, source=source)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


async def handle_add_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("comment_", ""))

    context.user_data["pending_comment_action"] = "card"
    context.user_data["pending_comment_task_id"] = task_id

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Отмена", callback_data=f"card_{task_id}")
    ]])
    await query.edit_message_text(
        "💬 *Напиши комментарий к задаче:*",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Жду ответа — спросить комментарий ────────────────────
async def handle_wait_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.replace("wait_", ""))
    task = db.get_task(task_id)
    name = escape_md(task["content"][:50]) if task else f"#{task_id}"

    context.user_data["pending_comment_action"] = "wait"
    context.user_data["pending_comment_task_id"] = task_id

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Пропустить", callback_data=f"nocomment_wait_{task_id}")
    ]])
    await query.message.reply_text(
        f"💬 Комментарий к «{name}»\n_или нажми Пропустить_",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Выполнено — спросить комментарий ─────────────────────
async def handle_complete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.replace("done_", ""))
    task = db.get_task(task_id)
    name = escape_md(task["content"][:50]) if task else f"#{task_id}"

    context.user_data["pending_comment_action"] = "done"
    context.user_data["pending_comment_task_id"] = task_id

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Пропустить", callback_data=f"nocomment_done_{task_id}")
    ]])
    await query.message.reply_text(
        f"💬 Комментарий к выполнению «{name}»\n_или нажми Пропустить_",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Пропустить комментарий ────────────────────────────────
async def handle_no_comment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # data: nocomment_wait_123 or nocomment_done_123
    parts = query.data.split("_", 2)  # ["nocomment", "wait", "123"]
    action = parts[1]
    task_id = int(parts[2])

    context.user_data.pop("pending_comment_action", None)
    context.user_data.pop("pending_comment_task_id", None)

    author = "Виктория" if query.from_user.id == get_victoria_id() else "Влад"
    await _execute_task_action(query.get_bot(), action, task_id, comment=None, author=author)
    await query.edit_message_text("✅ Готово!")


# ── Выполнить действие (wait/done) ────────────────────────
async def _execute_task_action(bot, action: str, task_id: int, comment: str | None, author: str):
    task = db.get_task(task_id)
    name = escape_md(task["content"][:60]) if task else f"#{task_id}"

    if action == "wait":
        db.mark_waiting(task_id)
        msg = f"⏳ *Виктория ждёт ответа:*\n\n«{name}»"
        if comment:
            msg += f"\n\n💬 _{escape_md(comment)}_"
        msg += "\n\nНажми «Ожидают ответ» чтобы посмотреть список"
        await notify_vlad(bot, msg)

    elif action == "done":
        db.mark_done(task_id)
        msg = f"✅ *Виктория выполнила задачу:*\n\n«{name}»"
        if comment:
            msg += f"\n\n💬 _{escape_md(comment)}_"
        await notify_vlad(bot, msg)


# ── Кнопки списка задач ───────────────────────────────────
async def btn_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tasks = db.get_active_tasks()
        logger.info(f"Tasks fetched: {len(tasks)}")

        if not tasks:
            await update.message.reply_text("Нет активных задач 🎉")
            return

        if is_victoria(update):
            text, markup = build_by_priority(tasks, victoria_view=True)
        else:
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
                    waiting = " ⏳" if t.get("status") == "waiting" else ""
                    lines.append(f"{counter}. {e} {escape_md(t['content'])}{waiting}")
                    indexed.append((counter, t))
                    counter += 1
            text = "\n".join(lines)
            markup = _build_keyboard(indexed, source="list")

        if len(text) > 4000:
            text = text[:4000] + "\n..."
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        logger.error(f"btn_task_list error: {e}")
        await update.message.reply_text(f"❌ Ошибка загрузки задач: {e}")


async def btn_view_by_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = db.get_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_by_priority(tasks, victoria_view=True)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def btn_view_by_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = db.get_active_tasks()
    if not tasks:
        await update.message.reply_text("Нет активных задач 🎉")
        return
    text, markup = build_by_project(tasks, victoria_view=True)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


# ── Ожидают ответ (Влад) — без кнопки закрытия ───────────
async def btn_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    waiting = db.get_waiting_tasks()
    logger.info(f"btn_waiting: waiting tasks={len(waiting)}")

    if not waiting:
        await update.message.reply_text("⏳ Нет задач, ожидающих ответа.")
        return

    lines = ["⏳ *Ожидают ответа:*\n"]
    indexed = []
    for i, t in enumerate(sorted(waiting, key=lambda t: -t.get("priority", 1)), 1):
        e = priority_emoji(t)
        due_text = f" — {t['due']}" if t.get("due") else ""
        lines.append(f"{i}. {e} {escape_md(t['content'])}{due_text}")
        indexed.append((i, t))

    markup = _build_keyboard(indexed, source="waiting")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=markup)


# ── Прямой текст ─────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Обработка ввода комментария
    if context.user_data.get("pending_comment_action"):
        action = context.user_data.pop("pending_comment_action")
        task_id = context.user_data.pop("pending_comment_task_id")
        author = "Виктория" if is_victoria(update) else "Влад"

        if action == "card":
            db.add_comment(task_id, author, text)
            card_text, card_markup = _card_text_and_markup(task_id)
            await update.message.reply_text("✅ Комментарий добавлен!")
            await update.message.reply_text(card_text, parse_mode="Markdown", reply_markup=card_markup)
        else:
            db.add_comment(task_id, author, text)
            await _execute_task_action(context.bot, action, task_id, comment=text, author=author)
            await update.message.reply_text("✅ Готово!")
        return

    due = parse_due_date(text)
    context.user_data["pending_task"] = text
    context.user_data["pending_due"] = due
    context.user_data["pending_sender"] = "Виктория" if is_victoria(update) else "Влад"
    context.user_data["pending_chat_id"] = update.effective_chat.id

    await update.message.reply_text(
        f"📝 *{escape_md(text)}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Голос ────────────────────────────────────────────────
def _voice_confirm_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Создать задачу", callback_data="voice_confirm"),
            InlineKeyboardButton("✏️ Изменить", callback_data="voice_edit"),
        ]
    ])


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
        context.user_data["pending_sender"] = "Виктория" if is_victoria(update) else "Влад"
        context.user_data["pending_chat_id"] = update.effective_chat.id

        e = PRIORITY_EMOJI[priority]
        pname = PRIORITY_NAMES[priority]
        cname = CATEGORY_DISPLAY.get(category, category)
        due_line = f"\n📅 {due}" if due else ""

        await msg.edit_text(
            f"🎙 *{escape_md(text)}*\n\n"
            f"{e} {pname}  •  {cname}{due_line}\n\n"
            f"Создать задачу?",
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
    sender = context.user_data.get("pending_sender", "Влад")

    db.create_task(content=text, priority=priority, project=category, sender=sender, due=due)

    e = PRIORITY_EMOJI[priority]
    cname = CATEGORY_DISPLAY.get(category, category)
    due_text = f" (срок: {due})" if due else ""
    await query.edit_message_text(f"✅ Задача создана!\n{e} {text}{due_text}\n{cname}")

    if sender == "Влад":
        await query.message.reply_text("Что дальше?" + VLAD_HELP, parse_mode="Markdown", reply_markup=VLAD_KEYBOARD)
        await notify_victoria(query.get_bot(), text, priority, due, sender="Влад")
    else:
        await query.message.reply_text("Что дальше?" + VICTORIA_HELP, parse_mode="Markdown", reply_markup=_victoria_keyboard(query.from_user.id))
        await notify_vlad(query.get_bot(), f"📌 *Виктория добавила задачу:*\n\n{e} {escape_md(text)}{due_text}\n{cname}")
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


# ── Фото ─────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Фото без описания"
    due = parse_due_date(caption)

    context.user_data["pending_task"] = caption
    context.user_data["pending_due"] = due
    context.user_data["pending_sender"] = "Виктория" if is_victoria(update) else "Влад"

    await update.message.reply_text(
        f"📎 *{escape_md(caption)}*\n\nВыбери приоритет:",
        parse_mode="Markdown",
        reply_markup=PRIORITY_KEYBOARD
    )


# ── Уведомления — toggle ─────────────────────────────────
async def btn_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in notifications_on:
        notifications_on.discard(chat_id)
        text = "🔕 Уведомления *отключены*. Новые задачи от Влада приходить не будут."
    else:
        notifications_on.add(chat_id)
        text = "🔔 Уведомления *включены*. Ты будешь получать сообщения о каждой новой задаче."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_victoria_keyboard(chat_id))


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
    db.init_db()

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
    app.add_handler(MessageHandler(filters.Regex("^Ожидают ответ$"), btn_waiting))
    app.add_handler(MessageHandler(
        filters.Regex("^(🔔 Уведомления включены|🔕 Уведомления отключены)$"),
        btn_notifications
    ))
    app.add_handler(CallbackQueryHandler(handle_voice_confirm, pattern="^voice_confirm$"))
    app.add_handler(CallbackQueryHandler(handle_voice_edit, pattern="^voice_edit$"))
    app.add_handler(CallbackQueryHandler(handle_priority_callback, pattern="^priority_"))
    app.add_handler(CallbackQueryHandler(handle_category_callback, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_wait_done_callback, pattern="^wait_"))
    app.add_handler(CallbackQueryHandler(handle_complete_callback, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(handle_no_comment_callback, pattern="^nocomment_"))
    app.add_handler(CallbackQueryHandler(handle_back_list_callback, pattern="^back_"))
    app.add_handler(CallbackQueryHandler(handle_card_callback, pattern="^card_"))
    app.add_handler(CallbackQueryHandler(handle_add_comment_callback, pattern="^comment_"))
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
                reply_markup=_victoria_keyboard(victoria_id)
            )
        except Exception as e:
            logger.error(f"Broadcast to Victoria failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
