import os
import asyncio
import logging
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from gigachat import Gigachat

# ====================== ТОКЕНЫ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ======================
TOKEN = os.getenv("TELEGRAM_TOKEN")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
DB_NAME = "users.db"

# Проверка что переменные установлены
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан в переменных окружения!")
if not GIGACHAT_CREDENTIALS:
    raise ValueError("GIGACHAT_CREDENTIALS не задан в переменных окружения!")

# ====================== ЛОГИ ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ====================== КЛАВИАТУРЫ ======================
zodiacs = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
]

zodiac_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=z)] for z in zodiacs],
    resize_keyboard=True,
    one_time_keyboard=True
)

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌟 Получить гороскоп сейчас")],
        [KeyboardButton(text="📚 Режим студента")],
        [KeyboardButton(text="🔄 Сбросить регистрацию")]
    ],
    resize_keyboard=True
)

# ====================== FSM ======================
class Register(StatesGroup):
    zodiac = State()
    birth_time = State()
    birth_place = State()

# ====================== DB FUNCTIONS ======================
@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        yield db

async def init_db():
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                zodiac TEXT NOT NULL,
                birth_time TEXT NOT NULL,
                birth_place TEXT NOT NULL,
                student_mode INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("✅ База данных инициализирована")

async def get_user(user_id: int):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT zodiac, birth_time, birth_place, student_mode FROM users WHERE user_id=?",
            (user_id,)
        )
        return await cursor.fetchone()

async def save_user(user_id: int, zodiac: str, birth_time: str, birth_place: str):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO users (user_id, zodiac, birth_time, birth_place, student_mode)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                zodiac=excluded.zodiac,
                birth_time=excluded.birth_time,
                birth_place=excluded.birth_place
        """, (user_id, zodiac, birth_time, birth_place))
        await db.commit()

async def update_student_mode(user_id: int, mode: int):
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET student_mode=? WHERE user_id=?",
            (mode, user_id)
        )
        await db.commit()

async def delete_user(user_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        await db.commit()

async def get_all_users():
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT user_id, zodiac, birth_time, birth_place, student_mode FROM users"
        )
        return await cursor.fetchall()

# ====================== GIGACHAT ======================
try:
    gigachat = GigaChat(
        credentials=GIGACHAT_CREDENTIALS,
        verify_ssl_certs=False,
        timeout=30
    )
    logger.info("✅ GigaChat инициализирован")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации GigaChat: {e}")
    gigachat = None

semaphore = asyncio.Semaphore(5)

async def generate_horoscope(zodiac: str, birth_time: str, birth_place: str, student: bool = False):
    if gigachat is None:
        return "⚠️ Сервис временно недоступен. Пожалуйста, попробуйте позже."
    
    async with semaphore:
        mode = ""
        if student:
            mode = "Акцент: учеба, экзамены, дедлайны, мотивация."

        prompt = f"""
Ты астролог с лёгким юмором.
{mode}

Знак: {zodiac}
Время рождения: {birth_time}
Место рождения: {birth_place}

