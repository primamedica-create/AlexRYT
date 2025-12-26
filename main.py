import flet as ft
import json
import os
import random
import requests
import time
import threading
from datetime import datetime, timedelta

# ВАЖНО: Мы убрали тяжелые импорты отсюда, чтобы приложение стартовало мгновенно!
# import yt_dlp (Убрано)
# import openpyxl (Убрано)

# --- КОНФИГУРАЦИЯ ---
APP_NAME = "AlexRYT"
BLACKLIST_DEFAULTS = ["MrBeast", "PewDiePie", "T-Series", "Cocomelon"]

# Глобальное состояние
state = {
    "favorites": {"videos": [], "channels": [], "shorts": []},
    "tracking": [], 
    "history": [],
    "proxies": [],
    "blacklist": BLACKLIST_DEFAULTS.copy(),
    "last_search": [] 
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_proxy():
    if not state["proxies"]: return None
    p = random.choice(state["proxies"])
    return f"http://{p}" if not p.startswith("http") else p

def format_number(num):
    if not num: return "0"
    try:
        num = int(num)
        if num >= 1000000: return f"{num/1000000:.1f}M"
        if num >= 1000: return f"{num/1000:.1f}K"
        return str(num)
    except: return str(num)

def check_monetization(subs, views):
    try:
        if subs is None: return False 
        if int(subs) >= 1000: return True
    except: pass
    return False

def parse_date(date_str):
    if not date_str: return "N/A"
    try:
        return datetime.strptime(date_str, '%Y%m%d').strftime('%d.%m.%Y')
    except: return date_str

def save_excel(data, filename="AlexRYT_Export.xlsx"):
    # ЛЕНИВЫЙ ИМПОРТ (Грузим только когда надо сохранить)
    try:
        import openpyxl
    except ImportError:
        print("Ошибка: openpyxl не установлен")
        return None

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Export"
        headers = ["Title", "URL", "Views", "Date", "Channel", "Monetization (Est.)", "Duration"]
        ws.append(headers)
        
        for row in data:
            is_mon = "YES" if check_monetization(row.get('subs', 0), 0) else "?"
            ws.append([
                row.get('title', ''),
                row.get('url', ''),
                row.get('views', 0),
                row.get('date', ''),
                row.get('channel', ''),
                is_mon,
                row.get('duration', '')
            ])
        
        # На Android сохраняем во временную папку, так как корень закрыт
        path = os.path.join(os.environ.get("h", "/storage/emulated/0/Download"), filename)
        try:
            wb.save(path)
            return path
        except:
            # Резервный путь
            wb.save(filename)
            return os.path.abspath(filename)
            
    except Exception as e:
        print(f"Excel Error: {e}")
        return None

# --- ЯДРО ПОИСКА (YT-DLP) ---

def search_youtube(query, limit=20, filters=None, is_shorts=False, is_channel=False):
    # ЛЕНИВЫЙ ИМПОРТ (Грузим yt-dlp только при поиске)
    try:
        import yt_dlp
    except ImportError:
        print("CRITICAL: yt-dlp not found")
        return []

    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'ignoreerrors': True,
        'search_limit': limit,
        'no_warnings': True
    }
    
    proxy = get_proxy()
    if proxy: ydl_opts['proxy'] = proxy

    full_query = query
    search_type = "ytsearch"
    
    if is_shorts:
        full_query = f"shorts {query}" 
    
    if is_channel:
        ydl_opts['extract_flat'] = False 
        search_type = "ytsearch" 
    
    cmd = f"{search_type}{limit}:{full_query}"
    results = []
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(cmd, download=False)
            if 'entries' in info:
                for entry in info['entries']:
                    if not entry: continue
                    
                    if is_shorts:
                        dur = entry.get('duration', 0)
                        if dur and dur > 65: continue 
                        if filters and 'date_limit' in filters:
                            up_date = entry.get('upload_date')
                            if up_date:
                                dt = datetime.strptime(up_date, '%Y%m%d')
                                diff = datetime.now() - dt
                                if diff.total_seconds() / 3600 > filters['date_limit']: continue

                    if is_channel:
                        subs = entry.get('channel_follower_count')
                        if filters:
                            if filters.get('min_subs') and (not subs or subs < filters['min_subs']): continue
                        
                        thumb = entry.get('thumbnail')
                        if not thumb: 
                             thumb = "https://cdn-icons-png.flaticon.com/512/847/847969.png"

                        results.append({
                            'type': 'channel',
                            'name': entry.get('channel') or entry.get('uploader'),
                            'url': entry.get('channel_url') or entry.get('uploader_url'),
                            'subs': subs,
                            'videos_count': entry.get('playlist_count'), 
                            'thumb': thumb, 
                            'is_monetized': check_monetization(subs, 0)
                        })
                        continue 

                    vid_id = entry.get('id')
                    thumb_url = entry.get('thumbnail')
                    if not thumb_url and vid_id:
                        thumb_url = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                    if not thumb_url:
                         thumb_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ac/No_image_available.svg/300px-No_image_available.svg.png"
                    
                    date_raw = entry.get('upload_date')
                    date_fmt = parse_date(date_raw)

                    res_obj = {
                        'type': 'video',
                        'title': entry.get('title'),
                        'url': entry.get('url') or f"https://www.youtube.com/watch?v={vid_id}",
                        'views': entry.get('view_count', 0),
                        'date': date_fmt,
                        'raw_date': date_raw,
                        'duration': entry.get('duration_string'),
                        'channel': entry.get('uploader'),
                        'channel_url': entry.get('uploader_url'),
                        'thumb': thumb_url,
                        'id': vid_id,
                        'is_shorts': is_shorts
                    }
                    results.append(res_obj)

    except Exception as e:
        print(f"Search Error: {e}")
    return results

