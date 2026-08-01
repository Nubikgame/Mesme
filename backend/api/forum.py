from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime
from typing import Optional
import sys
import os
import uuid
import shutil
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_db, SessionLocal
from db.models import User, ForumPost, ForumComment, ForumSupport

router = APIRouter()

# 🔥 Папка для картинок к багам (Mesme/media/forum)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORUM_MEDIA_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "media", "forum")
os.makedirs(FORUM_MEDIA_DIR, exist_ok=True)

STATUS_LABELS = {
    "considering": "Рассматривается",
    "in_progress": "В работе",
    "implemented": "Реализовано"
}


def is_developer(db: Session, username: Optional[str]) -> bool:
    if not username:
        return False
    user = db.query(User).filter(User.username == username).first()
    return bool(user and user.is_developer)


def require_developer(db: Session, username: str):
    if not is_developer(db, username):
        raise HTTPException(status_code=403, detail="Доступно только разработчику")


def post_to_dict(db: Session, p: ForumPost, username: Optional[str] = None) -> dict:
    support_count = db.query(ForumSupport).filter(ForumSupport.post_id == p.id).count()
    comment_count = db.query(ForumComment).filter(ForumComment.post_id == p.id).count()
    i_supported = False
    if username:
        i_supported = db.query(ForumSupport).filter(
            ForumSupport.post_id == p.id, ForumSupport.username == username
        ).first() is not None

    return {
        "id": p.id,
        "type": p.type,
        "title": p.title,
        "description": p.description,
        "steps_to_reproduce": p.steps_to_reproduce,
        "image_url": p.image_url,
        "author_username": p.author_username,
        "author_nickname": p.author_nickname,
        "created_at": p.created_at.isoformat(),
        "status": p.status,
        "status_label": STATUS_LABELS.get(p.status, p.status),
        "is_priority": p.is_priority,
        "is_pinned": p.is_pinned,
        "implemented_version": p.implemented_version,
        "implemented_at": p.implemented_at.isoformat() if p.implemented_at else None,
        "comments_closed": p.comments_closed,
        "support_count": support_count,
        "comment_count": comment_count,
        "i_supported": i_supported
    }


def dev_reply_exists(db: Session, post_id: str) -> bool:
    comments = db.query(ForumComment).filter(ForumComment.post_id == post_id).all()
    for c in comments:
        if is_developer(db, c.author_username):
            return True
    return False


# --- 🔥 СОЗДАНИЕ ПУБЛИКАЦИИ (идея или баг) ---
class ForumPostCreate(BaseModel):
    type: str  # "idea" | "bug"
    title: str
    description: str
    steps_to_reproduce: Optional[str] = None
    image_url: Optional[str] = None
    username: str

