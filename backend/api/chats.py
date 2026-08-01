from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import json
import sys
import os
import re
import shutil
import uuid
import secrets
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_db, SessionLocal
from db.models import Message, ChatGroup, GroupMember, User

router = APIRouter()

def normalize_group_username(raw: str):
    """Приводит @username группы/канала к единому виду. a-z, 0-9, _; от 5 до 32 символов; начинается с буквы."""
    if not raw:
        return None
    name = raw.strip().lstrip("@").lower()
    if not re.match(r"^[a-z][a-z0-9_]{4,31}$", name):
        return None
    return name

def check_channel_permission(db: Session, chat_name: str, sender_username: Optional[str], sender_nickname: Optional[str]) -> bool:
    """В каналах писать может только админ. В группах и личных чатах - всегда можно."""
    if not chat_name.startswith("group_"):
        return True

    group = db.query(ChatGroup).filter(ChatGroup.id == chat_name).first()
    if not group or not group.is_channel:
        return True

    resolved_username = sender_username
    if not resolved_username and sender_nickname:
        # Резерв для случая, если фронт ещё не передаёт sender_username - ищем по нику
        # (менее надёжно, так как ники не уникальны, но лучше, чем ничего)
        u = db.query(User).filter(User.nickname == sender_nickname).first()
        resolved_username = u.username if u else None

    if not resolved_username:
        return False

    member = db.query(GroupMember).filter(
        GroupMember.group_id == chat_name,
        GroupMember.user_username == resolved_username
    ).first()
    return bool(member and member.role == "admin")

# 🔥 Папка для файлов, присланных в чатах (Mesme/media/messages)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "media", "messages")
os.makedirs(MEDIA_DIR, exist_ok=True)

# 🔥 Кто сейчас онлайн - считаем количество активных соединений на юзера,
# чтобы при открытии в двух окнах/вкладках статус не гас после закрытия только одного из них
online_connections: dict[str, int] = {}

def mark_online(username: str):
    online_connections[username] = online_connections.get(username, 0) + 1

def mark_offline(username: str):
    if username in online_connections:
        online_connections[username] -= 1
        if online_connections[username] <= 0:
            del online_connections[username]

def is_online(username: str) -> bool:
    return online_connections.get(username, 0) > 0

# --- МЕНЕДЖЕР ВЕБ-СОКЕТОВ ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_name: str):
        await websocket.accept()
        if room_name not in self.active_connections:
            self.active_connections[room_name] = []
        self.active_connections[room_name].append(websocket)

    def disconnect(self, websocket: WebSocket, room_name: str):
        if room_name in self.active_connections:
            if websocket in self.active_connections[room_name]:
                self.active_connections[room_name].remove(websocket)

    def is_someone_else_online(self, room_name: str, exclude: WebSocket) -> bool:
        # 🔥 Есть ли в комнате прямо сейчас кто-то ещё, кроме этого соединения -
        # используем как признак "собеседник в сети" для статуса "доставлено"
        conns = self.active_connections.get(room_name, [])
        return any(c is not exclude for c in conns)

    def count_connections(self, room_name: str) -> int:
        return len(self.active_connections.get(room_name, []))

    async def broadcast(self, message: str, room_name: str):
        if room_name in self.active_connections:
            for connection in list(self.active_connections[room_name]):
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection, room_name)

manager = ConnectionManager()

# --- ПОЛУЧЕНИЕ ИСТОРИИ (с полями is_read, is_delivered и файлом) ---
@router.get("/history/{chat_name}")
def get_chat_history(chat_name: str, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.chat_name == chat_name).order_by(Message.timestamp).all()
    return [{
        "id": msg.id, 
        "sender": msg.sender, 
        "text": msg.text, 
        "timestamp": msg.timestamp.isoformat(),
        "is_read": msg.is_read,          # 🔥 Прочитано
        "is_delivered": msg.is_delivered,  # 🔥 Доставлено (для галочек как в Telegram)
        "file_url": msg.file_url,        # 🔥 Прикреплённый файл, если есть
        "file_name": msg.file_name
    } for msg in messages]

