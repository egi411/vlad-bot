from datetime import datetime
from todoist_client import get_tasks_today, get_all_active_tasks, get_completed_today


def _esc(text: str) -> str:
    for ch in ['\\', '*', '_', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


def build_morning_report() -> str:
    tasks = get_tasks_today()
    today = datetime.now().strftime("%Y-%m-%d")

    urgent = [t for t in tasks if "🔴_Срочно" in t.get("labels", [])]
    overdue = [t for t in tasks if t.get("due") and t["due"].get("date", "") < today]

    top5 = sorted(tasks, key=lambda t: (
        0 if "🔴_Срочно" in t.get("labels", []) else 1
    ))[:5]

    lines = ["☀️ *Доброе утро, Влад!*\n"]
    lines.append(f"🔴 Срочных задач: *{len(urgent)}*")
    lines.append(f"⚠️ Просроченных: *{len(overdue)}*\n")
    lines.append("📋 *ТОП-5 задач на сегодня:*")

    for i, t in enumerate(top5, 1):
        lines.append(f"{i}. {_esc(t['content'])}")

    if not top5:
        lines.append("Нет задач на сегодня 🎉")

    return "\n".join(lines)


def build_evening_report() -> str:
    completed = get_completed_today()
    active = get_all_active_tasks()
    today = datetime.now().strftime("%Y-%m-%d")

    waiting = [t for t in active if "zhdu_otveta" in t.get("labels", [])]
    overdue = [t for t in active if t.get("due") and t["due"].get("date", "") < today]
    in_progress = [t for t in active if "🟡_В_работе" in t.get("labels", [])]

    lines = ["🌙 *Итоги дня*\n"]

    lines.append("✅ *Выполнено:*")
    if completed:
        for t in completed[:10]:
            lines.append(f"• {_esc(t.get('content', ''))}")
    else:
        lines.append("• —")

    lines.append("\n📂 *Открыто:*")
    if in_progress:
        for t in in_progress[:5]:
            lines.append(f"• {_esc(t['content'])}")
    else:
        lines.append("• —")

    lines.append("\n⏳ *Ожидаем ответ:*")
    if waiting:
        for t in waiting[:5]:
            lines.append(f"• {_esc(t['content'])}")
    else:
        lines.append("• —")

    lines.append("\n🔴 *Просрочено:*")
    if overdue:
        for t in overdue[:5]:
            lines.append(f"• {_esc(t['content'])}")
    else:
        lines.append("• —")

    return "\n".join(lines)
