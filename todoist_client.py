import requests
import logging
from datetime import datetime
from config import TODOIST_TOKEN

logger = logging.getLogger(__name__)

BASE_URL = "https://api.todoist.com/api/v1"
HEADERS = {"Authorization": f"Bearer {TODOIST_TOKEN}"}

PROJECTS = {
    "VLAD BYKOV": {
        "sections": ["Клиенты", "Производство", "Поставщики", "Показы", "Новая коллекция"]
    },
    "Контент": {
        "sections": ["Instagram", "YouTube", "Коллаборации", "PR"]
    },
    "Restoria": {
        "sections": ["Dubai", "Москва", "Маркетинг", "Команда"]
    },
    "Личное": {
        "sections": ["Здоровье", "Спорт", "Путешествия"]
    },
}


def get_projects():
    r = requests.get(f"{BASE_URL}/projects", headers=HEADERS)
    r.raise_for_status()
    return r.json()["results"]


def create_project(name):
    r = requests.post(f"{BASE_URL}/projects", headers=HEADERS, json={"name": name})
    r.raise_for_status()
    return r.json()


def create_section(project_id, name):
    r = requests.post(f"{BASE_URL}/sections", headers=HEADERS, json={"project_id": project_id, "name": name})
    r.raise_for_status()
    return r.json()


def get_sections(project_id):
    r = requests.get(f"{BASE_URL}/sections", headers=HEADERS, params={"project_id": project_id})
    r.raise_for_status()
    return r.json().get("results", [])


def setup_structure():
    existing = {p["name"]: p["id"] for p in get_projects()}
    project_ids = {}

    for project_name, config in PROJECTS.items():
        if project_name not in existing:
            p = create_project(project_name)
            project_ids[project_name] = p["id"]
            print(f"Создан проект: {project_name}")
        else:
            project_ids[project_name] = existing[project_name]
            print(f"Проект уже есть: {project_name}")

        existing_sections = {s["name"] for s in get_sections(project_ids[project_name])}
        for section_name in config["sections"]:
            if section_name not in existing_sections:
                create_section(project_ids[project_name], section_name)
                print(f"  Секция: {section_name}")
            else:
                print(f"  Секция уже есть: {section_name}")

    return project_ids


def create_task(content, project_name="VLAD BYKOV", due_string=None, priority=None, labels=None):
    projects = {p["name"]: p["id"] for p in get_projects()}
    project_id = projects.get(project_name)

    payload = {"content": content}
    if project_id:
        payload["project_id"] = project_id
    if due_string:
        payload["due_string"] = due_string
        payload["due_lang"] = "ru"
    if priority:
        payload["priority"] = priority  # 4=p1 срочно, 3=p2 важно, 2=p3 обычно, 1=p4 условно
    if labels:
        payload["labels"] = labels

    r = requests.post(f"{BASE_URL}/tasks", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def get_tasks_today():
    r = requests.get(f"{BASE_URL}/tasks", headers=HEADERS, params={"filter": "today | overdue"})
    r.raise_for_status()
    return r.json().get("results", [])


def get_all_active_tasks():
    all_tasks = []
    cursor = None
    try:
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            r = requests.get(f"{BASE_URL}/tasks", headers=HEADERS, params=params)
            logger.info(f"Todoist GET /tasks status={r.status_code}")
            if r.status_code != 200:
                logger.error(f"Todoist error: {r.status_code} {r.text[:300]}")
                break
            data = r.json()
            logger.info(f"Todoist response keys: {list(data.keys())}")
            batch = data.get("results", data.get("items", []))
            logger.info(f"Todoist batch size: {len(batch)}")
            all_tasks.extend(batch)
            cursor = data.get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        logger.error(f"get_all_active_tasks error: {e}")
    logger.info(f"Total tasks fetched: {len(all_tasks)}")
    return all_tasks


def complete_task(task_id: str):
    r = requests.post(f"{BASE_URL}/tasks/{task_id}/close", headers=HEADERS)
    r.raise_for_status()


def add_label_to_task(task_id: str, label: str):
    r = requests.get(f"{BASE_URL}/tasks/{task_id}", headers=HEADERS)
    r.raise_for_status()
    task = r.json()
    labels = task.get("labels", [])
    if label not in labels:
        labels.append(label)
    r2 = requests.post(f"{BASE_URL}/tasks/{task_id}", headers=HEADERS, json={"labels": labels})
    r2.raise_for_status()


def get_completed_today():
    today = datetime.now().strftime("%Y-%m-%d")
    r = requests.get(
        "https://api.todoist.com/sync/v9/items/completed/get_all",
        headers=HEADERS,
        params={"since": f"{today}T00:00:00Z", "limit": 50}
    )
    if r.status_code == 200:
        return r.json().get("items", [])
    return []
