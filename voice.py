import os
import re
import json
import tempfile
from datetime import datetime, timedelta
from config import OPENAI_API_KEY


def _get_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def transcribe_voice(file_path: str) -> str:
    client = _get_client()
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru"
        )
    return transcript.text


def detect_task_meta(text: str) -> dict:
    """Возвращает {'priority': 1-4, 'category': 'VLAD BYKOV'|'Контент'|'Restoria'|'Личное'}"""
    client = _get_client()
    prompt = f"""Ты ассистент модного дизайнера Влада Быкова. Проанализируй задачу и определи:

1. Приоритет (одно число):
   4 = Срочно (горит, нужно сегодня)
   3 = Важно (важно, но не горит)
   2 = Обычно (стандартная задача)
   1 = Условно (когда-нибудь, не срочно)

2. Категорию — выбери ОДНУ из списка ниже (пиши точно как в кавычках):
   "Модный дом"          — бренд VLAD BYKOV, клиенты, заказы, примерки, коллекции, показы, производство, поставщики, ателье
   "RESTORIA"            — магазин Restoria, Dubai, команда магазина, продажи, маркетинг магазина
   "Идеи"                — общие идеи, концепции, планы без конкретной платформы
   "Идеи Instagram"      — идеи для постов, reels, сторис в Instagram
   "Идеи TikTok"         — идеи для видео, трендов, контента в TikTok
   "Идеи YouTube"        — идеи для видео, влогов, роликов на YouTube
   "Коллаборации"        — партнёрства, совместные проекты (без конкретного бренда)
   "Коллаборация Jetex"  — любые задачи по коллаборации с Jetex
   "Коллаборация Venuum" — любые задачи по коллаборации с Venuum

Задача: "{text}"

Ответь строго в JSON: {{"priority": <число>, "category": "<категория>"}}"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # убираем markdown-блок если есть
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return {"priority": 2, "category": "VLAD BYKOV"}


def shorten_task(full_text: str) -> str:
    """Extract a concise task title (5-8 words) from a full voice transcription."""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты извлекаешь короткое название задачи из голосовой заметки. "
                        "Правила: максимум 7 слов, только глагол + суть, без лишних деталей. "
                        "Пример: 'Нужно сегодня позвонить Марине по поводу примерки платья на пятницу' → 'Позвонить Марине по примерке'. "
                        "Никогда не копируй текст полностью. Отвечай ТОЛЬКО названием задачи, без кавычек."
                    )
                },
                {"role": "user", "content": full_text}
            ],
            max_tokens=40,
            temperature=0,
        )
        result = resp.choices[0].message.content.strip().strip('"').strip("'")
        # sanity check: if GPT returned almost the full text, truncate hard
        if len(result) > len(full_text) * 0.7 and len(result) > 60:
            words = full_text.split()[:7]
            return " ".join(words)
        return result
    except Exception:
        words = full_text.split()[:7]
        return " ".join(words)


_MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

_WEEKDAYS_RU = {
    "в понедельник": 0, "во вторник": 1, "в среду": 2,
    "в четверг": 3, "в пятницу": 4, "в субботу": 5, "в воскресенье": 6,
}


def parse_due_date(text: str) -> str | None:
    """Parse date from Russian text. Returns ISO 'YYYY-MM-DD' or None."""
    lower = text.lower()
    today = datetime.now()

    # Relative keywords
    if "сегодня" in lower:
        return today.strftime("%Y-%m-%d")
    if "послезавтра" in lower:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if "завтра" in lower:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # Weekdays: "в пятницу"
    for phrase, wd in _WEEKDAYS_RU.items():
        if phrase in lower:
            days_ahead = (wd - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Specific date: "15 июня", "3 июля 2026"
    m = re.search(r'(\d{1,2})\s+(' + '|'.join(_MONTHS_RU.keys()) + r')(?:\s+(\d{4}))?', lower)
    if m:
        day = int(m.group(1))
        month = _MONTHS_RU[m.group(2)]
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = datetime(year, month, day)
            if d.date() < today.date():
                d = d.replace(year=d.year + 1)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


_MONTHS_FULL = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]

# Legacy English strings from old DB records
_LEGACY_MAP = {
    "today": 0, "tomorrow": 1, "in 2 days": 2,
}


def format_due_date(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' (or legacy English) to readable Russian string."""
    today = datetime.now()

    # Handle legacy English strings stored before migration
    if iso_date in _LEGACY_MAP:
        d = today + timedelta(days=_LEGACY_MAP[iso_date])
        if _LEGACY_MAP[iso_date] == 0:
            return "сегодня"
        if _LEGACY_MAP[iso_date] == 1:
            return "завтра"
        return f"{d.day} {_MONTHS_FULL[d.month]}"
    if iso_date and iso_date.startswith("next "):
        return iso_date  # "next monday" etc — rare, just show as-is

    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        if d.date() == today.date():
            return "сегодня"
        if d.date() == (today + timedelta(days=1)).date():
            return "завтра"
        if d.year == today.year:
            return f"{d.day} {_MONTHS_FULL[d.month]}"
        return f"{d.day} {_MONTHS_FULL[d.month]} {d.year}"
    except Exception:
        return iso_date


