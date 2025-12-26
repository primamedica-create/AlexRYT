import flet as ft
import os
import sys
import time
import threading
import traceback
import random
from datetime import datetime

# ==========================================
# 1. ЛЕГКИЙ СТАРТ (БЕЗ ТЯЖЕЛЫХ ИМПОРТОВ)
# ==========================================

# Глобальные переменные-плейсхолдеры
yt_dlp = None
requests = None
openpyxl = None

APP_NAME = "AlexRYT Safe"
state = {
    "favorites": {"videos": [], "channels": [], "shorts": []},
    "tracking": [], 
    "history": [],
    "proxies": [],
    "last_search": [],
    "is_initialized": False
}

# Логгер для экрана
log_lines = []
log_view = None

def log_msg(page, txt, color="green"):
    print(txt) # В консоль (если на ПК)
    if log_view:
        log_view.controls.append(ft.Text(f"> {txt}", color=color, font_family="Consolas", size=12))
        page.update()

# ==========================================
# 2. ОСНОВНОЕ ПРИЛОЖЕНИЕ (v17 Logic)
#    Загружается только ПОСЛЕ проверки
# ==========================================

class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

def get_proxy():
    if not state["proxies"]: return None
    p = random.choice(state["proxies"])
    return f"http://{p}" if not p.startswith("http") else p

def format_number(num):
    if not num: return "0"
    try:
        n = float(num)
        if n >= 1000000: return f"{n/1000000:.1f}M"
        if n >= 1000: return f"{n/1000:.1f}K"
        return str(int(n))
    except: return str(num)

def check_monetization(subs, views):
    try:
        if not subs: return False
        return int(subs) >= 1000
    except: return False

def parse_date(date_str):
    if not date_str: return "..."
    try:
        return datetime.strptime(str(date_str), '%Y%m%d').strftime('%d.%m.%Y')
    except: return str(date_str)

def construct_url(vid_id):
    return f"https://www.youtube.com/watch?v={vid_id}"

# --- ФУНКЦИИ ПОИСКА ---

def search_youtube(query, limit=20, filters=None, is_shorts=False, is_channel=False):
    # ПРОВЕРКА НАЛИЧИЯ БИБЛИОТЕКИ ПЕРЕД ИСПОЛЬЗОВАНИЕМ
    if not yt_dlp: return []

    ydl_opts = {
        'quiet': True, 
        'extract_flat': True, 
        'ignoreerrors': True,
        'search_limit': limit, 
        'no_warnings': True,
        'socket_timeout': 30,
        'logger': QuietLogger(),
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
    }
    
    if get_proxy(): ydl_opts['proxy'] = get_proxy()
    full_query = f"shorts {query}" if is_shorts else query
    search_type = "ytsearch"
    if is_channel: ydl_opts['extract_flat'] = False 
    
    cmd = f"{search_type}{limit}:{full_query}"
    results = []

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(cmd, download=False)
            if 'entries' not in info: return []
            
            for entry in info['entries']:
                if not entry: continue
                
                if is_shorts:
                    dur = entry.get('duration', 0) or 0
                    if dur > 65: continue
                    if filters and filters.get('date_limit'):
                        ud = entry.get('upload_date')
                        if ud:
                            try:
                                dt = datetime.strptime(str(ud), '%Y%m%d')
                                if (datetime.now() - dt).total_seconds() / 3600 > filters['date_limit']: continue
                            except: pass

                if is_channel:
                    subs = entry.get('channel_follower_count') or 0
                    v_count = entry.get('playlist_count') or 0
                    view_count = entry.get('view_count') or 0
                    if filters:
                        if filters.get('min_subs') and subs < filters['min_subs']: continue
                        if filters.get('max_subs') and subs > filters['max_subs']: continue
                        if filters.get('min_videos') and v_count < filters['min_videos']: continue
                        if filters.get('max_videos') and v_count > filters['max_videos']: continue
                        c_date = entry.get('upload_date')
                        if filters.get('year') and c_date and str(filters['year']) not in str(c_date): continue

                    thumb = entry.get('thumbnail') or "https://cdn-icons-png.flaticon.com/512/847/847969.png"
                    results.append({
                        'type': 'channel', 'name': entry.get('channel') or entry.get('uploader'),
                        'url': entry.get('channel_url') or entry.get('uploader_url'),
                        'subs': subs, 'videos_count': v_count, 'view_count': view_count, 'thumb': thumb,
                        'is_monetized': check_monetization(subs, 0), 'id': entry.get('id')
                    })
                    continue

                vid_id = entry.get('id')
                if not vid_id: continue
                
                thumb = entry.get('thumbnail')
                if not thumb: thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                
                results.append({
                    'type': 'video', 'title': entry.get('title'),
                    'url': construct_url(vid_id), 'views': entry.get('view_count', 0),
                    'date': parse_date(entry.get('upload_date')),
                    'duration': entry.get('duration_string', 'N/A'),
                    'channel': entry.get('uploader'), 'thumb': thumb,
                    'id': vid_id, 'is_shorts': is_shorts
                })
    except Exception as e: pass
    return results

