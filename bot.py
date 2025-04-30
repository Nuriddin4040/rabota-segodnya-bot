# bot.py — BazarBlitzBot v2.4-full  (корзина + админка + RU/UZ)
# =============================================================
#   • Категории / товары / корзина (+ / – / ✔️)
#   • Оформление заказа, уведомление админу
#   • /admin  → статистика + 📢 рассылка
#   • /settings → выбор языка интерфейса (RU / UZ)
#   • База: SQLite через aiosqlite (users, orders)
# -------------------------------------------------------------
#   Требования: aiogram==3.7.0  aiosqlite>=0.19  python-dotenv>=1.0
# =============================================================

import asyncio, os, json
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv

# ---------- CONFIG ----------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "bazarblitz.db"

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID  = int(os.getenv("ADMIN_ID", "0"))

DEFAULT_LANG = "ru"
LANGS = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 Oʻzbekcha"}

T = {  # минимальный набор переводимых строк
    "start": {
        "ru": "<b>БазарБлиц</b> 🌟\nВыберите категорию:",
        "uz": "<b>BazarBlitz</b> 🌟\nTurkumni tanlang:",
    },
    "choose_cat": {
        "ru": "Выберите категорию:",
        "uz": "Turkumni tanlang:",
    },
    "cart_empty": {"ru": "Корзина пуста.", "uz": "Savat bo'sh."},
    "cart_head":  {"ru": "🛒 <b>Корзина</b>\n", "uz": "🛒 <b>Savat</b>\n"},
    "total":      {"ru": "Итого", "uz": "Jami"},
    "choose_time": {
        "ru": "Выберите время доставки:",
        "uz": "Yetkazib berish vaqtini tanlang:",
    },
    "ask_address": {
        "ru": "Пришлите адрес доставки.",
        "uz": "Yetkazib berish manzilini yuboring.",
    },
    "thanks": {
        "ru": "Спасибо! Заказ принят ✔️",
        "uz": "Rahmat! Buyurtma qabul qilindi ✔️",
    },
    "settings": {
        "ru": "Выберите язык интерфейса:",
        "uz": "Tildni tanlang:",
    },
}

# ---------- DATA ----------
CATEGORIES = {
    "veg": {"title": "🥦 Овощи", "items": {
        "potato":  {"title": {"ru": "🥔 Картофель 1 кг", "uz": "🥔 Kartoshka 1 kg"},
                    "price": 9000,  "photo": "https://i.imgur.com/0Q2w8sd.jpg"},
        "onion":   {"title": {"ru": "🧅 Лук 1 кг",       "uz": "🧅 Piyoz 1 kg"},
                    "price": 6000,  "photo": "https://i.imgur.com/SUgx7Hz.jpg"},
        "tomato":  {"title": {"ru": "🍅 Помидоры 1 кг",  "uz": "🍅 Pomidor 1 kg"},
                    "price": 14000, "photo": "https://i.imgur.com/yQW2CsN.jpg"},
    }},
    "fruit": {"title": "🍊 Фрукты", "items": {
        "banana": {"title": {"ru": "🍌 Бананы 1 кг", "uz": "🍌 Banan 1 kg"},
                   "price": 18000, "photo": "https://i.imgur.com/YQ2fY2y.jpg"},
        "apple":  {"title": {"ru": "🍏 Яблоки 1 кг", "uz": "🍏 Olma 1 kg"},
                   "price": 13000, "photo": "https://i.imgur.com/AebcMus.jpg"},
    }},
}

ITEMS = {code: item | {"cat": cat}
         for cat, cdata in CATEGORIES.items()
         for code, item in cdata["items"].items()}

# ---------- CART (in-memory) ----------
CART: dict[int, dict[str, int]] = {}

# ---------- FSM ----------
class Checkout(StatesGroup):
    choose_time = State()
    enter_addr  = State()

class AdminFSM(StatesGroup):
    broadcast = State()

