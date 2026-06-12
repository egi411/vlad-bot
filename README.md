# VB Assistant — Telegram Bot

## Установка на сервере

```bash
# 1. Клонировать / загрузить файлы на сервер
# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать структуру проектов в Todoist (один раз)
python setup_todoist.py

# 4. Запустить бота
python bot.py
```

## Запуск как сервис (чтобы не падал)

```bash
# Создать systemd-сервис
sudo nano /etc/systemd/system/vladbot.service
```

Содержимое файла:
```
[Unit]
Description=VB Assistant Telegram Bot
After=network.target

[Service]
WorkingDirectory=/path/to/vlad-bot
ExecStart=/usr/bin/python3 /path/to/vlad-bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable vladbot
sudo systemctl start vladbot
```

## Первый запуск

1. Открыть Telegram, найти бота по имени
2. Отправить /start — система запомнит chat_id Влада
3. Отчёты будут приходить в 09:00 и 20:00 по Москве