def run_deep_analysis(url):
    if not yt_dlp: return None
    ydl_opts = {'quiet': True, 'ignoreerrors': True, 'proxy': get_proxy(), 'socket_timeout': 30, 'logger': QuietLogger()}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info: return None
            
            views = info.get('view_count', 0)
            subs = info.get('channel_follower_count', 0)
            dur = info.get('duration', 0)
            is_shorts = dur < 65
            is_mon = check_monetization(subs, views)
            money = 0.0
            if is_mon:
                money = round((views / 1000) * 0.01, 2) if is_shorts else round((views / 1000000) * 750, 2)
            
            return {
                'seo': min(len(info.get('tags', []))*2 + 40, 100),
                'money': money, 'tags': info.get('tags', []),
                'subs': subs, 'real_date': parse_date(info.get('upload_date'))
            }
    except: return None

# --- ИНТЕРФЕЙС (UI) ---

def build_main_ui(page: ft.Page):
    # Это функция запускает основной интерфейс
    page.clean()
    page.bgcolor = "#111111"
    
    # 1. ПОИСК
    search_res = ft.Column(scroll="auto", expand=True)
    inp_search = ft.TextField(hint_text="Поиск...", expand=True)
    limit_txt = ft.Text("50")
    sl_limit = ft.Slider(min=1, max=3000000, value=50, label="{value}", on_change=lambda e: setattr(limit_txt, 'value', str(int(e.control.value))) or page.update(), expand=True)
    
    def on_search(e):
        if not inp_search.value: return
        search_res.controls.clear()
        search_res.controls.append(ft.ProgressBar()); page.update()
        def _t():
            res = search_youtube(inp_search.value, int(sl_limit.value))
            search_res.controls.clear()
            for r in res: search_res.controls.append(create_card(r))
            page.update()
        threading.Thread(target=_t).start()

    view_search = ft.Container(padding=10, content=ft.Column([
        ft.Row([inp_search, ft.IconButton("search", on_click=on_search)]),
        ft.Row([ft.Text("Лимит:"), limit_val_text, sl_limit]),
        search_res
    ]))

    # ... (Остальные вкладки строим тут, но для теста пока хватит поиска) ...
    # Чтобы код влез, я использую упрощенную навигацию для теста, если заработает - вернем фулл
    
    page.add(view_search)

# Универсальная карточка (для сокращения кода)
def create_card(data):
    if data.get('type') == 'video':
        return ft.Container(bgcolor="#222", padding=10, content=ft.Column([
            ft.Text(data['title'], weight="bold"),
            ft.Image(src=data['thumb'], height=150, fit=ft.ImageFit.COVER),
            ft.Text(f"Просмотры: {format_number(data['views'])} | Дата: {data['date']}")
        ]))
    else:
        return ft.Container(bgcolor="#333", padding=10, content=ft.Row([
            ft.CircleAvatar(foreground_image_src=data['thumb']),
            ft.Column([ft.Text(data['name']), ft.Text(f"Сабы: {format_number(data['subs'])}")])
        ]))

