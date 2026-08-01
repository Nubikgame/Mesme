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
    last_seen = Column(DateTime, nullable=True)  # 🔥 Когда юзера последний раз видели онлайн
    is_developer = Column(Boolean, default=False)  # 🔥 Права разработчика на форуме (статусы, приоритет, закреп, удаление)

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_name = Column(String, index=True) 
    sender = Column(String)                
    text = Column(String)                  
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)
    is_delivered = Column(Boolean, default=False)  # 🔥 Статус доставки (для галочек как в Telegram)
    file_url = Column(String, nullable=True)   # 🔥 Ссылка на прикреплённый файл (/media/messages/...)
    file_name = Column(String, nullable=True)  # 🔥 Оригинальное имя файла


class OtpCode(Base):
    __tablename__ = "otp_codes"

    email = Column(String, primary_key=True, index=True)
    code = Column(String)
    expires_at = Column(DateTime)

class ChatGroup(Base):
    __tablename__ = "chat_groups"
    
    # ID будет выглядеть как "group_12345"
    id = Column(String, primary_key=True, index=True) 
    name = Column(String)
    avatar_path = Column(String, nullable=True)
    is_public = Column(Boolean, default=True) # По умолчанию публичная
    is_channel = Column(Boolean, default=False)  # 🔥 группа или канал (в канале писать могут только админы)
    username = Column(String, unique=True, index=True, nullable=True)      # 🔥 паблик-хендл (только у публичных)
    invite_code = Column(String, unique=True, index=True, nullable=True)   # 🔥 приватная ссылка-приглашение
    description = Column(String, nullable=True)  # 🔥 описание/правила, задаёт создатель или админ
    owner_username = Column(String, index=True) # Кто создатель

class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String, index=True)
    user_username = Column(String, index=True)
    role = Column(String, default="member") # Может быть "member" или "admin"

# ==========================================
# 🔥 ФОРУМ ИДЕЙ
# ==========================================
class ForumPost(Base):
    __tablename__ = "forum_posts"

    id = Column(String, primary_key=True, index=True)  # "forum_xxxxxxxx"
    type = Column(String)  # "idea" или "bug"
    title = Column(String)
    description = Column(String)
    steps_to_reproduce = Column(String, nullable=True)  # только у багов
    image_url = Column(String, nullable=True)           # только у багов
    author_username = Column(String, index=True)
    author_nickname = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="considering")  # considering | in_progress | implemented
    is_priority = Column(Boolean, default=False)    # ⭐ ставит только разработчик
    is_pinned = Column(Boolean, default=False)       # 📌 закреп, ставит только разработчик
    implemented_version = Column(String, nullable=True)
    implemented_at = Column(DateTime, nullable=True)
    comments_closed = Column(Boolean, default=False)

class ForumComment(Base):
    __tablename__ = "forum_comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(String, index=True)
    author_username = Column(String)
    author_nickname = Column(String)
    text = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class ForumSupport(Base):
    __tablename__ = "forum_supports"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(String, index=True)
    username = Column(String, index=True)