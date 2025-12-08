# Инструкция по загрузке на GitHub и развертыванию на Railway

## Шаг 1: Загрузить на GitHub

Выполните эти команды в терминале:

```bash
cd /home/runner/workspace

git remote add origin https://github.com/supermashaandbear/Goldantelopegate_v2.0.git
git branch -M main
git push -u origin main
```

## Шаг 2: Переменные окружения Railway

Добавить в Railway Settings → Variables:
- `GATE_API_KEY` - API ключ Gate.io
- `GATE_API_SECRET` - API секрет Gate.io  
- `TELEGRAM_BOT_TOKEN` - Токен бота
- `TELEGRAM_CHAT_ID` - Chat ID
- `RUN_IN_PAPER` = `0` (для реальной торговли)

## Шаг 3: Запуск

Railway автоматически найдет Dockerfile и развернет проект.

URL будет как: `https://your-project.railway.app`
