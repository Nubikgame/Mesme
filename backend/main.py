import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.auth import router as auth_router
from api.chats import router as chat_router
from api.forum import router as forum_router, seed_pinned_posts
from db.database import engine, Base

# При запуске сервера проверяем и создаем таблицы в БД, если их еще нет
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mesme API")

# Подключаем модули авторизации, чатов и форума идей
app.include_router(auth_router, prefix="/auth")
app.include_router(chat_router, prefix="/chat") 
app.include_router(forum_router, prefix="/forum")

# 🔥 Закреплённые публикации форума (Добро пожаловать / Как сообщать о багах / Roadmap) -
# создаются один раз, если их ещё нет. Безопасно вызывать при каждом запуске.
seed_pinned_posts()

# 🔥 Раздаём файлы, присланные в чатах и аватарки (Mesme/media/...)
MEDIA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "media")
os.makedirs(MEDIA_ROOT, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

@app.get("/")
def read_root():
    return {"message": "Сервер Mesme запущен и готов к работе!"}