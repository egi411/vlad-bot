import os

VLAD_ID_FILE = "vlad_chat_id.txt"
VICTORIA_ID_FILE = "victoria_chat_id.txt"


def _get_id(filepath):
    if os.path.exists(filepath):
        val = open(filepath).read().strip()
        return int(val) if val else None
    return None


def _save_id(filepath, chat_id):
    with open(filepath, "w") as f:
        f.write(str(chat_id))


def get_vlad_id():
    return _get_id(VLAD_ID_FILE)


def get_victoria_id():
    return _get_id(VICTORIA_ID_FILE)


def save_vlad_id(chat_id):
    _save_id(VLAD_ID_FILE, chat_id)


def save_victoria_id(chat_id):
    _save_id(VICTORIA_ID_FILE, chat_id)
