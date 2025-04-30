<<<<<<< HEAD
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

=======
# Используем официальный Python образ
FROM python:3.11

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта в контейнер
COPY . .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем бота
>>>>>>> 502dceb42f3546d7e4c3396564a8f15542f4d8ce
CMD ["python", "bot.py"]
