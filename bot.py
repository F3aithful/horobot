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

# ====================== РўРћРљР•РќР« РР— РџР•Р Р•РњР•РќРќР«РҐ РћРљР РЈР–Р•РќРРЇ ======================
TOKEN = os.getenv("TELEGRAM_TOKEN")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
DB_NAME = "users.db"

# РџСЂРѕРІРµСЂРєР° С‡С‚Рѕ РїРµСЂРµРјРµРЅРЅС‹Рµ СѓСЃС‚Р°РЅРѕРІР»РµРЅС‹
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN РЅРµ Р·Р°РґР°РЅ РІ РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ!")
if not GIGACHAT_CREDENTIALS:
    raise ValueError("GIGACHAT_CREDENTIALS РЅРµ Р·Р°РґР°РЅ РІ РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ!")

# ====================== Р›РћР“Р ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ====================== РљР›РђР’РРђРўРЈР Р« ======================
zodiacs = [
    "РћРІРµРЅ", "РўРµР»РµС†", "Р‘Р»РёР·РЅРµС†С‹", "Р Р°Рє", "Р›РµРІ", "Р”РµРІР°",
    "Р’РµСЃС‹", "РЎРєРѕСЂРїРёРѕРЅ", "РЎС‚СЂРµР»РµС†", "РљРѕР·РµСЂРѕРі", "Р’РѕРґРѕР»РµР№", "Р С‹Р±С‹"
]

zodiac_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=z)] for z in zodiacs],
    resize_keyboard=True,
    one_time_keyboard=True
)

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="рџЊџ РџРѕР»СѓС‡РёС‚СЊ РіРѕСЂРѕСЃРєРѕРї СЃРµР№С‡Р°СЃ")],
        [KeyboardButton(text="рџ“љ Р РµР¶РёРј СЃС‚СѓРґРµРЅС‚Р°")],
        [KeyboardButton(text="рџ”„ РЎР±СЂРѕСЃРёС‚СЊ СЂРµРіРёСЃС‚СЂР°С†РёСЋ")]
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
    logger.info("вњ… Р‘Р°Р·Р° РґР°РЅРЅС‹С… РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅР°")

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
    logger.info("вњ… GigaChat РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅ")
except Exception as e:
    logger.error(f"вќЊ РћС€РёР±РєР° РёРЅРёС†РёР°Р»РёР·Р°С†РёРё GigaChat: {e}")
    gigachat = None

semaphore = asyncio.Semaphore(5)