limit_val_text = ft.Text("50")

# ==========================================
# 3. ЭКРАН ЗАГРУЗКИ (DIAGNOSTIC BOOT)
# ==========================================

def main(page: ft.Page):
    global log_view
    page.title = "AlexRYT Loader"
    page.bgcolor = "black"
    page.theme_mode = "dark"
    page.padding = 10
    
    log_view = ft.Column(scroll="auto", expand=True)
    status_text = ft.Text("Инициализация...", size=20, color="yellow")
    
    page.add(
        ft.Column([
            ft.Text("AlexRYT: Safe Boot Mode", size=30, color="green", weight="bold"),
            ft.Divider(),
            status_text,
            ft.Container(content=log_view, bgcolor="#111", border_radius=5, padding=10, expand=True)
        ], expand=True)
    )

    def load_libs():
        global yt_dlp, requests, openpyxl
        
        time.sleep(1)
        log_msg(page, "1. Начинаем импорт requests...")
        try:
            import requests
            log_msg(page, "✅ Requests загружен!", "green")
        except Exception as e:
            log_msg(page, f"❌ Ошибка requests: {e}", "red")

        time.sleep(0.5)
        log_msg(page, "2. Начинаем импорт openpyxl...")
        try:
            import openpyxl
            log_msg(page, "✅ Openpyxl загружен!", "green")
        except Exception as e:
            log_msg(page, f"❌ Ошибка openpyxl: {e}", "red")

        time.sleep(0.5)
        log_msg(page, "3. Начинаем импорт YT-DLP (самый тяжелый)...")
        try:
            import yt_dlp
            log_msg(page, "✅ YT-DLP загружен успешно!", "green")
        except Exception as e:
            # ВОТ ТУТ МЫ УВИДИМ ОШИБКУ НА ТЕЛЕФОНЕ ЕСЛИ ОНА ЕСТЬ
            log_msg(page, "CRITICAL ERROR LOADING YT-DLP:", "red")
            log_msg(page, traceback.format_exc(), "red")
            status_text.value = "Ошибка запуска!"
            status_text.color = "red"
            page.update()
            return # Останавливаемся, чтобы юзер успел прочитать

        log_msg(page, "Все библиотеки загружены. Запуск интерфейса...")
        time.sleep(1)
        
        # Если все ок - перестраиваем UI
        try:
            # Запускаем полнофункциональный интерфейс
            build_full_interface(page) 
        except Exception as e:
            log_msg(page, f"Ошибка построения UI: {e}", "red")
            log_msg(page, traceback.format_exc(), "red")

    threading.Thread(target=load_libs).start()

# ==========================================
# 4. ПОЛНЫЙ ИНТЕРФЕЙС (v17 код)
# ==========================================

