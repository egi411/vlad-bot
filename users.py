import os
import logging

logger = logging.getLogger(__name__)


def get_vlad_id() -> int | None:
    val = os.environ.get("VLAD_CHAT_ID", "").strip()
    if val:
        return int(val)
    try:
        import db
        stored = db.get_setting("vlad_id")
        return int(stored) if stored else None
    except Exception:
        return None


def get_victoria_id() -> int | None:
    val = os.environ.get("VICTORIA_CHAT_ID", "").strip()
    if val:
        return int(val)
    try:
        import db
        stored = db.get_setting("victoria_id")
        return int(stored) if stored else None
    except Exception:
        return None


def save_vlad_id(chat_id: int):
    try:
        import db
        db.set_setting("vlad_id", str(chat_id))
        logger.info(f"vlad_id saved to DB: {chat_id}")
    except Exception as e:
        logger.error(f"save_vlad_id error: {e}")


def save_victoria_id(chat_id: int):
    try:
        import db
        db.set_setting("victoria_id", str(chat_id))
        logger.info(f"victoria_id saved to DB: {chat_id}")
    except Exception as e:
        logger.error(f"save_victoria_id error: {e}")
