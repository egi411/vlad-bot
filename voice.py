import os
import json
import tempfile
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

2. Категорию (одно из):
   "VLAD BYKOV" — клиенты, заказы, примерки, показы, коллекции, производство, поставщики
   "Контент" — Instagram, YouTube, съёмки, контент, PR, коллаборации
   "Restoria" — магазин Restoria, Dubai, маркетинг магазина, команда магазина
   "Личное" — здоровье, спорт, путешествия, личные дела

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


def parse_due_date(text: str) -> str | None:
    """Простой парсинг даты из текста задачи."""
    keywords = {
        "сегодня": "today",
        "завтра": "tomorrow",
        "послезавтра": "in 2 days",
        "в понедельник": "next monday",
        "во вторник": "next tuesday",
        "в среду": "next wednesday",
        "в четверг": "next thursday",
        "в пятницу": "next friday",
    }
    lower = text.lower()
    for ru, en in keywords.items():
        if ru in lower:
            return en
    return None