def build_full_interface(page: ft.Page):
    page.clean()
    
    # --- UI ЭЛЕМЕНТЫ ---
    # Поиск
    search_col = ft.Column(scroll="auto", expand=True)
    inp_search = ft.TextField(hint_text="Поиск видео...", expand=True)
    sl_limit = ft.Slider(min=1, max=3000000, value=50, label="{value}")
    
    def do_search(e):
        search_col.controls.clear()
        search_col.controls.append(ft.ProgressBar())
        page.update()
        def _t():
            res = search_youtube(inp_search.value, int(sl_limit.value))
            search_col.controls.clear()
            if not res: search_col.controls.append(ft.Text("Пусто"))
            for r in res: search_col.controls.append(create_card_full(r, page)) # Исправил вызов
            page.update()
        threading.Thread(target=_t).start()

    tab_search = ft.Container(padding=10, content=ft.Column([
        ft.Row([inp_search, ft.IconButton(ft.icons.SEARCH, on_click=do_search)]),
        ft.Row([ft.Text("Лимит:"), sl_limit]),
        search_col
    ]))

    # Каналы
    chan_col = ft.Column(scroll="auto", expand=True)
    inp_chan = ft.TextField(hint_text="Канал...", expand=True)
    # Фильтры
    c_sub_min = ft.TextField(label="Мин.Сабов", width=70, text_size=10)
    c_sub_max = ft.TextField(label="Макс.Сабов", width=70, text_size=10)
    c_vid_min = ft.TextField(label="Мин.Видео", width=70, text_size=10)
    c_vid_max = ft.TextField(label="Макс.Видео", width=70, text_size=10)
    c_view_min = ft.TextField(label="Мин.Просм", width=70, text_size=10)
    c_view_max = ft.TextField(label="Макс.Просм", width=70, text_size=10)
    
    def do_chan(e):
        chan_col.controls.clear()
        chan_col.controls.append(ft.ProgressBar()); page.update()
        f = {}
        if c_sub_min.value.isdigit(): f['min_subs'] = int(c_sub_min.value)
        if c_sub_max.value.isdigit(): f['max_subs'] = int(c_sub_max.value)
        if c_vid_min.value.isdigit(): f['min_videos'] = int(c_vid_min.value)
        if c_vid_max.value.isdigit(): f['max_videos'] = int(c_vid_max.value)
        if c_view_min.value.isdigit(): f['min_views'] = int(c_view_min.value)
        if c_view_max.value.isdigit(): f['max_views'] = int(c_view_max.value)
        
        def _t():
            res = search_youtube(inp_chan.value, 30, filters=f, is_channel=True)
            chan_col.controls.clear()
            for r in res: chan_col.controls.append(create_card_full(r, page))
            page.update()
        threading.Thread(target=_t).start()

    tab_chan = ft.Container(padding=10, content=ft.Column([
        ft.Row([inp_chan, ft.IconButton(ft.icons.SEARCH, on_click=do_chan)]),
        ft.Row([c_sub_min, c_sub_max, c_vid_min, c_vid_max], scroll="auto"),
        ft.Row([c_view_min, c_view_max], scroll="auto"),
        chan_col
    ]))

    # Навигация
    t = ft.Tabs(selected_index=0, tabs=[
        ft.Tab(text="Поиск", icon=ft.icons.SEARCH, content=tab_search),
        ft.Tab(text="Каналы", icon=ft.icons.PERSON, content=tab_chan),
        ft.Tab(text="Настр", icon=ft.icons.SETTINGS, content=ft.Text("Настройки тут"))
    ], expand=True)
    
    page.add(t)

def create_card_full(data, page):
    if data.get('type') == 'video':
        # Кнопка анализа
        deep_cont = ft.Column(visible=False)
        def run_deep(e):
            deep_cont.visible = True
            deep_cont.controls = [ft.ProgressBar()]
            page.update()
            def _t():
                d = run_deep_analysis(data['url'])
                if d:
                    deep_cont.controls = [ft.Text(f"SEO: {d['seo']} | $: {d['money']} | {d['real_date']}")]
                else:
                    deep_cont.controls = [ft.Text("Ошибка анализа")]
                page.update()
            threading.Thread(target=_t).start()

        return ft.Container(bgcolor="#222", padding=10, border_radius=10, content=ft.Column([
            ft.Image(src=data['thumb'], height=150, fit=ft.ImageFit.COVER),
            ft.Text(data['title'], weight="bold"),
            ft.Row([
                ft.Text(f"{format_number(data['views'])} views"),
                ft.Text(data['date'])
            ]),
            ft.Row([
                ft.IconButton(ft.icons.ANALYTICS, on_click=run_deep),
                ft.IconButton(ft.icons.VISIBILITY, on_click=lambda e: page.launch_url(data['url']))
            ]),
            deep_cont
        ]))
    else:
        return ft.Container(bgcolor="#333", padding=10, border_radius=10, content=ft.Row([
            ft.CircleAvatar(foreground_image_src=data['thumb']),
            ft.Column([
                ft.Text(data['name'], weight="bold"),
                ft.Text(f"{format_number(data['subs'])} subs | {data['videos_count']} vids")
            ], expand=True),
            ft.IconButton(ft.icons.TRACK_CHANGES, tooltip="Следить")
        ]))

if __name__ == "__main__":
    ft.app(target=main)
