from fastapi import FastAPI
from api.auth import router as auth_router
from api.chats import router as chat_router
from db.database import engine, Base

# При запуске сервера проверяем и создаем таблицы в БД, если их еще нет
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mesme API")

# Подключаем модули авторизации и чатов
app.include_router(auth_router, prefix="/auth")
app.include_router(chat_router, prefix="/chat") 

@app.get("/")
def read_root():
    return {"message": "Сервер Mesme запущен и готов к работе!"}