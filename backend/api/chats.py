from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
import json
import sys
import os
import uuid
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_db, SessionLocal
from db.models import Message, ChatGroup, GroupMember, User

router = APIRouter()

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

    async def broadcast(self, message: str, room_name: str):
        if room_name in self.active_connections:
            for connection in list(self.active_connections[room_name]):
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection, room_name)

manager = ConnectionManager()

# --- ПОЛУЧЕНИЕ ИСТОРИИ (с полями is_read и is_delivered) ---
@router.get("/history/{chat_name}")
def get_chat_history(chat_name: str, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.chat_name == chat_name).order_by(Message.timestamp).all()
    return [{
        "id": msg.id, 
        "sender": msg.sender, 
        "text": msg.text, 
        "timestamp": msg.timestamp.isoformat(),
        "is_read": msg.is_read,          # 🔥 Прочитано
        "is_delivered": msg.is_delivered  # 🔥 Доставлено (для галочек как в Telegram)
    } for msg in messages]

# --- НОВЫЙ МАРШРУТ: СЧЕТЧИКИ НЕПРОЧИТАННЫХ ---
@router.get("/unread-counts/{username}")
def get_unread_counts(username: str, db: Session = Depends(get_db)):
    # 1. Сначала находим никнейм пользователя по его username, так как в базе в поле sender лежит именно никнейм
    user = db.query(User).filter(User.username == username).first()
    user_nickname = user.nickname if user else username

    # 2. Теперь фильтруем сообщения, исключая те, где sender равен нашему никнейму
    messages = db.query(Message).filter(
        Message.is_read == False,
        Message.sender != user_nickname,  # ← Теперь сравниваем никнейм с никнеймом!
        Message.chat_name.ilike(f"%{username}%")
    ).all()
    
    counts = {}
    for m in messages:
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
    owner_username: str

@router.post("/create-group")
def create_group(req: GroupCreate, db: Session = Depends(get_db)):
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    new_group = ChatGroup(id=group_id, name=req.name, is_public=req.is_public, owner_username=req.owner_username)
    db.add(new_group)
    new_member = GroupMember(group_id=group_id, user_username=req.owner_username, role="admin")
    db.add(new_member)
    db.commit()
    return {"group_id": group_id, "name": req.name}