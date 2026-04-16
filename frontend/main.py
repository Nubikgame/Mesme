import flet as ft
import requests
import time
import json
import websocket
import threading
from datetime import datetime

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

    # 🔥 ФУНКЦИЯ ГЕНЕРАЦИИ ID ПРИВАТНОЙ КОМНАТЫ
    def get_private_room_id(u1, u2):
        return "p2p_" + "_".join(sorted([u1, u2]))

    # 🔥 ОБРАБОТЧИК СООБЩЕНИЙ С УЧЕТОМ ВРЕМЕНИ
    def on_ws_message(ws, message):
        data = json.loads(message)
        sender = data.get("sender")
        text = data.get("text")
        timestamp = data.get("timestamp")
        
        append_message_to_ui(sender, text, timestamp)

    # 🔥 ДОБАВЛЕНО ОТОБРАЖЕНИЕ ВРЕМЕНИ В ПУЗЫРЯХ
    def append_message_to_ui(sender, text, timestamp=None):
        if timestamp:
            time_str = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%H:%M")
        else:
            time_str = datetime.now().strftime("%H:%M")

        is_me = (sender == user_info.get("nickname"))
        
        bubble_color = teal if is_me else "#F0F0F0"
        text_color = white if is_me else "black"
        alignment = ft.MainAxisAlignment.END if is_me else ft.MainAxisAlignment.START
        bubble_width = None if len(text) < 30 else 250

        message_bubble = ft.Row(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(sender, size=10, color=peach if is_me else "grey", weight="bold"),
                            ft.Text(text, color=text_color, selectable=True),
                            ft.Text(time_str, size=9, color="grey", text_align=ft.TextAlign.RIGHT)
                        ], 
                        spacing=2
                    ),
                    bgcolor=bubble_color, 
                    padding=10, 
                    border_radius=10, 
                    width=bubble_width
                )
            ], 
            alignment=alignment
        )

        chat_messages_list.controls.append(message_bubble)
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
                    saved_chats = page.client_storage.get("mesme_chats") or []
                    if not any(c["chat_id"] == group_id for c in saved_chats):
                        saved_chats.append({"title": name, "chat_id": group_id})
                        page.client_storage.set("mesme_chats", saved_chats)

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
                    chat_title = "Избранное (Я)" if target["username"] == my_un else target["nickname"]

                    saved_chats = page.client_storage.get("mesme_chats") or []
                    if not any(c["chat_id"] == room_id for c in saved_chats):
                        saved_chats.append({"title": chat_title, "chat_id": room_id})
                        page.client_storage.set("mesme_chats", saved_chats)

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
            on_submit=do_search
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
    def start_search(e):
        search_field = ft.TextField(label="Введите @username", autofocus=True)
        
        def confirm_search(e):
            username = search_field.value.replace("@", "").strip()
            if not username: return
            
            try:
                res = requests.get(f"{API_URL}/find-user/{username}")
                if res.status_code == 200:
                    target = res.json()
                    page.dialog.open = False
                    
                    my_un = user_info.get("username")
                    if not my_un:
                        page.snack_bar = ft.SnackBar(ft.Text("Сначала задайте @username в профиле!"))
                        page.snack_bar.open = True
                        page.update()
                        return
                    
                    room_id = get_private_room_id(my_un, target["username"])
                    
                    # 🔥 ПРОКАЧКА: Если ищем сами себя - делаем "Избранное"
                    if target["username"] == my_un:
                        chat_title = "Избранное (Я)"
                    else:
                        chat_title = target["nickname"]

                    # 🔥 ПРОКАЧКА: Запоминаем чат, чтобы он появился на главном экране!
                    saved_chats = page.client_storage.get("mesme_chats") or []
                    # Проверяем, нет ли уже такого чата в списке
                    if not any(c["chat_id"] == room_id for c in saved_chats):
                        saved_chats.append({"title": chat_title, "chat_id": room_id})
                        page.client_storage.set("mesme_chats", saved_chats)
                    
                    show_chat_screen(chat_title, room_id)
                else:
                    search_field.error_text = "Пользователь не найден"
                    page.update()
            except Exception as ex:
                print("Ошибка поиска:", ex)

        page.dialog = ft.AlertDialog(
            title=ft.Text("Поиск собеседника"),
            content=search_field,
            actions=[
                ft.TextButton("Отмена", on_click=lambda _: setattr(page.dialog, "open", False) or page.update()),
                ft.ElevatedButton("Найти", on_click=confirm_search, bgcolor=teal, color=white)
            ]
        )
        page.dialog.open = True
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
            response = requests.post(f"{API_URL}/request-code", json={"email": email}, timeout=3)
            if response.status_code == 200:
                login_card.content = otp_view
            else:
                email_field.error_text = "Ошибка сервера (Проверь пароль от почты!)"
        except requests.exceptions.ConnectionError:
            email_field.error_text = "Сервер недоступен!"

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
    def show_chat_screen(chat_title, chat_id="global"):
        nonlocal current_open_chat
        current_open_chat = chat_id
        
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
        
        if chat_title == "Mesme Bot":
            chat_messages_list.controls.append(
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Text("Привет! Я официальный бот Mesme.", color="black"), 
                            bgcolor=peach, 
                            padding=10, 
                            border_radius=10
                        )
                    ], 
                    alignment=ft.MainAxisAlignment.START
                )
            )
        else:
            # ЗАГРУЖАЕМ ИСТОРИЮ ИЗ БД ПО CHAT_ID
            try:
                hist_res = requests.get(f"{CHAT_API_URL}/history/{chat_id}")
                if hist_res.status_code == 200:
                    for msg in hist_res.json():
                        append_message_to_ui(msg["sender"], msg["text"], msg.get("timestamp"))
            except Exception as e:
                print("Ошибка истории:", e)

            # ПОДКЛЮЧАЕМ РЕАЛТАЙМ
            def connect_ws():
                nonlocal ws_app
                ws_url = f"ws://127.0.0.1:8000/chat/ws/{chat_id}"
                ws_app = websocket.WebSocketApp(ws_url, on_message=on_ws_message)
                ws_app.run_forever()
            
            wst = threading.Thread(target=connect_ws)
            wst.daemon = True
            wst.start()

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

            if chat_title == "Mesme Bot":
                append_message_to_ui(user_info.get("nickname", "Я"), val)
                time.sleep(0.5)
                append_message_to_ui("Mesme Bot", "Сообщение получено. Я пока не настроен на умные ответы!")

            else:
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

        page.add(chat_messages_list, input_row)
        page.update()

    def show_main_screen():
        page.clean()
        page.bgcolor = white
        page.vertical_alignment = ft.MainAxisAlignment.START
        page.horizontal_alignment = ft.CrossAxisAlignment.START
        page.floating_action_button = ft.FloatingActionButton(
            icon=ft.icons.ADD,
            bgcolor=peach,
            on_click=show_new_message_screen
        )

        # Базовые чаты, которые есть всегда
        chat_tiles = [
            ft.ListTile(
                leading=ft.CircleAvatar(content=ft.Text("G", color=white), bgcolor=peach),
                title=ft.Text("Глобальный Чат", weight="bold", color="black"),
                subtitle=ft.Text("Общий чат для всех онлайн", color="black54"),
                on_click=lambda _: show_chat_screen("Глобальный Чат", "global")
            ),
            ft.ListTile(
                leading=ft.CircleAvatar(content=ft.Text("M", color=white), bgcolor=teal),
                title=ft.Text("Mesme Bot", weight="bold", color="black"),
                subtitle=ft.Text("Добро пожаловать в Message Me!", color="black54"),
                on_click=lambda _: show_chat_screen("Mesme Bot", "bot")
            )
        ]

        # 🔥 ПРОКАЧКА: Загружаем все личные диалоги на главный экран
        saved_chats = page.client_storage.get("mesme_chats") or []
        for c in saved_chats:
            is_saved_msgs = (c["title"] == "Избранное (Я)")
            # Для "Избранного" делаем иконку звездочки, для остальных - первую букву имени
            avatar_content = "🌟" if is_saved_msgs else c["title"][0].upper()
            avatar_bg = "#4A90E2" if is_saved_msgs else "#8E8E93"

            chat_tiles.append(
                ft.ListTile(
                    leading=ft.CircleAvatar(content=ft.Text(avatar_content, color=white, size=18 if is_saved_msgs else 20), bgcolor=avatar_bg),
                    title=ft.Text(c["title"], weight="bold", color="black"),
                    # Используем lambda с сохранением контекста для правильного открытия нужного чата
                    on_click=lambda e, title=c["title"], cid=c["chat_id"]: show_chat_screen(title, cid)
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
                page.appbar.actions = [ft.IconButton(ft.icons.SEARCH, icon_color=teal, on_click=start_search)]
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
            actions=[ft.IconButton(ft.icons.SEARCH, icon_color=teal, on_click=start_search)]
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