async def generate_horoscope(zodiac: str, birth_time: str, birth_place: str, student: bool = False):
    if gigachat is None:
        return "вљ пёЏ РЎРµСЂРІРёСЃ РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ. РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РїРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ."
    
    async with semaphore:
        mode = ""
        if student:
            mode = "РђРєС†РµРЅС‚: СѓС‡РµР±Р°, СЌРєР·Р°РјРµРЅС‹, РґРµРґР»Р°Р№РЅС‹, РјРѕС‚РёРІР°С†РёСЏ."

        prompt = f"""
РўС‹ Р°СЃС‚СЂРѕР»РѕРі СЃ Р»С‘РіРєРёРј СЋРјРѕСЂРѕРј.
{mode}

Р—РЅР°Рє: {zodiac}
Р’СЂРµРјСЏ СЂРѕР¶РґРµРЅРёСЏ: {birth_time}
РњРµСЃС‚Рѕ СЂРѕР¶РґРµРЅРёСЏ: {birth_place}

РќР°РїРёС€Рё Р¶РёРІРѕР№ РіРѕСЂРѕСЃРєРѕРї (~150-200 СЃР»РѕРІ) Рё РґРѕР±Р°РІСЊ РїРѕР±РѕР»СЊС€Рµ СЌРјРѕРґР·Рё.
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
            return "вљ пёЏ РћС€РёР±РєР° РіРµРЅРµСЂР°С†РёРё РіРѕСЂРѕСЃРєРѕРїР°. РџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ."

# ====================== HANDLERS ======================
dp = Dispatcher()
last_request = {}

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    
    if user:
        await message.answer(
            "вњЁ РўС‹ СѓР¶Рµ Р·Р°СЂРµРіРёСЃС‚СЂРёСЂРѕРІР°РЅ! РСЃРїРѕР»СЊР·СѓР№ РєРЅРѕРїРєРё РЅРёР¶Рµ:",
            reply_markup=main_keyboard
        )
        return

    await state.set_state(Register.zodiac)
    await message.answer(
        "рџ”® РџСЂРёРІРµС‚! Р”Р°РІР°Р№ СЃРѕР·РґР°РґРёРј С‚РІРѕР№ РїРµСЂСЃРѕРЅР°Р»СЊРЅС‹Р№ РіРѕСЂРѕСЃРєРѕРї.\n\n"
        "Р’С‹Р±РµСЂРё СЃРІРѕР№ Р·РЅР°Рє Р·РѕРґРёР°РєР°:",
        reply_markup=zodiac_keyboard
    )

@dp.message(Register.zodiac)
async def zodiac_step(message: Message, state: FSMContext):
    if message.text not in zodiacs:
        return await message.answer("вќЊ РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РІС‹Р±РµСЂРё Р·РЅР°Рє РёР· РєРЅРѕРїРѕРє РЅРёР¶Рµ:", reply_markup=zodiac_keyboard)

    await state.update_data(zodiac=message.text)
    await state.set_state(Register.birth_time)
    await message.answer(
        "вЏ° РћС‚Р»РёС‡РЅРѕ! РўРµРїРµСЂСЊ СѓРєР°Р¶Рё РІСЂРµРјСЏ СЂРѕР¶РґРµРЅРёСЏ (РІ С„РѕСЂРјР°С‚Рµ Р§Р§:РњРњ)\n"
        "РџСЂРёРјРµСЂ: 14:30",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Register.birth_time)
async def time_step(message: Message, state: FSMContext):
    if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", message.text):
        return await message.answer("вќЊ РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚! РСЃРїРѕР»СЊР·СѓР№ Р§Р§:РњРњ, РЅР°РїСЂРёРјРµСЂ: 14:30")

    await state.update_data(birth_time=message.text)
    await state.set_state(Register.birth_place)
    await message.answer("рџЊЌ РЈРєР°Р¶Рё РјРµСЃС‚Рѕ СЂРѕР¶РґРµРЅРёСЏ (РіРѕСЂРѕРґ, СЃС‚СЂР°РЅР°):")

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
        "вњ… Р РµРіРёСЃС‚СЂР°С†РёСЏ Р·Р°РІРµСЂС€РµРЅР°!\n\n"
        "РўРµРїРµСЂСЊ С‚С‹ РјРѕР¶РµС€СЊ:\n"
        "рџЊџ РџРѕР»СѓС‡РёС‚СЊ РіРѕСЂРѕСЃРєРѕРї СЃРµР№С‡Р°СЃ\n"
        "рџ“љ Р’РєР»СЋС‡РёС‚СЊ СЂРµР¶РёРј СЃС‚СѓРґРµРЅС‚Р°\n"
        "рџ”„ РЎР±СЂРѕСЃРёС‚СЊ СЂРµРіРёСЃС‚СЂР°С†РёСЋ",
        reply_markup=main_keyboard
    )

@dp.message(F.text.contains("РџРѕР»СѓС‡РёС‚СЊ РіРѕСЂРѕСЃРєРѕРї"))
async def now(message: Message):
    uid = message.from_user.id

    if uid in last_request and datetime.now() - last_request[uid] < timedelta(seconds=20):
        remaining = 20 - (datetime.now() - last_request[uid]).seconds
        return await message.answer(f"вЏі РџРѕРґРѕР¶РґРё {remaining} СЃРµРєСѓРЅРґ РїРµСЂРµРґ СЃР»РµРґСѓСЋС‰РёРј Р·Р°РїСЂРѕСЃРѕРј")

    user = await get_user(uid)
    if not user:
        return await message.answer(
            "вќЊ РЎРЅР°С‡Р°Р»Р° Р·Р°СЂРµРіРёСЃС‚СЂРёСЂСѓР№СЃСЏ СЃ РїРѕРјРѕС‰СЊСЋ /start",
            reply_markup=ReplyKeyboardRemove()
        )

    last_request[uid] = datetime.now()
    
    status_msg = await message.answer("рџ”® Р“РµРЅРµСЂРёСЂСѓСЋ РіРѕСЂРѕСЃРєРѕРї... РџРѕРґРѕР¶РґРё РЅРµРјРЅРѕРіРѕ")
    
    text = await generate_horoscope(user[0], user[1], user[2], bool(user[3]))
    
    await status_msg.delete()
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text.contains("Р РµР¶РёРј СЃС‚СѓРґРµРЅС‚Р°"))
async def toggle_student(message: Message):
    uid = message.from_user.id

    user = await get_user(uid)
    if not user:
        return await message.answer("вќЊ РЎРЅР°С‡Р°Р»Р° Р·Р°СЂРµРіРёСЃС‚СЂРёСЂСѓР№СЃСЏ СЃ РїРѕРјРѕС‰СЊСЋ /start")

    new_mode = 1 - user[3]
    await update_student_mode(uid, new_mode)
    
    status = "вњ… Р’РљР›Р®Р§Р•Рќ" if new_mode else "вќЊ Р’Р«РљР›Р®Р§Р•Рќ"
    await message.answer(f"рџ“љ Р РµР¶РёРј СЃС‚СѓРґРµРЅС‚Р° {status}\n\nРўРµРїРµСЂСЊ РіРѕСЂРѕСЃРєРѕРїС‹ Р±СѓРґСѓС‚ СЃ Р°РєС†РµРЅС‚РѕРј РЅР° СѓС‡РµР±Сѓ!")

@dp.message(F.text.contains("РЎР±СЂРѕСЃРёС‚СЊ СЂРµРіРёСЃС‚СЂР°С†РёСЋ"))
async def reset(message: Message, state: FSMContext):
    await state.clear()
    await delete_user(message.from_user.id)
    
    await message.answer(
        "рџ”„ Р РµРіРёСЃС‚СЂР°С†РёСЏ СЃР±СЂРѕС€РµРЅР°!\n"
        "Р§С‚РѕР±С‹ РЅР°С‡Р°С‚СЊ Р·Р°РЅРѕРІРѕ, РѕС‚РїСЂР°РІСЊ /start",
        reply_markup=ReplyKeyboardRemove()
    )

# ====================== DAILY SCHEDULER ======================
async def send_daily(bot: Bot):
    logger.info("рџ“Ё РќР°С‡РёРЅР°СЋ РµР¶РµРґРЅРµРІРЅСѓСЋ СЂР°СЃСЃС‹Р»РєСѓ РіРѕСЂРѕСЃРєРѕРїРѕРІ")
    
    users = await get_all_users()
    
    if not users:
        logger.info("РќРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РґР»СЏ СЂР°СЃСЃС‹Р»РєРё")
        return
    
    success_count = 0
    for user in users:
        try:
            text = await generate_horoscope(user[1], user[2], user[3], bool(user[4]))
            await bot.send_message(user[0], text, parse_mode=ParseMode.HTML)
            success_count += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РіРѕСЂРѕСЃРєРѕРї РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ {user[0]}: {e}")
    
    logger.info(f"вњ… Р Р°СЃСЃС‹Р»РєР° Р·Р°РІРµСЂС€РµРЅР°. РћС‚РїСЂР°РІР»РµРЅРѕ: {success_count}/{len(users)}")

# ====================== MAIN ======================
async def on_startup(bot: Bot):
    await init_db()
    logger.info("рџљЂ Р‘РѕС‚ Р·Р°РїСѓС‰РµРЅ Рё РіРѕС‚РѕРІ Рє СЂР°Р±РѕС‚Рµ")

async def on_shutdown(bot: Bot):
    logger.info("рџ‘‹ Р‘РѕС‚ РѕСЃС‚Р°РЅР°РІР»РёРІР°РµС‚СЃСЏ...")
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
    print("рџљЂ Р—Р°РїСѓСЃРє Р±РѕС‚Р°...")
    asyncio.run(main())
