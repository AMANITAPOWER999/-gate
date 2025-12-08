# Railway Deployment Guide

## Быстрый старт

### 1. Создать проект на Railway
1. Перейдите на [railway.app](https://railway.app)
2. Нажмите "New Project" → "Deploy from GitHub"
3. Выберите репозиторий `manuninkirill-bot/tradingbot`
4. Railway автоматически обнаружит Dockerfile

### 2. Добавить переменные окружения
В Railway Dashboard → Project Settings → Variables:

**ОБЯЗАТЕЛЬНЫЕ:**
```
GATE_API_KEY=<ваш API ключ от Gate.io>
GATE_API_SECRET=<ваш API секрет от Gate.io>
TELEGRAM_BOT_TOKEN=<токен бота от @BotFather>
TELEGRAM_CHAT_ID=<ваш Telegram Chat ID>
```

**ОПЦИОНАЛЬНЫЕ:**
```
RUN_IN_PAPER=1                    # 1=бумажная торговля, 0=реальная
SESSION_SECRET=<любая строка>     # для защиты сессий
DASHBOARD_PASSWORD=admin           # пароль для доступа
```

### 3. Развернуть
Railway автоматически развернет приложение при push-е в GitHub.

### 4. Получить URL
В Railway Dashboard → Deployments → найдите `Domains`

Доступ к боту: `https://your-project.railway.app/`

## Структура файлов для Railway

```
├── Dockerfile           ✅ Python 3.11 образ
├── Procfile            ✅ Для процесса web
├── railway.json        ✅ Конфигурация Railway
├── requirements.txt    ✅ Зависимости Python
├── app.py              ✅ Flask приложение
├── trading_bot.py      ✅ Торговая логика
├── .gitignore          ✅ Исключает .env и состояние
└── .env.example        ✅ Пример переменных
```

## Troubleshooting

### Приложение не стартует
1. Проверьте Environment Variables в Railway Dashboard
2. Проверьте логи: Railway Dashboard → Deployments → Logs
3. Убедитесь что Dockerfile существует

### API ошибки Gate.io
- Проверьте что API ключи верные
- Убедитесь что на аккаунте достаточно баланса для тестирования
- В paper mode используется виртуальный $100 баланс

### Telegram уведомления не приходят
- Проверьте TELEGRAM_BOT_TOKEN в Railway
- Проверьте TELEGRAM_CHAT_ID
- Убедитесь что бот подписан на ваш chat

## Сохранение состояния

Состояние торговли хранится в файле `goldantelopegate_v1.0_state.json`.
Railway использует ephemeral storage - файл будет потерян при рестарте!

**Для постоянного хранилища:**
1. Используйте Railway Postgres Database
2. Или подключите S3 хранилище
3. Или используйте Railway Volumes

## Scale & Performance

- **Workers**: Gunicorn запускается с 4 workers
- **Timeout**: 30 сек на healthcheck
- **Memory**: ~256MB для Python приложения
- **CPU**: Базовый Railway tier достаточно

## Безопасность

✅ API ключи хранятся в Railway Secrets
✅ Используется HTTPS для всех запросов
✅ Пароль для dashboard защищает доступ
✅ Session secret защищает cookies

**НЕ коммитьте:**
- `.env` файл
- API ключи
- Telegram токены
- Приватные данные
