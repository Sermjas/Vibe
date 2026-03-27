# 1. Base Image: Берем официальный образ Python
FROM python:3.11-slim

# 2. Working Directory: Создаем папку внутри контейнера, где будет лежать код
WORKDIR /app

# 3. Dependencies: Копируем список библиотек и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy Code: Копируем все остальные файлы проекта в контейнер
COPY . .

# 5. Entry Point: Команда, которая запустит бота при старте контейнера
CMD ["python", "bot.py"]