@router.post("/create-post")
def create_post(req: ForumPostCreate, db: Session = Depends(get_db)):
    if req.type not in ("idea", "bug"):
        raise HTTPException(status_code=400, detail="Некорректный тип публикации")
    if not req.title.strip() or not req.description.strip():
        raise HTTPException(status_code=400, detail="Заполните заголовок и описание")

    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    post = ForumPost(
        id=f"forum_{uuid.uuid4().hex[:8]}",
        type=req.type,
        title=req.title.strip(),
        description=req.description.strip(),
        steps_to_reproduce=req.steps_to_reproduce.strip() if req.steps_to_reproduce else None,
        image_url=req.image_url,
        author_username=req.username,
        author_nickname=user.nickname
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post_to_dict(db, post, req.username)


# --- 🔥 ПОИСК ПОХОЖИХ ТЕМ - вызывается перед публикацией идеи ---
@router.get("/search-similar")
def search_similar(title: str, db: Session = Depends(get_db)):
    words = [w for w in title.strip().lower().split() if len(w) > 2]
    if not words:
        return []
    conditions = [ForumPost.title.ilike(f"%{w}%") for w in words]
    posts = db.query(ForumPost).filter(or_(*conditions)).limit(5).all()
    return [{"id": p.id, "title": p.title, "type": p.type} for p in posts]


# --- 🔥 СПИСОК ПУБЛИКАЦИЙ (с фильтрами, сортировкой, поиском) ---
@router.get("/posts")
def get_posts(
    filter: Optional[str] = None,   # priority | implemented | mine
    sort: Optional[str] = "new",    # new | popular | no_dev_reply
    username: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    q = db.query(ForumPost)

    if search:
        like = f"%{search}%"
        q = q.filter(or_(ForumPost.title.ilike(like), ForumPost.description.ilike(like)))

    if filter == "priority":
        q = q.filter(ForumPost.is_priority == True)
    elif filter == "implemented":
        q = q.filter(ForumPost.status == "implemented")
    elif filter == "mine":
        if not username:
            raise HTTPException(status_code=400, detail="Не указан username")
        q = q.filter(ForumPost.author_username == username)

    posts = q.all()

    # Сортировка (стабильная - сначала по дате, затем, если нужно, перегруппировка)
    posts.sort(key=lambda p: p.created_at, reverse=True)
    if sort == "popular":
        posts.sort(key=lambda p: db.query(ForumSupport).filter(ForumSupport.post_id == p.id).count(), reverse=True)
    elif sort == "no_dev_reply":
        posts.sort(key=lambda p: dev_reply_exists(db, p.id))

    # 📌 Закреплённые - всегда сверху, но только в дефолтном виде (без фильтра и без активного поиска)
    if not filter and not search:
        pinned = [p for p in posts if p.is_pinned]
        rest = [p for p in posts if not p.is_pinned]
        posts = pinned + rest

    return [post_to_dict(db, p, username) for p in posts]


# --- 🔥 ОДНА ПУБЛИКАЦИЯ + КОММЕНТАРИИ ---
@router.get("/post/{post_id}")
def get_post(post_id: str, username: Optional[str] = None, db: Session = Depends(get_db)):
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")

    comments = db.query(ForumComment).filter(ForumComment.post_id == post_id).order_by(ForumComment.created_at).all()
    comments_data = [{
        "id": c.id,
        "author_nickname": c.author_nickname,
        "text": c.text,
        "created_at": c.created_at.isoformat(),
        "is_developer_reply": is_developer(db, c.author_username)
    } for c in comments]

    data = post_to_dict(db, post, username)
    data["comments"] = comments_data
    data["is_developer"] = is_developer(db, username)
    return data


# --- 🔥 ПОДДЕРЖАТЬ / СНЯТЬ ПОДДЕРЖКУ (тоггл) ---
class SupportRequest(BaseModel):
    post_id: str
    username: str

@router.post("/support")
def toggle_support(req: SupportRequest, db: Session = Depends(get_db)):
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")

    existing = db.query(ForumSupport).filter(
        ForumSupport.post_id == req.post_id, ForumSupport.username == req.username
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        supported = False
    else:
        db.add(ForumSupport(post_id=req.post_id, username=req.username))
        db.commit()
        supported = True

    count = db.query(ForumSupport).filter(ForumSupport.post_id == req.post_id).count()
    return {"supported": supported, "support_count": count}


# --- 🔥 КОММЕНТАРИЙ ---
class CommentRequest(BaseModel):
    post_id: str
    username: str
    text: str

@router.post("/comment")
def add_comment(req: CommentRequest, db: Session = Depends(get_db)):
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    if post.comments_closed:
        raise HTTPException(status_code=403, detail="Комментарии к этой публикации закрыты")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Пустой комментарий")

    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    comment = ForumComment(
        post_id=req.post_id,
        author_username=req.username,
        author_nickname=user.nickname,
        text=req.text.strip()
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return {
        "id": comment.id,
        "author_nickname": comment.author_nickname,
        "text": comment.text,
        "created_at": comment.created_at.isoformat(),
        "is_developer_reply": bool(user.is_developer)
    }


# --- 🔥 ЗАГРУЗКА КАРТИНКИ К БАГУ ---
@router.post("/upload-image")
async def upload_forum_image(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(FORUM_MEDIA_DIR, unique_name)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"image_url": f"/media/forum/{unique_name}"}


# =====================================================
# 🔥 ДЕЙСТВИЯ РАЗРАБОТЧИКА
# =====================================================
class DevActionRequest(BaseModel):
    post_id: str
    username: str

@router.post("/toggle-priority")
def toggle_priority(req: DevActionRequest, db: Session = Depends(get_db)):
    require_developer(db, req.username)
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    post.is_priority = not post.is_priority
    db.commit()
    return {"is_priority": post.is_priority}

@router.post("/toggle-pin")
def toggle_pin(req: DevActionRequest, db: Session = Depends(get_db)):
    require_developer(db, req.username)
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    post.is_pinned = not post.is_pinned
    db.commit()
    return {"is_pinned": post.is_pinned}

@router.post("/toggle-comments-closed")
def toggle_comments_closed(req: DevActionRequest, db: Session = Depends(get_db)):
    require_developer(db, req.username)
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    post.comments_closed = not post.comments_closed
    db.commit()
    return {"comments_closed": post.comments_closed}

@router.post("/delete-post")
def delete_post(req: DevActionRequest, db: Session = Depends(get_db)):
    require_developer(db, req.username)
    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")

    db.query(ForumComment).filter(ForumComment.post_id == req.post_id).delete()
    db.query(ForumSupport).filter(ForumSupport.post_id == req.post_id).delete()
    db.delete(post)
    db.commit()
    return {"message": "Публикация удалена"}

class SetStatusRequest(BaseModel):
    post_id: str
    username: str
    status: str
    implemented_version: Optional[str] = None

@router.post("/set-status")
def set_status(req: SetStatusRequest, db: Session = Depends(get_db)):
    require_developer(db, req.username)
    if req.status not in ("considering", "in_progress", "implemented"):
        raise HTTPException(status_code=400, detail="Некорректный статус")

    post = db.query(ForumPost).filter(ForumPost.id == req.post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")

    post.status = req.status
    if req.status == "implemented":
        post.implemented_at = datetime.utcnow()
        post.implemented_version = req.implemented_version
    else:
        post.implemented_at = None
        post.implemented_version = None
    db.commit()
    return {"message": "Статус обновлён"}


# =====================================================
# 🔥 ЗАСЕВ ЗАКРЕПЛЁННЫХ ПУБЛИКАЦИЙ - один раз, при старте сервера (main.py вызывает это)
# =====================================================
def seed_pinned_posts():
    db = SessionLocal()
    try:
        seeds = [
            {
                "id": "forum_welcome",
                "title": "Добро пожаловать",
                "description": (
                    "Это форум идей MesMe. Здесь можно предложить идею для приложения или "
                    "сообщить об ошибке — разработчик читает всё и отвечает прямо тут.\n\n"
                    "Как это работает:\n"
                    "— Нажмите «Предложить идею» или «Сообщить о баге» вверху экрана.\n"
                    "— Поддерживайте лайком те публикации, которые считаете важными — это "
                    "помогает понять, что делать в первую очередь.\n"
                    "— Следите за статусом: 🟡 рассматривается, 🔵 в работе, 🟢 реализовано.\n\n"
                    "Спасибо, что помогаете делать MesMe лучше!"
                ),
            },
            {
                "id": "forum_bug_guide",
                "title": "Как правильно сообщать о багах",
                "description": (
                    "Чтобы баг быстрее нашли и починили, постарайтесь указать:\n\n"
                    "1. Что вы делали перед тем, как что-то пошло не так.\n"
                    "2. Что произошло на самом деле и что вы ожидали увидеть вместо этого.\n"
                    "3. Повторяется ли проблема каждый раз или иногда.\n"
                    "4. Скриншот, если проблема визуальная — его можно прикрепить прямо в форме.\n\n"
                    "Чем подробнее шаги воспроизведения, тем быстрее дойдёт очередь до исправления."
                ),
            },
            {
                "id": "forum_roadmap",
                "title": "Планы развития MesMe (Roadmap)",
                "description": (
                    "Здесь будут появляться крупные направления развития MesMe по мере того, "
                    "как они определяются. Пока список короткий — он будет пополняться.\n\n"
                    "Если хотите повлиять на порядок, в котором всё это будет делаться — "
                    "поддерживайте лайками соответствующие идеи в разделе ⭐ Приоритетные."
                ),
            },
        ]
        for s in seeds:
            if not db.query(ForumPost).filter(ForumPost.id == s["id"]).first():
                db.add(ForumPost(
                    id=s["id"],
                    type="idea",
                    title=s["title"],
                    description=s["description"],
                    author_username="mesme",
                    author_nickname="Команда MesMe",
                    is_pinned=True,
                    status="considering"
                ))
        db.commit()
    finally:
        db.close()