# ---------- DB helpers ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                lang       TEXT DEFAULT 'ru',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                items_json TEXT,
                total INTEGER,
                delivery_window TEXT,
                address TEXT,
                created_at TEXT
            );
            """
        )
        await db.commit()

async def add_user(uid:int, username:str|None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?,?,?)",
            (uid, username, datetime.utcnow().isoformat())
        )
        await db.commit()

async def set_lang(uid:int, lang:str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, uid))
        await db.commit()

async def get_lang(uid:int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT lang FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    return row[0] if row else DEFAULT_LANG

async def save_order(uid:int, items, total, window, addr):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(user_id,items_json,total,delivery_window,address,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uid, json.dumps(items), total, window, addr, datetime.utcnow().isoformat())
        )
        await db.commit()

async def db_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        users = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM orders")
        orders = (await cur.fetchone())[0]
    return users, orders

async def all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
    return [r[0] for r in rows]

# ---------- i18n helpers ----------
def tr(key:str, lang:str) -> str:
    return T[key].get(lang, T[key][DEFAULT_LANG])

def item_title(code:str, lang:str) -> str:
    return ITEMS[code]["title"].get(lang, ITEMS[code]["title"][DEFAULT_LANG])

# ---------- keyboards ----------
def kb_categories(lang:str):
    kb = InlineKeyboardBuilder()
    for cat_code, cat in CATEGORIES.items():
        kb.button(text=cat["title"], callback_data=f"cat_{cat_code}")
    kb.button(text="🛒" + (" Корзина" if lang=="ru" else " Savat"),
              callback_data="show_cart")
    kb.adjust(2)
    return kb.as_markup()

def kb_items(cat:str, lang:str):
    kb = InlineKeyboardBuilder()
    for code, it in CATEGORIES[cat]["items"].items():
        title = item_title(code, lang)
        kb.button(text=f"{title} — {it['price']:,} сум", callback_data=f"info_{code}")
    kb.button(text="🛒" + (" Корзина" if lang=="ru" else " Savat"), callback_data="show_cart")
    kb.button(text="⬅️ " + ("Назад" if lang=="ru" else "Ortga"), callback_data="back_cats")
    kb.adjust(1)
    return kb.as_markup()

def kb_item(code:str, lang:str):
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 " + ("В корзину" if lang=="ru" else "Savatga"), callback_data=f"add_{code}")
    back_txt = "⬅️ " + ("Товары" if lang=="ru" else "Mahsulotlar")
    kb.button(text=back_txt, callback_data=f"back_items_{ITEMS[code]['cat']}")
    kb.adjust(1)
    return kb.as_markup()

def kb_cart(uid:int, lang:str):
    cart = CART.get(uid, {})
    kb = InlineKeyboardBuilder()
    for code, qty in cart.items():
        kb.button(text="➖", callback_data=f"dec_{code}")
        kb.button(text=f"{item_title(code,lang)} ×{qty}", callback_data="noop")
        kb.button(text="➕", callback_data=f"inc_{code}")
    if cart:
        kb.button(text="✔️ " + ("Оформить" if lang=="ru" else "Buyurtma berish"),
                  callback_data="checkout")
    kb.button(text="⬅️ " + ("Категории" if lang=="ru" else "Turkumlar"), callback_data="back_cats")
    kb.adjust(3)
    return kb.as_markup()

def kb_time(lang:str):
    kb = InlineKeyboardBuilder()
    kb.button(text="11:00-13:00", callback_data="time_11_13")
    kb.button(text="17:00-19:00", callback_data="time_17_19")
    kb.adjust(2)
    return kb.as_markup()

def kb_admin():
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Рассылка", callback_data="admin_broadcast")
    kb.adjust(1)
    return kb.as_markup()

def kb_lang(current:str):
    kb = InlineKeyboardBuilder()
    for code,label in LANGS.items():
        txt = ("✅ " if code==current else "") + label
        kb.button(text=txt, callback_data=f"setlang_{code}")
    kb.adjust(1)
    return kb.as_markup()

# ---------- ROUTER ----------
router = Router()

@router.message(Command("start"))
async def cmd_start(m:types.Message):
    await add_user(m.from_user.id, m.from_user.username)
    lang = await get_lang(m.from_user.id)
    await m.answer(tr("start", lang),
                   reply_markup=kb_categories(lang),
                   parse_mode=ParseMode.HTML)

@router.message(Command("settings"))
async def cmd_settings(m:types.Message):
    lang = await get_lang(m.from_user.id)
    await m.answer(tr("settings", lang),
                   reply_markup=kb_lang(lang),
                   parse_mode=ParseMode.HTML)

@router.callback_query(lambda c:c.data.startswith("setlang_"))
async def cb_setlang(cb:types.CallbackQuery):
    lang = cb.data.split("_",1)[1]
    await set_lang(cb.from_user.id, lang)
    await cb.answer("Язык обновлён!" if lang=="ru" else "Til yangilandi!")
    await cb.message.delete()

# ---------- Навигация категорий / товаров ----------
@router.callback_query(lambda c:c.data.startswith("cat_"))
async def open_cat(cb:types.CallbackQuery):
    cat = cb.data.split("_",1)[1]
    lang = await get_lang(cb.from_user.id)
    await cb.message.edit_text(f"<b>{CATEGORIES[cat]['title']}</b>",
                               reply_markup=kb_items(cat, lang),
                               parse_mode=ParseMode.HTML)
    await cb.answer()

@router.callback_query(lambda c:c.data=="back_cats")
async def back_cats(cb:types.CallbackQuery):
    lang = await get_lang(cb.from_user.id)
    try:
        await cb.message.edit_text(tr("choose_cat", lang),
                                   reply_markup=kb_categories(lang),
                                   parse_mode=ParseMode.HTML)
    except:
        await cb.message.answer(tr("choose_cat", lang),
                                reply_markup=kb_categories(lang),
                                parse_mode=ParseMode.HTML)
    await cb.answer()

@router.callback_query(lambda c:c.data.startswith("back_items_"))
async def back_items(cb:types.CallbackQuery):
    cat = cb.data.split("_",2)[2]
    lang = await get_lang(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=kb_items(cat, lang))
    await cb.answer()

# ---------- Информация о товаре ----------
@router.callback_query(lambda c:c.data.startswith("info_"))
async def info_item(cb:types.CallbackQuery):
    code = cb.data.split("_",1)[1]
    lang = await get_lang(cb.from_user.id)
    it = ITEMS[code]
    await cb.message.answer_photo(
        it["photo"],
        caption=f"<b>{item_title(code,lang)}</b>\nЦена: {it['price']:,} сум",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_item(code, lang))
    await cb.answer()

# ---------- Корзина ----------
@router.callback_query(lambda c:c.data=="show_cart")
async def show_cart(cb:types.CallbackQuery):
    lang = await get_lang(cb.from_user.id)
    await cb.message.answer(cart_text(cb.from_user.id, lang),
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb_cart(cb.from_user.id, lang))
    await cb.answer()

def cart_text(uid:int, lang:str)->str:
    cart = CART.get(uid,{})
    if not cart:
        return tr("cart_empty",lang)
    txt = tr("cart_head",lang); total=0
    for code,q in cart.items():
        sub = ITEMS[code]['price']*q; total+=sub
        txt+=f"• {item_title(code,lang)} ×{q} = {sub:,} сум\n"
    txt+=f"\n{tr('total',lang)}: <b>{total:,} сум</b>"
    return txt

@router.callback_query(lambda c:c.data.startswith("add_"))
async def add_cart(cb:types.CallbackQuery):
    code = cb.data.split("_",1)[1]
    CART.setdefault(cb.from_user.id, {}).setdefault(code,0)
    CART[cb.from_user.id][code]+=1
    await cb.answer("Добавлено ✅")

@router.callback_query(lambda c:c.data.startswith(("inc_","dec_")))
async def qty_change(cb:types.CallbackQuery):
    code = cb.data.split("_",1)[1]
    cart = CART.get(cb.from_user.id,{})
    if code in cart:
        cart[code] += 1 if cb.data.startswith("inc_") else -1
        if cart[code]<=0: cart.pop(code)
    lang = await get_lang(cb.from_user.id)
    await cb.message.edit_text(cart_text(cb.from_user.id,lang),
                               parse_mode=ParseMode.HTML,
                               reply_markup=kb_cart(cb.from_user.id,lang))
    await cb.answer()

@router.message(Command("cart"))
async def cmd_cart(m:types.Message):
    lang = await get_lang(m.from_user.id)
    await m.answer(cart_text(m.from_user.id,lang),
                   parse_mode=ParseMode.HTML,
                   reply_markup=kb_cart(m.from_user.id,lang))

# ---------- Checkout ----------
@router.callback_query(lambda c:c.data=="checkout")
async def checkout(cb:types.CallbackQuery,state:FSMContext):
    if not CART.get(cb.from_user.id):
        await cb.answer("Cart empty"); return
    lang = await get_lang(cb.from_user.id)
    await state.set_state(Checkout.choose_time)
    await cb.message.edit_text(tr("choose_time",lang), reply_markup=kb_time(lang))
    await cb.answer()

@router.callback_query(Checkout.choose_time, lambda c:c.data.startswith("time_"))
async def choose_time(cb:types.CallbackQuery,state:FSMContext):
    window = cb.data.split("_",1)[1].replace("_","-")
    await state.update_data(window=window)
    await state.set_state(Checkout.enter_addr)
    lang = await get_lang(cb.from_user.id)
    await cb.message.edit_text(tr("ask_address",lang))
    await cb.answer()

@router.message(Checkout.enter_addr)
async def finish_order(m:types.Message,state:FSMContext,bot:Bot):
    data = await state.get_data(); lang = await get_lang(m.from_user.id)
    cart = CART.pop(m.from_user.id,{})
    total = sum(ITEMS[c]['price']*q for c,q in cart.items())
    await save_order(m.from_user.id,cart,total,data['window'],m.text.strip())
    if ADMIN_ID:
        lines = "\n".join(f"• {item_title(c,'ru')} ×{q}" for c,q in cart.items())
        await bot.send_message(ADMIN_ID,
            f"🆕 Заказ @{m.from_user.username or m.from_user.id}\n{lines}\n"
            f"Итого: {total:,} сум\nОкно: {data['window']}\nАдрес: {m.text.strip()}")
    await m.answer(tr("thanks",lang))
    await state.clear()

# ---------- Админ ----------
@router.message(Command("admin"))
async def admin_cmd(m:types.Message):
    if m.from_user.id!=ADMIN_ID: return
    users,orders = await db_stats()
    await m.answer(f"👑 <b>Админ-панель</b>\nПользователей: {users}\nЗаказов: {orders}",
                   parse_mode=ParseMode.HTML,
                   reply_markup=kb_admin())

@router.callback_query(lambda c:c.data=="admin_broadcast")
async def ask_broadcast(cb:types.CallbackQuery,state:FSMContext):
    if cb.from_user.id!=ADMIN_ID: return
    await cb.message.answer("Введите текст рассылки:")
    await state.set_state(AdminFSM.broadcast); await cb.answer()

@router.message(AdminFSM.broadcast)
async def do_broadcast(m:types.Message,state:FSMContext,bot:Bot):
    if m.from_user.id!=ADMIN_ID: return
    ids = await all_user_ids(); ok=fail=0
    for uid in ids:
        try: await bot.send_message(uid,m.text,parse_mode=ParseMode.HTML); ok+=1
        except: fail+=1
    await m.answer(f"📢 Рассылка завершена. Успешно: {ok}, ошибок: {fail}.")
    await state.clear()

# ---------- MAIN ----------
async def main():
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(); dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("BazarBlitzBot started…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
