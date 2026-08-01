from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import sys
import os
import uuid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.security import generate_otp, send_otp_email, normalize_email
from db.database import get_db
from db.models import User, OtpCode

router = APIRouter()

class EmailRequest(BaseModel):
    email: str

class VerifyRequest(BaseModel):
    email: str
    code: str

class ProfileUpdate(BaseModel):
    email: str
    nickname: str
    username: Optional[str] = None

class TokenRequest(BaseModel):
    token: str

# 🔥 КОД ХРАНИМ В БАЗЕ, А НЕ В ПАМЯТИ ПРОЦЕССА!
# Раньше temp_codes был обычным словарём в оперативной памяти - и любой
# перезапуск сервера (например, автоперезагрузка uvicorn --reload при
# сохранении файла) полностью его стирал. Итог: код только что пришёл
# на почту и совпадает с введённым, а сервер всё равно говорит "неверный код",
# потому что уже не помнит, что вообще его выдавал. Храня код в БД,
# переживаем любой перезапуск.

# 🔥 ПИСЬМО ШЛЁТСЯ ПО-НАСТОЯЩЕМУ (SMTP), ПОЭТОМУ ЭТО БЛОКИРУЮЩАЯ ОПЕРАЦИЯ -
# async ЗДЕСЬ УБРАН, ЧТОБЫ FASTAPI САМ УНЁС ВЫЗОВ В ОТДЕЛЬНЫЙ ПОТОК
# И НЕ ЗАВИСАЛ НА ВРЕМЯ ОТПРАВКИ ПИСЬМА
@router.post("/request-code")
def request_code(req: EmailRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    if not email:
        raise HTTPException(status_code=400, detail="Некорректный email")

    existing = db.query(OtpCode).filter(OtpCode.email == email).first()

    # Защита от спама кнопкой
    if existing and existing.expires_at > datetime.utcnow():
        raise HTTPException(status_code=429, detail="Код уже отправлен. Подождите.")

    code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    if existing:
        existing.code = code
        existing.expires_at = expires_at
    else:
        db.add(OtpCode(email=email, code=code, expires_at=expires_at))
    db.commit()

    sent = send_otp_email(email, code)

    # Код всегда дублируем в консоль сервера - удобно для локальной разработки,
    # даже если письмо ушло успешно
    print("\n" + "="*50)
    print(f"КОД ДЛЯ ВХОДА ({email}): {code}" + ("" if sent else "  [письмо НЕ отправлено — см. ошибку выше]"))
    print("="*50 + "\n")

    if sent:
        return {"message": "Код отправлен на почту"}
    return {"message": "Почта не настроена — код выведен в консоль сервера"}


# 🔥 А ВОТ ОТСЮДА И НИЖЕ СЛОВО ASYNC УБРАНО!
@router.post("/verify-code")
def verify_code(req: VerifyRequest, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    if not email:
        raise HTTPException(status_code=400, detail="Некорректный email")

    data = db.query(OtpCode).filter(OtpCode.email == email).first()
    
    # Проверяем наличие, срок годности и сам код
    if not data or data.expires_at < datetime.utcnow() or data.code != req.code:
        raise HTTPException(status_code=400, detail="Неверный или просроченный код")
    
    db.delete(data)
    db.commit()
    
    user = db.query(User).filter(User.email == email).first()
    is_new = False
    
    if not user:
        is_new = True
        user = User(email=email)
        db.add(user)
    
    access_token = str(uuid.uuid4())
    user.token = access_token
    db.commit()
    db.refresh(user)
    
    return {
        "token": access_token,
        "is_new_user": is_new,
        "email": user.email,
        "nickname": user.nickname,
        "username": user.username
    }

@router.post("/update-profile")
def update_profile(req: ProfileUpdate, db: Session = Depends(get_db)):
    email = normalize_email(req.email)
    if not email:
        raise HTTPException(status_code=400, detail="Некорректный email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Юзер не найден")
    
    user.nickname = req.nickname
    user.username = req.username
    db.commit()
    return {"message": "Успешно"}

@router.post("/get-profile")
def get_profile(req: TokenRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.token == req.token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Токен невалиден")
    
    return {
        "email": user.email,
        "nickname": user.nickname,
        "username": user.username,
        "avatar_path": user.avatar_path
    }

@router.get("/find-user/{username}")
def find_user(username: str, db: Session = Depends(get_db)):
    clean_username = username.replace("@", "").strip()
    user = db.query(User).filter(User.username == clean_username).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    return {"nickname": user.nickname, "username": user.username}

# --- 🔥 ПРОФИЛЬ ДРУГОГО ПОЛЬЗОВАТЕЛЯ (для карточки "инфо о юзере") ---
@router.get("/user-profile/{username}")
def get_user_profile(username: str, db: Session = Depends(get_db)):
    clean_username = username.replace("@", "").strip()
    user = db.query(User).filter(User.username == clean_username).first()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    return {
        "nickname": user.nickname,
        "username": user.username,
        "avatar_path": user.avatar_path
    }

@router.get("/search-users/{query}")
def search_users(query: str, db: Session = Depends(get_db)):
    clean_query = query.replace("@", "").strip()
    
    # Ищем частичные совпадения (ilike игнорирует регистр)
    users = db.query(User).filter(
        (User.username.ilike(f"%{clean_query}%")) | 
        (User.nickname.ilike(f"%{clean_query}%"))
    ).limit(10).all()
    
    # Возвращаем список найденных пользователей
    return [{"nickname": u.nickname, "username": u.username} for u in users]