def run_deep_analysis(url):
    # ЛЕНИВЫЙ ИМПОРТ
    try:
        import yt_dlp
    except: return None

    ydl_opts = {'quiet': True, 'ignoreerrors': True, 'proxy': get_proxy()}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info: return None
        
        views = info.get('view_count', 0)
        subs = info.get('channel_follower_count', 0)
        is_mon = check_monetization(subs, views)
        
        est_money = 0
        if is_mon:
            est_money = round((views / 1000) * 1.0, 2)
        
        tags = info.get('tags', [])
        desc = info.get('description', '')
        seo_score = min(len(tags)*2 + (10 if len(desc)>200 else 0) + 30, 100)

        return {
            'seo_score': seo_score,
            'est_money': est_money,
            'tags': tags[:10],
            'engagement': round((info.get('like_count', 0)/views)*100, 2) if views else 0,
            'real_date': parse_date(info.get('upload_date')),
            'subs': subs,
            'is_monetized': is_mon
        }

# --- GUI ПРИЛОЖЕНИЯ (FLET 0.22.1) ---

def main(page: ft.Page):
    page.title = APP_NAME
    page.theme_mode = "dark"
    page.bgcolor = "#0F0F0F" 
    page.padding = 0 
    
    loading_overlay = ft.ProgressBar(visible=False, color="red", bgcolor="#222")
    snack = ft.SnackBar(ft.Text(""))
    page.overlay.append(snack)

    def show_msg(text):
        snack.content = ft.Text(text)
        snack.open = True
        page.update()

    def render_video(vid, parent_col, is_fav_tab=False):
        img_h = 180
        if vid.get('is_shorts'): img_h = 250 
        
        img = ft.Image(src=vid['thumb'], height=img_h, fit=ft.ImageFit.COVER, border_radius=8)
        
        mon_icon = ft.Icon(ft.icons.MONETIZATION_ON, color="green", size=16) if vid.get('is_monetized_est') else ft.Container()
        
        title_btn = ft.TextButton(
            text=vid['title'], 
            style=ft.ButtonStyle(color="white"),
            on_click=lambda e: page.launch_url(vid['url'])
        )
        
        subtitle = ft.Row([
            ft.Text(f"👁 {format_number(vid['views'])}", size=12, color="grey"),
            ft.Text("•", color="grey"),
            ft.Text(f"{vid['date']}", size=12, color="grey"),
            mon_icon
        ], spacing=5)
        
        analysis_container = ft.Column(visible=False)
        
        def toggle_analysis(e):
            if analysis_container.visible:
                analysis_container.visible = False
            else:
                loading_overlay.visible = True
                page.update()
                
                def _analyze():
                    data = run_deep_analysis(vid['url'])
                    if data:
                        analysis_container.controls = [
                            ft.Container(bgcolor="#222", padding=10, border_radius=5, content=ft.Column([
                                ft.Row([
                                    ft.Text(f"SEO: {data['seo_score']}/100", color="green"),
                                    ft.Text(f"Доход: ${data['est_money']}" if data['is_monetized'] else "Доход: $0", color="yellow")
                                ], alignment="spaceBetween"),
                                ft.Text(f"Подписчиков: {format_number(data['subs'])}", size=12),
                                ft.Text("Теги: " + ", ".join(data['tags']), size=10, color="grey"),
                            ]))
                        ]
                    loading_overlay.visible = False
                    analysis_container.visible = True
                    page.update()
                threading.Thread(target=_analyze).start()
            page.update()

        def toggle_fav(e):
            found = False
            target_list = state['favorites']['shorts'] if vid.get('is_shorts') else state['favorites']['videos']
            for i, v in enumerate(target_list):
                if v['id'] == vid['id']:
                    target_list.pop(i)
                    found = True
                    break
            
            if not found:
                target_list.append(vid)
                fav_icon.icon = ft.icons.STAR
                fav_icon.icon_color = "yellow"
                show_msg("Добавлено в избранное")
            else:
                fav_icon.icon = ft.icons.STAR_BORDER
                fav_icon.icon_color = "white"
                show_msg("Удалено из избранного")
                if is_fav_tab: refresh_favs_ui(None) 
            page.update()

        is_in_fav = False
        check_list = state['favorites']['shorts'] if vid.get('is_shorts') else state['favorites']['videos']
        for v in check_list:
            if v['id'] == vid['id']: is_in_fav = True
        
        fav_icon = ft.IconButton(
            icon=ft.icons.STAR if is_in_fav else ft.icons.STAR_BORDER,
            icon_color="yellow" if is_in_fav else "white",
            on_click=toggle_fav
        )

        def show_preview(e):
            dlg = ft.AlertDialog(
                title=ft.Text("Предпросмотр"),
                content=ft.Column([
                    ft.Image(src=vid['thumb']),
                    ft.Text(vid['title'], weight="bold"),
                    ft.Text(f"Канал: {vid['channel']}"),
                    ft.ElevatedButton("Открыть в YouTube", on_click=lambda x: page.launch_url(vid['url']))
                ], height=300, scroll="auto")
            )
            page.dialog = dlg
            dlg.open = True
            page.update()

        card = ft.Container(
            bgcolor="#1E1E1E",
            border_radius=10,
            padding=10,
            content=ft.Column([
                ft.Stack([
                    img,
                    ft.Container(content=ft.Text(str(vid.get('duration','')), size=10, color="white", weight="bold"), bgcolor="black", padding=3, border_radius=3, bottom=5, right=5)
                ]),
                title_btn,
                subtitle,
                ft.Row([
                    ft.IconButton(ft.icons.ANALYTICS, on_click=toggle_analysis, tooltip="Анализ"),
                    ft.IconButton(ft.icons.VISIBILITY, on_click=show_preview, tooltip="Превью"),
                    fav_icon
                ], alignment="end"),
                analysis_container
            ])
        )
        return card

    def render_channel(chan, parent_col):
        mon_status = "✅ ЕСТЬ" if chan['is_monetized'] else "❌ НЕТ"
        mon_color = "green" if chan['is_monetized'] else "red"
        
        def track_channel(e):
            if chan not in state['tracking']:
                state['tracking'].append(chan)
                show_msg(f"Отслеживаем: {chan['name']}")
            else:
                show_msg("Уже отслеживается")

        def toggle_fav_chan(e):
            if chan in state['favorites']['channels']:
                state['favorites']['channels'].remove(chan)
                fav_btn.icon = ft.icons.STAR_BORDER
                show_msg("Удален из избранного")
            else:
                state['favorites']['channels'].append(chan)
                fav_btn.icon = ft.icons.STAR
                show_msg("Сохранен в избранное")
            page.update()

        is_fav = chan in state['favorites']['channels']
        fav_btn = ft.IconButton(icon=ft.icons.STAR if is_fav else ft.icons.STAR_BORDER, on_click=toggle_fav_chan)

        card = ft.Container(
            bgcolor="#252525",
            padding=10,
            border_radius=10,
            content=ft.Row([
                ft.CircleAvatar(foreground_image_src=chan['thumb'], radius=30),
                ft.Column([
                    ft.Text(chan['name'], weight="bold", size=16),
                    ft.Text(f"Подписчики: {format_number(chan['subs'])}", size=12, color="grey"),
                    ft.Row([
                        ft.Text("Монетизация:", size=12),
                        ft.Text(mon_status, color=mon_color, weight="bold", size=12)
                    ])
                ], expand=True),
                ft.Column([
                    fav_btn,
                    ft.IconButton(ft.icons.TRACK_CHANGES, on_click=track_channel, tooltip="Отслеживать")
                ])
            ], alignment="start")
        )
        return card

    # 1. ПОИСК
    search_results_col = ft.Column(spacing=10, scroll="auto")
    inp_search = ft.TextField(hint_text="Поиск видео...", expand=True, border_color="green", height=45)
    slider_limit = ft.Slider(min=10, max=200, divisions=19, label="{value}", value=20)
    
    def do_search(e):
        if not inp_search.value: return
        loading_overlay.visible = True
        search_results_col.controls.clear()
        page.update()
        
        def _task():
            res = search_youtube(inp_search.value, int(slider_limit.value))
            state['last_search'] = res
            for r in res:
                search_results_col.controls.append(render_video(r, search_results_col))
            loading_overlay.visible = False
            show_msg(f"Найдено: {len(res)}")
            page.update()
        threading.Thread(target=_task).start()
    
    def export_excel_search(e):
        if 'last_search' in state and state['last_search']:
            path = save_excel(state['last_search'])
            if path: show_msg(f"Сохранено: {path}")
            else: show_msg("Ошибка сохранения")
        else: show_msg("Сначала выполните поиск")

    tab_search = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([inp_search, ft.IconButton(ft.icons.SEARCH, on_click=do_search, bgcolor="green")]),
            ft.Row([ft.Text("Лимит:"), slider_limit, ft.IconButton(ft.icons.SAVE_ALT, on_click=export_excel_search, tooltip="В Excel")], alignment="center"),
            search_results_col 
        ], expand=True)
    )

    # 2. ХАЙП
    hype_col = ft.Column(spacing=10, scroll="auto")
    inp_hype_niche = ft.TextField(hint_text="Ниша (или пусто)...", expand=True)
    
    def do_hype(e):
        loading_overlay.visible = True
        hype_col.controls.clear()
        page.update()
        
        def _task():
            seeds = ["viral", "trending", "hit", "popular"]
            base = inp_hype_niche.value if inp_hype_niche.value else random.choice(seeds)
            res = search_youtube(base, limit=100)
            clean = [v for v in res if v['views'] > 10000]
            clean.sort(key=lambda x: x['views'], reverse=True)
            seen = set()
            for v in clean[:50]:
                if v['id'] not in seen:
                    hype_col.controls.append(render_video(v, hype_col))
                    seen.add(v['id'])
            loading_overlay.visible = False
            page.update()
        threading.Thread(target=_task).start()

    tab_hype = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("🔥 Поиск вирусного контента"),
            ft.Row([inp_hype_niche, ft.IconButton(ft.icons.LOCAL_FIRE_DEPARTMENT, on_click=do_hype, icon_color="orange")]),
            hype_col
        ], expand=True)
    )

    # 3. SHORTS
    shorts_col = ft.Column(spacing=10, scroll="auto")
    inp_shorts = ft.TextField(hint_text="Поиск Shorts...", expand=True)
    chk_24 = ft.Checkbox(label="24 часа", value=False)
    chk_72 = ft.Checkbox(label="72 часа", value=False)

    def do_shorts(e):
        loading_overlay.visible = True
        shorts_col.controls.clear()
        page.update()
        filters = {}
        if chk_24.value: filters['date_limit'] = 24
        elif chk_72.value: filters['date_limit'] = 72
        query = inp_shorts.value if inp_shorts.value else "trending shorts"

        def _task():
            res = search_youtube(query, limit=50, filters=filters, is_shorts=True)
            for r in res:
                shorts_col.controls.append(render_video(r, shorts_col))
            loading_overlay.visible = False
            page.update()
        threading.Thread(target=_task).start()

    tab_shorts = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([inp_shorts, ft.IconButton(ft.icons.SEARCH, on_click=do_shorts)]),
            ft.Row([chk_24, chk_72]),
            shorts_col
        ], expand=True)
    )

    # 4. КАНАЛЫ
    chan_col = ft.Column(spacing=10, scroll="auto")
    inp_chan_q = ft.TextField(hint_text="Имя канала...", expand=True)
    inp_min_subs = ft.TextField(label="Мин. Subs", width=100, value="0")
    
    def do_chan_search(e):
        loading_overlay.visible = True
        chan_col.controls.clear()
        page.update()
        f = {'min_subs': int(inp_min_subs.value) if inp_min_subs.value.isdigit() else 0}
        def _task():
            res = search_youtube(inp_chan_q.value, limit=20, filters=f, is_channel=True)
            for r in res:
                chan_col.controls.append(render_channel(r, chan_col))
            loading_overlay.visible = False
            page.update()
        threading.Thread(target=_task).start()

    tab_channels = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Row([inp_chan_q, ft.IconButton(ft.icons.SEARCH, on_click=do_chan_search)]),
            ft.Row([inp_min_subs]),
            chan_col
        ], expand=True)
    )

    # 5. ТРЕКИНГ
    track_col = ft.Column(spacing=10, scroll="auto")
    inp_track_url = ft.TextField(label="Ссылка на канал/видео")
    
    def add_track_manual(e):
        if inp_track_url.value:
            state['tracking'].append({'name': 'Custom Link', 'url': inp_track_url.value, 'subs': 0, 'thumb': '', 'is_monetized': False})
            refresh_tracking(None)

    def refresh_tracking(e):
        track_col.controls.clear()
        for item in state['tracking']:
            def remove_me(e, i=item):
                state['tracking'].remove(i)
                refresh_tracking(None)
            
            card = ft.Container(
                bgcolor="#333", padding=10, border_radius=5,
                content=ft.Row([
                    ft.Text(item.get('name', 'Link'), expand=True),
                    ft.IconButton(ft.icons.DELETE, icon_color="red", on_click=remove_me)
                ])
            )
            track_col.controls.append(card)
        page.update()

    tab_tracking = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("📡 Отслеживание каналов"),
            ft.Row([inp_track_url, ft.IconButton(ft.icons.ADD, on_click=add_track_manual)]),
            ft.ElevatedButton("Обновить данные", on_click=refresh_tracking),
            track_col
        ], expand=True)
    )

    # 6. ИЗБРАННОЕ
    fav_col = ft.Column(spacing=10, scroll="auto")
    def refresh_favs_ui(e):
        fav_col.controls.clear()
        if state['favorites']['videos']:
            fav_col.controls.append(ft.Text("ВИДЕО:", weight="bold"))
            for v in state['favorites']['videos']:
                fav_col.controls.append(render_video(v, fav_col, is_fav_tab=True))
        if state['favorites']['shorts']:
            fav_col.controls.append(ft.Divider())
            fav_col.controls.append(ft.Text("SHORTS:", weight="bold"))
            for v in state['favorites']['shorts']:
                fav_col.controls.append(render_video(v, fav_col, is_fav_tab=True))
        if state['favorites']['channels']:
            fav_col.controls.append(ft.Divider())
            fav_col.controls.append(ft.Text("КАНАЛЫ:", weight="bold"))
            for c in state['favorites']['channels']:
                fav_col.controls.append(render_channel(c, fav_col))
        page.update()

    tab_fav = ft.Container(
        padding=10,
        content=ft.Column([
            ft.ElevatedButton("🔄 Обновить список", on_click=refresh_favs_ui),
            fav_col
        ], expand=True)
    )

    # 7. МОЗГ
    brain_col = ft.Column(scroll="auto")
    inp_brain = ft.TextField(hint_text="Minecraft...")
    def do_brain(e):
        brain_col.controls.clear()
        u = f"http://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={inp_brain.value}"
        try:
            suggs = json.loads(requests.get(u).content)[1]
            for s in suggs:
                brain_col.controls.append(
                    ft.ListTile(leading=ft.Icon(ft.icons.LIGHTBULB), title=ft.Text(s), on_click=lambda e, val=s: show_msg(f"Скопировано: {val}"))
                )
        except: pass
        page.update()

    tab_brain = ft.Container(
        padding=10, content=ft.Column([
            ft.Row([inp_brain, ft.IconButton(ft.icons.PSYCHOLOGY, on_click=do_brain)]),
            brain_col
        ])
    )

    # 8. НАСТРОЙКИ
    term_out = ft.Text("Console Ready...", font_family="Consolas")
    term_in = ft.TextField(label="Terminal Command", bgcolor="black", color="#00FF00")
    
    def run_term(e):
        cmd = term_in.value
        try:
            exec(cmd)
            term_out.value = f"> {cmd}\nSuccess."
        except Exception as ex:
            term_out.value = f"> {cmd}\nError: {ex}"
        page.update()

    inp_proxies = ft.TextField(label="Прокси", multiline=True)
    def save_settings(e):
        state['proxies'] = inp_proxies.value.split('\n')
        show_msg("Настройки сохранены")

    tab_settings = ft.Container(
        padding=10,
        content=ft.Column([
            ft.Text("Developer Terminal", weight="bold"),
            ft.Container(content=term_out, bgcolor="#111", padding=10, border_radius=5),
            ft.Row([term_in, ft.IconButton(ft.icons.PLAY_ARROW, on_click=run_term)]),
            ft.Divider(),
            ft.Text("Proxy List"),
            inp_proxies,
            ft.ElevatedButton("Save Settings", on_click=save_settings)
        ], scroll="auto")
    )

    # --- TAB BAR (Для версии 0.22.1) ---
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(text="ПОИСК", icon=ft.icons.SEARCH, content=tab_search),
            ft.Tab(text="ХАЙП", icon=ft.icons.LOCAL_FIRE_DEPARTMENT, content=tab_hype),
            ft.Tab(text="SHORTS", icon=ft.icons.SMARTPHONE, content=tab_shorts),
            ft.Tab(text="КАНАЛЫ", icon=ft.icons.PERSON, content=tab_channels),
            ft.Tab(text="ТРЕКИНГ", icon=ft.icons.TRACK_CHANGES, content=tab_tracking),
            ft.Tab(text="МОЗГ", icon=ft.icons.PSYCHOLOGY, content=tab_brain),
            ft.Tab(text="ИЗБР.", icon=ft.icons.STAR, content=tab_fav),
            ft.Tab(text="НАСТР.", icon=ft.icons.SETTINGS, content=tab_settings),
        ],
        expand=True
    )

    page.add(loading_overlay, tabs)

if __name__ == "__main__":
    ft.app(target=main)
