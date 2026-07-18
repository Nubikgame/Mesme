import flet as ft
import requests
import time
import json
import websocket
import threading
from datetime import datetime, timezone


SERVER_IP = "127.0.0.1"

API_URL = "http://127.0.0.1:8000/auth"
CHAT_API_URL = "http://127.0.0.1:8000/chat"

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
    ws_app = None
    blue_accent = "#4A90E2" 
    my_message_status_ctrls = {}  # msg_id -> ft.Text с галочками (пока сообщение не прочитано)
    def get_chats_key():
        return f"mesme_chats_{user_info['email']}"

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
                msg_id=data.get("id")
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

    def append_message_to_ui(sender, text, timestamp=None, is_read=False, is_delivered=False, msg_id=None, update=True):
        if timestamp:
            utc_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=timezone.utc)
            local_dt = utc_dt.astimezone()
            time_str = local_dt.strftime("%H:%M")
        else:
            time_str = datetime.now().strftime("%H:%M")

        is_me = (sender == user_info.get("nickname"))
        
        bubble_color = teal if is_me else "#F0F0F0"
        text_color = white if is_me else "black"
        alignment = ft.MainAxisAlignment.END if is_me else ft.MainAxisAlignment.START
        bubble_width = None if len(text) < 30 else 250

        is_private_chat = current_open_chat and current_open_chat.startswith("p2p_")
        
        bubble_content = []
        if not is_private_chat:
            bubble_content.append(ft.Text(sender, size=10, color=peach if is_me else "grey", weight="bold"))
        
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
    def show_create_group_screen(e):
        page.clean()
        page.bgcolor = white

        # Скрываем навигацию и FAB
        if page.navigation_bar:
            page.navigation_bar.visible = False
        if page.floating_action_button:
            page.floating_action_button.visible = False

        def go_back(e):
            show_new_message_screen(None)   # возвращаемся на экран "Новое сообщение"

        page.appbar = ft.AppBar(
            leading=ft.IconButton(
                icon=ft.icons.ARROW_BACK,
                icon_color="black",
                on_click=go_back
            ),
            title=ft.Text("Новая группа", color="black", weight="bold"),
            bgcolor=white,
            elevation=0,
            visible=True
        )

        group_name_input = ft.TextField(
            label="Название группы", 
            border_color=teal, 
            autofocus=True,
            width=350
        )
        
        is_public_switch = ft.Switch(
            label="Публичная группа (видна в поиске)", 
            value=True, 
            active_color=peach
        )

        def create_btn_click(e):
            name = group_name_input.value.strip()
            if not name:
                group_name_input.error_text = "Введите название группы!"
                page.update()
                return

            my_un = user_info.get("username")
            if not my_un:
                page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                page.snack_bar.open = True
                page.update()
                return

            try:
                res = requests.post(
                    f"{CHAT_API_URL}/create-group", 
                    json={
                        "name": name,
                        "is_public": is_public_switch.value,
                        "owner_username": my_un
                    },
                    timeout=8
                )

                if res.status_code == 200:
                    data = res.json()
                    group_id = data["group_id"]

                    # Сохраняем группу в сохранённые чаты
                    saved_chats = page.client_storage.get(get_chats_key()) or []
                    if not any(c["chat_id"] == group_id for c in saved_chats):
                        saved_chats.append({"title": name, "chat_id": group_id})
                        page.client_storage.set(get_chats_key(), saved_chats)

                    # Переходим сразу в созданную группу
                    show_chat_screen(name, group_id)
                else:
                    page.snack_bar = ft.SnackBar(ft.Text(f"Ошибка сервера: {res.status_code}"))
                    page.snack_bar.open = True
                    page.update()
            except Exception as ex:
                print("Ошибка создания группы:", ex)
                page.snack_bar = ft.SnackBar(ft.Text("Сервер недоступен или ошибка соединения"))
                page.snack_bar.open = True
                page.update()

        # Кнопка создания
        create_button = ft.ElevatedButton(
            "Создать группу",
            bgcolor=teal,
            color=white,
            width=350,
            on_click=create_btn_click
        )

        # Основной контент
        content = ft.Column(
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=20,
            controls=[
                ft.Container(height=30),
                ft.Text("Создание новой группы", size=22, weight="bold", color=teal),
                group_name_input,
                is_public_switch,
                ft.Container(height=20),
                create_button
            ]
        )

        page.add(content)
        page.update()   # ← Это критично!

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
        def do_search(e):
            username = search_field.value.replace("@", "").strip()
            if not username:
                return

            try:
                search_field.prefix_icon = ft.icons.HOURGLASS_EMPTY
                page.update()

                res = requests.get(f"{API_URL}/find-user/{username}", timeout=5)
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
                        saved_chats.append({"title": chat_title, "chat_id": room_id})
                        page.client_storage.set(get_chats_key(), saved_chats)

                    show_chat_screen(chat_title, room_id)
                else:
                    search_field.error_text = "Пользователь не найден"
                    search_field.prefix_icon = ft.icons.SEARCH
                    page.update()
            except Exception as ex:
                print("Ошибка поиска:", ex)
                search_field.error_text = "Ошибка сервера"
                search_field.prefix_icon = ft.icons.SEARCH
                page.update()

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
                    on_click=lambda _: None # Убрал show_snack_bar чтобы не крашилось
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

        search_results_list = ft.ListView(expand=True, spacing=10, padding=10)


        def go_back(e):
            show_main_screen()

        # Функция для сохранения и перехода в чат
        def save_and_open_chat(chat_title, room_id):
            saved_chats = page.client_storage.get(get_chats_key()) or []
            if not any(c["chat_id"] == room_id for c in saved_chats):
                saved_chats.append({"title": chat_title, "chat_id": room_id})
                page.client_storage.set(get_chats_key(), saved_chats)
            show_chat_screen(chat_title, room_id)

        # Главная функция поиска
        def perform_search(e):
            query = search_input.value.strip()
            search_results_list.controls.clear()
            
            if len(query) < 2:
                page.update()
                return

            # 1. ЛОКАЛЬНЫЙ ПОИСК (по твоим открытым чатам)
            saved_chats = page.client_storage.get(get_chats_key()) or []
            local_results = [c for c in saved_chats if query.lower() in c["title"].lower()]
            
            if local_results:
                search_results_list.controls.append(ft.Text("Мои чаты", color=teal, weight="bold"))
                for c in local_results:
                    search_results_list.controls.append(
                        ft.ListTile(
                            leading=ft.CircleAvatar(content=ft.Text(c["title"][0].upper(), color=white), bgcolor="#8E8E93"),
                            title=ft.Text(c["title"], color="black", weight="bold"),
                            on_click=lambda e, t=c["title"], cid=c["chat_id"]: show_chat_screen(t, cid)
                        )
                    )

            # 2. ГЛОБАЛЬНЫЙ ПОИСК (по всей базе)
            search_results_list.controls.append(ft.Text("Глобальный поиск", color=teal, weight="bold"))
            
            try:
                # Обращаемся к нашей новой умной функции в бэкенде
                res = requests.get(f"{API_URL}/search-users/{query}", timeout=5)
                found_users = res.json() if res.status_code == 200 else []
                my_un = user_info.get("username")

                if not found_users:
                    search_results_list.controls.append(ft.Text("Никто не найден", color="grey"))
                else:
                    # 🔥 ВОТ ЭТА ЗАЩИТА СПАСЕТ ПРИЛОЖЕНИЕ ОТ КРАША!
                    if not my_un:
                        search_results_list.controls.append(ft.Text("⚠️ Сначала задайте @username в Профиле!", color="red"))
                        page.update()
                        return

                    for target in found_users:
                        # Пропускаем тех, у кого нет username
                        if not target.get("username"): continue 

                        room_id = get_private_room_id(my_un, target["username"])
                        chat_title = "Избранное (Я)" if target["username"] == my_un else target["nickname"]
                        
                        search_results_list.controls.append(
                            ft.ListTile(
                                leading=ft.CircleAvatar(content=ft.Text(chat_title[0].upper(), color=white), bgcolor="#4A90E2"),
                                title=ft.Text(chat_title, color="black", weight="bold"),
                                subtitle=ft.Text(f"@{target['username']}", color="grey"),
                                on_click=lambda e, t=chat_title, cid=room_id: save_and_open_chat(t, cid)
                            )
                        )
            except Exception as ex:
                print("Ошибка глобального поиска:", ex)
                search_results_list.controls.append(ft.Text("Ошибка соединения с сервером", color="red"))
            
            page.update()

        # Поле ввода, которое выглядит как встроенное в шапку
        search_input = ft.TextField(
            hint_text="Поиск...", 
            border_radius=30,
            content_padding=10,
            border_color="transparent",
            bgcolor="#F5F5F5",
            expand=True,
            autofocus=True,
            on_change=perform_search, # Поиск запускается по нажатию Enter на клавиатуре
        )

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=search_input,
            bgcolor=white,
            elevation=0
        )
        page.appbar.visible = True

        page.add(search_results_list)
        page.update()

    # --- ИНСТРУМЕНТ ВЫБОРА ФОТО ---
    def on_avatar_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            user_info["avatar_path"] = e.files[0].path
            reg_avatar.foreground_image_src = user_info["avatar_path"]
            reg_avatar.content = None
            page.update()

    avatar_picker = ft.FilePicker(on_result=on_avatar_picked)
    page.overlay.append(avatar_picker)

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
                otp_field.error_text = "Неверный код!"
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
        page.client_storage.remove("mesme_token")
        
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
    def show_chat_screen(chat_title, chat_id):
        nonlocal current_open_chat
        current_open_chat = chat_id
        my_message_status_ctrls.clear()
        
        page.clean()
        page.bgcolor = white
        
        if page.navigation_bar: 
            page.navigation_bar.visible = False
        if page.floating_action_button: page.floating_action_button.visible = False
        
        def go_back(e):
            nonlocal current_open_chat
            current_open_chat = None
            nonlocal ws_app
            
            if ws_app:
                try: 
                    ws_app.close()
                except: 
                    pass
            show_main_screen()

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.icons.ARROW_BACK, icon_color=teal, on_click=go_back),
            title=ft.Text(chat_title, color=teal, weight="bold"),
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

            msg_data = {"sender": user_info.get("nickname", "Аноним"), "text": val}
            nonlocal ws_app
            if ws_app:
                try: 
                    ws_app.send(json.dumps(msg_data))
                except: 
                    print("Ошибка отправки в сокет")

        input_row = ft.Container(
            padding=10, 
            bgcolor=white, 
            content=ft.Row(
                [
                    ft.IconButton(ft.icons.ATTACH_FILE, icon_color=teal),
                    message_input,
                    ft.IconButton(ft.icons.SEND, icon_color=teal, on_click=send_message),
                ]
            )
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

    def show_main_screen():
        page.clean()
        page.bgcolor = white
        page.vertical_alignment = ft.MainAxisAlignment.START
        page.horizontal_alignment = ft.CrossAxisAlignment.START
        page.floating_action_button = ft.FloatingActionButton(icon=ft.icons.ADD, bgcolor=peach, on_click=show_new_message_screen)
        my_username = user_info.get('username')

        # Личные чаты и группы пользователя (глобальный чат и бот убраны -
        # они были нужны не всем, и по факту просто занимали место)
        chat_tiles = []

        # 🔥 ПРОКАЧКА: Загружаем все личные диалоги на главный экран (сразу, из локального кэша, без сети)
        saved_chats = page.client_storage.get(get_chats_key()) or []
        chat_id_to_tile = {}  # запомним тайлы, чтобы потом дорисовать бейджи непрочитанных
        for c in saved_chats:
            is_saved_msgs = (c["title"] == "Избранное (Я)")
            avatar_content = "🌟" if is_saved_msgs else c["title"][0].upper()
            avatar_bg = blue_accent if is_saved_msgs else "#8E8E93"

            tile = ft.ListTile(
                leading=ft.CircleAvatar(content=ft.Text(avatar_content, color=white, size=18 if is_saved_msgs else 20), bgcolor=avatar_bg),
                title=ft.Text(c["title"], weight="bold", color="black"),
                trailing=None,
                on_click=lambda e, title=c["title"], cid=c["chat_id"]: show_chat_screen(title, cid)
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
                if count > 0:
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