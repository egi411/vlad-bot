"""Запустить один раз — создаёт структуру проектов в Todoist."""
from todoist_client import setup_structure

if __name__ == "__main__":
    print("Создаю структуру проектов в Todoist...")
    setup_structure()
    print("Готово!")
