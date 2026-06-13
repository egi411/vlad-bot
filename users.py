import os

def get_vlad_id():
    val = os.environ.get("VLAD_CHAT_ID", "").strip()
    return int(val) if val else None

def get_victoria_id():
    val = os.environ.get("VICTORIA_CHAT_ID", "").strip()
    return int(val) if val else None

def save_vlad_id(chat_id: int):
    # На Railway переменные задаются вручную — просто логируем
    import logging
    logging.getLogger(__name__).info(f"VLAD_CHAT_ID={chat_id} — добавь в Railway Variables")

def save_victoria_id(chat_id: int):
    import logging
    logging.getLogger(__name__).info(f"VICTORIA_CHAT_ID={chat_id} — добавь в Railway Variables")
