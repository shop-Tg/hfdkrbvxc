import os
import json
import hmac
import hashlib
import logging
import sqlite3
import asyncio
from datetime import datetime
from fastapi import FastAPI, Request, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import aiohttp
import uvicorn
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("webhook_logs.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("webhook_server")

# Получаем токен из переменных окружения
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_BOT_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Создаем директории, если они не существуют
os.makedirs('logs', exist_ok=True)
os.makedirs('static', exist_ok=True)

# Инициализация базы данных SQLite для хранения вебхуков
conn = sqlite3.connect('data/webhooks.db')
cursor = conn.cursor()

# Создаем таблицу для хранения вебхуков
cursor.execute('''
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id TEXT,
    update_type TEXT,
    invoice_id TEXT,
    status TEXT,
    user_id TEXT,
    amount TEXT,
    asset TEXT,
    payload TEXT,
    received_at TEXT DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT FALSE
)
''')

# Создаем таблицу для хранения инвойсов
cursor.execute('''
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    user_id TEXT,
    amount TEXT,
    asset TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT,
    payload TEXT
)
''')

conn.commit()
conn.close()

# Создаем FastAPI приложение
app = FastAPI(title="CryptoBot Webhook Logger")

# Настраиваем CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтируем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# Модель данных для создания инвойса
class InvoiceCreate(BaseModel):
    user_id: int
    amount: float
    asset: str = "USDT"
    description: str = "Пополнение баланса"

# Функция для проверки подписи вебхука
def verify_webhook_signature(token, data, signature):
    """Проверяет подпись вебхука от Crypto Pay API"""
    secret_key = hashlib.sha256(token.encode()).digest()
    data_string = json.dumps(data, separators=(',', ':'))
    hmac_string = hmac.new(secret_key, data_string.encode(), hashlib.sha256).hexdigest()
    return hmac_string == signature

# Функция для сохранения вебхука в базу данных
def save_webhook_to_db(update_data):
    """Сохраняет данные вебхука в базу данных"""
    try:
        conn = sqlite3.connect('data/webhooks.db')
        cursor = conn.cursor()
        
        update_id = update_data.get("update_id")
        update_type = update_data.get("update_type")
        payload = update_data.get("payload", {})
        invoice_id = payload.get("invoice_id")
        status = payload.get("status")
        
        cursor.execute(
            "INSERT INTO webhooks (update_id, update_type, invoice_id, status, payload) VALUES (?, ?, ?, ?, ?)",
            (update_id, update_type, invoice_id, status, json.dumps(update_data))
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении вебхука: {e}")
        return False

# Функция для обновления статуса инвойса в базе данных
def update_invoice_status(invoice_id, status, paid_at=None):
    """Обновляет статус инвойса в базе данных"""
    try:
        conn = sqlite3.connect('data/webhooks.db')
        cursor = conn.cursor()
        
        if paid_at:
            cursor.execute(
                "UPDATE invoices SET status = ?, paid_at = ? WHERE invoice_id = ?",
                (status, paid_at, invoice_id)
            )
        else:
            cursor.execute(
                "UPDATE invoices SET status = ? WHERE invoice_id = ?",
                (status, invoice_id)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса инвойса: {e}")
        return False

# Функция для получения данных инвойса из базы данных
def get_invoice_data(invoice_id):
    """Получает данные инвойса из базы данных"""
    try:
        conn = sqlite3.connect('data/webhooks.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
        invoice = cursor.fetchone()
        
        conn.close()
        
        if invoice:
            columns = ["invoice_id", "user_id", "amount", "asset", "status", "created_at", "paid_at", "payload"]
            return dict(zip(columns, invoice))
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении данных инвойса: {e}")
        return None

# Функция для сохранения инвойса в базу данных
def save_invoice_to_db(invoice_id, user_id, amount, asset="USDT", payload=None):
    """Сохраняет данные инвойса в базу данных"""
    try:
        conn = sqlite3.connect('data/webhooks.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO invoices (invoice_id, user_id, amount, asset, payload) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, user_id, amount, asset, json.dumps(payload) if payload else None)
        )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении инвойса: {e}")
        return False

# Функция для отправки уведомления пользователю через Telegram Bot API
async def send_notification_to_user(user_id, message, reply_markup=None):
    """Отправляет уведомление пользователю через Telegram Bot API"""
    try:
        url = f"{TELEGRAM_BOT_URL}/sendMessage"
        
        data = {
            "chat_id": user_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"Уведомление отправлено пользователю {user_id}")
                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка при отправке уведомления: {response.status}, {error_text}")
                    return None
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")
        return None

# Функция для обновления баланса пользователя через Telegram Bot API
async def update_user_balance(user_id, amount):
    """Обновляет баланс пользователя через специальную команду бота"""
    try:
        url = f"{TELEGRAM_BOT_URL}/sendMessage"
        
        # Формируем команду для обновления баланса
        command_text = f"/update_balance {user_id} {amount}"
        
        data = {
            "chat_id": user_id,  # Отправляем боту
            "text": command_text
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"Команда обновления баланса отправлена для пользователя {user_id}")
                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка при отправке команды обновления баланса: {response.status}, {error_text}")
                    return None
    except Exception as e:
        logger.error(f"Ошибка при обновлении баланса: {e}")
        return None

# Функция для создания инвойса через CryptoBot API
async def create_crypto_invoice(amount, user_id, description="Пополнение баланса", asset="USDT"):
    """Создает инвойс через CryptoBot API"""
    try:
        logger.info(f"Создание инвойса для пользователя {user_id} на сумму {amount} {asset}")
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN
            }
            
            # Формируем данные для создания инвойса
            data = {
                "asset": asset,
                "amount": str(amount),
                "description": description,
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/BotRolseBot?start=paid_{user_id}_{amount}",
                "payload": json.dumps({
                    "user_id": user_id,
                    "amount": amount
                })
            }
            
            # Отправляем запрос на создание инвойса
            async with session.post("https://pay.crypt.bot/api/createInvoice", headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("ok"):
                        # Получаем данные созданного инвойса
                        invoice_data = result.get("result", {})
                        invoice_id = invoice_data.get("invoice_id")
                        
                        # Сохраняем инвойс в базу данных
                        save_invoice_to_db(
                            invoice_id=invoice_id,
                            user_id=user_id,
                            amount=amount,
                            asset=asset,
                            payload=data
                        )
                        
                        logger.info(f"Создан инвойс {invoice_id} для пользователя {user_id} на сумму {amount} {asset}")
                        return {
                            "status": "success",
                            "invoice_id": invoice_id,
                            "bot_invoice_url": invoice_data.get("bot_invoice_url")
                        }
                    else:
                        error = result.get("error", "Unknown error")
                        logger.error(f"Ошибка при создании инвойса: {error}")
                        return {
                            "status": "error",
                            "message": f"CryptoBot API error: {error}"
                        }
                else:
                    error_text = await response.text()
                    logger.error(f"HTTP error: {response.status}, {error_text}")
                    return {
                        "status": "error",
                        "message": f"HTTP error: {response.status}"
                    }
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

# Обработчик корневого пути - возвращает index.html
@app.get("/")
async def root():
    return FileResponse("index.html")

# Обработчик для вебхуков от CryptoBot
@app.post("/webhook")
async def webhook(request: Request, crypto_pay_api_signature: str = Header(None)):
    try:
        # Получаем тело запроса
        body = await request.json()
        
        # Логируем полученный вебхук
        logger.info(f"Получен вебхук от CryptoBot: {json.dumps(body, indent=2)}")
        
        # Сохраняем вебхук в файл
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        webhook_file = f"logs/webhook_{timestamp}.json"
        
        with open(webhook_file, 'w') as f:
            json.dump(body, f, indent=2)
        
        # Возвращаем успешный ответ
        return {"status": "success", "message": "Webhook received and logged"}
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Обработчик для создания инвойса
@app.post("/create-invoice/{user_id}/{amount}")
async def create_invoice(user_id: int, amount: float, asset: str = "USDT"):
    try:
        # Проверяем, что сумма положительная
        if amount <= 0:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Amount must be positive"}
            )
        
        # Создаем инвойс через CryptoBot API
        result = await create_crypto_invoice(amount, user_id, asset=asset)
        
        if result.get("status") == "success":
            return {
                "status": "success",
                "invoice_id": result.get("invoice_id"),
                "bot_invoice_url": result.get("bot_invoice_url")
            }
        else:
            return JSONResponse(
                status_code=500,
                content=result
            )
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# Обработчик для получения всех инвойсов
@app.get("/invoices")
async def get_invoices(limit: int = 100, offset: int = 0):
    try:
        conn = sqlite3.connect('data/webhooks.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM invoices ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
        invoices = cursor.fetchall()
        
        # Получаем имена столбцов
        cursor.execute("PRAGMA table_info(invoices)")
        columns = [column[1] for column in cursor.fetchall()]
        
        conn.close()
        
        # Преобразуем результаты в список словарей
        result = []
        for invoice in invoices:
            invoice_dict = dict(zip(columns, invoice))
            result.append(invoice_dict)
        
        return {"status": "success", "invoices": result}
    except Exception as e:
        logger.error(f"Ошибка при получении инвойсов: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# Обработчик для проверки статуса сервера
@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# Обработчик для просмотра последних вебхуков
@app.get("/logs")
async def view_logs(limit: int = 10):
    try:
        log_files = []
        for file in os.listdir('logs'):
            if file.startswith('webhook_') and file.endswith('.json'):
                log_files.append(file)
        
        # Сортируем файлы по времени создания (от новых к старым)
        log_files.sort(reverse=True)
        
        # Ограничиваем количество файлов
        log_files = log_files[:limit]
        
        # Читаем содержимое файлов
        logs = []
        for file in log_files:
            with open(f'logs/{file}', 'r') as f:
                logs.append({
                    "timestamp": file.replace("webhook_", "").replace(".json", ""),
                    "data": json.load(f)
                })
        
        return {"status": "success", "logs": logs}
    except Exception as e:
        logger.error(f"Ошибка при чтении логов: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# Запуск сервера
if __name__ == "__main__":
    uvicorn.run(
        "webhook_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=True
    ) 
