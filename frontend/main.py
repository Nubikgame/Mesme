import flet as ft
import requests
import time
import json
import os
import websocket
import threading
from datetime import datetime, timezone


SERVER_IP = "127.0.0.1"

API_URL = "http://127.0.0.1:8000/auth"
CHAT_API_URL = "http://127.0.0.1:8000/chat"
FORUM_API_URL = "http://127.0.0.1:8000/forum"
MEDIA_BASE_URL = "http://127.0.0.1:8000"

# 🔥 Палитра для аватарок без фото - у одного и того же человека всегда один и тот же цвет
AVATAR_PALETTE = ["#4A90E2", "#E27D60", "#8E44AD", "#16A085", "#C0392B", "#2980B9", "#D35400", "#27AE60"]

def get_avatar_color(name: str) -> str:
    if not name:
        return AVATAR_PALETTE[0]
    return AVATAR_PALETTE[sum(ord(ch) for ch in name) % len(AVATAR_PALETTE)]

# 🔥 Набор эмодзи для быстрой вставки в сообщение (без внешних библиотек)
COMMON_EMOJIS = [
    "😀", "😂", "😍", "😊", "😉", "😎", "🤔", "😅", "😢", "😭",
    "😡", "😴", "🥳", "🤗", "😱", "🙄", "👍", "👎", "👏", "🙏",
    "👋", "💪", "❤️", "🔥", "🎉", "💯", "✅", "❌", "⭐", "😘"
]

