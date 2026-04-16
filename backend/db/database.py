from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

# Указываем, где будет лежать файл базы данных
SQLALCHEMY_DATABASE_URL = "sqlite:///./mesme_base.db"

# Создаем движок
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Фабрика сессий
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Базовый класс для всех таблиц
Base = declarative_base()

# Специальная функция-помощник для FastAPI, которая выдает и закрывает сессии
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()