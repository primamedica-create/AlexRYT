import flet as ft
import os
import sys
import time
import threading
import traceback
import random
import json  # Ты забыл json, он нужен для подсказок в поиске
from datetime import datetime

# ==========================================
# 0. ИСПРАВЛЕНИЕ ВЫЛЕТОВ ANDROID (SSL FIX)
# ==========================================
# ЭТО САМОЕ ВАЖНОЕ: Без этого requests и yt-dlp не заработают на телефоне
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass # На компьютере сработает и так, это для телефона

# ==========================================
#               КОНФИГУРАЦИЯ
# ==========================================

APP_NAME = "AlexRYT v16 Ultimate"
BLACKLIST_DEFAULTS = [
    "MrBeast", 
    "PewDiePie", 
    "T-Series", 
    "Cocomelon", 
    "5-Minute Crafts"
]

# Глобальные переменные для библиотек (Ленивая загрузка)
yt_dlp = None
requests = None
openpyxl = None

# Глобальное состояние приложения
state = {
    "favorites": {
        "videos": [], 
        "channels": [], 
        "shorts": []
    },
    "tracking": [], 
    "history": [],
    "proxies": [],
    "last_search": [],
    "is_initialized": False
}

# ==========================================
#           ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def get_proxy():
    """Возвращает случайный прокси из списка, если они есть."""
    if not state["proxies"]:
        return None
    p = random.choice(state["proxies"])
    if not p.startswith("http"):
        return f"http://{p}"
    return p

def format_number(num):
    """Красивое форматирование чисел (1.2M, 500K)."""
    if not num:
        return "0"
    try:
        n = float(num)
        if n >= 1000000:
            return f"{n/1000000:.1f}M"
        if n >= 1000:
            return f"{n/1000:.1f}K"
        return str(int(n))
    except:
        return str(num)

def check_monetization(subs, views):
    """Эвристическая проверка монетизации."""
    try:
        if not subs:
            return False
        s = int(subs)
        # Если больше 1000 подписчиков - вероятно монетизация есть
        if s >= 1000:
            return True
    except:
        pass
    return False

def parse_date(date_str):
    """Превращает YYYYMMDD в DD.MM.YYYY."""
    if not date_str:
        return "Нет даты"
    try:
        # YouTube API (flat) отдает дату как строку YYYYMMDD
        dt = datetime.strptime(str(date_str), '%Y%m%d')
        return dt.strftime('%d.%m.%Y')
    except:
        return str(date_str)

def construct_url(vid_id):
    """Создает прямую ссылку, избегая редиректов /oops."""
    return f"https://www.youtube.com/watch?v={vid_id}"

def save_excel(data, filename="AlexRYT_Export.xlsx"):
    """Сохранение результатов в Excel."""
    global openpyxl
    if not openpyxl: 
        try:
            import openpyxl
        except ImportError:
            return "Ошибка: библиотека openpyxl не установлена"
    
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Export"
        
        # Заголовки
        headers = ["Title", "URL", "Views", "Date", "Channel", "Monetized", "Duration"]
        ws.append(headers)
        
        for row in data:
            mon = "YES" if check_monetization(row.get('subs', 0), 0) else "NO"
            ws.append([
                row.get('title', ''),
                row.get('url', ''),
                row.get('views', 0),
                row.get('date', ''),
                row.get('channel', ''),
                mon,
                row.get('duration', '')
            ])
        
        # Путь для Android (папка Download)
        path = os.path.join(os.environ.get("EXTERNAL_STORAGE", "/storage/emulated/0/Download"), filename)
        
        try:
            wb.save(path)
            return f"Сохранено в: {path}"
        except:
            # Резервный путь (в папку приложения)
            wb.save(filename)
            return f"Сохранено в папку app: {filename}"
            
    except Exception as e:
        return f"Ошибка Excel: {e}"

# ==========================================
#           ЛОГИКА ПОИСКА (YT-DLP)
# ==========================================