def format_last_seen(iso_ts) -> str:
    """Превращает ISO-таймстамп последнего захода в текст вроде 'был(а) в сети 5 мин назад'"""
    if not iso_ts:
        return "не в сети"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "не в сети"

    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = delta.total_seconds()

    if seconds < 60:
        return "был(а) в сети только что"
    if seconds < 3600:
        mins = int(seconds // 60)
        return f"был(а) в сети {mins} мин назад"
    if seconds < 86400 and dt.astimezone().date() == now.astimezone().date():
        return f"был(а) в сети сегодня в {dt.astimezone().strftime('%H:%M')}"
    if seconds < 172800:
        return f"был(а) в сети вчера в {dt.astimezone().strftime('%H:%M')}"
    return f"был(а) в сети {dt.astimezone().strftime('%d.%m.%Y')}"

def derive_other_username(chat_id: str, my_username: str):
    """Восстанавливает username собеседника прямо из id приватного чата (p2p_a_b),
    зная свой username. Нужно на случай, если в сохранённом чате other_username
    не записан (например, чат создан ещё до того, как появилось это поле).
    Работает даже если в username встречаются подчёркивания - ищем не по split("_"),
    а проверяем точное совпадение своего username как префикса или суффикса."""
    if not chat_id or not my_username or not chat_id.startswith("p2p_"):
        return None
    combined = chat_id[len("p2p_"):]
    if combined.startswith(my_username + "_"):
        return combined[len(my_username) + 1:]
    if combined.endswith("_" + my_username):
        return combined[:-(len(my_username) + 1)]
    return None

# 🔥 Статусы публикаций форума - ровно три, как в ТЗ
STATUS_ICONS = {"considering": "🟡", "in_progress": "🔵", "implemented": "🟢"}

def format_relative_date(iso_ts) -> str:
    """Относительная дата без 'был в сети' - для постов и комментариев форума"""
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return ""

    now = datetime.now(timezone.utc)
    seconds = (now - dt).total_seconds()

    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    if seconds < 86400 and dt.astimezone().date() == now.astimezone().date():
        return dt.astimezone().strftime("%H:%M")
    if seconds < 172800:
        return "вчера"
    return dt.astimezone().strftime("%d.%m.%Y")

def main(page: ft.Page):
    page.title = "Mesme"
    page.window.width = 400
    page.window.height = 700

    # --- Цветовая палитра ---
    teal = "#1E4B4B"
    peach = "#FAD6A5"
    white = "#FFFFFF"
    dark_bg = "#1E2A2A"
    card_bg = "#152020"

    # Глобальные данные текущего юзера
    user_info = {
        "email": "", 
        "nickname": "", 
        "username": "", 
        "avatar_path": None
    }
    
    # ==========================================
    # ПЕРЕМЕННЫЕ ЧАТА И СОКЕТОВ
    # ==========================================
    chat_messages_list = ft.ListView(
        expand=True, 
        spacing=10, 
        padding=20, 
        auto_scroll=True
    )
    
    current_open_chat = None
    current_chat_is_channel = False  # 🔥 если True - все сообщения рисуем слева, даже свои (это канал, а не ЛС)
    ws_app = None
    blue_accent = "#4A90E2" 
    my_message_status_ctrls = {}  # msg_id -> ft.Text с галочками (пока сообщение не прочитано)
    presence_started = False  # чтобы не открывать соединение "онлайн" повторно на каждый show_main_screen
    presence_ws = None

    def get_chats_key():
        return f"mesme_chats_{user_info['email']}"

    # 🔥 ЧИНИМ СТАРЫЕ СОХРАНЁННЫЕ ЛС БЕЗ other_username - поле появилось не сразу,
    # чаты, созданные до этого, просто не пускали в профиль собеседника (тап ничего не делал,
    # потому что там реально было нечего открывать). Восстанавливаем username из chat_id.
    def heal_saved_chats():
        my_un = user_info.get("username")
        if not my_un:
            return
        sc = page.client_storage.get(get_chats_key()) or []
        changed = False
        for c in sc:
            if c.get("chat_id", "").startswith("p2p_") and not c.get("other_username"):
                derived = derive_other_username(c["chat_id"], my_un)
                if derived:
                    c["other_username"] = derived
                    changed = True
        if changed:
            page.client_storage.set(get_chats_key(), sc)

    # 🔥 ОБЩИЕ ХЕЛПЕРЫ ОФОРМЛЕНИЯ СПИСКОВ - используются в поиске и на форуме
    def section_header(text, icon):
        return ft.Container(
            padding=ft.padding.only(left=16, right=16, top=14, bottom=6),
            content=ft.Row(
                [ft.Icon(icon, size=14, color=teal), ft.Text(text.upper(), color=teal, weight="bold", size=12)],
                spacing=6
            )
        )

    def empty_state(icon, text):
        return ft.Container(
            padding=ft.padding.only(top=60, left=30, right=30),
            alignment=ft.alignment.center,
            content=ft.Column(
                [ft.Icon(icon, size=42, color="#BDBDBD"), ft.Container(height=8), ft.Text(text, color="grey", size=14, text_align=ft.TextAlign.CENTER)],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER
            )
        )

    # 🔥 ЛЁГКОЕ ПОСТОЯННОЕ СОЕДИНЕНИЕ "Я ОНЛАЙН" - живёт всё время, пока открыто приложение
    def start_presence_connection():
        nonlocal presence_started
        username = user_info.get("username")
        if not username or presence_started:
            return
        presence_started = True

        def run_presence():
            nonlocal presence_ws
            while True:
                try:
                    presence_ws = websocket.WebSocketApp(f"ws://127.0.0.1:8000/chat/presence/{username}")
                    presence_ws.run_forever()
                except Exception:
                    pass
                time.sleep(5)  # если соединение оборвалось - пробуем переподключиться

        threading.Thread(target=run_presence, daemon=True).start()

    # 🔥 ФУНКЦИЯ ГЕНЕРАЦИИ ID ПРИВАТНОЙ КОМНАТЫ
    def get_private_room_id(u1, u2):
        return "p2p_" + "_".join(sorted([u1, u2]))

    # 🔥 ОБРАБОТЧИК СООБЩЕНИЙ С УЧЕТОМ ВРЕМЕНИ
    def on_ws_message(ws, message):
        data = json.loads(message)
        action = data.get("action", "new_message")
        
        if action == "new_message":
            append_message_to_ui(
                sender=data.get("sender"), 
                text=data.get("text"), 
                timestamp=data.get("timestamp"), 
                is_read=data.get("is_read", False),
                is_delivered=data.get("is_delivered", False),
                msg_id=data.get("id"),
                file_url=data.get("file_url"),
                file_name=data.get("file_name")
            )
            # 🔥 Если сообщение прислал не я, а чат у меня в этот момент открыт -
            # значит я его вижу вживую, сразу шлём mark_read. Раньше mark_read
            # уходил только один раз при заходе в чат, и статус "прочитано" переставал
            # обновляться для сообщений, пришедших уже во время открытой переписки
            if data.get("sender") != user_info.get("nickname"):
                try:
                    ws.send(json.dumps({"action": "mark_read", "sender": user_info.get("nickname")}))
                except:
                    pass
        elif action == "messages_read":
            reader = data.get("reader")
            # Если прочитал не я, значит прочитали МОИ сообщения. Красим галочки в синий!
            if reader != user_info.get("nickname"):
                for status_ctrl in my_message_status_ctrls.values():
                    status_ctrl.value = "✓✓"
                    status_ctrl.color = blue_accent
                page.update()
                my_message_status_ctrls.clear()
        elif action == "error":
            # 🔥 Например, попытка написать в канал не будучи админом
            page.snack_bar = ft.SnackBar(ft.Text(data.get("detail", "Ошибка")))
            page.snack_bar.open = True
            page.update()

    def append_message_to_ui(sender, text, timestamp=None, is_read=False, is_delivered=False, msg_id=None, file_url=None, file_name=None, update=True):
        if timestamp:
            utc_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=timezone.utc)
            local_dt = utc_dt.astimezone()
            time_str = local_dt.strftime("%H:%M")
        else:
            time_str = datetime.now().strftime("%H:%M")

        # 🔥 В каналах пост всегда выглядит как "не моё сообщение" - слева, без галочек,
        # даже если его отправил я сам. Так во всех мессенджерах: канал - это вещание
        # от имени канала, а не личная переписка автора поста.
        is_me = (sender == user_info.get("nickname")) and not current_chat_is_channel
        
        bubble_color = teal if is_me else "#F0F0F0"
        text_color = white if is_me else "black"
        alignment = ft.MainAxisAlignment.END if is_me else ft.MainAxisAlignment.START
        bubble_width = None if len(text or "") < 30 else 250

        is_private_chat = current_open_chat and current_open_chat.startswith("p2p_")
        
        bubble_content = []
        if not is_private_chat:
            bubble_content.append(ft.Text(sender, size=10, color=peach if is_me else "grey", weight="bold"))

        # 🔥 Прикреплённый файл - картинка показывается превью, остальное - карточкой со скачиванием
        if file_url:
            full_url = f"{MEDIA_BASE_URL}{file_url}"
            ext = (file_name or "").lower().rsplit(".", 1)[-1] if "." in (file_name or "") else ""
            if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
                bubble_content.append(
                    ft.Container(
                        content=ft.Image(src=full_url, width=200, border_radius=10, fit=ft.ImageFit.COVER),
                        on_click=lambda _, u=full_url: page.launch_url(u),
                        border_radius=10
                    )
                )
            else:
                bubble_content.append(
                    ft.Container(
                        padding=10, border_radius=8,
                        bgcolor="#00000022" if is_me else "#00000011",
                        on_click=lambda _, u=full_url: page.launch_url(u),
                        content=ft.Row(
                            [ft.Icon(ft.icons.INSERT_DRIVE_FILE, color=text_color), 
                             ft.Text(file_name or "Файл", color=text_color, size=13)],
                            spacing=8
                        )
                    )
                )
            bubble_width = 220

        if text:
            bubble_content.append(ft.Text(text, color=text_color, selectable=True))

        # 🔥 ЛОГИКА ОТРИСОВКИ ГАЛОЧЕК - как в Telegram:
        # ✓ серая = отправлено, ✓✓ серые = доставлено, ✓✓ синие = прочитано
        status_ctrl = ft.Text("", size=10)
        if is_me:
            if is_read:
                status_ctrl.value = "✓✓"
                status_ctrl.color = blue_accent
            elif is_delivered:
                status_ctrl.value = "✓✓"
                status_ctrl.color = "grey"
            else:
                status_ctrl.value = "✓"
                status_ctrl.color = "grey"

            # Пока сообщение не прочитано - запоминаем контрол по id, чтобы потом
            # обновить его на месте (доставлено -> прочитано), не перерисовывая весь чат
            if not is_read and msg_id is not None:
                my_message_status_ctrls[msg_id] = status_ctrl

        bottom_row = ft.Row(
            [ft.Text(time_str, size=9, color="grey"), status_ctrl], 
            alignment=ft.MainAxisAlignment.END, spacing=3
        )
        bubble_content.append(bottom_row)

        message_bubble = ft.Row([
            ft.Container(
                content=ft.Column(bubble_content, spacing=2),
                bgcolor=bubble_color, padding=10, border_radius=10, width=bubble_width
            )
        ], alignment=alignment)
        
        chat_messages_list.controls.append(message_bubble)
        if update:
            page.update()
    def show_create_group_screen(e, is_channel=False):
        page.clean()
        page.bgcolor = white

        # Скрываем навигацию и FAB
        if page.navigation_bar:
            page.navigation_bar.visible = False
        if page.floating_action_button:
            page.floating_action_button.visible = False

        word = "канал" if is_channel else "группу"
        word_a = "канала" if is_channel else "группы"

        def go_back(e):
            show_new_message_screen(None)   # возвращаемся на экран "Новое сообщение"

        page.appbar = ft.AppBar(
            leading=ft.IconButton(
                icon=ft.icons.ARROW_BACK,
                icon_color="black",
                on_click=go_back
            ),
            title=ft.Text("Новый канал" if is_channel else "Новая группа", color="black", weight="bold"),
            bgcolor=white,
            elevation=0,
            visible=True
        )

        name_input = ft.TextField(
            label=f"Название {word_a}", 
            border_color=teal, 
            autofocus=True,
            width=350
        )

        username_input = ft.TextField(
            label="Публичная ссылка",
            prefix_text="@",
            hint_text="myname",
            border_color=teal,
            width=350,
            visible=True
        )

        hint_widget = ft.Text(
            f"Публичный {word} виден в поиске всем. Приватный - только по ссылке-приглашению.",
            color="grey", size=12, width=350
        )

        def on_public_change(e):
            username_input.visible = is_public_switch.value
            username_input.error_text = None
            page.update()

        is_public_switch = ft.Switch(
            label="Публичный (виден в поиске)", 
            value=True, 
            active_color=peach,
            on_change=on_public_change
        )

        # 🔥 Экран после успешного создания ПРИВАТНОГО чата - показываем код-приглашение
        def show_invite_code_screen(name, group_id, invite_code):
            page.clean()

            code_field = ft.TextField(value=invite_code, read_only=True, border_color=teal, width=350, text_size=13)

            def copy_code(e):
                try:
                    page.set_clipboard(invite_code)
                    page.snack_bar = ft.SnackBar(ft.Text("Код скопирован!"))
                    page.snack_bar.open = True
                    page.update()
                except Exception:
                    pass

            page.add(
                ft.Column(
                    expand=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=16,
                    controls=[
                        ft.Container(height=40),
                        ft.Icon(ft.icons.LOCK_OUTLINE, size=42, color=teal),
                        ft.Text(f"«{name}» создан{'' if is_channel else 'а'}!", size=20, weight="bold", color=teal),
                        ft.Text(
                            "Это приватный чат. Отправьте этот код тем, кого хотите пригласить:", 
                            color="grey", size=13, text_align=ft.TextAlign.CENTER, width=300
                        ),
                        code_field,
                        ft.TextButton("Скопировать код", icon=ft.icons.COPY, on_click=copy_code),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Перейти в чат", bgcolor=teal, color=white, width=350,
                            on_click=lambda e: show_chat_screen(name, group_id, is_channel=is_channel)
                        )
                    ]
                )
            )
            page.update()

        def create_btn_click(e):
            name = name_input.value.strip()
            if not name:
                name_input.error_text = f"Введите название {word_a}!"
                page.update()
                return

            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return

            payload = {
                "name": name,
                "is_public": is_public_switch.value,
                "is_channel": is_channel,
                "owner_username": my_un
            }
            if is_public_switch.value:
                payload["username"] = username_input.value

            create_button.disabled = True
            create_button.text = "Создание..."
            page.update()

            try:
                res = requests.post(f"{CHAT_API_URL}/create-group", json=payload, timeout=8)

                if res.status_code == 200:
                    data = res.json()
                    group_id = data["group_id"]

                    # Сохраняем группу в сохранённые чаты
                    saved_chats = page.client_storage.get(get_chats_key()) or []
                    if not any(c["chat_id"] == group_id for c in saved_chats):
                        saved_chats.append({"title": name, "chat_id": group_id, "is_channel": is_channel})
                        page.client_storage.set(get_chats_key(), saved_chats)

                    if data.get("invite_code"):
                        show_invite_code_screen(name, group_id, data["invite_code"])
                    else:
                        # Переходим сразу в созданную публичную группу/канал
                        show_chat_screen(name, group_id, is_channel=is_channel)
                else:
                    try:
                        detail = res.json().get("detail", "Ошибка сервера")
                    except Exception:
                        detail = "Ошибка сервера"
                    page.snack_bar = ft.SnackBar(ft.Text(detail))
                    page.snack_bar.open = True
                    create_button.disabled = False
                    create_button.text = f"Создать {word}"
                    page.update()
            except Exception as ex:
                print(f"Ошибка создания {word_a}:", ex)
                page.snack_bar = ft.SnackBar(ft.Text("Сервер недоступен или ошибка соединения"))
                page.snack_bar.open = True
                create_button.disabled = False
                create_button.text = f"Создать {word}"
                page.update()

        # Кнопка создания
        create_button = ft.ElevatedButton(
            f"Создать {word}",
            bgcolor=teal,
            color=white,
            width=350,
            on_click=create_btn_click
        )

        # Основной контент
        content = ft.Column(
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=15,
            controls=[
                ft.Container(height=20),
                ft.Text(f"Создание нового {word_a}" if is_channel else f"Создание новой {word_a}", size=22, weight="bold", color=teal),
                name_input,
                is_public_switch,
                username_input,
                hint_widget,
                ft.Container(height=10),
                create_button
            ]
        )

        page.add(content)
        page.update()   # ← Это критично!

    # 🔥 ВСТУПЛЕНИЕ В ГРУППУ/КАНАЛ ПО @USERNAME ИЛИ ИНВАЙТ-КОДУ
    def show_join_group_screen(e=None):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar:
            page.navigation_bar.visible = False
        if page.floating_action_button:
            page.floating_action_button.visible = False

        def go_back(e):
            show_new_message_screen(None)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(icon=ft.icons.ARROW_BACK, icon_color="black", on_click=go_back),
            title=ft.Text("Присоединиться", color="black", weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        code_input = ft.TextField(
            label="@username или код-приглашение",
            border_color=teal, autofocus=True, width=350
        )

        def join_click(e):
            code = code_input.value.strip()
            if not code:
                code_input.error_text = "Введите @username или код!"
                page.update()
                return

            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return

            join_button.disabled = True
            join_button.text = "Вход..."
            page.update()

            try:
                res = requests.post(f"{CHAT_API_URL}/join-group", json={"code": code, "username": my_un}, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    group_id = data["group_id"]
                    name = data["name"]

                    saved_chats = page.client_storage.get(get_chats_key()) or []
                    if not any(c["chat_id"] == group_id for c in saved_chats):
                        saved_chats.append({"title": name, "chat_id": group_id, "is_channel": data.get("is_channel", False)})
                        page.client_storage.set(get_chats_key(), saved_chats)

                    show_chat_screen(name, group_id, is_channel=data.get("is_channel", False))
                else:
                    try:
                        detail = res.json().get("detail", "Ничего не найдено")
                    except Exception:
                        detail = "Ничего не найдено"
                    code_input.error_text = detail
                    join_button.disabled = False
                    join_button.text = "Присоединиться"
                    page.update()
            except Exception as ex:
                print("Ошибка вступления:", ex)
                code_input.error_text = "Сервер недоступен"
                join_button.disabled = False
                join_button.text = "Присоединиться"
                page.update()

        join_button = ft.ElevatedButton("Присоединиться", bgcolor=teal, color=white, width=350, on_click=join_click)

        page.add(
            ft.Column(
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    ft.Container(height=30),
                    ft.Text("Есть ссылка или username?", size=20, weight="bold", color=teal),
                    ft.Text("Вставьте @username публичного чата или код-приглашение приватного", color="grey", size=12, text_align=ft.TextAlign.CENTER, width=300),
                    code_input,
                    join_button
                ]
            )
        )
        page.update()

    def show_new_message_screen(e):
        page.clean()
        page.bgcolor = white
        
        # Скрываем нижнюю панель и FAB
        if page.navigation_bar:
            page.navigation_bar.visible = False
        if page.floating_action_button:
            page.floating_action_button.visible = False

        def go_back(e):
            show_main_screen()

        page.appbar = ft.AppBar(
            leading=ft.IconButton(
                icon=ft.icons.ARROW_BACK,
                icon_color="black",
                on_click=go_back
            ),
            title=ft.Text("Новое сообщение", color="black", weight="bold"),
            bgcolor=white,
            elevation=0
        )

        # Поиск
        do_search_generation = {"value": 0}  # 🔥 та же защита от гонки, что и в show_search_screen

        def do_search(e):
            do_search_generation["value"] += 1
            my_generation = do_search_generation["value"]

            username = search_field.value.replace("@", "").strip()
            if not username:
                return

            def do_search_work():
                try:
                    search_field.prefix_icon = ft.icons.HOURGLASS_EMPTY
                    page.update()

                    res = requests.get(f"{API_URL}/find-user/{username}", timeout=5)

                    # Пока ждали ответ, юзер мог напечатать что-то ещё - тогда этот ответ уже неактуален
                    if my_generation != do_search_generation["value"]:
                        return

                    if res.status_code == 200:
                        target = res.json()
                        my_un = user_info.get("username")
                        if not my_un:
                            page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                            page.snack_bar.open = True
                            search_field.prefix_icon = ft.icons.SEARCH
                            page.update()
                            return

                        room_id = get_private_room_id(my_un, target["username"])
                        
                        # 🔥 Проверяем, ищем ли мы сами себя
                        if target["username"] == my_un:
                            chat_title = "Избранное (Я)"
                        else:
                            chat_title = target["nickname"]

                        # 🔥 ИСПОЛЬЗУЕМ ЛИЧНУЮ ПАМЯТЬ АККАУНТА
                        saved_chats = page.client_storage.get(get_chats_key()) or []
                        if not any(c["chat_id"] == room_id for c in saved_chats):
                            saved_chats.append({"title": chat_title, "chat_id": room_id, "other_username": target["username"]})
                            page.client_storage.set(get_chats_key(), saved_chats)

                        show_chat_screen(chat_title, room_id, other_username=target["username"])
                    else:
                        search_field.error_text = "Пользователь не найден"
                        search_field.prefix_icon = ft.icons.SEARCH
                        page.update()
                except Exception as ex:
                    print("Ошибка поиска:", ex)
                    if my_generation == do_search_generation["value"]:
                        search_field.error_text = "Ошибка сервера"
                        search_field.prefix_icon = ft.icons.SEARCH
                        page.update()

            threading.Thread(target=do_search_work, daemon=True).start()

        search_field = ft.TextField(
            hint_text="Поиск контактов (@username)",
            prefix_icon=ft.icons.SEARCH,
            border_radius=30,
            content_padding=15,
            border_color="transparent",
            bgcolor="#F5F5F5",
            on_change=do_search
        )

        options_list = ft.ListView(
            spacing=10,
            padding=10,
            controls=[
                ft.ListTile(
                    leading=ft.CircleAvatar(
                        content=ft.Icon(ft.icons.PEOPLE, color=white, size=20),
                        bgcolor="#4A90E2",
                        radius=20
                    ),
                    title=ft.Text("Создать группу", color="black", size=16),
                    on_click=show_create_group_screen
                ),
                ft.ListTile(
                    leading=ft.CircleAvatar(
                        content=ft.Icon(ft.icons.CAMPAIGN, color=white, size=20),
                        bgcolor="#4CD964",
                        radius=20
                    ),
                    title=ft.Text("Создать канал", color="black", size=16),
                    on_click=lambda e: show_create_group_screen(e, is_channel=True)
                ),
                ft.ListTile(
                    leading=ft.CircleAvatar(
                        content=ft.Icon(ft.icons.LINK, color=white, size=20),
                        bgcolor="#8E44AD",
                        radius=20
                    ),
                    title=ft.Text("Присоединиться по ссылке", color="black", size=16),
                    on_click=show_join_group_screen
                )
            ]
        )

        # Главный контейнер
        main_column = ft.Column(
            expand=True,
            controls=[
                ft.Container(
                    padding=ft.padding.only(left=10, right=10, top=10, bottom=5),
                    content=search_field
                ),
                options_list
            ]
        )

        page.add(main_column)
        page.update()

    # 🔥 ЛОГИКА ПОИСКА ПОЛЬЗОВАТЕЛЕЙ
    def show_search_screen(e=None):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False
        heal_saved_chats()

        search_results_list = ft.ListView(expand=True, spacing=2, padding=ft.padding.only(top=5, bottom=10))

        def go_back(e):
            show_main_screen()

        def result_tile(title, subtitle, on_click_fn):
            avatar_letter = title[0].upper() if title else "?"
            return ft.ListTile(
                leading=ft.CircleAvatar(content=ft.Text(avatar_letter, color=white, weight="bold"), bgcolor=get_avatar_color(title)),
                title=ft.Text(title, color="black", weight="bold", size=15),
                subtitle=ft.Text(subtitle, color="grey", size=12) if subtitle else None,
                on_click=on_click_fn
            )

        # Функция для сохранения и перехода в чат (глобальный поиск даёт новых людей)
        def save_and_open_chat(chat_title, room_id, other_username=None):
            saved_chats = page.client_storage.get(get_chats_key()) or []
            if not any(c["chat_id"] == room_id for c in saved_chats):
                saved_chats.append({"title": chat_title, "chat_id": room_id, "other_username": other_username})
                page.client_storage.set(get_chats_key(), saved_chats)
            show_chat_screen(chat_title, room_id, other_username=other_username)

        # 🔥 Тап по публичной группе/каналу в поиске - сразу вступаем и открываем чат
        def join_and_open_group(group_id, name, group_username, is_channel=False):
            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return
            try:
                requests.post(f"{CHAT_API_URL}/join-group", json={"code": group_username, "username": my_un}, timeout=8)
            except Exception as ex:
                print("Ошибка вступления в группу:", ex)

            saved_chats = page.client_storage.get(get_chats_key()) or []
            if not any(c["chat_id"] == group_id for c in saved_chats):
                saved_chats.append({"title": name, "chat_id": group_id, "is_channel": is_channel})
                page.client_storage.set(get_chats_key(), saved_chats)
            show_chat_screen(name, group_id, is_channel=is_channel)

        # До того, как начали печатать - показываем свои чаты, как в Telegram
        def show_default_state():
            search_results_list.controls.clear()
            saved_chats = page.client_storage.get(get_chats_key()) or []
            if saved_chats:
                search_results_list.controls.append(section_header("Мои чаты", ft.icons.CHAT_BUBBLE_OUTLINE))
                for c in saved_chats:
                    search_results_list.controls.append(
                        result_tile(
                            c["title"], None, 
                            lambda e, t=c["title"], cid=c["chat_id"], ou=c.get("other_username"), ic=c.get("is_channel", False): show_chat_screen(t, cid, other_username=ou, is_channel=ic)
                        )
                    )
            else:
                search_results_list.controls.append(empty_state(ft.icons.SEARCH, "Найдите людей по @username"))
            page.update()

        # 🔥 Счётчик "поколений" запроса. Раньше при быстром наборе текста несколько
        # перекрывающихся вызовов perform_search дописывали результаты в один и тот же
        # список одновременно - отсюда и задвоение чатов. Теперь каждый новый вызов
        # получает свой номер, и в конце проверяется: если пока мы искали, юзер
        # успел напечатать что-то ещё (номер вырос) - наши результаты просто отбрасываются.
        search_generation = {"value": 0}

        # Главная функция поиска
        def perform_search(e):
            search_generation["value"] += 1
            my_generation = search_generation["value"]

            query = search_input.value.strip()
            clear_btn.visible = bool(query)
            page.update()

            if len(query) < 2:
                show_default_state()
                return

            def do_search_work():
                time.sleep(0.35)  # небольшая пауза - если юзер печатает дальше, более новый запрос отменит этот
                if my_generation != search_generation["value"]:
                    return

                new_controls = []

                # 1. ЛОКАЛЬНЫЙ ПОИСК (по твоим открытым чатам)
                saved_chats = page.client_storage.get(get_chats_key()) or []
                local_results = [c for c in saved_chats if query.lower() in c["title"].lower()]
                if local_results:
                    new_controls.append(section_header("Мои чаты", ft.icons.CHAT_BUBBLE_OUTLINE))
                    for c in local_results:
                        new_controls.append(
                            result_tile(
                                c["title"], None, 
                                lambda e, t=c["title"], cid=c["chat_id"], ou=c.get("other_username"), ic=c.get("is_channel", False): show_chat_screen(t, cid, other_username=ou, is_channel=ic)
                            )
                        )
                    new_controls.append(ft.Divider(height=1, color="#F0F0F0"))

                if my_generation != search_generation["value"]:
                    return

                # 2. ГЛОБАЛЬНЫЙ ПОИСК (по людям)
                new_controls.append(section_header("Глобальный поиск", ft.icons.PUBLIC))
                try:
                    # Обращаемся к нашей новой умной функции в бэкенде
                    res = requests.get(f"{API_URL}/search-users/{query}", timeout=5)
                    found_users = res.json() if res.status_code == 200 else []
                    my_un = user_info.get("username")

                    if not found_users:
                        new_controls.append(empty_state(ft.icons.PERSON_SEARCH, "Никто не найден"))
                    elif not my_un:
                        new_controls.append(ft.Text("⚠️ Сначала задайте @username в Профиле!", color="red"))
                    else:
                        for target in found_users:
                            # Пропускаем тех, у кого нет username
                            if not target.get("username"): continue

                            room_id = get_private_room_id(my_un, target["username"])
                            chat_title = "Избранное (Я)" if target["username"] == my_un else target["nickname"]

                            new_controls.append(
                                result_tile(
                                    chat_title, f"@{target['username']}", 
                                    lambda e, t=chat_title, cid=room_id, ou=target["username"]: save_and_open_chat(t, cid, ou)
                                )
                            )
                except Exception as ex:
                    print("Ошибка глобального поиска:", ex)
                    new_controls.append(ft.Text("Ошибка соединения с сервером", color="red"))

                if my_generation != search_generation["value"]:
                    return

                # 3. ПУБЛИЧНЫЕ ГРУППЫ И КАНАЛЫ
                try:
                    res_g = requests.get(f"{CHAT_API_URL}/search-groups/{query}", timeout=5)
                    found_groups = res_g.json() if res_g.status_code == 200 else []
                    if found_groups:
                        new_controls.append(section_header("Группы и каналы", ft.icons.GROUPS))
                        for g in found_groups:
                            kind = "канал" if g.get("is_channel") else "группа"
                            new_controls.append(
                                result_tile(
                                    g["name"], f"@{g['username']} · {kind}",
                                    lambda e, gid=g["group_id"], name=g["name"], gu=g["username"], ic=g.get("is_channel", False): join_and_open_group(gid, name, gu, ic)
                                )
                            )
                except Exception as ex:
                    print("Ошибка поиска групп:", ex)

                # 🔥 Финальная проверка: если за время поиска юзер напечатал что-то ещё -
                # эти результаты уже не актуальны, отбрасываем их вместо показа
                if my_generation != search_generation["value"]:
                    return
                search_results_list.controls = new_controls
                page.update()

            threading.Thread(target=do_search_work, daemon=True).start()

        def clear_search(e):
            search_input.value = ""
            clear_btn.visible = False
            show_default_state()
            search_input.focus()
            page.update()

        clear_btn = ft.IconButton(ft.icons.CLOSE, icon_size=18, icon_color="grey", visible=False, on_click=clear_search)

        # Поле ввода, которое выглядит как встроенное в шапку
        search_input = ft.TextField(
            hint_text="Поиск по @username...", 
            border_radius=30,
            content_padding=10,
            border_color="transparent",
            bgcolor="#F5F5F5",
            expand=True,
            autofocus=True,
            on_change=perform_search, # Срабатывает на каждое изменение текста (поиск по мере ввода)
        )

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Row([search_input, clear_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            bgcolor=white,
            elevation=0
        )
        page.appbar.visible = True

        page.add(search_results_list)
        show_default_state()

    # --- ИНСТРУМЕНТ ВЫБОРА ФОТО ---
    def on_avatar_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            user_info["avatar_path"] = e.files[0].path
            reg_avatar.foreground_image_src = user_info["avatar_path"]
            reg_avatar.content = None
            page.update()

    avatar_picker = ft.FilePicker(on_result=on_avatar_picked)
    page.overlay.append(avatar_picker)

    # 🔥 ОТПРАВКА ФАЙЛОВ В ЧАТ
    def upload_chat_file(file_path, file_name):
        try:
            size = os.path.getsize(file_path)
            if size > 20 * 1024 * 1024:
                print(f"Файл '{file_name}' слишком большой (лимит 20 МБ)")
                return
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                data = {
                    "chat_name": current_open_chat, 
                    "sender": user_info.get("nickname", "Аноним"),
                    "sender_username": user_info.get("username") or ""
                }
                res = requests.post(f"{CHAT_API_URL}/upload", files=files, data=data, timeout=30)
                if res.status_code == 403:
                    print("Файл не отправлен: только администраторы канала могут писать")
        except Exception as ex:
            print("Ошибка загрузки файла:", ex)

    def on_chat_file_picked(e: ft.FilePickerResultEvent):
        if not e.files or not current_open_chat:
            return
        picked = e.files[0]
        threading.Thread(target=upload_chat_file, args=(picked.path, picked.name), daemon=True).start()

    chat_file_picker = ft.FilePicker(on_result=on_chat_file_picked)
    page.overlay.append(chat_file_picker)

    # --- ЭЛЕМЕНТЫ ВВОДА ---
    email_field = ft.TextField(
        label="Ваша почта", 
        keyboard_type=ft.KeyboardType.EMAIL,
        width=300, 
        border_color=peach, 
        cursor_color=peach, 
        color=peach
    )
    
    otp_field = ft.TextField(
        label="Код из письма", 
        password=True, 
        can_reveal_password=True, 
        width=300, 
        border_color=peach, 
        color=peach
    )
    
    nick_input = ft.TextField(
        label="Ваше имя (Никнейм)", 
        width=300, 
        border_color=teal
    )
    
    username_display = ft.Text(
        "", 
        size=16, 
        color="blue", 
        weight="bold"
    )
    
    def save_username(e):
        val = username_edit.value.strip()
        safe_val = val if val else None 
        
        try:
            requests.post(f"{API_URL}/update-profile", json={
                "email": user_info["email"],
                "nickname": user_info["nickname"],
                "username": safe_val
            })
            user_info["username"] = val
            username_display.value = f"@{val}" if val else "Нажмите, чтобы задать @username"
            username_edit.visible = False
            page.update()
        except Exception as ex:
            print("Ошибка сохранения:", ex)

    username_edit = ft.TextField(
        label="Придумайте @username", 
        visible=False, 
        on_submit=save_username, 
        on_blur=save_username
    )
    
    reg_avatar = ft.CircleAvatar(
        radius=50, 
        bgcolor=peach, 
        content=ft.Icon(ft.icons.PERSON, size=40, color=white)
    )

    # ==========================================
    # ЛОГИКА АВТОРИЗАЦИИ
    # ==========================================
    def request_code_click(e):
        email = email_field.value
        if not email:
            email_field.error_text = "Введите почту!"
            page.update()
            return

        btn_request.disabled = True
        btn_request.text = "Отправка..."
        page.update()

        try:
            # Таймаут увеличен: письмо реально уходит через SMTP, это не мгновенно
            response = requests.post(f"{API_URL}/request-code", json={"email": email}, timeout=10)
            if response.status_code == 200:
                login_card.content = otp_view
            else:
                try:
                    detail = response.json().get("detail", "Ошибка сервера")
                except Exception:
                    detail = "Ошибка сервера"
                email_field.error_text = detail
        except requests.exceptions.ConnectionError:
            email_field.error_text = "Сервер недоступен!"
        except requests.exceptions.Timeout:
            email_field.error_text = "Сервер долго отвечает, попробуйте ещё раз"

        btn_request.disabled = False
        btn_request.text = "Получить код"
        page.update()

    def verify_code_click(e):
        try:
            data = {"email": email_field.value, "code": otp_field.value}
            response = requests.post(f"{API_URL}/verify-code", json=data, timeout=3)

            if response.status_code == 200:
                result = response.json()
                page.client_storage.set("mesme_token", result.get("token"))
                user_info["email"] = result.get("email")

                if result.get("is_new_user"):
                    show_registration_details()
                else:
                    user_info["nickname"] = result.get("nickname")
                    user_info["username"] = result.get("username")
                    show_main_screen()
            else:
                try:
                    detail = response.json().get("detail", "Неверный код!")
                except Exception:
                    detail = "Неверный код!"
                otp_field.error_text = detail
                page.update()
        except Exception as ex:
            otp_field.error_text = "Ошибка соединения"
            page.update()

    def finish_registration(e):
        if not nick_input.value:
            nick_input.error_text = "Никнейм обязателен!"
            page.update()
            return
            
        user_info["nickname"] = nick_input.value
        
        try:
            requests.post(f"{API_URL}/update-profile", json={
                "email": user_info["email"],
                "nickname": user_info["nickname"],
                "username": user_info.get("username")
            })
            show_main_screen()
        except:
            nick_input.error_text = "Ошибка сохранения"
            page.update()

    def logout(e):
        nonlocal presence_started
        page.client_storage.remove("mesme_token")

        # 🔥 Закрываем соединение "я онлайн" - иначе для других мы останемся "в сети"
        if presence_ws:
            try:
                presence_ws.close()
            except:
                pass
        presence_started = False
        
        user_info.update({
            "email": "", 
            "nickname": "", 
            "username": "", 
            "avatar_path": None
        })
        
        email_field.value = ""
        otp_field.value = ""
        email_field.error_text = None
        otp_field.error_text = None
        show_login_screen()

    # ==========================================
    # ЭКРАНЫ (UI)
    # ==========================================
    def show_login_screen():
        page.clean()
        page.bgcolor = dark_bg
        
        if page.navigation_bar: 
            page.navigation_bar.visible = False
        if page.appbar: 
            page.appbar.visible = False
            
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        
        login_card.content = login_view
        page.add(login_card)
        page.update()

    def show_registration_details():
        page.clean()
        page.bgcolor = white
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        
        reg_avatar.foreground_image_src = None
        reg_avatar.content = ft.Icon(ft.icons.PERSON, size=40, color=white)

        page.add(
            ft.Column(
                [
                    ft.Text("Почти готово!", size=30, weight="bold", color=teal),
                    ft.Text("Как вас будут видеть другие?", color="grey"),
                    ft.Container(height=20),
                    reg_avatar,
                    ft.TextButton(
                        "Выбрать фото (по желанию)", 
                        icon=ft.icons.ADD_A_PHOTO, 
                        icon_color=teal, 
                        on_click=lambda _: avatar_picker.pick_files(allow_multiple=False)
                    ),
                    ft.Container(height=10),
                    nick_input,
                    ft.Container(height=20),
                    ft.ElevatedButton(
                        "Завершить", 
                        on_click=finish_registration, 
                        bgcolor=teal, 
                        color=white, 
                        width=300
                    )
                ], 
                horizontal_alignment=ft.CrossAxisAlignment.CENTER
            )
        )
        page.update()

    # 🔥 ИЗМЕНЕНО: теперь принимает chat_id
    def show_chat_screen(chat_title, chat_id, other_username=None, is_channel=False):
        nonlocal current_open_chat, current_chat_is_channel
        current_open_chat = chat_id
        current_chat_is_channel = is_channel
        my_message_status_ctrls.clear()
        chat_screen_alive = True
        
        page.clean()
        page.bgcolor = white
        
        if page.navigation_bar: 
            page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False
        
        def go_back(e):
            nonlocal current_open_chat, chat_screen_alive, current_chat_is_channel
            current_open_chat = None
            current_chat_is_channel = False
            chat_screen_alive = False
            nonlocal ws_app
            
            if ws_app:
                try: 
                    ws_app.close()
                except: 
                    pass
            show_main_screen()

        # 🔥 Подзаголовок: "в сети/был в сети" для ЛС, "N участников/подписчиков" для групп и каналов
        status_subtitle = ft.Text("", size=12, color="grey")
        title_controls = [ft.Text(chat_title, color=teal, weight="bold", size=16)]
        is_group_chat = chat_id.startswith("group_")
        if other_username or is_group_chat:
            title_controls.append(status_subtitle)

        # 🔥 Тап по названию чата - переход на страницу информации (как во всех мессенджерах)
        def open_chat_info(e):
            if is_group_chat:
                show_group_info_screen(chat_title, chat_id, is_channel)
            elif other_username:
                show_user_info_screen(other_username, lambda: show_chat_screen(chat_title, chat_id, other_username=other_username))

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Container(content=ft.Column(title_controls, spacing=0, tight=True), on_click=open_chat_info),
            bgcolor=white, 
            elevation=0
        )
        page.appbar.visible = True
        
        chat_messages_list.controls.clear()

        message_input = ft.TextField(
            hint_text="Сообщение...", 
            expand=True, 
            border_color=teal, 
            border_radius=20, 
            content_padding=10
        )

        def send_message(e):
            if not message_input.value: 
                return
                
            val = message_input.value
            message_input.value = ""
            page.update()

            msg_data = {
                "sender": user_info.get("nickname", "Аноним"), 
                "sender_username": user_info.get("username"),
                "text": val
            }
            nonlocal ws_app
            if ws_app:
                try: 
                    ws_app.send(json.dumps(msg_data))
                except: 
                    print("Ошибка отправки в сокет")

        # 🔥 ПАНЕЛЬ ЭМОДЗИ - открывается/закрывается по кнопке рядом с полем ввода
        def insert_emoji(emj):
            message_input.value = (message_input.value or "") + emj
            page.update()
            message_input.focus()

        emoji_panel = ft.Container(
            visible=False,
            padding=10,
            bgcolor="#F5F5F5",
            content=ft.Row(
                [ft.TextButton(text=em, on_click=lambda e, x=em: insert_emoji(x)) for em in COMMON_EMOJIS],
                wrap=True, spacing=0, run_spacing=0
            )
        )

        def toggle_emoji_panel(e):
            emoji_panel.visible = not emoji_panel.visible
            page.update()

        # 🔥 Баннер вместо поля ввода - показываем подписчикам канала, кому нельзя писать
        read_only_banner = ft.Container(
            padding=15,
            bgcolor="#F5F5F5",
            alignment=ft.alignment.center,
            content=ft.Text("Только администраторы канала могут отправлять сообщения", color="grey", size=13, text_align=ft.TextAlign.CENTER)
        )

        input_row = ft.Column(
            [
                emoji_panel,
                ft.Container(
                    padding=10, 
                    bgcolor=white, 
                    content=ft.Row(
                        [
                            ft.IconButton(ft.icons.ATTACH_FILE, icon_color=teal, on_click=lambda _: chat_file_picker.pick_files(allow_multiple=False)),
                            ft.IconButton(ft.icons.EMOJI_EMOTIONS_OUTLINED, icon_color=teal, on_click=toggle_emoji_panel),
                            message_input,
                            ft.IconButton(ft.icons.SEND, icon_color=teal, on_click=send_message),
                        ]
                    )
                )
            ],
            spacing=0
        )

        # 🔥 Сначала показываем сам экран чата (пустым) и только потом лезем в сеть -
        # раньше история грузилась ДО этого page.update(), причём вообще без
        # таймаута, поэтому при недоступном сервере экран мог зависнуть насовсем
        page.add(chat_messages_list, input_row)
        page.update()

        # ЗАГРУЖАЕМ ИСТОРИЮ ИЗ БД ПО CHAT_ID - в фоновом потоке, с таймаутом
        def load_history():
            try:
                hist_res = requests.get(f"{CHAT_API_URL}/history/{chat_id}", timeout=8)
                if hist_res.status_code == 200:
                    for msg in hist_res.json():
                        append_message_to_ui(
                            msg["sender"], msg["text"], msg.get("timestamp"), 
                            is_read=msg.get("is_read", False), 
                            is_delivered=msg.get("is_delivered", False), 
                            msg_id=msg.get("id"), 
                            file_url=msg.get("file_url"),
                            file_name=msg.get("file_name"),
                            update=False
                        )
                    page.update()
            except Exception as e:
                print("Ошибка истории:", e)

        threading.Thread(target=load_history, daemon=True).start()

        # ПОДКЛЮЧАЕМ РЕАЛТАЙМ
        def connect_ws():
            nonlocal ws_app
            ws_url = f"ws://127.0.0.1:8000/chat/ws/{chat_id}"

            def on_open(ws):
                ws.send(json.dumps({"action": "mark_read", "sender": user_info.get("nickname")}))
                
            ws_app = websocket.WebSocketApp(ws_url, on_message=on_ws_message, on_open=on_open)
            ws_app.run_forever()
        
        wst = threading.Thread(target=connect_ws)
        wst.daemon = True
        wst.start()

        # 🔥 ОПРОС СТАТУСА "В СЕТИ / БЫЛ(А) В СЕТИ" - раз в 15 секунд, пока чат открыт (только для ЛС)
        def presence_loop():
            while chat_screen_alive:
                try:
                    res = requests.get(f"{CHAT_API_URL}/user-status/{other_username}", timeout=5)
                    if res.status_code == 200 and chat_screen_alive:
                        st = res.json()
                        if st.get("is_online"):
                            status_subtitle.value = "в сети"
                            status_subtitle.color = blue_accent
                        else:
                            status_subtitle.value = format_last_seen(st.get("last_seen"))
                            status_subtitle.color = "grey"
                        page.update()
                except Exception:
                    pass
                time.sleep(15)

        if other_username:
            threading.Thread(target=presence_loop, daemon=True).start()

        # 🔥 ИНФО О ГРУППЕ/КАНАЛЕ: счётчик участников + для канала - могу ли я вообще писать
        def load_group_info():
            my_username = user_info.get("username")
            if not my_username:
                return
            try:
                res = requests.get(f"{CHAT_API_URL}/group-info/{chat_id}", params={"username": my_username}, timeout=8)
                if res.status_code != 200:
                    return
                info = res.json()

                count = info.get("member_count", 0)
                noun = "подписчик" if is_channel else "участник"
                if count % 10 == 1 and count % 100 != 11:
                    word = noun
                elif 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
                    word = noun + "а"
                else:
                    word = noun + "ов"
                status_subtitle.value = f"{count} {word}"
                status_subtitle.color = "grey"

                # В канале писать могут только владелец и админы - остальным показываем баннер вместо поля ввода.
                # /group-info отдаёт "owner" отдельно от "admin" (для бейджика "Создатель"),
                # поэтому раньше создатель канала ошибочно попадал под "не админ" и терял поле ввода
                if is_channel and info.get("my_role") not in ("admin", "owner"):
                    input_row.controls = [read_only_banner]

                page.update()
            except Exception:
                pass

        if is_group_chat:
            threading.Thread(target=load_group_info, daemon=True).start()

    # 🔥 Карточка одного участника с бейджиком роли - используется и на странице
    # инфо о группе, и на отдельном экране участников
    def build_member_tile(m, on_back):
        role = m.get("role")
        badge = None
        if role == "owner":
            badge = ft.Container(
                content=ft.Text("Создатель", color=white, size=10, weight="bold"),
                bgcolor="#E67E22", padding=ft.padding.only(left=8, right=8, top=3, bottom=3), border_radius=10
            )
        elif role == "admin":
            badge = ft.Container(
                content=ft.Text("Админ", color=white, size=10, weight="bold"),
                bgcolor=blue_accent, padding=ft.padding.only(left=8, right=8, top=3, bottom=3), border_radius=10
            )

        nickname = m.get("nickname") or m.get("username") or "?"
        return ft.ListTile(
            leading=ft.CircleAvatar(content=ft.Text(nickname[0].upper(), color=white), bgcolor=get_avatar_color(nickname)),
            title=ft.Text(nickname, color="black"),
            subtitle=ft.Text(f"@{m.get('username')}", color="grey", size=12),
            trailing=badge,
            on_click=lambda e, un=m.get("username"): show_user_info_screen(un, on_back)
        )

    # 🔥 ОТДЕЛЬНЫЙ ЭКРАН СПИСКА УЧАСТНИКОВ - для каналов (кнопкой, чтобы не засорять инфо-страницу)
    def show_group_members_screen(chat_id, chat_title, is_channel):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_group_info_screen(chat_title, chat_id, is_channel)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Подписчики" if is_channel else "Участники", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        members_list = ft.ListView(expand=True, spacing=0)
        page.add(members_list)
        page.update()

        def load_members():
            try:
                res = requests.get(f"{CHAT_API_URL}/group-members/{chat_id}", timeout=8)
                if res.status_code == 200:
                    for m in res.json():
                        members_list.controls.append(build_member_tile(m, lambda: show_group_members_screen(chat_id, chat_title, is_channel)))
                    page.update()
            except Exception as ex:
                print("Ошибка загрузки участников:", ex)

        threading.Thread(target=load_members, daemon=True).start()

    # 🔥 РЕДАКТИРОВАНИЕ ОПИСАНИЯ (доступно создателю и админам)
    def show_edit_description_screen(chat_title, chat_id, is_channel, current_desc):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_group_info_screen(chat_title, chat_id, is_channel)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Описание", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        desc_input = ft.TextField(
            value=current_desc, multiline=True, min_lines=4, max_lines=10,
            hint_text="О чём этот чат, правила и т.п.",
            border_color=teal, width=350, autofocus=True
        )

        def save_click(e):
            my_un = user_info.get("username")
            save_btn.disabled = True
            save_btn.text = "Сохранение..."
            page.update()
            try:
                res = requests.post(f"{CHAT_API_URL}/update-group-description", json={
                    "group_id": chat_id, "username": my_un, "description": desc_input.value
                }, timeout=8)
                if res.status_code == 200:
                    show_group_info_screen(chat_title, chat_id, is_channel)
                else:
                    try:
                        detail = res.json().get("detail", "Ошибка")
                    except Exception:
                        detail = "Ошибка"
                    page.snack_bar = ft.SnackBar(ft.Text(detail))
                    page.snack_bar.open = True
                    save_btn.disabled = False
                    save_btn.text = "Сохранить"
                    page.update()
            except Exception as ex:
                print("Ошибка сохранения описания:", ex)
                page.snack_bar = ft.SnackBar(ft.Text("Сервер недоступен"))
                page.snack_bar.open = True
                save_btn.disabled = False
                save_btn.text = "Сохранить"
                page.update()

        save_btn = ft.ElevatedButton("Сохранить", bgcolor=teal, color=white, width=350, on_click=save_click)

        page.add(
            ft.Column(
                expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=20,
                controls=[ft.Container(height=20), desc_input, save_btn]
            )
        )
        page.update()

    # 🔥 ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ - необратимое действие, отдельный экран вместо диалога
    def show_delete_confirm_screen(chat_title, chat_id, is_channel):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_group_info_screen(chat_title, chat_id, is_channel)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            bgcolor=white, elevation=0, visible=True
        )

        def really_delete(e):
            my_un = user_info.get("username")
            delete_btn.disabled = True
            delete_btn.text = "Удаление..."
            page.update()
            try:
                requests.post(f"{CHAT_API_URL}/delete-group", json={"group_id": chat_id, "username": my_un}, timeout=8)
            except Exception as ex:
                print("Ошибка удаления:", ex)

            saved_chats = page.client_storage.get(get_chats_key()) or []
            saved_chats = [c for c in saved_chats if c["chat_id"] != chat_id]
            page.client_storage.set(get_chats_key(), saved_chats)
            show_main_screen()

        delete_btn = ft.ElevatedButton("Да, удалить навсегда", bgcolor="red", color=white, width=350, on_click=really_delete)
        cancel_btn = ft.TextButton("Отмена", on_click=go_back)

        page.add(
            ft.Column(
                expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=20,
                controls=[
                    ft.Container(height=40),
                    ft.Icon(ft.icons.WARNING_AMBER_ROUNDED, size=48, color="red"),
                    ft.Text(f"Удалить «{chat_title}»?", size=20, weight="bold", color="black"),
                    ft.Text(
                        "Это необратимо. Вся история сообщений и список участников будут удалены безвозвратно.",
                        color="grey", size=13, text_align=ft.TextAlign.CENTER, width=300
                    ),
                    ft.Container(height=10),
                    delete_btn,
                    cancel_btn
                ]
            )
        )
        page.update()

    # 🔥 ИНФОРМАЦИЯ О ГРУППЕ/КАНАЛЕ - шапка, описание, ссылка, участники, действия
    def show_group_info_screen(chat_title, chat_id, is_channel):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_chat_screen(chat_title, chat_id, is_channel=is_channel)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Информация о канале" if is_channel else "Информация о группе", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        member_count_text = ft.Text("...", color="grey", size=13)
        description_text = ft.Text("", color="black", size=14)
        description_section = ft.Container(
            visible=False, padding=ft.padding.only(left=20, right=20, top=15, bottom=15),
            content=ft.Column([ft.Text("Описание", size=12, color="grey", weight="bold"), description_text], spacing=4)
        )

        link_label = ft.Text("", color=teal, size=14, selectable=True, weight="bold")

        def copy_link(e):
            try:
                page.set_clipboard(link_label.value)
                page.snack_bar = ft.SnackBar(ft.Text("Скопировано!"))
                page.snack_bar.open = True
                page.update()
            except Exception:
                pass

        link_section = ft.Container(
            visible=False, padding=ft.padding.only(left=20, right=20, top=15, bottom=15),
            content=ft.Column([
                ft.Text("Ссылка", size=12, color="grey", weight="bold"),
                ft.Row([link_label, ft.IconButton(ft.icons.COPY, icon_size=18, icon_color=teal, on_click=copy_link)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            ], spacing=4)
        )

        members_button = ft.ListTile(
            leading=ft.Icon(ft.icons.PEOPLE_OUTLINE, color=teal),
            title=ft.Text("Подписчики" if is_channel else "Участники", color="black"),
            trailing=ft.Text("...", color="grey"),
            visible=False,
            on_click=lambda e: show_group_members_screen(chat_id, chat_title, is_channel)
        )

        members_inline_header = ft.Container(
            visible=False,
            padding=ft.padding.only(left=20, top=15, bottom=5),
            content=ft.Text("УЧАСТНИКИ", color=teal, weight="bold", size=12)
        )
        members_inline_list = ft.Column([], spacing=0)

        # 🔥 Mute - чисто локальная настройка, применяется сразу, без сети
        saved_chats_now = page.client_storage.get(get_chats_key()) or []
        currently_muted = any(c["chat_id"] == chat_id and c.get("muted") for c in saved_chats_now)

        def toggle_mute(e):
            value = e.control.value
            sc = page.client_storage.get(get_chats_key()) or []
            for c in sc:
                if c["chat_id"] == chat_id:
                    c["muted"] = value
            page.client_storage.set(get_chats_key(), sc)

        mute_switch = ft.Switch(value=currently_muted, active_color=peach, on_change=toggle_mute)

        owner_actions = ft.Column([], spacing=0)

        def leave_click(e):
            my_un = user_info.get("username")
            try:
                requests.post(f"{CHAT_API_URL}/leave-group", json={"group_id": chat_id, "username": my_un}, timeout=8)
            except Exception as ex:
                print("Ошибка выхода из чата:", ex)

            sc = page.client_storage.get(get_chats_key()) or []
            sc = [c for c in sc if c["chat_id"] != chat_id]
            page.client_storage.set(get_chats_key(), sc)
            show_main_screen()

        content = ft.ListView(
            expand=True, spacing=0,
            controls=[
                ft.Container(height=20),
                ft.Column(
                    [
                        ft.CircleAvatar(
                            content=ft.Text(chat_title[0].upper() if chat_title else "?", color=white, size=32, weight="bold"),
                            bgcolor=get_avatar_color(chat_title), radius=50
                        ),
                        ft.Container(height=10),
                        ft.Text(chat_title, size=22, weight="bold", color="black", text_align=ft.TextAlign.CENTER),
                        member_count_text
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2
                ),
                ft.Container(height=15),
                ft.Divider(height=1, color="#F0F0F0"),
                description_section,
                link_section,
                ft.Divider(height=1, color="#F0F0F0"),
                members_button,
                members_inline_header,
                members_inline_list,
                ft.Divider(height=1, color="#F0F0F0"),
                ft.ListTile(
                    leading=ft.Icon(ft.icons.NOTIFICATIONS_OFF_OUTLINED, color=teal),
                    title=ft.Text("Выключить уведомления", color="black"),
                    trailing=mute_switch
                ),
                owner_actions
            ]
        )

        page.add(content)
        page.update()

        def load_info():
            my_un = user_info.get("username")
            try:
                res = requests.get(f"{CHAT_API_URL}/group-info/{chat_id}", params={"username": my_un}, timeout=8)
                if res.status_code != 200:
                    return
                info = res.json()
            except Exception:
                return

            count = info.get("member_count", 0)
            noun = "подписчик" if is_channel else "участник"
            if count % 10 == 1 and count % 100 != 11:
                word = noun
            elif 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
                word = noun + "а"
            else:
                word = noun + "ов"
            member_count_text.value = f"{count} {word}"

            my_role = info.get("my_role")
            is_owner_or_admin = my_role in ("owner", "admin")
            desc = info.get("description")

            if desc:
                description_text.value = desc
                description_text.color = "black"
                description_section.visible = True
            elif is_owner_or_admin:
                description_text.value = "Нажмите, чтобы добавить описание"
                description_text.color = "grey"
                description_section.visible = True

            if is_owner_or_admin:
                description_section.on_click = lambda e: show_edit_description_screen(chat_title, chat_id, is_channel, desc or "")

            if info.get("is_public"):
                link_label.value = f"@{info.get('username')}"
                link_section.visible = True
            elif info.get("invite_code"):
                link_label.value = info.get("invite_code")
                link_section.visible = True

            if is_channel:
                members_button.trailing.value = str(count)
                members_button.visible = True
            else:
                members_inline_header.visible = True
                members_inline_list.controls.clear()
                try:
                    res_m = requests.get(f"{CHAT_API_URL}/group-members/{chat_id}", timeout=8)
                    if res_m.status_code == 200:
                        for m in res_m.json():
                            members_inline_list.controls.append(build_member_tile(m, lambda: show_group_info_screen(chat_title, chat_id, is_channel)))
                except Exception:
                    pass

            owner_actions.controls.clear()
            if my_role == "owner":
                owner_actions.controls.append(
                    ft.ListTile(
                        leading=ft.Icon(ft.icons.DELETE_OUTLINE, color="red"),
                        title=ft.Text("Удалить чат", color="red"),
                        on_click=lambda e: show_delete_confirm_screen(chat_title, chat_id, is_channel)
                    )
                )
            elif my_role:
                owner_actions.controls.append(
                    ft.ListTile(
                        leading=ft.Icon(ft.icons.LOGOUT, color="red"),
                        title=ft.Text("Покинуть чат", color="red"),
                        on_click=leave_click
                    )
                )

            page.update()

        threading.Thread(target=load_info, daemon=True).start()

    # 🔥 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ - аватар, ник, username, статус (почта скрыта - только в своём профиле)
    def show_user_info_screen(username, on_back_action):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            on_back_action()

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Информация", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        display_name_text = ft.Text("...", size=22, weight="bold", color="black", text_align=ft.TextAlign.CENTER)
        username_text = ft.Text(f"@{username}", color="grey", size=14)
        status_text = ft.Text("", color="grey", size=13)

        content = ft.Column(
            expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4,
            controls=[
                ft.Container(height=30),
                ft.CircleAvatar(
                    content=ft.Text(username[0].upper(), color=white, size=32, weight="bold"),
                    bgcolor=get_avatar_color(username), radius=50
                ),
                ft.Container(height=10),
                display_name_text,
                username_text,
                status_text
            ]
        )

        page.add(content)
        page.update()

        def load_profile():
            try:
                res = requests.get(f"{API_URL}/user-profile/{username}", timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    display_name_text.value = data.get("nickname", username)
            except Exception as ex:
                print("Ошибка загрузки профиля:", ex)

            try:
                res2 = requests.get(f"{CHAT_API_URL}/user-status/{username}", timeout=8)
                if res2.status_code == 200:
                    st = res2.json()
                    if st.get("is_online"):
                        status_text.value = "в сети"
                        status_text.color = blue_accent
                    else:
                        status_text.value = format_last_seen(st.get("last_seen"))
                        status_text.color = "grey"
            except Exception:
                pass

            page.update()

        threading.Thread(target=load_profile, daemon=True).start()

    # =====================================================
    # 🔥 ФОРУМ ИДЕЙ
    # =====================================================

    # --- ГЛАВНЫЙ ЭКРАН ФОРУМА ---
    def show_forum_screen(filter_type=None, sort_mode="new"):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            if filter_type:
                show_forum_screen(filter_type=None, sort_mode=sort_mode)
            else:
                show_main_screen()

        def open_menu(e):
            show_forum_menu_screen(filter_type, sort_mode)

        filter_labels = {"priority": "⭐ Приоритетные", "implemented": "✅ Реализованные", "mine": "👤 Мои публикации"}
        title_suffix = f" · {filter_labels[filter_type]}" if filter_type in filter_labels else ""

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text(f"Форум MesMe{title_suffix}", color=teal, weight="bold", size=16),
            bgcolor=white, elevation=0,
            actions=[ft.IconButton(ft.icons.MORE_VERT, icon_color=teal, on_click=open_menu)]
        )
        page.appbar.visible = True

        posts_list = ft.ListView(expand=True, spacing=8, padding=ft.padding.only(left=12, right=12, bottom=10))
        forum_search_generation = {"value": 0}  # 🔥 защита от гонки при быстром наборе текста в поиске

        def open_post(post_id):
            show_forum_post_screen(post_id, filter_type, sort_mode)

        def build_post_tile(p):
            type_label = "💡 Идея" if p["type"] == "idea" else "🐞 Баг"
            prefix = ("📌 " if p.get("is_pinned") else "") + ("⭐ " if p.get("is_priority") else "")

            return ft.Container(
                padding=14, border_radius=12, bgcolor="#FAFAFA",
                on_click=lambda e, pid=p["id"]: open_post(pid),
                content=ft.Column(
                    spacing=6,
                    controls=[
                        ft.Row(
                            [
                                ft.Text(f"{prefix}{type_label}", size=12, color=teal, weight="bold"),
                                ft.Text(f"{STATUS_ICONS.get(p['status'], '')} {p['status_label']}", size=12, color="grey")
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        ft.Text(p["title"], size=16, weight="bold", color="black"),
                        ft.Text(p["description"], size=13, color="#555555", max_lines=3, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Row(
                            [
                                ft.Text(f"{p['author_nickname']} · {format_relative_date(p['created_at'])}", size=11, color="grey"),
                                ft.Row(
                                    [
                                        ft.Icon(
                                            ft.icons.FAVORITE if p.get("i_supported") else ft.icons.FAVORITE_BORDER,
                                            size=14, color=blue_accent if p.get("i_supported") else "grey"
                                        ),
                                        ft.Text(str(p["support_count"]), size=11, color="grey"),
                                        ft.Icon(ft.icons.CHAT_BUBBLE_OUTLINE, size=13, color="grey"),
                                        ft.Text(str(p["comment_count"]), size=11, color="grey"),
                                    ],
                                    spacing=4
                                )
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        )
                    ]
                )
            )

        def load_posts(search_query=None):
            forum_search_generation["value"] += 1
            my_generation = forum_search_generation["value"]

            def work():
                time.sleep(0.3 if search_query else 0)  # дебаунс - только когда реально ищем текст
                if my_generation != forum_search_generation["value"]:
                    return

                try:
                    params = {"sort": sort_mode, "username": user_info.get("username") or ""}
                    if filter_type:
                        params["filter"] = filter_type
                    if search_query:
                        params["search"] = search_query
                    res = requests.get(f"{FORUM_API_URL}/posts", params=params, timeout=8)
                    posts = res.json() if res.status_code == 200 else None
                except Exception as ex:
                    print("Ошибка загрузки форума:", ex)
                    posts = None

                if my_generation != forum_search_generation["value"]:
                    return

                posts_list.controls.clear()
                if posts is None:
                    posts_list.controls.append(ft.Text("Сервер недоступен", color="red"))
                elif not posts:
                    posts_list.controls.append(empty_state(ft.icons.FORUM_OUTLINED, "Пока ничего нет"))
                else:
                    for p in posts:
                        posts_list.controls.append(build_post_tile(p))
                page.update()

            threading.Thread(target=work, daemon=True).start()

        def on_search_change(e):
            load_posts(search_input.value.strip() if search_input.value else None)

        search_input = ft.TextField(
            hint_text="Поиск по форуму...", border_radius=20, content_padding=10,
            border_color="transparent", bgcolor="#F5F5F5", on_change=on_search_change
        )

        action_buttons = ft.Row(
            [
                ft.ElevatedButton("💡 Предложить идею", bgcolor=teal, color=white, expand=True, on_click=show_create_idea_screen),
                ft.ElevatedButton("🐞 Сообщить о баге", bgcolor="#E67E22", color=white, expand=True, on_click=show_create_bug_screen),
            ],
            spacing=8
        )

        page.add(
            ft.Container(padding=ft.padding.only(left=12, right=12, top=10), content=search_input),
            ft.Container(padding=ft.padding.only(left=12, right=12, top=10, bottom=6), content=action_buttons),
            posts_list
        )
        page.update()

        load_posts()

    # --- МЕНЮ ФОРУМА (⋮) ---
    def show_forum_menu_screen(filter_type, sort_mode):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_screen(filter_type=filter_type, sort_mode=sort_mode)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Меню форума", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        def pick(new_filter):
            show_forum_screen(filter_type=new_filter, sort_mode=sort_mode)

        sort_sub_labels = {
            "new": "Сначала новые",
            "popular": "Сначала популярные",
            "no_dev_reply": "Сначала без ответа разработчика"
        }

        menu_list = ft.ListView(
            expand=True, spacing=4, padding=10,
            controls=[
                ft.ListTile(leading=ft.Text("⭐", size=20), title=ft.Text("Приоритетные"), on_click=lambda e: pick("priority")),
                ft.ListTile(leading=ft.Text("✅", size=20), title=ft.Text("Реализованные"), on_click=lambda e: pick("implemented")),
                ft.ListTile(leading=ft.Text("👤", size=20), title=ft.Text("Мои публикации"), on_click=lambda e: pick("mine")),
                ft.Divider(),
                ft.ListTile(
                    leading=ft.Text("↕", size=20), title=ft.Text("Сортировка"),
                    subtitle=ft.Text(sort_sub_labels.get(sort_mode, "")),
                    on_click=lambda e: show_forum_sort_screen(filter_type, sort_mode)
                ),
            ]
        )

        page.add(menu_list)
        page.update()

    # --- ВЫБОР СОРТИРОВКИ ---
    def show_forum_sort_screen(filter_type, current_sort):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_menu_screen(filter_type, current_sort)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Сортировка", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        options = [
            ("new", "Сначала новые"),
            ("popular", "Сначала популярные"),
            ("no_dev_reply", "Сначала без ответа разработчика"),
        ]

        def pick(mode):
            show_forum_screen(filter_type=filter_type, sort_mode=mode)

        tiles = [
            ft.ListTile(
                title=ft.Text(label),
                trailing=ft.Icon(ft.icons.CHECK, color=teal) if key == current_sort else None,
                on_click=lambda e, k=key: pick(k)
            )
            for key, label in options
        ]

        page.add(ft.ListView(expand=True, spacing=2, padding=10, controls=tiles))
        page.update()

    # --- ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ ПУБЛИКАЦИИ (только разработчик) ---
    def show_forum_delete_confirm_screen(post_id, title, filter_type, sort_mode):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_post_screen(post_id, filter_type, sort_mode)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            bgcolor=white, elevation=0, visible=True
        )

        def really_delete(e):
            my_un = user_info.get("username")
            delete_btn.disabled = True
            delete_btn.text = "Удаление..."
            page.update()
            try:
                requests.post(f"{FORUM_API_URL}/delete-post", json={"post_id": post_id, "username": my_un}, timeout=8)
            except Exception as ex:
                print("Ошибка удаления публикации:", ex)
            show_forum_screen(filter_type=filter_type, sort_mode=sort_mode)

        delete_btn = ft.ElevatedButton("Да, удалить навсегда", bgcolor="red", color=white, width=350, on_click=really_delete)
        cancel_btn = ft.TextButton("Отмена", on_click=go_back)

        page.add(
            ft.Column(
                expand=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=20,
                controls=[
                    ft.Container(height=40),
                    ft.Icon(ft.icons.WARNING_AMBER_ROUNDED, size=48, color="red"),
                    ft.Text(f"Удалить «{title}»?", size=20, weight="bold", color="black"),
                    ft.Text("Это необратимо. Публикация и все комментарии к ней будут удалены.",
                            color="grey", size=13, text_align=ft.TextAlign.CENTER, width=300),
                    ft.Container(height=10),
                    delete_btn,
                    cancel_btn
                ]
            )
        )
        page.update()

    # --- ОТКРЫТАЯ ПУБЛИКАЦИЯ: полный текст, поддержка, комментарии, панель разработчика ---
    def show_forum_post_screen(post_id, filter_type=None, sort_mode="new"):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_screen(filter_type=filter_type, sort_mode=sort_mode)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Публикация", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        body_list = ft.ListView(expand=True, spacing=0, padding=ft.padding.only(bottom=10))

        def load_post():
            body_list.controls.clear()
            try:
                my_un = user_info.get("username") or ""
                res = requests.get(f"{FORUM_API_URL}/post/{post_id}", params={"username": my_un}, timeout=8)
                if res.status_code != 200:
                    body_list.controls.append(ft.Text("Публикация не найдена", color="red"))
                    page.update()
                    return
                data = res.json()
            except Exception as ex:
                print("Ошибка загрузки публикации:", ex)
                body_list.controls.append(ft.Text("Сервер недоступен", color="red"))
                page.update()
                return

            is_dev = data.get("is_developer", False)
            type_label = "💡 Идея" if data["type"] == "idea" else "🐞 Баг"

            def toggle_support(e):
                my_un = user_info.get("username")
                if not my_un:
                    page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                    page.snack_bar.open = True
                    page.update()
                    return
                try:
                    requests.post(f"{FORUM_API_URL}/support", json={"post_id": post_id, "username": my_un}, timeout=8)
                except Exception as ex:
                    print("Ошибка поддержки:", ex)
                load_post()

            header_items = [
                ft.Row(
                    [
                        ft.Text(type_label, color=teal, weight="bold", size=13),
                        ft.Text(f"{STATUS_ICONS.get(data['status'], '')} {data['status_label']}", color="grey", size=13)
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                ),
                ft.Text(data["title"], size=20, weight="bold", color="black"),
                ft.Text(f"{data['author_nickname']} · {format_relative_date(data['created_at'])}", color="grey", size=12),
                ft.Container(height=10),
                ft.Text(data["description"], color="black", size=14, selectable=True),
            ]

            if data.get("steps_to_reproduce"):
                header_items.append(ft.Container(height=10))
                header_items.append(ft.Text("Шаги воспроизведения", color="grey", size=12, weight="bold"))
                header_items.append(ft.Text(data["steps_to_reproduce"], color="black", size=14, selectable=True))

            if data.get("image_url"):
                header_items.append(ft.Container(height=10))
                header_items.append(ft.Image(src=f"{MEDIA_BASE_URL}{data['image_url']}", width=300, border_radius=10, fit=ft.ImageFit.CONTAIN))

            if data.get("implemented_version"):
                header_items.append(ft.Container(height=10))
                header_items.append(
                    ft.Container(
                        padding=10, bgcolor="#E8F8EF", border_radius=8,
                        content=ft.Text(
                            f"🟢 Реализовано в версии {data['implemented_version']} · {format_relative_date(data['implemented_at'])}",
                            color="#1E7A46", size=12
                        )
                    )
                )

            header_items.append(ft.Container(height=14))
            header_items.append(
                ft.ElevatedButton(
                    f"{'✓ Поддержано' if data.get('i_supported') else '👍 Поддержать'} ({data['support_count']})",
                    bgcolor=blue_accent if data.get("i_supported") else teal,
                    color=white,
                    on_click=toggle_support
                )
            )

            if is_dev:
                header_items.append(ft.Container(height=14))
                header_items.append(ft.Divider(height=1, color="#F0F0F0"))
                header_items.append(ft.Text("ПАНЕЛЬ РАЗРАБОТЧИКА", color=teal, weight="bold", size=11))
                header_items.append(build_dev_panel(data))

            header_items.append(ft.Container(height=10))
            header_items.append(ft.Divider(height=1, color="#F0F0F0"))
            header_items.append(ft.Text(f"Комментарии ({len(data['comments'])})", color="black", weight="bold", size=14))

            body_list.controls.append(ft.Container(padding=16, content=ft.Column(header_items, spacing=4)))

            for c in data["comments"]:
                body_list.controls.append(build_comment_tile(c))

            if data.get("comments_closed"):
                body_list.controls.append(
                    ft.Container(
                        padding=ft.padding.only(left=16, right=16, top=10),
                        content=ft.Text("Комментарии закрыты", color="grey", size=12, text_align=ft.TextAlign.CENTER)
                    )
                )
                comment_row.visible = False
            else:
                comment_row.visible = True

            page.update()

        def build_comment_tile(c):
            is_dev_reply = c.get("is_developer_reply")
            col_controls = [
                ft.Row(
                    [
                        ft.Text(c["author_nickname"], weight="bold", size=13, color="black"),
                        ft.Text(format_relative_date(c["created_at"]), size=11, color="grey")
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                )
            ]
            if is_dev_reply:
                col_controls.append(
                    ft.Container(
                        content=ft.Text("Ответ разработчика", size=10, color=white, weight="bold"),
                        bgcolor=teal, padding=ft.padding.only(left=6, right=6, top=2, bottom=2), border_radius=6
                    )
                )
            col_controls.append(ft.Text(c["text"], color="black", size=13))

            return ft.Container(
                padding=ft.padding.only(left=16, right=16, top=10, bottom=10),
                bgcolor="#FFF8EC" if is_dev_reply else None,
                content=ft.Column(col_controls, spacing=4)
            )

        def build_dev_panel(data):
            version_input = ft.TextField(
                label="Версия (для статуса «Реализовано»)",
                value=data.get("implemented_version") or "",
                border_color=teal, dense=True, width=280
            )

            def set_status(new_status):
                def handler(e):
                    my_un = user_info.get("username")
                    payload = {"post_id": post_id, "username": my_un, "status": new_status}
                    if new_status == "implemented":
                        payload["implemented_version"] = version_input.value
                    try:
                        requests.post(f"{FORUM_API_URL}/set-status", json=payload, timeout=8)
                    except Exception as ex:
                        print("Ошибка смены статуса:", ex)
                    load_post()
                return handler

            def status_button(status_key, label):
                selected = data["status"] == status_key
                return ft.ElevatedButton(
                    label, bgcolor=teal if selected else "#EEEEEE", color=white if selected else "black",
                    on_click=set_status(status_key)
                )

            def toggle_priority(e):
                my_un = user_info.get("username")
                try:
                    requests.post(f"{FORUM_API_URL}/toggle-priority", json={"post_id": post_id, "username": my_un}, timeout=8)
                except Exception as ex:
                    print("Ошибка приоритета:", ex)
                load_post()

            def toggle_pin(e):
                my_un = user_info.get("username")
                try:
                    requests.post(f"{FORUM_API_URL}/toggle-pin", json={"post_id": post_id, "username": my_un}, timeout=8)
                except Exception as ex:
                    print("Ошибка закрепления:", ex)
                load_post()

            def toggle_comments(e):
                my_un = user_info.get("username")
                try:
                    requests.post(f"{FORUM_API_URL}/toggle-comments-closed", json={"post_id": post_id, "username": my_un}, timeout=8)
                except Exception as ex:
                    print("Ошибка закрытия комментариев:", ex)
                load_post()

            def go_delete(e):
                show_forum_delete_confirm_screen(post_id, data["title"], filter_type, sort_mode)

            return ft.Column(
                [
                    ft.Row(
                        [
                            status_button("considering", "🟡 Рассматривается"),
                            status_button("in_progress", "🔵 В работе"),
                            status_button("implemented", "🟢 Реализовано"),
                        ],
                        wrap=True, spacing=6
                    ),
                    version_input,
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                "⭐ Убрать приоритет" if data.get("is_priority") else "⭐ Сделать приоритетным",
                                bgcolor="#EEEEEE" if data.get("is_priority") else peach, color="black",
                                on_click=toggle_priority
                            ),
                            ft.ElevatedButton(
                                "📌 Открепить" if data.get("is_pinned") else "📌 Закрепить",
                                bgcolor="#EEEEEE" if data.get("is_pinned") else peach, color="black",
                                on_click=toggle_pin
                            ),
                        ],
                        spacing=6, wrap=True
                    ),
                    ft.Row(
                        [
                            ft.Text("Комментарии закрыты", size=13, color="black"),
                            ft.Switch(value=data.get("comments_closed", False), active_color=peach, on_change=toggle_comments)
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                    ),
                    ft.TextButton("🗑 Удалить публикацию", style=ft.ButtonStyle(color="red"), on_click=go_delete)
                ],
                spacing=10
            )

        comment_input = ft.TextField(hint_text="Написать комментарий...", expand=True, border_color=teal, border_radius=20, content_padding=10)

        def send_comment(e):
            text = comment_input.value.strip() if comment_input.value else ""
            if not text:
                return
            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return
            comment_input.value = ""
            page.update()
            try:
                requests.post(f"{FORUM_API_URL}/comment", json={"post_id": post_id, "username": my_un, "text": text}, timeout=8)
            except Exception as ex:
                print("Ошибка комментария:", ex)
            load_post()

        comment_row = ft.Container(
            padding=10, bgcolor=white,
            content=ft.Row([comment_input, ft.IconButton(ft.icons.SEND, icon_color=teal, on_click=send_comment)])
        )

        page.add(body_list, comment_row)
        page.update()

        load_post()

    # --- ПРЕДЛОЖИТЬ ИДЕЮ (с проверкой похожих тем перед публикацией) ---
    def show_create_idea_screen(e=None):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_screen()

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Предложить идею", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        title_input = ft.TextField(label="Заголовок", border_color=teal, width=350, autofocus=True)
        desc_input = ft.TextField(label="Описание", multiline=True, min_lines=5, max_lines=12, border_color=teal, width=350)
        similar_list = ft.Column([], spacing=4)

        def do_publish(e):
            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return
            try:
                res = requests.post(f"{FORUM_API_URL}/create-post", json={
                    "type": "idea", "title": title_input.value, "description": desc_input.value, "username": my_un
                }, timeout=8)
                if res.status_code == 200:
                    show_forum_post_screen(res.json()["id"])
                else:
                    try:
                        detail = res.json().get("detail", "Ошибка")
                    except Exception:
                        detail = "Ошибка"
                    page.snack_bar = ft.SnackBar(ft.Text(detail))
                    page.snack_bar.open = True
                    page.update()
            except Exception as ex:
                print("Ошибка публикации идеи:", ex)
                page.snack_bar = ft.SnackBar(ft.Text("Сервер недоступен"))
                page.snack_bar.open = True
                page.update()

        def publish_click(e):
            title = title_input.value.strip() if title_input.value else ""
            desc = desc_input.value.strip() if desc_input.value else ""
            if not title or not desc:
                page.snack_bar = ft.SnackBar(ft.Text("Заполните заголовок и описание"))
                page.snack_bar.open = True
                page.update()
                return

            publish_btn.disabled = True
            publish_btn.text = "Проверка..."
            page.update()

            def check_similar_work():
                try:
                    res = requests.get(f"{FORUM_API_URL}/search-similar", params={"title": title}, timeout=8)
                    similar = res.json() if res.status_code == 200 else []
                except Exception:
                    similar = []

                publish_btn.disabled = False
                publish_btn.text = "Опубликовать"

                if similar:
                    similar_list.controls.clear()
                    similar_list.controls.append(ft.Text("Похожие темы уже есть:", color=teal, weight="bold", size=13))
                    for s in similar:
                        icon = "💡" if s["type"] == "idea" else "🐞"
                        similar_list.controls.append(
                            ft.ListTile(
                                title=ft.Text(f"{icon} {s['title']}", size=13),
                                on_click=lambda ev, pid=s["id"]: show_forum_post_screen(pid)
                            )
                        )
                    similar_list.controls.append(ft.TextButton("Всё равно опубликовать", on_click=do_publish))
                    page.update()
                else:
                    do_publish(e)

            threading.Thread(target=check_similar_work, daemon=True).start()

        publish_btn = ft.ElevatedButton("Опубликовать", bgcolor=teal, color=white, width=350, on_click=publish_click)

        page.add(
            ft.Column(
                expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=15,
                controls=[ft.Container(height=10), title_input, desc_input, publish_btn, similar_list]
            )
        )
        page.update()

    # --- СООБЩИТЬ О БАГЕ (с прикреплением картинки) ---
    def show_create_bug_screen(e=None):
        page.clean()
        page.bgcolor = white
        if page.navigation_bar: page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False

        def go_back(e):
            show_forum_screen()

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text("Сообщить о баге", color=teal, weight="bold"),
            bgcolor=white, elevation=0, visible=True
        )

        title_input = ft.TextField(label="Заголовок", border_color=teal, width=350, autofocus=True)
        desc_input = ft.TextField(label="Описание проблемы", multiline=True, min_lines=4, max_lines=10, border_color=teal, width=350)
        steps_input = ft.TextField(label="Шаги воспроизведения", multiline=True, min_lines=3, max_lines=8, border_color=teal, width=350)

        attached_image_url = {"value": None}
        attach_status_text = ft.Text("", color="grey", size=12)
        image_preview = ft.Container(visible=False, content=ft.Image(width=200, border_radius=8))

        def on_bug_image_picked(ev: ft.FilePickerResultEvent):
            if not ev.files:
                return
            picked = ev.files[0]
            attach_status_text.value = "Загрузка изображения..."
            page.update()

            def upload_work():
                try:
                    with open(picked.path, "rb") as f:
                        files = {"file": (picked.name, f)}
                        res = requests.post(f"{FORUM_API_URL}/upload-image", files=files, timeout=30)
                    if res.status_code == 200:
                        url = res.json().get("image_url")
                        attached_image_url["value"] = url
                        image_preview.content.src = f"{MEDIA_BASE_URL}{url}"
                        image_preview.visible = True
                        attach_status_text.value = "Изображение прикреплено ✓"
                    else:
                        attach_status_text.value = "Не удалось загрузить изображение"
                except Exception as ex:
                    print("Ошибка загрузки изображения бага:", ex)
                    attach_status_text.value = "Ошибка загрузки"
                page.update()

            threading.Thread(target=upload_work, daemon=True).start()

        bug_image_picker = ft.FilePicker(on_result=on_bug_image_picked)
        page.overlay.append(bug_image_picker)

        def do_publish(e):
            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return
            try:
                res = requests.post(f"{FORUM_API_URL}/create-post", json={
                    "type": "bug",
                    "title": title_input.value,
                    "description": desc_input.value,
                    "steps_to_reproduce": steps_input.value,
                    "image_url": attached_image_url["value"],
                    "username": my_un
                }, timeout=8)
                if res.status_code == 200:
                    show_forum_post_screen(res.json()["id"])
                else:
                    try:
                        detail = res.json().get("detail", "Ошибка")
                    except Exception:
                        detail = "Ошибка"
                    page.snack_bar = ft.SnackBar(ft.Text(detail))
                    page.snack_bar.open = True
                    page.update()
            except Exception as ex:
                print("Ошибка публикации бага:", ex)
                page.snack_bar = ft.SnackBar(ft.Text("Сервер недоступен"))
                page.snack_bar.open = True
                page.update()

        def publish_click(e):
            title = title_input.value.strip() if title_input.value else ""
            desc = desc_input.value.strip() if desc_input.value else ""
            if not title or not desc:
                page.snack_bar = ft.SnackBar(ft.Text("Заполните заголовок и описание"))
                page.snack_bar.open = True
                page.update()
                return
            do_publish(e)

        publish_btn = ft.ElevatedButton("Опубликовать", bgcolor="#E67E22", color=white, width=350, on_click=publish_click)

        page.add(
            ft.Column(
                expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=15,
                controls=[
                    ft.Container(height=10),
                    title_input,
                    desc_input,
                    steps_input,
                    ft.TextButton("📎 Прикрепить изображение (по желанию)", on_click=lambda e: bug_image_picker.pick_files(allow_multiple=False)),
                    attach_status_text,
                    image_preview,
                    publish_btn
                ]
            )
        )
        page.update()

    def show_main_screen():
        page.clean()
        page.bgcolor = white
        page.vertical_alignment = ft.MainAxisAlignment.START
        page.horizontal_alignment = ft.CrossAxisAlignment.START
        page.floating_action_button = ft.FloatingActionButton(icon=ft.icons.ADD, bgcolor=peach, on_click=show_new_message_screen)
        my_username = user_info.get('username')

        # 🔥 Открываем (если ещё не открыто) постоянное соединение "я онлайн"
        start_presence_connection()
        # 🔥 Чиним старые сохранённые ЛС, в которых нет other_username
        heal_saved_chats()

        # Личные чаты и группы пользователя (глобальный чат и бот убраны -
        # они были нужны не всем, и по факту просто занимали место)
        chat_tiles = []

        # 🔥 ПРОКАЧКА: Загружаем все личные диалоги на главный экран (сразу, из локального кэша, без сети)
        saved_chats = page.client_storage.get(get_chats_key()) or []
        chat_id_to_tile = {}  # запомним тайлы, чтобы потом дорисовать бейджи непрочитанных
        muted_chat_ids = {c["chat_id"] for c in saved_chats if c.get("muted")}  # 🔥 заглушённые - без бейджа
        for c in saved_chats:
            is_saved_msgs = (c["title"] == "Избранное (Я)")
            avatar_content = "🌟" if is_saved_msgs else c["title"][0].upper()
            avatar_bg = blue_accent if is_saved_msgs else get_avatar_color(c["title"])

            title_row_controls = [ft.Text(c["title"], weight="bold", color="black")]
            if c.get("muted"):
                title_row_controls.append(ft.Icon(ft.icons.NOTIFICATIONS_OFF_OUTLINED, size=14, color="grey"))

            tile = ft.ListTile(
                leading=ft.CircleAvatar(content=ft.Text(avatar_content, color=white, size=18 if is_saved_msgs else 20), bgcolor=avatar_bg),
                title=ft.Row(title_row_controls, spacing=6),
                trailing=None,
                on_click=lambda e, title=c["title"], cid=c["chat_id"], ou=c.get("other_username"), ic=c.get("is_channel", False): show_chat_screen(title, cid, other_username=ou, is_channel=ic)
            )
            chat_tiles.append(tile)
            chat_id_to_tile[c["chat_id"]] = tile

        # 🔥 Если чатов пока нет вообще - показываем подсказку, а не пустой экран
        if not chat_tiles:
            chat_tiles.append(
                ft.Container(
                    padding=ft.padding.only(top=80, left=40, right=40),
                    alignment=ft.alignment.center,
                    content=ft.Column(
                        [
                            ft.Icon(ft.icons.CHAT_BUBBLE_OUTLINE, size=48, color="#BDBDBD"),
                            ft.Container(height=10),
                            ft.Text("Пока нет ни одного чата", color="grey", size=16, weight="bold"),
                            ft.Text("Нажмите + внизу, чтобы найти собеседника", color="grey", size=13),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=2
                    )
                )
            )

        chats_content = ft.ListView(expand=True, controls=chat_tiles)
        display_username = user_info.get('username')
        username_display.value = f"@{display_username}" if display_username else "Нажмите, чтобы задать @username"
        
        display_nick = user_info.get("nickname") or "Пользователь"
        first_letter = display_nick[0].upper()
        
        profile_avatar = ft.CircleAvatar(
            radius=50, 
            bgcolor=teal, 
            foreground_image_src=user_info.get("avatar_path"),
            content=ft.Text(first_letter, size=40, color=white) if not user_info.get("avatar_path") else None
        )
        CURRENT_VERSION = "v1.0.0"

        def check_for_updates(e):
            btn_update.text = "Проверка обновлений..."
            btn_update.disabled = True
            page.update()
            
            # Имитация запроса к GitHub (потом заменим на реальный requests.get)
            time.sleep(1.5) 
            
            page.snack_bar = ft.SnackBar(ft.Text(f"У вас установлена последняя версия ({CURRENT_VERSION})"))
            page.snack_bar.open = True
            btn_update.text = f"Версия {CURRENT_VERSION} (Проверить)"
            btn_update.disabled = False
            page.update()

        btn_update = ft.ElevatedButton(
            f"Версия {CURRENT_VERSION} (Проверить)", 
            icon=ft.icons.SYSTEM_UPDATE, 
            bgcolor=teal, 
            color=white,
            on_click=check_for_updates
        )

        settings_content = ft.Column(
            expand=True, 
            controls=[
                ft.ListTile(title=ft.Text("Настройки приложения", size=20, weight="bold")),
                ft.Divider(),
                ft.ListTile(
                    leading=ft.Icon(ft.icons.FORUM_OUTLINED, color=teal),
                    title=ft.Text("Форум идей"),
                    subtitle=ft.Text("Предложить идею или сообщить о баге"),
                    trailing=ft.Icon(ft.icons.CHEVRON_RIGHT, color="grey"),
                    on_click=lambda e: show_forum_screen()
                ),
                ft.Divider(),
                ft.ListTile(
                    leading=ft.Icon(ft.icons.COLOR_LENS, color=teal),
                    title=ft.Text("Тема оформления"),
                    subtitle=ft.Text("Пока доступна только светлая тема")
                ),
                ft.ListTile(
                    leading=ft.Icon(ft.icons.NOTIFICATIONS, color=teal),
                    title=ft.Text("Уведомления"),
                    trailing=ft.Switch(value=True, active_color=peach)
                ),
                ft.Divider(),
                ft.Container(
                    padding=20,
                    alignment=ft.alignment.center,
                    content=ft.Column([
                        ft.Text("Обновление системы", color="grey"),
                        btn_update
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)
                )
            ]
        )

        profile_content = ft.Column(
            expand=True, 
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(height=30), 
                profile_avatar,
                ft.Text(display_nick, size=24, weight="bold", color="black"),
                ft.Text(user_info.get("email", ""), color="grey"), 
                ft.Divider(),
                ft.ListTile(
                    title=ft.Text("Имя пользователя"), 
                    subtitle=username_display, 
                    on_click=lambda _: setattr(username_edit, 'visible', True) or page.update()
                ),
                ft.Container(padding=20, content=username_edit),
                ft.ElevatedButton(
                    "Выйти из аккаунта", 
                    icon=ft.icons.LOGOUT, 
                    bgcolor="#FFF0F0", 
                    color="red", 
                    on_click=logout
                )
            ]
        )

        container = ft.Container(content=chats_content, expand=True)

        def switch_tab(e):
            selected = e.control.selected_index
            if selected == 0:
                page.appbar.title.value = "Mesme"
                page.appbar.actions = [ft.IconButton(ft.icons.SEARCH, icon_color=teal, on_click=lambda _: show_search_screen())]
                page.floating_action_button.visible = True # 🔥 Показываем плюсик
                container.content = chats_content
            elif selected == 1:
                page.appbar.title.value = "Настройки"
                page.appbar.actions = []
                page.floating_action_button.visible = False # 🔥 Скрываем
                container.content = settings_content
            else:
                page.appbar.title.value = "Профиль"
                page.appbar.actions = []
                page.floating_action_button.visible = False # 🔥 Скрываем
                container.content = profile_content
            page.update()

        page.appbar = ft.AppBar(
            title=ft.Text("Mesme", color=teal, weight="bold"), 
            bgcolor=white, 
            # 🔥 Добавлена кнопка поиска
            actions=[ft.IconButton(ft.icons.SEARCH, icon_color=teal, on_click=lambda _: show_search_screen())]
        )
        page.appbar.visible = True
        
        page.navigation_bar = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.icons.CHAT_BUBBLE_OUTLINE,
                    selected_icon=ft.icons.CHAT_BUBBLE,
                    label="Чаты"
                ),
                ft.NavigationBarDestination(
                    icon=ft.icons.SETTINGS_OUTLINED,
                    selected_icon=ft.icons.SETTINGS,
                    label="Настройки"
                ),
                ft.NavigationBarDestination(
                    icon=ft.icons.PERSON_OUTLINE,
                    selected_icon=ft.icons.PERSON,
                    label="Профиль"
                )
            ],
            on_change=switch_tab,
            bgcolor=white,
            indicator_color=peach
        )
        page.navigation_bar.visible = True
        
        page.add(container)
        page.update()

        # 🔥 Бейджи непрочитанных подгружаем ПОСЛЕ отрисовки, отдельным фоновым
        # потоком - раньше этот запрос стоял в самом начале функции и держал
        # экран пустым (после page.clean()) все те 1-3 секунды, что сервер отвечал
        def load_unread_counts():
            if not my_username:
                return
            try:
                res = requests.get(f"{CHAT_API_URL}/unread-counts/{my_username}", timeout=8)
                if res.status_code != 200:
                    return
                counts = res.json()
            except Exception:
                return

            for cid, tile in chat_id_to_tile.items():
                count = counts.get(cid, 0)
                if count > 0 and cid not in muted_chat_ids:
                    tile.trailing = ft.Container(
                        content=ft.Text(str(count), color=white, size=12, weight="bold"),
                        bgcolor=blue_accent, padding=ft.padding.only(left=8, right=8, top=4, bottom=4), border_radius=15
                    )
                else:
                    tile.trailing = None
            page.update()

        threading.Thread(target=load_unread_counts, daemon=True).start()

    # --- Инициализация кнопок ---
    btn_request = ft.ElevatedButton(
        "Получить код", 
        on_click=request_code_click, 
        width=300, 
        bgcolor=peach, 
        color="black"
    )
    
    btn_verify = ft.ElevatedButton(
        "Войти", 
        on_click=verify_code_click, 
        width=300, 
        bgcolor=peach, 
        color="black"
    )
    
    login_view = ft.Column(
        [
            ft.Text("Вход в Mesme", size=30, weight="bold", color=peach), 
            email_field, 
            btn_request
        ], 
        horizontal_alignment=ft.CrossAxisAlignment.CENTER
    )
    
    otp_view = ft.Column(
        [
            ft.Text("Введите 6 цифр", size=25, weight="bold", color=peach), 
            otp_field, 
            btn_verify
        ], 
        horizontal_alignment=ft.CrossAxisAlignment.CENTER
    )
    
    login_card = ft.Container(
        content=login_view, 
        padding=40, 
        bgcolor=card_bg, 
        border_radius=20
    )

    # --- Точка входа ---
    token = page.client_storage.get("mesme_token")
    if token:
        try:
            response = requests.post(f"{API_URL}/get-profile", json={"token": token}, timeout=3)
            if response.status_code == 200:
                data = response.json()
                user_info["email"] = data.get("email")
                user_info["nickname"] = data.get("nickname")
                user_info["username"] = data.get("username")
                user_info["avatar_path"] = data.get("avatar_path")
                show_main_screen()
            else:
                page.client_storage.remove("mesme_token")
                show_login_screen()
        except Exception as e:
            # 🔥 ТЕПЕРЬ МЫ УВИДИМ РЕАЛЬНУЮ ОШИБКУ В ТЕРМИНАЛЕ
            print(f"КРИТИЧЕСКАЯ ОШИБКА ПОДКЛЮЧЕНИЯ: {e}") 
            show_login_screen()
    else:
        show_login_screen()

ft.app(target=main)