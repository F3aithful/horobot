# Используем официальный образ Python 3.11
FROM python:3.11-slim

# Устанавливаем системные зависимости и обновляем pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app

# Копируем и устанавливаем зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код бота
COPY . .

# Команда для запуска бота
CMD ["python", "bot.py"]