def search_youtube(query, limit=20, filters=None, is_shorts=False, is_channel=False):
    """
    Основная функция поиска.
    Использует yt-dlp с настройками под Android-клиент для получения дат.
    """
    global yt_dlp
    if not yt_dlp: 
        try:
            import yt_dlp
        except ImportError:
            return []

    # Настройки экстрактора
    ydl_opts = {
        'quiet': True, 
        'extract_flat': True, # Быстрый поиск без скачивания
        'ignoreerrors': True,
        'search_limit': limit, 
        'no_warnings': True,
        # ВАЖНО: Прикидываемся Android-клиентом API. 
        # Это решает проблему отсутствия дат и JS-ошибок.
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web']
            }
        }
    }
    
    # Прокси
    proxy_url = get_proxy()
    if proxy_url:
        ydl_opts['proxy'] = proxy_url

    # Формирование запроса
    full_query = query
    if is_shorts:
        full_query = f"shorts {query}"
        
    search_type = "ytsearch"
    
    # Если ищем каналы, отключаем flat, чтобы получить больше метаданных (сабы)
    if is_channel: 
        ydl_opts['extract_flat'] = False 
    
    # Команда поиска с лимитом
    cmd = f"{search_type}{limit}:{full_query}"
    results = []

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Запуск поиска
            info = ydl.extract_info(cmd, download=False)
            
            if 'entries' not in info:
                return []
            
            for entry in info['entries']:
                if not entry:
                    continue
                
                # --- ЛОГИКА ДЛЯ SHORTS ---
                if is_shorts:
                    dur = entry.get('duration', 0) or 0
                    # Фильтр: Шортс должен быть коротким
                    if dur > 65: 
                        continue 
                    
                    # Фильтр по дате (24ч / 72ч)
                    if filters and filters.get('date_limit'):
                        ud = entry.get('upload_date')
                        if ud:
                            try:
                                dt = datetime.strptime(str(ud), '%Y%m%d')
                                hours_diff = (datetime.now() - dt).total_seconds() / 3600
                                if hours_diff > filters['date_limit']:
                                    continue
                            except:
                                pass

                # --- ЛОГИКА ДЛЯ КАНАЛОВ ---
                if is_channel:
                    subs = entry.get('channel_follower_count') or 0
                    v_count = entry.get('playlist_count') or 0
                    view_count = entry.get('view_count') or 0
                    
                    # Применяем фильтры каналов
                    if filters:
                        # Подписчики
                        if filters.get('min_subs') and subs < filters['min_subs']: continue
                        if filters.get('max_subs') and subs > filters['max_subs']: continue
                        
                        # Количество видео
                        if filters.get('min_videos') and v_count < filters['min_videos']: continue
                        if filters.get('max_videos') and v_count > filters['max_videos']: continue
                        
                        # Просмотры (если доступны)
                        if view_count > 0:
                            if filters.get('min_views') and view_count < filters['min_views']: continue
                            if filters.get('max_views') and view_count > filters['max_views']: continue
                        
                        # Год создания (фильтр по строке даты)
                        c_date = entry.get('upload_date')
                        if filters.get('year') and c_date:
                            if str(filters['year']) not in str(c_date):
                                continue

                    thumb = entry.get('thumbnail') or "https://cdn-icons-png.flaticon.com/512/847/847969.png"
                    
                    results.append({
                        'type': 'channel',
                        'name': entry.get('channel') or entry.get('uploader'),
                        'url': entry.get('channel_url') or entry.get('uploader_url'),
                        'subs': subs,
                        'videos_count': v_count,
                        'view_count': view_count,
                        'thumb': thumb,
                        'is_monetized': check_monetization(subs, 0),
                        'id': entry.get('id')
                    })
                    continue

                # --- ЛОГИКА ДЛЯ ОБЫЧНЫХ ВИДЕО ---
                vid_id = entry.get('id')
                if not vid_id:
                    continue
                
                thumb = entry.get('thumbnail')
                if not thumb:
                    thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                
                # Получаем дату (теперь она должна быть благодаря extractor_args)
                raw_date = entry.get('upload_date')
                display_date = parse_date(raw_date)

                results.append({
                    'type': 'video',
                    'title': entry.get('title'),
                    'url': construct_url(vid_id),
                    'views': entry.get('view_count', 0),
                    'date': display_date,
                    'duration': entry.get('duration_string', 'N/A'),
                    'channel': entry.get('uploader'),
                    'thumb': thumb,
                    'id': vid_id,
                    'is_shorts': is_shorts,
                    'subs': 0 # В быстром поиске сабов нет, нужны через Deep Analysis
                })
                
    except Exception as e:
        print(f"Search Error: {e}")
    
    return results

