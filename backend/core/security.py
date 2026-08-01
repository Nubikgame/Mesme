import os
import re
import random
import smtplib
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# ⚠️ Грузим .env здесь же, а не полагаемся на то, что это уже сделал
# db/database.py. Раньше SMTP_PASSWORD всегда оказывался пустым, потому что
# api/auth.py импортирует core.security РАНЬШЕ db.database, а значит
# на момент чтения os.getenv() ниже load_dotenv() из database.py ещё ни разу
# не вызывался, и значения из .env просто не попадали в os.environ.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# ==========================================
# НАСТРОЙКИ ПОЧТЫ
# ==========================================
SMTP_SERVER = "smtp.gmail.com"  # Если у тебя Яндекс, то smtp.yandex.ru
SMTP_PORT = 465                 # Порт для SSL (безопасного соединения)
SMTP_USER = "mesme.support@gmail.com"
# ❗️ПАРОЛЬ БЕРЁМ ТОЛЬКО ИЗ .env, НИКОГДА не пиши его прямо в коде -
# он утёк в гит-историю в прошлый раз именно из-за того, что был хардкоднут тут.
# Сгенерируй НОВЫЙ пароль приложения (16 символов) в Google-аккаунте:
# Google Account -> Безопасность -> Пароли приложений, и впиши его в backend/.env
# как SMTP_PASSWORD=xxxxxxxxxxxxxxxx
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").replace(" ", "").strip() or None

def generate_otp() -> str:
    """Генерирует случайный 6-значный код"""
    return str(random.randint(100000, 999999))

def normalize_email(raw_email: str) -> Optional[str]:
    """
    Приводит почту к единому виду (без пробелов по краям, в нижнем регистре),
    чтобы Test@Gmail.com и test@gmail.com считались одним и тем же аккаунтом.

    Возвращает None, если почта явно невалидна.
    """
    if not raw_email:
        return None

    email = raw_email.strip().lower()

    # Простая проверка формата, без лишних библиотек
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None

    return email

def send_otp_email(receiver_email: str, otp_code: str) -> bool:
    """Отправляет письмо с кодом через Gmail SMTP"""
    if not SMTP_PASSWORD:
        print("⚠️ SMTP_PASSWORD не задан в backend/.env — письмо не отправлено (код есть только в консоли).")
        return False

    subject = "Код подтверждения Mesme"

    # Текст письма
    body = f"""
    Добро пожаловать в Mesme! 🚀
    
    Ваш код для входа: {otp_code}
    
    Если вы не запрашивали этот код, просто проигнорируйте письмо.
    """

    # Собираем письмо
    msg = MIMEMultipart()
    msg['From'] = f"Mesme App <{SMTP_USER}>"
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        # Подключаемся к серверу и отправляем
        print(f"⏳ Подключение к серверу почты...")
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"✅ Успех! Код {otp_code} отправлен на {receiver_email}")
        return True
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False

# ==========================================
# БЛОК ДЛЯ ТЕСТИРОВАНИЯ
# ==========================================
if __name__ == "__main__":
    test_email = input("Введите почту, куда отправить код: ")
    test_code = generate_otp()
    send_otp_email(test_email, test_code)