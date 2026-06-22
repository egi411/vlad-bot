from datetime import datetime
import db
from voice import format_due_date


def _esc(text: str) -> str:
    for ch in ['\\', '*', '_', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


def build_morning_report() -> str:
    tasks = db.get_active_tasks()
    today = datetime.now().strftime("%Y-%m-%d")

    urgent = [t for t in tasks if t.get("priority") == 4]
    overdue = [t for t in tasks if t.get("due") and t["due"] < today]

    top5 = sorted(tasks, key=lambda t: -t.get("priority", 1))[:5]

    lines = ["☀️ *Доброе утро, Влад!*\n"]
    lines.append(f"🔴 Срочных задач: *{len(urgent)}*")
    lines.append(f"⚠️ Просроченных: *{len(overdue)}*\n")
    lines.append("📋 *ТОП-5 задач на сегодня:*")

    for i, t in enumerate(top5, 1):
        due_label = f" — {format_due_date(t['due'])}" if t.get("due") else ""
        lines.append(f"{i}. {_esc(t['content'])}{due_label}")

    if not top5:
        lines.append("Нет задач на сегодня 🎉")

    return "\n".join(lines)


def build_evening_report() -> str:
    completed = db.get_completed_today()
    active = db.get_active_tasks()
    today = datetime.now().strftime("%Y-%m-%d")

    overdue = [t for t in active if t.get("due") and t["due"] < today]

    lines = ["🌙 *Итоги дня*\n"]

    lines.append("✅ *Выполнено:*")
    if completed:
        for t in completed[:10]:
            lines.append(f"• {_esc(t.get('content', ''))}")
    else:
        lines.append("• —")

    lines.append(f"\n📋 *Активных задач:* {len(active)}")

    lines.append("\n🔴 *Просрочено:*")
    if overdue:
        for t in overdue[:5]:
            due_label = f" ({format_due_date(t['due'])})" if t.get("due") else ""
            lines.append(f"• {_esc(t['content'])}{due_label}")
    else:
        lines.append("• —")

    return "\n".join(lines)
