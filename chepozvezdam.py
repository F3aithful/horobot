import telebot
from telebot import types
import sqlite3
import threading
import time
import schedule
import datetime

TOKEN = "8762682425:AAF41Nx6iksQdyWE38RpUl3kYwHo9pdTky8"
bot = telebot.TeleBot(TOKEN)

# ====================== БАЗА ДАННЫХ (с авто-обновлением колонок) ======================
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

# Создаём таблицу, если её нет
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    sign TEXT,
    student_mode INTEGER DEFAULT 0,
    send_time TEXT DEFAULT "08:00"
)''')

# Добавляем колонки, если их ещё нет (защита от будущих изменений)
def add_column_if_not_exists(column_name, column_type):
    cursor.execute(f"PRAGMA table_info(users)")
    columns = [info[1] for info in cursor.fetchall()]
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
        print(f"Добавлена колонка: {column_name}")

add_column_if_not_exists("sign", "TEXT")
add_column_if_not_exists("student_mode", "INTEGER DEFAULT 0")
add_column_if_not_exists("send_time", "TEXT DEFAULT '08:00'")

conn.commit()

def get_user(chat_id):
    cursor.execute("SELECT sign, student_mode, send_time FROM users WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    if row:
        return {"sign": row[0], "student_mode": bool(row[1]), "send_time": row[2]}
    return None

def save_user(chat_id, sign=None, student_mode=None, send_time=None):
    user = get_user(chat_id)
    if user:
        if sign is not None:
            cursor.execute("UPDATE users SET sign=? WHERE chat_id=?", (sign, chat_id))
        if student_mode is not None:
            cursor.execute("UPDATE users SET student_mode=? WHERE chat_id=?", (int(student_mode), chat_id))
        if send_time is not None:
            cursor.execute("UPDATE users SET send_time=? WHERE chat_id=?", (send_time, chat_id))
    else:
        cursor.execute("""INSERT INTO users (chat_id, sign, student_mode, send_time) 
                          VALUES (?, ?, ?, ?)""",
                       (chat_id, sign, int(student_mode) if student_mode is not None else 0, send_time or "08:00"))
    conn.commit()

# ====================== КЛАВИАТУРЫ ======================
zodiac_signs = ["Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева", "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"]

def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    markup.add(*[types.KeyboardButton(sign) for sign in zodiac_signs])
    markup.add(types.KeyboardButton("⚙️ Настройки"), types.KeyboardButton("🌟 Гороскоп сегодня"))
    return markup

# ====================== КОМАНДЫ ======================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    if not get_user(chat_id):
        save_user(chat_id)
    
    bot.send_message(
        chat_id,
        "Привет! 👋 Я бот ежедневных гороскопов.\n\n"
        "Выбери свой знак Зодиака с помощью кнопок ниже:",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text in zodiac_signs)
def set_sign(message):
    chat_id = message.chat.id
    sign = message.text
    save_user(chat_id, sign=sign)
    
    bot.send_message(
        chat_id,
        f"✅ Твой знак сохранён: <b>{sign}</b>\n\n"
        "Теперь можешь получить гороскоп кнопкой «🌟 Гороскоп сегодня» или командой /horoscope",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

@bot.message_handler(commands=['horoscope'])
@bot.message_handler(func=lambda m: m.text == "🌟 Гороскоп сегодня")
def send_horoscope(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    
    if not user or not user.get("sign"):
        bot.send_message(chat_id, "Сначала выбери свой знак Зодиака!")
        return
    
    mode = "студенческий" if user["student_mode"] else "обычный"
    bot.send_message(
        chat_id,
        f"🌟 Гороскоп на сегодня для <b>{user['sign']}</b> ({mode} режим)\n\n"
        "⏳ Загружаю свежий прогноз...",
        parse_mode="HTML"
    )
    
    # Заглушка — в следующем сообщении заменим на реальный гороскоп с русского сайта
    bot.send_message(chat_id, f"Пока гороскоп в разработке.\n\nСкоро здесь будет текст с horo.mail.ru + студенческая версия, если режим включён.", parse_mode="HTML")

# ====================== НАСТРОЙКИ ======================
@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки")
def settings(message):
    chat_id = message.chat.id
    user = get_user(chat_id) or {"student_mode": False, "send_time": "08:00"}
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    mode_text = "✅ Выключить студенческий режим" if user["student_mode"] else "🎓 Включить студенческий режим"
    markup.add(types.InlineKeyboardButton(mode_text, callback_data="toggle_student"))
    markup.add(types.InlineKeyboardButton("⏰ Изменить время рассылки", callback_data="change_time"))
    
    bot.send_message(
        chat_id,
        f"⚙️ Твои настройки:\n\n"
        f"🎓 Студенческий режим: {'Включён ✅' if user['student_mode'] else 'Выключен'}\n"
        f"⏰ Время ежедневной рассылки: {user['send_time']}",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    if call.data == "toggle_student":
        user = get_user(chat_id)
        new_mode = not user["student_mode"]
        save_user(chat_id, student_mode=new_mode)
        bot.answer_callback_query(call.id, f"Студенческий режим {'включён' if new_mode else 'выключен'}!")
        bot.edit_message_text(
            f"Студенческий режим {'включён ✅' if new_mode else 'выключен'}",
            chat_id, call.message.message_id
        )
    
    elif call.data == "change_time":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Напиши новое время рассылки в формате ЧЧ:ММ\nПример: 07:30 или 21:00")

@bot.message_handler(func=lambda m: len(m.text) == 5 and m.text[2] == ":")
def set_time(message):
    chat_id = message.chat.id
    try:
        datetime.datetime.strptime(message.text, "%H:%M")
        save_user(chat_id, send_time=message.text)
        bot.send_message(chat_id, f"✅ Время ежедневной рассылки установлено на <b>{message.text}</b>", 
                         parse_mode="HTML", reply_markup=main_keyboard())
    except ValueError:
        bot.send_message(chat_id, "❌ Неверный формат! Используй ЧЧ:ММ (например 08:00)")

print("Бот запущен...")
bot.infinity_polling()