import os
import json
import logging

logger = logging.getLogger(__name__)
DATA_FILE = "/tmp/vlad_bot_users.json"


def _load() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"users _save error: {e}")


def get_vlad_id() -> int | None:
    val = os.environ.get("VLAD_CHAT_ID", "").strip()
    if val:
        return int(val)
    return _load().get("vlad_id")


def get_victoria_id() -> int | None:
    val = os.environ.get("VICTORIA_CHAT_ID", "").strip()
    if val:
        return int(val)
    return _load().get("victoria_id")


def save_vlad_id(chat_id: int):
    data = _load()
    data["vlad_id"] = chat_id
    _save(data)
    logger.info(f"vlad_id saved: {chat_id}")


def save_victoria_id(chat_id: int):
    data = _load()
    data["victoria_id"] = chat_id
    _save(data)
    logger.info(f"victoria_id saved: {chat_id}")
