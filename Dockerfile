# Базовый образ Python 3.11 для контейнера с ботами.
FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей проекта.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копирование исходников приложения в контейнер.
COPY . ./

# По умолчанию запускаем основной бот.
# Для запуска поддержки используйте: python support.py
CMD ["python", "via.py"]