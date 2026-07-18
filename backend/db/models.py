from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime
from db.database import Base

# --- СТАРЫЕ ТАБЛИЦЫ ---
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    nickname = Column(String, default="Пользователь")
    username = Column(String, unique=True, index=True, nullable=True)
    avatar_path = Column(String, nullable=True)
    token = Column(String, unique=True, index=True, nullable=True)

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_name = Column(String, index=True) 
    sender = Column(String)                
    text = Column(String)                  
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)
    is_delivered = Column(Boolean, default=False)  # 🔥 Статус доставки (для галочек как в Telegram)


class ChatGroup(Base):
    __tablename__ = "chat_groups"
    
    # ID будет выглядеть как "group_12345"
    id = Column(String, primary_key=True, index=True) 
    name = Column(String)
    avatar_path = Column(String, nullable=True)
    is_public = Column(Boolean, default=True) # По умолчанию публичная
    owner_username = Column(String, index=True) # Кто создатель

class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String, index=True)
    user_username = Column(String, index=True)
    role = Column(String, default="member") # Может быть "member" или "admin"