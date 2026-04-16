from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
import json
import sys
import os
import uuid
from pydantic import BaseModel
from db.models import Message, ChatGroup, GroupMember

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_db, SessionLocal
from db.models import Message

router = APIRouter()

# --- МЕНЕДЖЕР ВЕБ-СОКЕТОВ (С КОМНАТАМИ) ---
class ConnectionManager:
    def __init__(self):
        # Словарь: Ключ = ID комнаты, Значение = список сокетов
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

    async def broadcast(self, message: str, room_name: str):
        if room_name in self.active_connections:
            for connection in list(self.active_connections[room_name]):
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection, room_name)

manager = ConnectionManager()

# --- МАРШРУТ 1: ПОЛУЧЕНИЕ ИСТОРИИ ЧАТА ---
@router.get("/history/{chat_name}")
def get_chat_history(chat_name: str, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.chat_name == chat_name).order_by(Message.timestamp).all()
    # Возвращаем историю вместе со временем
    return [{"sender": msg.sender, "text": msg.text, "timestamp": msg.timestamp.isoformat()} for msg in messages]

# --- МАРШРУТ 2: ТРУБА (ВЕБ-СОКЕТ) ---
@router.websocket("/ws/{chat_name}")
async def websocket_endpoint(websocket: WebSocket, chat_name: str):
    await manager.connect(websocket, chat_name)
    try:
        while True:
            data = await websocket.receive_text()
            data_dict = json.loads(data)
            
            db = SessionLocal()
            try:
                new_msg = Message(
                    chat_name=chat_name, 
                    sender=data_dict["sender"], 
                    text=data_dict["text"]
                )
                db.add(new_msg)
                db.commit() 
                db.refresh(new_msg)
                
                broadcast_data = {
                    "sender": new_msg.sender,
                    "text": new_msg.text,
                    "timestamp": new_msg.timestamp.isoformat()
                }
                
                await manager.broadcast(json.dumps(broadcast_data), chat_name)
            finally:
                db.close()
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, chat_name)
    except Exception:
        manager.disconnect(websocket, chat_name)
class GroupCreate(BaseModel):
    name: str
    is_public: bool
    owner_username: str

@router.post("/create-group")
def create_group(req: GroupCreate, db: Session = Depends(get_db)):
    # 1. Генерируем уникальный ID для группы (например, group_a1b2c3d4)
    group_id = f"group_{uuid.uuid4().hex[:8]}"

    # 2. Создаем саму группу в БД
    new_group = ChatGroup(
        id=group_id,
        name=req.name,
        is_public=req.is_public,
        owner_username=req.owner_username
    )
    db.add(new_group)

    # 3. Делаем создателя админом этой группы
    new_member = GroupMember(
        group_id=group_id,
        user_username=req.owner_username,
        role="admin"
    )
    db.add(new_member)

    # Сохраняем всё разом
    db.commit()

    # Отдаем приложению ID новой группы
    return {"group_id": group_id, "name": req.name}