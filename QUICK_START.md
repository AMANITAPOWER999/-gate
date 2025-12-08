# Goldantelopegate v2.0 - Quick Start

## Скачанные файлы содержат:

✅ Полный исходный код бота для Gate.io  
✅ Flask Dashboard с real-time мониторингом  
✅ Telegram интеграция и уведомления  
✅ Конфигурация для Railway deployment  
✅ Dockerfile для контейнеризации  

## Быстрый старт локально:

```bash
# 1. Распаковать архив
unzip goldantelopegate_railway.zip
# или
tar -xzf goldantelopegate_railway.tar.gz

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env файл (скопировать из .env.example)
cp .env.example .env
# Отредактировать .env с вашими ключами:
# - GATE_API_KEY
# - GATE_API_SECRET
# - TELEGRAM_BOT_TOKEN
# - TELEGRAM_CHAT_ID

# 4. Запустить
python app.py

# 5. Открыть в браузере
# http://localhost:5000
```

## Развертывание на Railway:

1. Создайте аккаунт на [railway.app](https://railway.app)
2. Создайте новый проект
3. Подключите GitHub репозиторий
4. Railway автоматически обнаружит Dockerfile
5. Добавьте переменные окружения в Railway Dashboard
6. Готово! Приложение развернется автоматически

**Подробнее:** Читайте `RAILWAY_DEPLOYMENT.md`

## Структура проекта:

```
├── app.py                      # Flask приложение (главный файл)
├── trading_bot.py              # Логика SAR торговли
├── telegram_notifications.py   # Telegram интеграция
├── signal_sender.py            # Webhook сигналы
├── market_simulator.py         # Симулятор для тестов
│
├── templates/
│   ├── dashboard.html          # Web Dashboard
│   ├── webapp.html             # Telegram WebApp
│   └── login.html              # Страница входа
│
├── static/
│   ├── css/dashboard.css       # Стили
│   ├── js/dashboard.js         # JavaScript логика
│   └── images/                 # Изображения
│
├── Dockerfile                  # Docker конфигурация
├── Procfile                    # Railway/Heroku конфиг
├── railway.json               # Railway специфичные настройки
├── requirements.txt           # Python зависимости
└── .env.example              # Пример переменных окружения
```

## Основные команды:

```bash
# Запуск локально с Gunicorn (как на Railway)
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Запуск в Docker локально
docker build -t goldantelopegate .
docker run -p 5000:5000 --env-file .env goldantelopegate

# Установить новые зависимости
pip install package_name
pip freeze > requirements.txt
```

## Важные переменные окружения:

**Обязательные для торговли:**
- `GATE_API_KEY` - от gate.io
- `GATE_API_SECRET` - от gate.io
- `TELEGRAM_BOT_TOKEN` - от @BotFather
- `TELEGRAM_CHAT_ID` - ваш chat ID

**По умолчанию:**
- `RUN_IN_PAPER=1` (бумажная торговля с $100)
- `DASHBOARD_PASSWORD=admin`

## Возможности:

🤖 **Автоматическая торговля** - SAR стратегия на 5m/30m  
📊 **Dashboard** - Real-time график с SAR маркерами  
💬 **Telegram** - Уведомления о сделках  
⚙️ **Гибкая конфигурация** - Выбирайте стратегию через UI  
🔄 **Paper Trading** - Начните с $100 виртуального баланса  
💰 **Реальная торговля** - Подключите Gate.io API  

## Поддержка:

- GitHub: [manuninkirill-bot/tradingbot](https://github.com/manuninkirill-bot/tradingbot)
- Telegram: [@goldantelopegate_bot](https://t.me/goldantelopegate_bot)
- Issues: Создавайте issue в GitHub репозитории

## Лицензия: MIT

**Версия:** v2.11 (December 6, 2025)
