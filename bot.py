import asyncio
import sqlite3
import logging
import os
import datetime
import aiohttp

from aiogram import Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandStart
from aiogram.client.bot import Bot, DefaultBotProperties
# Вставь свой действующий токен бота:
BOT_TOKEN = "7914894994:AAF1ZN721rA3xDBgGjEUYWeniSjvn7jaINk"
# Задай свой user_id (ID админа):
ADMIN_ID = 1754012821

# HH API (Россия)
API_URL = "https://api.hh.ru/vacancies"

# === ЛОГИ ===
logging.basicConfig(
    level=logging.INFO,
    filename='errors.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
# В aiogram 3.7+ parse_mode не передаётся напрямую, а через default=DefaultBotProperties(...)
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Режим рассылки для админа (чтобы админ мог включить/выключить рассылку)
broadcast_mode = set()

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            region_id INTEGER,
            date_joined TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def add_user(user):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, region_id, date_joined)
        VALUES (?, ?, ?, ?, COALESCE((SELECT region_id FROM users WHERE user_id = ?), NULL), ?)
    """, (
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        user.id,
        datetime.datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_user_region(user_id: int):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT region_id FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def update_user_region(user_id: int, region_id: int):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET region_id = ? WHERE user_id = ?", (region_id, user_id))
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    result = cursor.fetchall()
    conn.close()
    return [r[0] for r in result]

def count_users():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    (res,) = cursor.fetchone()
    conn.close()
    return res

# === РЕГИОНЫ (по area_id для hh.ru) ===
regions = {
    1: "Москва",
    2: "Санкт-Петербург",
    66: "Краснодар",
    73: "Новосибирск",
    88: "Екатеринбург",
    104: "Казань",
    112: "Нижний Новгород",
    113: "Самара",
    120: "Челябинск"
}

# === КАТЕГОРИИ ВАКАНСИЙ (по ключевому слову) ===
categories = [
    "Водитель 🚗",
    "Продавец 🛍",
    "Курьер 📦",
    "Уборщик 🧹",
    "Программист 💻",
    "Репетитор 👨‍🏫",
    "Строитель 👷"
]

# === КНОПКИ ===
def main_menu():
    """
    Главное меню бота
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск вакансий", callback_data="search")],
        [InlineKeyboardButton(text="📂 Категории", callback_data="categories")],
        [InlineKeyboardButton(text="🌍 Изменить регион", callback_data="change_region")]
    ])

def region_keyboard():
    """
    Клавиатура с выбором региона (10 городов, ID для hh.ru)
    """
    builder = InlineKeyboardBuilder()
    for r_id, name in regions.items():
        builder.button(text=name, callback_data=f"region_{r_id}")
    # adjust(2) → по 2 кнопки в строке
    builder.adjust(2)
    return builder.as_markup()

def category_keyboard():
    """
    Клавиатура с выбором категории
    """
    builder = InlineKeyboardBuilder()
    for cat in categories:
        # Ключевое слово возьмём до пробела (напр. 'Водитель')
        keyword = cat.split()[0]
        builder.button(text=cat, callback_data=f"category_{keyword}")
    builder.adjust(2)
    return builder.as_markup()

