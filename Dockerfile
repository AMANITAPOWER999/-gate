FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway указывает порт через $PORT, а не руками!
EXPOSE $PORT

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:$PORT", "app:app"]
