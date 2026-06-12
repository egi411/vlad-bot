import os
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
