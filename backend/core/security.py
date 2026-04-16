import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================
# ⚠️ НАСТРОЙКИ ПОЧТЫ (Замени на свои данные)
# ==========================================
SMTP_SERVER = "smtp.gmail.com" # Если у тебя Яндекс, то smtp.yandex.ru
SMTP_PORT = 465                # Порт для SSL (безопасного соединения)
SMTP_USER = "mesme.support@gmail.com" 
SMTP_PASSWORD = "crxhkzxglxdhkoj" # ❗️ Сюда нужен не обычный пароль от почты!

def generate_otp() -> str:
    """Генерирует случайный 6-значный код"""
    return str(random.randint(100000, 999999))

def send_otp_email(receiver_email: str, otp_code: str):
    """Отправляет красивое письмо с кодом"""
    subject = "Код подтверждения Mesmi"
    
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