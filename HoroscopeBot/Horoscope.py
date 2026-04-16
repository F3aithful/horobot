import asyncio
import logging
import re
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from gigachat import GigaChat

# ====================== ТОКЕНЫ ======================
TOKEN = "8762682425:AAF41Nx6iksQdyWE38RpUl3kYwHo9pdTky8"
GIGACHAT_CREDENTIALS = "MDE5ZDkyNjEtOGNlMS03MGMwLTg4ODktMGViZTQzYTVhNTk2OmY5MDQ4YmUyLTA5NmItNGFjNC04Mzg1LWVlMjYxMmU5NDRjMg=="

DB_NAME = "users.db"

# ====================== ЛОГИ ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

dp = Dispatcher()

# ====================== КЛАВИАТУРЫ ======================
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Получить гороскоп сейчас")],
        [KeyboardButton(text="Режим студента")],
        [KeyboardButton(text="Сбросить регистрацию")]
    ],
    resize_keyboard=True
)

zodiacs = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
]

zodiac_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=z)] for z in zodiacs],
    resize_keyboard=True,
    one_time_keyboard=True
)

# ====================== DB ======================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
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

# ====================== FSM ======================
class Register(StatesGroup):
    zodiac = State()
    birth_time = State()
    birth_place = State()

# ====================== GIGACHAT ======================
gigachat = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    verify_ssl_certs=False
)

semaphore = asyncio.Semaphore(5)

async def generate_horoscope(zodiac: str, birth_time: str, birth_place: str, student: bool = False):
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
                gigachat.chat,
                prompt
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"GigaChat error: {e}")
            return "⚠️ Ошибка генерации гороскопа"

# ====================== STATE ======================
last_request = {}

# ====================== HANDLERS ======================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE user_id=?",
            (message.from_user.id,)
        )
        if await cursor.fetchone():
            await message.answer("Ты уже зарегистрирован", reply_markup=main_keyboard)
            return

    await state.set_state(Register.zodiac)
    await message.answer("Выбери знак зодиака:", reply_markup=zodiac_keyboard)


@dp.message(Register.zodiac)
async def zodiac_step(message: Message, state: FSMContext):
    if message.text not in zodiacs:
        return await message.answer("Выбери знак из кнопок")

    await state.update_data(zodiac=message.text)
    await state.set_state(Register.birth_time)
    await message.answer("Время рождения (HH:MM)", reply_markup=ReplyKeyboardRemove())


@dp.message(Register.birth_time)
async def time_step(message: Message, state: FSMContext):
    if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", message.text):
        return await message.answer("Неверный формат. Пример: 14:30")

    await state.update_data(birth_time=message.text)
    await state.set_state(Register.birth_place)
    await message.answer("Город, страна")


@dp.message(Register.birth_place)
async def place_step(message: Message, state: FSMContext):
    data = await state.get_data()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (user_id, zodiac, birth_time, birth_place, student_mode)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                zodiac=excluded.zodiac,
                birth_time=excluded.birth_time,
                birth_place=excluded.birth_place
        """, (
            message.from_user.id,
            data["zodiac"],
            data["birth_time"],
            message.text
        ))
        await db.commit()

    await state.clear()
    await message.answer("✅ Готово!", reply_markup=main_keyboard)

# ====================== HOROSCOPE NOW ======================
@dp.message(F.text == "Получить гороскоп сейчас")
async def now(message: Message):
    uid = message.from_user.id

    if uid in last_request and datetime.now() - last_request[uid] < timedelta(seconds=20):
        return await message.answer("⏳ Подожди немного")

    last_request[uid] = datetime.now()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT zodiac, birth_time, birth_place, student_mode FROM users WHERE user_id=?",
            (uid,)
        )
        user = await cursor.fetchone()

    if not user:
        return await message.answer("Сначала /start")

    text = await generate_horoscope(*user)
    await message.answer(text)

# ====================== STUDENT MODE ======================
@dp.message(F.text == "Режим студента")
async def toggle_student(message: Message):
    uid = message.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT student_mode FROM users WHERE user_id=?",
            (uid,)
        )
        row = await cursor.fetchone()

        if not row:
            return await message.answer("Сначала /start")

        new_mode = 1 - row[0]

        await db.execute(
            "UPDATE users SET student_mode=? WHERE user_id=?",
            (new_mode, uid)
        )
        await db.commit()

    await message.answer(f"Режим студента: {'ON' if new_mode else 'OFF'}")

# ====================== RESET ======================
@dp.message(F.text == "Сбросить регистрацию")
async def reset(message: Message, state: FSMContext):
    await state.clear()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM users WHERE user_id=?", (message.from_user.id,))
        await db.commit()

    await message.answer("Сброшено. /start", reply_markup=ReplyKeyboardRemove())

# ====================== DAILY SCHEDULER ======================
async def send_daily(bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id, zodiac, birth_time, birth_place, student_mode FROM users"
        )
        users = await cursor.fetchall()

    for u in users:
        try:
            text = await generate_horoscope(u[1], u[2], u[3], bool(u[4]))
            await bot.send_message(u[0], text)
        except Exception as e:
            logger.warning(f"Send error {u[0]}: {e}")

# ====================== MAIN ======================
async def main():
    await init_db()

    bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_daily, "cron", hour=7, minute=30, args=[bot])
    scheduler.start()

    logger.info("🚀 Бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())