# --- 🔥 СТАТУС ПОЛЬЗОВАТЕЛЯ: онлайн сейчас / был в сети когда-то ---
@router.get("/user-status/{username}")
def get_user_status(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {
        "is_online": is_online(username),
        "last_seen": user.last_seen.isoformat() if user.last_seen else None
    }

# --- 🔥 ПРИСУТСТВИЕ: постоянное лёгкое соединение, живёт всё время работы приложения ---
@router.websocket("/presence/{username}")
async def presence_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    mark_online(username)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.last_seen = datetime.utcnow()
            db.commit()
    finally:
        db.close()

    try:
        while True:
            # Клиент ничего не присылает - соединение держим только чтобы знать, что юзер онлайн
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        mark_offline(username)
        db2 = SessionLocal()
        try:
            user = db2.query(User).filter(User.username == username).first()
            if user:
                user.last_seen = datetime.utcnow()
                db2.commit()
        finally:
            db2.close()

# --- 🔥 ЗАГРУЗКА ФАЙЛОВ В ЧАТ (картинки, документы и т.п.) ---
@router.post("/upload")
async def upload_chat_file(
    chat_name: str = Form(...),
    sender: str = Form(...),
    sender_username: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not check_channel_permission(db, chat_name, sender_username, sender):
        raise HTTPException(status_code=403, detail="Только администраторы канала могут отправлять сообщения")

    # Уникальное имя на диске, чтобы разные файлы не затирали друг друга
    ext = os.path.splitext(file.filename or "")[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(MEDIA_DIR, unique_name)

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_url = f"/media/messages/{unique_name}"

    # Та же логика "доставлено", что и для текстовых сообщений
    delivered_now = manager.count_connections(chat_name) > 1

    new_msg = Message(
        chat_name=chat_name,
        sender=sender,
        text="",
        file_url=file_url,
        file_name=file.filename,
        is_read=False,
        is_delivered=delivered_now
    )
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)

    broadcast_data = {
        "action": "new_message",
        "id": new_msg.id,
        "sender": new_msg.sender,
        "text": "",
        "file_url": file_url,
        "file_name": file.filename,
        "timestamp": new_msg.timestamp.isoformat(),
        "is_read": False,
        "is_delivered": delivered_now
    }
    await manager.broadcast(json.dumps(broadcast_data), chat_name)

    return {"success": True, "file_url": file_url, "file_name": file.filename}

# --- НОВЫЙ МАРШРУТ: СЧЕТЧИКИ НЕПРОЧИТАННЫХ ---
@router.get("/unread-counts/{username}")
def get_unread_counts(username: str, db: Session = Depends(get_db)):
    # 1. Сначала находим никнейм пользователя по его username, так как в базе в поле sender лежит именно никнейм
    user = db.query(User).filter(User.username == username).first()
    user_nickname = user.nickname if user else username

    # 2. Личные чаты (p2p) - имя чата содержит username обоих участников
    p2p_messages = db.query(Message).filter(
        Message.is_read == False,
        Message.sender != user_nickname,
        Message.chat_name.like("p2p_%"),
        Message.chat_name.ilike(f"%{username}%")
    ).all()

    # 3. 🔥 Группы и каналы - у них chat_name это случайный id (group_xxxxxxxx),
    # он НЕ содержит username, поэтому фильтр выше их никогда не находил.
    # Непрочитанные тут считаются только для чатов, где юзер реально состоит.
    my_group_ids = [
        g.group_id for g in db.query(GroupMember).filter(GroupMember.user_username == username).all()
    ]
    group_messages = []
    if my_group_ids:
        group_messages = db.query(Message).filter(
            Message.is_read == False,
            Message.sender != user_nickname,
            Message.chat_name.in_(my_group_ids)
        ).all()

    counts = {}
    for m in p2p_messages + group_messages:
        counts[m.chat_name] = counts.get(m.chat_name, 0) + 1
        
    return counts

# --- УМНЫЙ ВЕБ-СОКЕТ ---
@router.websocket("/ws/{chat_name}")
async def websocket_endpoint(websocket: WebSocket, chat_name: str):
    await manager.connect(websocket, chat_name)
    try:
        while True:
            data = await websocket.receive_text()
            data_dict = json.loads(data)
            action = data_dict.get("action", "new_message") # По умолчанию - новое сообщение
            
            db = SessionLocal()
            try:
                if action == "mark_read":
                    # 🔥 Кто-то открыл чат и прочитал сообщения
                    reader = data_dict.get("sender")
                    unread_msgs = db.query(Message).filter(
                        Message.chat_name == chat_name,
                        Message.sender != reader,
                        Message.is_read == False
                    ).all()
                    
                    if unread_msgs:
                        for m in unread_msgs:
                            m.is_read = True
                            m.is_delivered = True  # прочитано -> значит точно доставлено
                        db.commit()
                        
                        # Сообщаем всем в чате, что сообщения прочитаны
                        await manager.broadcast(json.dumps({
                            "action": "messages_read",
                            "reader": reader
                        }), chat_name)

                elif action == "new_message":
                    # 🔥 В каналах писать могут только админы - если это канал и
                    # отправитель не админ, отклоняем и сообщаем только ему
                    if not check_channel_permission(db, chat_name, data_dict.get("sender_username"), data_dict.get("sender")):
                        await websocket.send_text(json.dumps({
                            "action": "error",
                            "detail": "Только администраторы канала могут отправлять сообщения"
                        }))
                        continue

                    # 🔥 Если в комнате прямо сейчас есть кто-то ещё (собеседник онлайн и
                    # смотрит в этот чат) - сообщение сразу считается доставленным
                    delivered_now = manager.is_someone_else_online(chat_name, exclude=websocket)

                    new_msg = Message(
                        chat_name=chat_name, 
                        sender=data_dict["sender"], 
                        text=data_dict["text"],
                        is_read=False,
                        is_delivered=delivered_now
                    )
                    db.add(new_msg)
                    db.commit() 
                    db.refresh(new_msg)
                    
                    broadcast_data = {
                        "action": "new_message",
                        "id": new_msg.id,
                        "sender": new_msg.sender,
                        "text": new_msg.text,
                        "timestamp": new_msg.timestamp.isoformat(),
                        "is_read": False,
                        "is_delivered": delivered_now
                    }
                    await manager.broadcast(json.dumps(broadcast_data), chat_name)
            finally:
                db.close()
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, chat_name)
    except Exception as e:
        print(f"WS Error: {e}")
        manager.disconnect(websocket, chat_name)

class GroupCreate(BaseModel):
    name: str
    is_public: bool
    is_channel: bool = False
    owner_username: str
    username: Optional[str] = None  # обязателен, если is_public=True

@router.post("/create-group")
def create_group(req: GroupCreate, db: Session = Depends(get_db)):
    group_username = None
    invite_code = None

    if req.is_public:
        group_username = normalize_group_username(req.username or "")
        if not group_username:
            raise HTTPException(
                status_code=400, 
                detail="Некорректная ссылка: только a-z, 0-9, _, от 5 до 32 символов, начинается с буквы"
            )
        # 🔥 Юзернеймы людей и групп/каналов - одно общее пространство имён, как в Telegram
        taken_by_user = db.query(User).filter(User.username == group_username).first()
        taken_by_group = db.query(ChatGroup).filter(ChatGroup.username == group_username).first()
        if taken_by_user or taken_by_group:
            raise HTTPException(status_code=409, detail="Эта ссылка уже занята")
    else:
        # 🔥 Приватная группа/канал - вместо username даём длинный секретный код-приглашение
        invite_code = secrets.token_urlsafe(12)

    group_id = f"group_{uuid.uuid4().hex[:8]}"
    new_group = ChatGroup(
        id=group_id, 
        name=req.name, 
        is_public=req.is_public, 
        is_channel=req.is_channel,
        username=group_username,
        invite_code=invite_code,
        owner_username=req.owner_username
    )
    db.add(new_group)
    new_member = GroupMember(group_id=group_id, user_username=req.owner_username, role="admin")
    db.add(new_member)
    db.commit()
    return {
        "group_id": group_id, 
        "name": req.name,
        "is_public": req.is_public,
        "is_channel": req.is_channel,
        "username": group_username,
        "invite_code": invite_code
    }

# --- 🔥 ИНФОРМАЦИЯ О ГРУППЕ/КАНАЛЕ (моя роль, счётчик участников, инвайт, описание) ---
@router.get("/group-info/{group_id}")
def get_group_info(group_id: str, username: str, db: Session = Depends(get_db)):
    group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    member = db.query(GroupMember).filter(
        GroupMember.group_id == group_id,
        GroupMember.user_username == username
    ).first()
    member_count = db.query(GroupMember).filter(GroupMember.group_id == group_id).count()

    my_role = None
    if member:
        my_role = "owner" if username == group.owner_username else member.role

    return {
        "group_id": group.id,
        "name": group.name,
        "description": group.description,
        "is_public": group.is_public,
        "is_channel": group.is_channel,
        "username": group.username,
        "owner_username": group.owner_username,
        # Инвайт-код отдаём только тому, кто уже состоит в группе - иначе он бесполезен для чужих
        "invite_code": group.invite_code if member else None,
        "my_role": my_role,
        "member_count": member_count
    }

# --- 🔥 СПИСОК УЧАСТНИКОВ С РОЛЯМИ (создатель/админ/участник) ---
@router.get("/group-members/{group_id}")
def get_group_members(group_id: str, db: Session = Depends(get_db)):
    group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    result = []
    for m in members:
        user = db.query(User).filter(User.username == m.user_username).first()
        role = "owner" if m.user_username == group.owner_username else m.role
        result.append({
            "username": m.user_username,
            "nickname": user.nickname if user else m.user_username,
            "avatar_path": user.avatar_path if user else None,
            "role": role
        })
    # Создатель всегда первым, затем админы, затем остальные
    order = {"owner": 0, "admin": 1, "member": 2}
    result.sort(key=lambda x: order.get(x["role"], 3))
    return result

class UpdateGroupDescription(BaseModel):
    group_id: str
    username: str
    description: str

# --- 🔥 РЕДАКТИРОВАНИЕ ОПИСАНИЯ (только владелец/админ) ---
@router.post("/update-group-description")
def update_group_description(req: UpdateGroupDescription, db: Session = Depends(get_db)):
    group = db.query(ChatGroup).filter(ChatGroup.id == req.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    member = db.query(GroupMember).filter(
        GroupMember.group_id == req.group_id,
        GroupMember.user_username == req.username
    ).first()
    is_owner = req.username == group.owner_username
    if not member or not (is_owner or member.role == "admin"):
        raise HTTPException(status_code=403, detail="Изменять описание могут только создатель и админы")

    group.description = req.description.strip()
    db.commit()
    return {"message": "Описание обновлено"}

class LeaveGroupRequest(BaseModel):
    group_id: str
    username: str

# --- 🔥 ВЫХОД ИЗ ГРУППЫ/КАНАЛА ---
@router.post("/leave-group")
def leave_group(req: LeaveGroupRequest, db: Session = Depends(get_db)):
    group = db.query(ChatGroup).filter(ChatGroup.id == req.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    if req.username == group.owner_username:
        raise HTTPException(status_code=400, detail="Создатель не может выйти - только удалить чат целиком")

    member = db.query(GroupMember).filter(
        GroupMember.group_id == req.group_id,
        GroupMember.user_username == req.username
    ).first()
    if member:
        db.delete(member)
        db.commit()
    return {"message": "Вы вышли из чата"}

class DeleteGroupRequest(BaseModel):
    group_id: str
    username: str

# --- 🔥 УДАЛЕНИЕ ГРУППЫ/КАНАЛА ЦЕЛИКОМ (только создатель) ---
@router.post("/delete-group")
def delete_group(req: DeleteGroupRequest, db: Session = Depends(get_db)):
    group = db.query(ChatGroup).filter(ChatGroup.id == req.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    if req.username != group.owner_username:
        raise HTTPException(status_code=403, detail="Удалить чат может только создатель")

    db.query(Message).filter(Message.chat_name == req.group_id).delete()
    db.query(GroupMember).filter(GroupMember.group_id == req.group_id).delete()
    db.delete(group)
    db.commit()
    return {"message": "Чат удалён"}

class JoinGroupRequest(BaseModel):
    code: str
    username: str

# --- 🔥 ВСТУПЛЕНИЕ В ГРУППУ/КАНАЛ: по @username (паблик) или по инвайт-коду (приват) ---
@router.post("/join-group")
def join_group(req: JoinGroupRequest, db: Session = Depends(get_db)):
    cleaned = req.code.strip().lstrip("@")

    group = db.query(ChatGroup).filter(ChatGroup.username == cleaned.lower()).first()
    if not group:
        group = db.query(ChatGroup).filter(ChatGroup.invite_code == req.code.strip()).first()

    if not group:
        raise HTTPException(status_code=404, detail="Ничего не найдено по этой ссылке или username")

    existing_member = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.user_username == req.username
    ).first()

    if not existing_member:
        db.add(GroupMember(group_id=group.id, user_username=req.username, role="member"))
        db.commit()

    return {
        "group_id": group.id,
        "name": group.name,
        "is_public": group.is_public,
        "is_channel": group.is_channel,
        "username": group.username
    }

# --- 🔥 ПОИСК ПУБЛИЧНЫХ ГРУПП/КАНАЛОВ (для глобального поиска) ---
@router.get("/search-groups/{query}")
def search_groups(query: str, db: Session = Depends(get_db)):
    q = f"%{query}%"
    groups = db.query(ChatGroup).filter(
        ChatGroup.is_public == True,
        (ChatGroup.name.ilike(q)) | (ChatGroup.username.ilike(q))
    ).limit(20).all()

    return [{
        "group_id": g.id,
        "name": g.name,
        "username": g.username,
        "is_channel": g.is_channel
    } for g in groups]