def run_deep_analysis(url):
    """
    Глубокий анализ конкретного видео.
    Получает теги, точные просмотры, сабы канала.
    """
    global yt_dlp
    if not yt_dlp: return None
    
    ydl_opts = {
        'quiet': True, 
        'ignoreerrors': True, 
        'proxy': get_proxy()
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info: return None
            
            views = info.get('view_count', 0)
            subs = info.get('channel_follower_count', 0)
            dur = info.get('duration', 0)
            is_shorts = dur < 65
            
            is_mon = check_monetization(subs, views)
            
            # --- РАСЧЕТ ДОХОДА ---
            money = 0.0
            if is_mon:
                if is_shorts:
                    # Shorts: 0.01$ за 1000 просмотров
                    money = round((views / 1000) * 0.01, 2)
                else:
                    # Long: 750$ за 1,000,000 (0.75 за 1000)
                    money = round((views / 1000000) * 750, 2)
            
            return {
                'seo': min(len(info.get('tags', []))*2 + 40, 100),
                'money': money,
                'tags': info.get('tags', []),
                'subs': subs,
                'real_date': parse_date(info.get('upload_date'))
            }
    except:
        return None

# ==========================================
#           ИНТЕРФЕЙС (GUI)
# ==========================================

def build_app_ui(page: ft.Page):
    page.clean()
    page.bgcolor = "#111111"
    page.theme_mode = "dark"
    page.padding = 0
    
    # --- Элементы загрузки ---
    loading_bar = ft.ProgressBar(visible=False, color="green", bgcolor="#333")
    loading_text = ft.Text("Ожидание...", visible=False, color="green", size=12)
    
    snack = ft.SnackBar(ft.Text(""))
    page.overlay.append(snack)
    
    def msg(txt):
        snack.content = ft.Text(txt)
        snack.open = True
        page.update()

    def set_loading(active, text="Ищу..."):
        loading_bar.visible = active
        loading_text.value = f"{text}"
        loading_text.visible = active
        page.update()

    # --- ВКЛАДКА 1: ПОИСК ---
    
    search_res = ft.Column(scroll="auto", expand=True)
    inp_search = ft.TextField(
        hint_text="Введите запрос...", 
        expand=True, 
        height=40, 
        content_padding=10,
        border_color="green"
    )
    
    # Лимит с числовым отображением
    limit_val_text = ft.Text("50", color="white")
    
    def slider_change(e):
        limit_val_text.value = str(int(e.control.value))
        page.update()
        
    sl_limit = ft.Slider(
        min=1, 
        max=3000000, 
        value=50, 
        label="{value}", 
        on_change=slider_change, 
        expand=True,
        active_color="green"
    )
    
    def on_search_click(e):
        if not inp_search.value: return
        set_loading(True, "Сканирование YouTube...")
        search_res.controls.clear()
        
        def _task():
            limit = int(sl_limit.value)
            res = search_youtube(inp_search.value, limit)
            state['last_search'] = res
            
            if not res:
                msg("Ничего не найдено")
            else:
                for r in res:
                    search_res.controls.append(create_video_card(r, search_res))
                msg(f"Найдено результатов: {len(res)}")
                
            set_loading(False)
            page.update()
            
        threading.Thread(target=_task).start()

    view_search = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([
                inp_search, 
                ft.IconButton(icon="search", on_click=on_search_click, bgcolor="green", icon_color="black")
            ]),
            ft.Row([
                ft.Text("Лимит:", color="grey"), 
                limit_val_text, 
                sl_limit
            ], alignment="center"),
            ft.Row([
                ft.IconButton(icon="save_alt", tooltip="Сохранить в Excel", 
                              on_click=lambda e: msg(f"Excel: {save_excel(state['last_search'])}"))
            ]),
            ft.Row([
                ft.ProgressRing(width=16, height=16, stroke_width=2, color="green", visible=False), 
                loading_text
            ]),
            search_res
        ], expand=True)
    )

    # --- ВКЛАДКА 2: ХАЙП ---
    
    hype_res = ft.Column(scroll="auto", expand=True)
    inp_hype = ft.TextField(hint_text="Ниша (например: Minecraft)...", expand=True)
    
    def on_hype_click(e):
        set_loading(True, "Анализ трендов...")
        hype_res.controls.clear()
        
        def _task():
            q = inp_hype.value if inp_hype.value else "trending viral"
            # Ищем 100 видео, сортируем и берем лучшие
            res = search_youtube(q, 100)
            
            # Фильтр: >10000 просмотров, сортировка по убыванию
            top_vids = sorted([v for v in res if v['views'] > 10000], key=lambda x: x['views'], reverse=True)
            
            seen_ids = set()
            for v in top_vids[:50]:
                if v['id'] not in seen_ids:
                    hype_res.controls.append(create_video_card(v, hype_res))
                    seen_ids.add(v['id'])
            
            set_loading(False)
            page.update()
            
        threading.Thread(target=_task).start()

    view_hype = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("🔥 Поиск вирусных видео", size=20, weight="bold"),
            ft.Row([
                inp_hype, 
                ft.IconButton(icon="local_fire_department", on_click=on_hype_click, icon_color="orange")
            ]),
            hype_res
        ], expand=True)
    )

    # --- ВКЛАДКА 3: SHORTS ---
    
    shorts_res = ft.Column(scroll="auto", expand=True)
    inp_shorts = ft.TextField(hint_text="Тема шортс...", expand=True)
    chk_24 = ft.Checkbox(label="24 часа", value=False)
    chk_72 = ft.Checkbox(label="72 часа", value=False)
    
    def on_shorts_click(e):
        set_loading(True, "Поиск Shorts...")
        shorts_res.controls.clear()
        
        filters = {}
        if chk_24.value:
            filters['date_limit'] = 24
        elif chk_72.value:
            filters['date_limit'] = 72
        
        def _task():
            q = inp_shorts.value if inp_shorts.value else "shorts"
            res = search_youtube(q, 50, filters=filters, is_shorts=True)
            
            for r in res:
                shorts_res.controls.append(create_video_card(r, shorts_res))
                
            set_loading(False)
            page.update()
            
        threading.Thread(target=_task).start()

    view_shorts = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([
                inp_shorts, 
                ft.IconButton(icon="search", on_click=on_shorts_click)
            ]),
            ft.Row([chk_24, chk_72]),
            shorts_res
        ], expand=True)
    )

    # --- ВКЛАДКА 4: КАНАЛЫ (ФИЛЬТРЫ) ---
    
    chan_res = ft.Column(scroll="auto", expand=True)
    inp_chan_q = ft.TextField(hint_text="Тема канала...", expand=True)
    
    # Расширенные фильтры (6 полей + год)
    c_sub_min = ft.TextField(label="Мин.Сабов", width=80, text_size=11)
    c_sub_max = ft.TextField(label="Макс.Сабов", width=80, text_size=11)
    
    c_vid_min = ft.TextField(label="Мин.Видео", width=80, text_size=11)
    c_vid_max = ft.TextField(label="Макс.Видео", width=80, text_size=11)
    
    c_view_min = ft.TextField(label="Мин.Просм", width=80, text_size=11)
    c_view_max = ft.TextField(label="Макс.Просм", width=80, text_size=11)
    
    c_date = ft.TextField(label="Год (2024)", width=80, text_size=11)

    def on_chan_click(e):
        set_loading(True, "Анализ авторов...")
        chan_res.controls.clear()
        
        f_params = {}
        # Сбор данных с полей
        if c_sub_min.value.isdigit(): f_params['min_subs'] = int(c_sub_min.value)
        if c_sub_max.value.isdigit(): f_params['max_subs'] = int(c_sub_max.value)
        if c_vid_min.value.isdigit(): f_params['min_videos'] = int(c_vid_min.value)
        if c_vid_max.value.isdigit(): f_params['max_videos'] = int(c_vid_max.value)
        if c_view_min.value.isdigit(): f_params['min_views'] = int(c_view_min.value)
        if c_view_max.value.isdigit(): f_params['max_views'] = int(c_view_max.value)
        if c_date.value: f_params['year'] = c_date.value

        def _task():
            # Ищем 30 каналов
            res = search_youtube(inp_chan_q.value, 30, filters=f_params, is_channel=True)
            for r in res:
                chan_res.controls.append(create_channel_card(r))
                
            set_loading(False)
            msg(f"Найдено каналов: {len(res)}")
            page.update()
            
        threading.Thread(target=_task).start()

    view_chan = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([inp_chan_q, ft.IconButton(icon="search", on_click=on_chan_click)]),
            # Ряды фильтров
            ft.Row([c_sub_min, c_sub_max, c_vid_min, c_vid_max], scroll="auto"),
            ft.Row([c_view_min, c_view_max, c_date], scroll="auto"),
            chan_res
        ], expand=True)
    )

    # --- ВКЛАДКА 5: ТРЕКИНГ ---
    
    track_res = ft.Column(scroll="auto", expand=True)
    inp_track_link = ft.TextField(hint_text="Ссылка или имя канала...", expand=True)
    
    def refresh_track_ui():
        track_res.controls.clear()
        if not state['tracking']:
            track_res.controls.append(ft.Text("Список отслеживания пуст."))
        
        for ch in state['tracking']:
            track_res.controls.append(create_channel_card(ch, is_tracking=True))
        page.update()

    def add_to_track_click(e):
        url = inp_track_link.value
        if not url: return
        set_loading(True, "Поиск канала...")
        
        def _task():
            # Сначала ищем канал, чтобы получить его метаданные (аватар, сабы)
            res = search_youtube(url, 1, is_channel=True)
            if res:
                item = res[0]
                # Проверка дубликатов
                exists = any(t['url'] == item['url'] for t in state['tracking'])
                if not exists:
                    state['tracking'].append(item)
                    msg(f"Добавлен: {item['name']}")
                else:
                    msg("Этот канал уже в списке")
            else:
                msg("Канал не найден")
            
            set_loading(False)
            refresh_track_ui()
            
        threading.Thread(target=_task).start()

    def update_track_stats_click(e):
        set_loading(True, "Обновление статистики...")
        def _task():
            new_list = []
            for ch in state['tracking']:
                # Пере-сканируем канал
                res = search_youtube(ch['url'], 1, is_channel=True)
                if res:
                    new_list.append(res[0])
                else:
                    new_list.append(ch) # Оставляем старые данные, если ошибка
            state['tracking'] = new_list
            
            set_loading(False)
            refresh_track_ui()
            msg("Данные обновлены!")
            
        threading.Thread(target=_task).start()

    view_track = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("📡 Трекинг каналов", size=20, weight="bold"),
            ft.Row([
                inp_track_link, 
                ft.IconButton(icon="add", on_click=add_to_track_click)
            ]),
            ft.ElevatedButton("Обновить все данные", on_click=update_track_stats_click),
            track_res
        ], expand=True)
    )

    # --- ВКЛАДКА 6: МОЗГ (ГЕНЕРАТОР ИДЕЙ) ---
    
    brain_res = ft.Column(scroll="auto", expand=True)
    inp_brain = ft.TextField(hint_text="Введите тему (например: Minecraft)...", expand=True)
    
    def on_brain_click(e):
        brain_res.controls.clear()
        global requests
        if not requests: 
            import requests
            
        q = inp_brain.value
        if not q: return
        
        try:
            # Google Suggest API
            url = f"http://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={q}"
            response = requests.get(url)
            suggs = json.loads(response.content)[1]
            
            for s in suggs:
                # Функция для клика по идее -> переход в поиск
                def go_search(e, txt=s):
                    inp_search.value = txt
                    # Переключаем на 1 вкладку
                    nav_rail.selected_index = 0
                    page_content.content = view_search
                    # Запускаем поиск
                    on_search_click(None)
                    page.update()

                brain_res.controls.append(
                    ft.ListTile(
                        leading=ft.Icon("lightbulb", color="yellow"),
                        title=ft.Text(s),
                        on_click=go_search
                    )
                )
        except Exception as ex:
            msg(f"Ошибка API: {ex}")
        page.update()

    view_brain = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([
                inp_brain, 
                ft.IconButton(icon="psychology", on_click=on_brain_click)
            ]),
            ft.Text("Нажми на идею для поиска:", color="grey"),
            brain_res
        ], expand=True)
    )

    # --- ВКЛАДКА 7: ИЗБРАННОЕ ---
    
    fav_res = ft.Column(scroll="auto", expand=True)
    
    def refresh_favs():
        fav_res.controls.clear()
        
        # Видео
        if state['favorites']['videos']:
            fav_res.controls.append(ft.Text("ВИДЕО:", weight="bold", size=16))
            for v in state['favorites']['videos']:
                fav_res.controls.append(create_video_card(v, fav_res, True))
        
        # Shorts
        if state['favorites']['shorts']:
            fav_res.controls.append(ft.Divider())
            fav_res.controls.append(ft.Text("SHORTS:", weight="bold", size=16))
            for v in state['favorites']['shorts']:
                fav_res.controls.append(create_video_card(v, fav_res, True))
                
        # Каналы
        if state['favorites']['channels']:
            fav_res.controls.append(ft.Divider())
            fav_res.controls.append(ft.Text("КАНАЛЫ:", weight="bold", size=16))
            for c in state['favorites']['channels']:
                fav_res.controls.append(create_channel_card(c))
                
        page.update()

    view_fav = ft.Container(
        padding=10,
        content=ft.Column([
            ft.ElevatedButton("Обновить список", on_click=lambda e: refresh_favs()),
            fav_res
        ], expand=True)
    )

    # --- ВКЛАДКА 8: НАСТРОЙКИ ---
    
    inp_proxies = ft.TextField(label="Прокси (ip:port)", multiline=True, height=100)
    
    # Терминал
    term_out = ft.Column(scroll="auto", height=150)
    term_in = ft.TextField(label="Команда (введите help)", bgcolor="black", color="green", border_color="green")

    def term_log(txt):
        term_out.controls.append(ft.Text(f"> {txt}", font_family="Consolas", color="green"))
        page.update()

    def run_term(e):
        cmd = term_in.value.strip()
        term_log(cmd)
        term_in.value = ""
        
        if cmd == "help":
            term_log("Доступные команды:\n- clear: очистить историю\n- state: показать текущее состояние\n- reset_track: сброс трекинга")
        elif cmd == "clear":
            state['history'].clear()
            term_log("История очищена")
        elif cmd == "reset_track":
            state['tracking'] = []
            term_log("Список трекинга сброшен")
        elif cmd == "state":
            term_log(str(list(state.keys())))
        else:
            try:
                # Опасная команда, но для дебага полезна
                exec(cmd)
                term_log("Выполнено успешно.")
            except Exception as ex:
                term_log(f"Ошибка исполнения: {ex}")
        page.update()

    def save_proxies(e):
        lines = inp_proxies.value.split('\n')
        state['proxies'] = [l.strip() for l in lines if l.strip()]
        msg(f"Сохранено {len(state['proxies'])} прокси")

    def test_proxy(e):
        if not state['proxies']: 
            return msg("Список прокси пуст")
        msg("Тест первого прокси...")
        p = state['proxies'][0]
        try:
            import requests
            r = requests.get("https://www.google.com", proxies={"http": f"http://{p}", "https": f"http://{p}"}, timeout=5)
            if r.status_code == 200:
                msg(f"✅ {p} Живой!")
            else:
                msg(f"❌ {p} Ошибка {r.status_code}")
        except:
            msg(f"❌ {p} Мертв")

    view_set = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("Настройки Прокси", weight="bold"),
            inp_proxies,
            ft.Row([
                ft.ElevatedButton("Сохранить", on_click=save_proxies),
                ft.ElevatedButton("Тест", on_click=test_proxy),
                ft.TextButton("Сброс", on_click=lambda e: setattr(inp_proxies, 'value', "") or page.update())
            ]),
            ft.Divider(),
            ft.Text("Терминал Разработчика", weight="bold"),
            ft.Container(content=term_out, bgcolor="#000000", padding=5, border_radius=5, border=ft.border.all(1, "green")),
            ft.Row([
                term_in, 
                ft.IconButton(icon="play_arrow", on_click=run_term, icon_color="green")
            ])
        ], scroll="auto")
    )

    # ==========================================
    #           ГЕНЕРАЦИЯ КАРТОЧЕК
    # ==========================================

    def create_video_card(vid, parent, is_fav_screen=False):
        # Проверяем, есть ли в избранном
        is_fav = False
        target_list = state['favorites']['shorts'] if vid['is_shorts'] else state['favorites']['videos']
        for x in target_list:
            if x['id'] == vid['id']:
                is_fav = True
                break
        
        fav_icon = ft.Icon(name="star" if is_fav else "star_border", color="yellow" if is_fav else "white")
        
        def toggle_fav(e):
            lst = state['favorites']['shorts'] if vid['is_shorts'] else state['favorites']['videos']
            
            # Ищем индекс
            found_idx = -1
            for i, x in enumerate(lst):
                if x['id'] == vid['id']:
                    found_idx = i
                    break
            
            if found_idx != -1:
                lst.pop(found_idx)
                fav_icon.name = "star_border"
                fav_icon.color = "white"
                msg("Удалено из избранного")
                if is_fav_screen: refresh_favs()
            else:
                lst.append(vid)
                fav_icon.name = "star"
                fav_icon.color = "yellow"
                msg("Сохранено в избранное")
            
            page.update()

        deep_view = ft.Column(visible=False)
        
        def run_analysis_click(e):
            set_loading(True, "Глубокий анализ...")
            
            def _task():
                d = run_deep_analysis(vid['url'])
                if d:
                    tags_str = ", ".join(d['tags']) if d['tags'] else "Нет тегов"
                    
                    deep_view.controls = [
                        ft.Container(
                            bgcolor="#333", 
                            padding=10, 
                            border_radius=5, 
                            content=ft.Column([
                                ft.Text(f"SEO Score: {d['seo']}/100", color="green", weight="bold"),
                                ft.Text(f"Оценка дохода: ${d['money']}", color="yellow", weight="bold"),
                                ft.Text(f"Подписчиков: {format_number(d['subs'])}", size=12),
                                ft.Text(f"Теги: {tags_str}", size=11, color="grey")
                            ])
                        )
                    ]
                    deep_view.visible = True
                set_loading(False)
                page.update()
                
            threading.Thread(target=_task).start()

        def show_preview_click(e):
            dlg = ft.AlertDialog(
                title=ft.Text(vid['title'], size=14),
                content=ft.Column([
                    ft.Image(src=vid['thumb']),
                    ft.Text(f"Канал: {vid['channel']}"),
                    ft.ElevatedButton("Открыть в YouTube", on_click=lambda x: page.launch_url(vid['url']))
                ], height=300, scroll="auto")
            )
            page.dialog = dlg
            dlg.open = True
            page.update()

        return ft.Container(
            bgcolor="#1E1E1E",
            padding=10,
            border_radius=10,
            content=ft.Column([
                ft.Stack([
                    ft.Image(src=vid['thumb'], height=180, fit=ft.ImageFit.COVER, border_radius=5),
                    ft.Container(
                        content=ft.Text(vid['duration'], size=10, color="white", weight="bold"),
                        bgcolor="black", padding=4, border_radius=4, bottom=5, right=5
                    )
                ]),
                ft.Text(vid['title'], weight="bold", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([
                    ft.Text(f"👁 {format_number(vid['views'])}", size=11, color="grey"),
                    ft.Text(f"📅 {vid['date']}", size=11, color="grey")
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([
                    ft.IconButton(icon="analytics", on_click=run_analysis_click, icon_size=20, tooltip="Анализ и Теги"),
                    ft.IconButton(icon="visibility", on_click=show_preview_click, icon_size=20, tooltip="Превью"),
                    ft.IconButton(content=fav_icon, on_click=toggle_fav, icon_size=20)
                ]),
                deep_view
            ])
        )

    def create_channel_card(chan, is_tracking=False):
        mon_col = "green" if chan['is_monetized'] else "red"
        mon_txt = "ЕСТЬ МОНЕТА" if chan['is_monetized'] else "НЕТ МОНЕТЫ"
        
        def open_chan_click(e): 
            page.launch_url(chan['url'])
        
        # Кнопка трекинга
        track_btn = ft.IconButton(
            icon="track_changes", 
            tooltip="Отслеживать", 
            on_click=lambda e: (state['tracking'].append(chan), msg(f"Следим: {chan['name']}")) 
            if chan not in state['tracking'] else msg("Уже следим")
        )

        action_btn = None
        if is_tracking:
            def remove_track(e):
                if chan in state['tracking']: 
                    state['tracking'].remove(chan)
                refresh_track_ui()
            action_btn = ft.IconButton(icon="delete", icon_color="red", on_click=remove_track)
        else:
            is_fav = chan in state['favorites']['channels']
            f_icon = ft.Icon(name="star" if is_fav else "star_border", color="yellow" if is_fav else "white")
            
            def toggle_f(e):
                if chan in state['favorites']['channels']:
                    state['favorites']['channels'].remove(chan)
                    f_icon.name = "star_border"
                else:
                    state['favorites']['channels'].append(chan)
                    f_icon.name = "star"
                page.update()
            action_btn = ft.IconButton(content=f_icon, on_click=toggle_f)

        return ft.Container(
            bgcolor="#252525", 
            padding=10, 
            border_radius=10,
            on_click=open_chan_click, 
            content=ft.Row([
                ft.CircleAvatar(foreground_image_src=chan['thumb'], radius=25),
                ft.Column([
                    ft.Text(chan['name'], weight="bold", size=16),
                    ft.Text(f"{format_number(chan['subs'])} subs | {chan.get('videos_count',0)} vids", size=11, color="grey"),
                    ft.Text(mon_txt, color=mon_col, size=10, weight="bold")
                ], expand=True),
                track_btn, 
                action_btn
            ], alignment=ft.MainAxisAlignment.START)
        )

    # ==========================================
    #           ГЛАВНАЯ НАВИГАЦИЯ (SIDEBAR)
    # ==========================================
    
    page_content = ft.Container(content=view_search, expand=True, padding=10)

    def nav_change(e):
        idx = e.control.selected_index
        if idx == 0: 
            page_content.content = view_search
        elif idx == 1: 
            page_content.content = view_hype
        elif idx == 2: 
            page_content.content = view_shorts
        elif idx == 3: 
            page_content.content = view_chan
        elif idx == 4: 
            page_content.content = view_track
            refresh_track_ui()
        elif idx == 5: 
            page_content.content = view_brain
        elif idx == 6: 
            page_content.content = view_fav
            refresh_favs()
        elif idx == 7: 
            page_content.content = view_set
        page.update()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type="none",
        min_width=50,
        min_extended_width=150,
        group_alignment=-0.9,
        bgcolor="#0F0F0F",
        destinations=[
            ft.NavigationRailDestination(icon="search", label="Поиск"),
            ft.NavigationRailDestination(icon="local_fire_department", label="Хайп"),
            ft.NavigationRailDestination(icon="smartphone", label="Shorts"),
            ft.NavigationRailDestination(icon="person", label="Каналы"),
            ft.NavigationRailDestination(icon="track_changes", label="Трек"),
            ft.NavigationRailDestination(icon="psychology", label="Мозг"),
            ft.NavigationRailDestination(icon="star", label="Избр"),
            ft.NavigationRailDestination(icon="settings", label="Настр"),
        ],
        on_change=nav_change
    )

    # Компоновка экрана
    page.add(
        loading_bar,
        ft.Row(
            controls=[
                nav_rail,
                ft.VerticalDivider(width=1, color="grey"),
                page_content
            ],
            expand=True
        )
    )

# ==========================================
#           СИСТЕМА ЗАПУСКА (МАТРИЦА)
# ==========================================

def matrix_intro(page):
    """Эффект матрицы при запуске."""
    txt = ft.Text("", color="green", font_family="Consolas", size=12)
    page.add(
        ft.Container(
            content=txt, 
            alignment=ft.alignment.center, 
            expand=True,
            bgcolor="black"
        )
    )
    
    chars = "01"
    # Цикл анимации (примерно 2.5 секунды)
    for _ in range(25):
        lines = []
        for _ in range(20):
            lines.append("".join(random.choice(chars) for _ in range(40)))
        txt.value = "\n".join(lines)
        page.update()
        time.sleep(0.08)
    
    txt.size = 30
    txt.weight = "bold"
    txt.value = "Welcome AlexRider"
    page.update()
    time.sleep(2)
    page.clean()

def main(page: ft.Page):
    page.title = APP_NAME
    page.theme_mode = "dark"
    page.bgcolor = "black"
    page.padding = 0
    
    # Проверка первого запуска (файл-флаг)
    init_file = os.path.join(os.environ.get("EXTERNAL_STORAGE", "."), ".alexryt_init_v16")
    is_first_run = not os.path.exists(init_file)

    # 1. Показываем матрицу (всегда)
    matrix_intro(page)

    # 2. Если уже установлено - быстрый старт
    if not is_first_run:
        build_app_ui(page)
        # В фоне догружаем тяжелые библиотеки
        def bg_load():
            global yt_dlp, requests, openpyxl
            try:
                import yt_dlp
                import requests
                import openpyxl
            except: pass
        threading.Thread(target=bg_load).start()
        return

    # 3. Если первый раз - Показываем экран установки (Загрузчик)
    logo = ft.Text("AlexRYT v16", size=40, weight="bold", color="green")
    status = ft.Text("System Init...", color="grey")
    prog = ft.ProgressBar(width=200, color="green", bgcolor="#333")
    console = ft.Column(scroll="auto", height=200)
    
    page.add(
        ft.Column(
            controls=[
                ft.Container(height=100), 
                logo, 
                ft.Container(height=20), 
                prog, 
                status, 
                ft.Container(
                    content=console, 
                    bgcolor="#111", 
                    padding=10, 
                    width=300, 
                    height=200,
                    border_radius=5
                )
            ],
            horizontal_alignment="center"
        )
    )

    def log(m): 
        console.controls.append(ft.Text(f"> {m}", size=10, color="green"))
        page.update()

    def init_sequence():
        global yt_dlp, requests, openpyxl
        try:
            time.sleep(1)
            log("Loading requests module..."); import requests; log("OK")
            log("Loading openpyxl module..."); import openpyxl; log("OK")
            log("Loading yt-dlp core..."); import yt_dlp; log("OK")
            
            # Создаем файл, чтобы в след. раз не ждать
            try: 
                open(init_file, 'w').write("ok")
            except: 
                pass
            
            log("Starting User Interface...")
            time.sleep(0.5)
            
            # Переход к главному экрану
            build_app_ui(page)
            page.update()
            
        except Exception as e:
            log(f"CRITICAL ERROR: {e}")
            status.value = "Installation Failed"
            status.color = "red"
            page.update()

    threading.Thread(target=init_sequence).start()

if __name__ == "__main__":
    ft.app(target=main)
