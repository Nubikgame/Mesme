import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

# Ищем .env железобетонно
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mesme_base.db")

print("\n" + "="*50)
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    print("⚠️ ВНИМАНИЕ: Подключена ЛОКАЛЬНАЯ SQLite база.")
    print(f"Искал файл .env тут: {env_path}")
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    print("✅ УСПЕХ: Подключена ОБЛАЧНАЯ база (Neon)!")
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,  # проверяет соединение перед каждым запросом:
                             # Neon "усыпляет" базу при простое, и старое
                             # соединение из пула становится мёртвым -
                             # именно это давало "server closed the connection unexpectedly"
        pool_recycle=300,   # и на всякий случай переоткрываем соединения старше 5 минут
    )
print("="*50 + "\n")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()