def admin_menu():
    """
    Админ-панель
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
    ])

# === ФУНКЦИИ ДЛЯ ПОИСКА ВАКАНСИЙ ===
async def fetch_vacancies(area_id: int, keyword: str = ""):
    """
    Запрос к hh.ru для получения вакансий.
    area_id — ID региона (Москва=1, СПб=2 и т.д.)
    keyword — ключевое слово (например, 'курьер')
    Возвращаем первые 5 вакансий
    """
    params = {"text": keyword, "area": area_id, "per_page": 5}
    logging.info(f"📤 Отправлен запрос: {params}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, params=params) as resp:
                if resp.status != 200:
                    logging.error(f"HH API ошибка: статус {resp.status}")
                    return []
                data = await resp.json()
                items = data.get("items", [])
                logging.info(f"📥 Ответ: найдено {len(items)} вакансий")
                return items
    except Exception as e:
        logging.error(f"Ошибка при получении вакансий: {e}")
        return []

async def send_vacancies(message: Message, region_id: int, keyword: str):
    """
    Получаем список вакансий fetch_vacancies и отправляем пользователю
    """
    vacancies = await fetch_vacancies(region_id, keyword)
    if not vacancies:
        await message.answer("😕 По вашему запросу вакансии не найдены.")
        return

    for vac in vacancies:
        name = vac.get("name")
        url = vac.get("alternate_url")
        employer = vac.get("employer", {}).get("name", "")
        salary = vac.get("salary")
        if salary:
            # Соберём зарплату аккуратно
            salary_from = salary.get("from")
            salary_to = salary.get("to")
            currency = salary.get("currency", "RUR")
            if salary_from and salary_to:
                salary_text = f"{salary_from} - {salary_to} {currency}"
            elif salary_from:
                salary_text = f"от {salary_from} {currency}"
            elif salary_to:
                salary_text = f"до {salary_to} {currency}"
            else:
                salary_text = "не указана"
        else:
            salary_text = "не указана"

        text = f"<b>{name}</b>\n"
        if employer:
            text += f"Компания: {employer}\n"
        text += f"Зарплата: {salary_text}\n"
        text += f"<a href='{url}'>Подробнее</a>"

        await message.answer(text)

# === ХЕНДЛЕРЫ ===

@dp.message(CommandStart())
async def start_cmd(message: Message):
    """
    /start — приветствие, добавляем юзера в базу, показываем главное меню
    """
    add_user(message.from_user)
    logging.info(f"/start от {message.from_user.id} ({message.from_user.username})")
    await message.answer(
        "👋 Добро пожаловать в бот <b>Работа Сегодня</b>!\n"
        "Выберите действие или введите ключевое слово для поиска:",
        reply_markup=main_menu()
    )

@dp.message(Command("menu"))
async def menu_cmd(message: Message):
    """
    /menu — показать главное меню
    """
    await message.answer("📋 Главное меню:", reply_markup=main_menu())

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    """
    /admin — показать админ-панель (только для ADMIN_ID)
    """
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа.")
        return
    await message.answer("👑 Админ-панель:", reply_markup=admin_menu())

@dp.callback_query(F.data == "change_region")
async def change_region(callback: CallbackQuery):
    """
    Кнопка «Изменить регион»
    """
    await callback.message.answer("🌍 Выберите ваш регион:", reply_markup=region_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("region_"))
async def set_region(callback: CallbackQuery):
    """
    Выбор региона, запись в базу
    """
    region_id = int(callback.data.split("_")[1])
    update_user_region(callback.from_user.id, region_id)
    await callback.message.answer(
        f"✅ Регион установлен: <b>{regions[region_id]}</b>\n"
        "Введите ключевое слово для поиска вакансий (например: водитель)"
    )
    await callback.answer()

@dp.callback_query(F.data == "search")
async def prompt_search(callback: CallbackQuery):
    """
    Кнопка «Поиск вакансий» — просим ввести ключевое слово
    """
    await callback.message.answer("🔎 Введите ключевое слово для поиска вакансий:")
    await callback.answer()

@dp.callback_query(F.data == "categories")
async def show_categories(callback: CallbackQuery):
    """
    Кнопка «Категории» — показываем список популярных категорий
    """
    await callback.message.answer("📂 Выберите категорию:", reply_markup=category_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("category_"))
async def handle_category(callback: CallbackQuery):
    """
    Пользователь выбрал категорию, берём keyword до пробела (например, 'Водитель')
    """
    keyword = callback.data.split("_")[1]
    region_id = get_user_region(callback.from_user.id)
    if not region_id:
        await callback.message.answer("❗ Сначала выберите регион: /menu → «Изменить регион»")
        await callback.answer()
        return
    await callback.message.answer(f"🔎 Поиск по категории: <b>{keyword}</b>")
    await send_vacancies(callback.message, region_id, keyword)
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """
    Кнопка «Назад» в админ-панели
    """
    await callback.message.answer("📋 Главное меню:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data == "broadcast")
async def ask_broadcast(callback: CallbackQuery):
    """
    Кнопка «Сделать рассылку» в админ-панели
    """
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа")
        return
    broadcast_mode.add(callback.from_user.id)
    await callback.message.answer("✉️ Отправьте текст, фото или видео для рассылки всем пользователям (или /cancel для отмены).")
    await callback.answer()

@dp.callback_query(F.data == "stats")
async def send_stats(callback: CallbackQuery):
    """
    Кнопка «Статистика» — общее число пользователей
    """
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа")
        return
    c = count_users()
    await callback.message.answer(f"📊 Всего пользователей: <b>{c}</b>")
    await callback.answer()

@dp.message(F.text == "/cancel")
async def cancel_broadcast(message: Message):
    """
    Если админ передумал делать рассылку
    """
    if message.from_user.id in broadcast_mode:
        broadcast_mode.remove(message.from_user.id)
        await message.answer("🚫 Режим рассылки отменён.")
    else:
        await message.answer("Нет активной рассылки.")

@dp.message()
async def handle_message(message: Message):
    """
    Универсальный хендлер на все остальные сообщения:
    1. Если админ в режиме рассылки → делаем рассылку
    2. Иначе считаем, что пользователь ввёл ключевое слово для поиска
    """
    # Если админ в режиме рассылки
    if message.from_user.id in broadcast_mode:
        user_ids = get_all_user_ids()
        count = 0
        for uid in user_ids:
            try:
                if message.photo:
                    await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption or "")
                elif message.video:
                    await bot.send_video(uid, message.video.file_id, caption=message.caption or "")
                else:
                    await bot.send_message(uid, message.text)
                count += 1
            except Exception as e:
                logging.error(f"Ошибка при рассылке пользователю {uid}: {e}")
        broadcast_mode.remove(message.from_user.id)
        await message.answer(f"✅ Рассылка завершена. Отправлено: {count}")
        return

    # Иначе (обычный пользователь, или админ не в режиме рассылки) — поиск вакансий
    keyword = message.text.strip()
    region_id = get_user_region(message.from_user.id)

    if not region_id:
        await message.answer("❗ Сначала выберите регион: /menu → «Изменить регион»")
        return

    # Ищем вакансии
    await send_vacancies(message, region_id, keyword)

# === ЗАПУСК БОТА ===
async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