Напиши живой гороскоп (~150-200 слов) и добавь побольше эмодзи.
"""

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: gigachat.chat(prompt)
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GigaChat error: {e}")
            return "⚠️ Ошибка генерации гороскопа. Попробуйте позже."

# ====================== HANDLERS ======================
dp = Dispatcher()
last_request = {}

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    
    if user:
        await message.answer(
            "✨ Ты уже зарегистрирован! Используй кнопки ниже:",
            reply_markup=main_keyboard
        )
        return

    await state.set_state(Register.zodiac)
    await message.answer(
        "🔮 Привет! Давай создадим твой персональный гороскоп.\n\n"
        "Выбери свой знак зодиака:",
        reply_markup=zodiac_keyboard
    )

@dp.message(Register.zodiac)
async def zodiac_step(message: Message, state: FSMContext):
    if message.text not in zodiacs:
        return await message.answer("❌ Пожалуйста, выбери знак из кнопок ниже:", reply_markup=zodiac_keyboard)

    await state.update_data(zodiac=message.text)
    await state.set_state(Register.birth_time)
    await message.answer(
        "⏰ Отлично! Теперь укажи время рождения (в формате ЧЧ:ММ)\n"
        "Пример: 14:30",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Register.birth_time)
async def time_step(message: Message, state: FSMContext):
    if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", message.text):
        return await message.answer("❌ Неверный формат! Используй ЧЧ:ММ, например: 14:30")

    await state.update_data(birth_time=message.text)
    await state.set_state(Register.birth_place)
    await message.answer("🌍 Укажи место рождения (город, страна):")

@dp.message(Register.birth_place)
async def place_step(message: Message, state: FSMContext):
    data = await state.get_data()
    
    await save_user(
        message.from_user.id,
        data["zodiac"],
        data["birth_time"],
        message.text
    )

    await state.clear()
    await message.answer(
        "✅ Регистрация завершена!\n\n"
        "Теперь ты можешь:\n"
        "🌟 Получить гороскоп сейчас\n"
        "📚 Включить режим студента\n"
        "🔄 Сбросить регистрацию",
        reply_markup=main_keyboard
    )

@dp.message(F.text.contains("Получить гороскоп"))
async def now(message: Message):
    uid = message.from_user.id

    if uid in last_request and datetime.now() - last_request[uid] < timedelta(seconds=20):
        remaining = 20 - (datetime.now() - last_request[uid]).seconds
        return await message.answer(f"⏳ Подожди {remaining} секунд перед следующим запросом")

    user = await get_user(uid)
    if not user:
        return await message.answer(
            "❌ Сначала зарегистрируйся с помощью /start",
            reply_markup=ReplyKeyboardRemove()
        )

    last_request[uid] = datetime.now()
    
    status_msg = await message.answer("🔮 Генерирую гороскоп... Подожди немного")
    
    text = await generate_horoscope(user[0], user[1], user[2], bool(user[3]))
    
    await status_msg.delete()
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text.contains("Режим студента"))
async def toggle_student(message: Message):
    uid = message.from_user.id

    user = await get_user(uid)
    if not user:
        return await message.answer("❌ Сначала зарегистрируйся с помощью /start")

    new_mode = 1 - user[3]
    await update_student_mode(uid, new_mode)
    
    status = "✅ ВКЛЮЧЕН" if new_mode else "❌ ВЫКЛЮЧЕН"
    await message.answer(f"📚 Режим студента {status}\n\nТеперь гороскопы будут с акцентом на учебу!")

@dp.message(F.text.contains("Сбросить регистрацию"))
async def reset(message: Message, state: FSMContext):
    await state.clear()
    await delete_user(message.from_user.id)
    
    await message.answer(
        "🔄 Регистрация сброшена!\n"
        "Чтобы начать заново, отправь /start",
        reply_markup=ReplyKeyboardRemove()
    )

# ====================== DAILY SCHEDULER ======================
async def send_daily(bot: Bot):
    logger.info("📨 Начинаю ежедневную рассылку гороскопов")
    
    users = await get_all_users()
    
    if not users:
        logger.info("Нет пользователей для рассылки")
        return
    
    success_count = 0
    for user in users:
        try:
            text = await generate_horoscope(user[1], user[2], user[3], bool(user[4]))
            await bot.send_message(user[0], text, parse_mode=ParseMode.HTML)
            success_count += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Не удалось отправить гороскоп пользователю {user[0]}: {e}")
    
    logger.info(f"✅ Рассылка завершена. Отправлено: {success_count}/{len(users)}")

# ====================== MAIN ======================
async def on_startup(bot: Bot):
    await init_db()
    logger.info("🚀 Бот запущен и готов к работе")

async def on_shutdown(bot: Bot):
    logger.info("👋 Бот останавливается...")
    if hasattr(bot, 'session'):
        await bot.session.close()

async def main():
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_daily, "cron", hour=7, minute=30, args=[bot])
    scheduler.start()
    
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.shutdown()

if __name__ == "__main__":
    print("🚀 Запуск бота...")
    asyncio.run(main())