_TIME_WORDS = {
    "ноль": 0, "один": 1, "одну": 1, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "двадцать один": 21, "двадцать два": 22, "двадцать три": 23,
}


def parse_due_time(text: str) -> str | None:
    """Parse specific time from Russian text. Returns 'HH:MM' string or None."""
    lower = text.lower()

    # "в 15:30", "в 15:00", "в 15 часов" — digits
    m = re.search(r'(?<!\d)в\s+(\d{1,2})(?::(\d{2}))?(?:\s+час|\b)', lower)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return f"{h:02d}:{mins:02d}"

    if "в полдень" in lower:
        return "12:00"
    if "в полночь" in lower:
        return "00:00"

    # "в пятнадцать часов", "в три часа" — words
    for word, h in _TIME_WORDS.items():
        if re.search(r'в\s+' + word + r'\s+час', lower):
            return f"{h:02d}:00"

    # "через полчаса"
    if re.search(r'через\s+пол\s*часа', lower):
        t = datetime.now() + timedelta(minutes=30)
        return t.strftime("%H:%M")

    # "через час"
    if re.search(r'через\s+час', lower):
        t = datetime.now() + timedelta(hours=1)
        return t.strftime("%H:%M")

    # "через N часов/часа" — digits
    m = re.search(r'через\s+(\d+)\s+час', lower)
    if m:
        t = datetime.now() + timedelta(hours=int(m.group(1)))
        return t.strftime("%H:%M")

    # "через два/три часа" — words
    for word, n in _TIME_WORDS.items():
        if n >= 2 and re.search(r'через\s+' + word + r'\s+час', lower):
            t = datetime.now() + timedelta(hours=n)
            return t.strftime("%H:%M")

    return None


def detect_voice_intent(text: str, active_tasks: list) -> dict:
    """
    Detect intent from voice transcription.
    Returns:
      {"intent": "new_task"}
      {"intent": "mark_done", "task_id": int | None}
      {"intent": "add_comment", "task_id": int | None, "comment": str}
    """
    client = _get_client()
    tasks_json = json.dumps(
        [{"id": t["id"], "content": t["content"]} for t in active_tasks[:30]],
        ensure_ascii=False
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты анализируешь голосовую команду для системы задач дизайнера Влада.\n"
                        "Определи намерение:\n"
                        "- 'new_task': создать новую задачу (нет упоминания существующей)\n"
                        "- 'mark_done': задача выполнена/готова/сделана\n"
                        "- 'add_comment': добавить комментарий/заметку/обновление к задаче\n\n"
                        "Если mark_done или add_comment — найди task_id из списка задач по смыслу.\n"
                        "Ответь строго в JSON:\n"
                        '{"intent": "...", "task_id": <число или null>, "comment": "<текст или null>"}'
                    )
                },
                {
                    "role": "user",
                    "content": f"Список задач:\n{tasks_json}\n\nГолосовая команда: {text}"
                }
            ],
            max_tokens=80,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        intent = result.get("intent", "new_task")
        if intent not in ("new_task", "mark_done", "add_comment"):
            intent = "new_task"
        return {
            "intent": intent,
            "task_id": result.get("task_id"),
            "comment": result.get("comment"),
        }
    except Exception:
        return {"intent": "new_task", "task_id": None, "comment": None}
