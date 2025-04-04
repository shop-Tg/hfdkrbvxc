import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

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

# Создаем директорию для логов, если она не существует
os.makedirs('logs', exist_ok=True)

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

# Обработчик корневого пути
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
