import threading
import queue
import time
import tkinter as tk
from tkinter import ttk
import requests
import icalendar
import recurring_ical_events
import datetime
import calendar
import pytz
import customtkinter as ctk
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_TITLE,
    emoji,
)
from ui.components.card import Card

# --- KONFIGURATION ---
ICAL_URLS = [
    "https://calendar.google.com/calendar/ical/laurenzbandzauner%40gmail.com/private-ee12d630b1b19a7f6754768f56f1a76c/basic.ics",
    "https://calendar.google.com/calendar/ical/ukrkc67kki9lm9lllj6l0je1ag%40group.calendar.google.com/public/basic.ics",
    "https://calendar.google.com/calendar/ical/h53q4om49cgioc2gff7j5r5pi4%40group.calendar.google.com/public/basic.ics",
    "https://calendar.google.com/calendar/ical/pehhg3u2a6ha539oql87fuao0j9aqteu%40import.calendar.google.com/public/basic.ics"
]

class CalendarTab:
    """Moderne Kalenderansicht mit Card-Layout."""
    
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.displayed_month = datetime.datetime.now().date().replace(day=1)
        
        self.status_var = tk.StringVar(value="Lade Kalender...")
        self.events_data = []
        self._events_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._refresh_inflight = False

        # Separate cache for statusbar overlay (today's events), independent of displayed month.
        self.today_events_data: list[dict] = []
        self._today_overlay_text: str = ""
        self._today_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._today_refresh_inflight = False
        
        # Tab Frame - Use provided frame or create legacy one
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("📅 Kalender", "Kalender"))
        
        self.tab_frame.grid_columnconfigure(0, weight=1)
        self.tab_frame.grid_rowconfigure(1, weight=1)

        # Header mit Navigation - modernere Buttons
        header = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        
        # Navigation Buttons mit CustomTkinter
        ctk.CTkButton(
            header, text="◀ Zurück", 
            command=self._prev_month, 
            width=120,
            height=36,
            font=("Segoe UI", 12, "bold"),
            fg_color=COLOR_CARD,
            hover_color=COLOR_PRIMARY,
            text_color=COLOR_TEXT
        ).pack(side=tk.LEFT, padx=4)
        
        tk.Label(header, text="Kalender", font=("Segoe UI", 15, "bold"), bg=COLOR_ROOT, fg=COLOR_TITLE).pack(side=tk.LEFT, padx=20, expand=True)
        tk.Label(header, textvariable=self.status_var, font=("Segoe UI", 10), bg=COLOR_ROOT, fg=COLOR_SUBTEXT).pack(side=tk.RIGHT, padx=4)
        
        ctk.CTkButton(
            header, text="Weiter ▶", 
            command=self._next_month, 
            width=120,
            height=36,
            font=("Segoe UI", 12, "bold"),
            fg_color=COLOR_CARD,
            hover_color=COLOR_PRIMARY,
            text_color=COLOR_TEXT
        ).pack(side=tk.LEFT, padx=4)

        # Scrollable Content
        self.canvas = tk.Canvas(self.tab_frame, highlightthickness=0, bg=COLOR_ROOT)
        self.scrollbar = ttk.Scrollbar(self.tab_frame, orient="vertical", command=self.canvas.yview)
        self.scroll_frame = tk.Frame(self.canvas, bg=COLOR_ROOT)
        
        self.scroll_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window_id = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        
        def _on_canvas_resize(event):
            try:
                self.canvas.itemconfigure(self.window_id, width=event.width)
            except:
                pass
        
        self.canvas.bind("<Configure>", _on_canvas_resize)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.scrollbar.grid(row=1, column=1, sticky="ns", pady=(0, 12))

        # Start Update Loop (no Tk calls from worker threads)
        self.root.after(0, self._schedule_refresh)
        self.root.after(200, self._poll_queue)

        # Today overlay refresh for statusbar
        self.root.after(0, self._schedule_today_refresh)
        self.root.after(250, self._poll_today_queue)

    def stop(self):
        self.alive = False
        # Explicitly destroy canvas and scroll widgets to prevent memory leaks
        try:
            if hasattr(self, 'canvas') and self.canvas:
                self.canvas.destroy()
            if hasattr(self, 'scrollbar') and self.scrollbar:
                self.scrollbar.destroy()
            if hasattr(self, 'scroll_frame') and self.scroll_frame:
                self.scroll_frame.destroy()
            if hasattr(self, 'tab_frame') and self.tab_frame:
                self.tab_frame.destroy()
        except Exception:
            pass

    def _ui_set(self, var: tk.StringVar, value: str):
        try:
            self.root.after(0, var.set, value)
        except Exception:
            pass

    def _prev_month(self):
        """Gehe einen Monat zurück."""
        if self.displayed_month.month == 1:
            self.displayed_month = self.displayed_month.replace(year=self.displayed_month.year - 1, month=12)
        else:
            self.displayed_month = self.displayed_month.replace(month=self.displayed_month.month - 1)
        self._render_calendar()

    def _next_month(self):
        """Gehe einen Monat weiter."""
        if self.displayed_month.month == 12:
            self.displayed_month = self.displayed_month.replace(year=self.displayed_month.year + 1, month=1)
        else:
            self.displayed_month = self.displayed_month.replace(month=self.displayed_month.month + 1)
        self._render_calendar()

    def _load_events(self):
        """Lade Kalender von Google Calendar."""
        events = []
        tz = pytz.timezone('Europe/Vienna')
        
        for url in ICAL_URLS:
            try:
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                
                cal = icalendar.Calendar.from_ical(response.content)
                
                start = datetime.datetime(self.displayed_month.year, self.displayed_month.month, 1, tzinfo=tz)
                end = start + datetime.timedelta(days=40)
                
                expanded = recurring_ical_events.of(cal).between(start, end)
                
                for event in expanded:
                    try:
                        title = str(event.get('summary', 'Event'))
                        dt_start = event.get('dtstart')
                        
                        if hasattr(dt_start, 'dt'):
                            start_dt = dt_start.dt
                            if isinstance(start_dt, datetime.date) and not isinstance(start_dt, datetime.datetime):
                                start_dt = datetime.datetime.combine(start_dt, datetime.time(0, 0))
                        else:
                            start_dt = datetime.datetime.now(tz)
                        
                        if isinstance(start_dt, datetime.datetime) and start_dt.tzinfo is None:
                            start_dt = tz.localize(start_dt)
                        
                        events.append({'title': title, 'start': start_dt})
                    except:
                        pass
            except Exception as e:
                print(f"Kalender-Fehler: {e}")
                continue
        
        return sorted(events, key=lambda e: e['start'])

    def _load_today_events(self):
        """Load today's events for the statusbar overlay (does not affect UI month view)."""
        events = []
        tz = pytz.timezone('Europe/Vienna')
        now = datetime.datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=1)

        for url in ICAL_URLS:
            try:
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                cal = icalendar.Calendar.from_ical(response.content)
                expanded = recurring_ical_events.of(cal).between(start, end)

                for event in expanded:
                    try:
                        title = str(event.get('summary', 'Event'))
                        dt_start = event.get('dtstart')

                        all_day = False
                        if hasattr(dt_start, 'dt'):
                            start_dt = dt_start.dt
                            if isinstance(start_dt, datetime.date) and not isinstance(start_dt, datetime.datetime):
                                all_day = True
                                start_dt = datetime.datetime.combine(start_dt, datetime.time(0, 0))
                        else:
                            start_dt = start

                        if isinstance(start_dt, datetime.datetime) and start_dt.tzinfo is None:
                            start_dt = tz.localize(start_dt)
                        if isinstance(start_dt, datetime.date) and not isinstance(start_dt, datetime.datetime):
                            start_dt = tz.localize(datetime.datetime.combine(start_dt, datetime.time(0, 0)))

                        # Only keep events that start today in local tz
                        try:
                            start_local = start_dt.astimezone(tz) if isinstance(start_dt, datetime.datetime) else start_dt
                        except Exception:
                            start_local = start_dt
                        if isinstance(start_local, datetime.datetime) and start_local.date() != start.date():
                            continue
                        events.append({'title': title, 'start': start_local, 'all_day': bool(all_day)})
                    except Exception:
                        pass
            except Exception as e:
                # Do not spam UI; best-effort.
                continue

        return sorted(events, key=lambda e: e['start'])

    @staticmethod
    def build_today_overlay_text(events: list[dict], now: datetime.datetime | None = None) -> str:
        """Return compact statusbar text like: 'Heute: 2 Termine (14:30 Meeting)'."""
        try:
            if now is None:
                now = datetime.datetime.now().astimezone()
            today = now.date()
        except Exception:
            return ""

        todays: list[dict] = []
        for e in events or []:
            try:
                s = e.get('start')
                if not isinstance(s, datetime.datetime):
                    continue
                if s.date() != today:
                    continue
                title = str(e.get('title') or '').strip()
                if not title:
                    title = "Termin"
                all_day = bool(e.get('all_day', False))
                todays.append({'start': s, 'title': title, 'all_day': all_day})
            except Exception:
                continue

        todays.sort(key=lambda x: x.get('start'))
        count = len(todays)
        if count <= 0:
            return "Heute: 0 Termine"

        next_ev = None
        try:
            future = [ev for ev in todays if isinstance(ev.get('start'), datetime.datetime) and ev['start'] >= now]
            if future:
                next_ev = future[0]
            else:
                # Nothing upcoming; show last event of today as context.
                next_ev = todays[-1]
        except Exception:
            next_ev = None

        term_word = "Termin" if count == 1 else "Termine"
        text = f"Heute: {count} {term_word}"

        if next_ev is not None:
            try:
                s = next_ev.get('start')
                title = (next_ev.get('title') or '').strip()
                if len(title) > 32:
                    title = title[:31] + "…"

                if bool(next_ev.get('all_day', False)):
                    # All-day events: avoid misleading 00:00 time.
                    if title:
                        text += f" (ganztägig {title})"
                    else:
                        text += " (ganztägig)"
                else:
                    t = s.strftime('%H:%M') if isinstance(s, datetime.datetime) else "--:--"
                    if title:
                        text += f" ({t} {title})"
                    else:
                        text += f" ({t})"
            except Exception:
                pass
        return text

    def get_today_overlay_text(self) -> str:
        """Best-effort cached overlay text for the statusbar."""
        try:
            return (self._today_overlay_text or "").strip()
        except Exception:
            return ""

    def _render_calendar(self):
        """Rendere Kalender."""
        # Clear old widgets
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        
        # Load events
        all_events = list(self.events_data)
        
        # Title mit größerer Schrift
        month_name = self.displayed_month.strftime("%B %Y")
        title_label = tk.Label(self.scroll_frame, text=month_name, font=("Segoe UI", 18, "bold"), bg=COLOR_ROOT, fg=COLOR_TITLE)
        title_label.pack(pady=12)
        
        # Wochentage Header mit Grid - größere Schrift
        weekdays = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
        header_frame = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
        header_frame.pack(fill=tk.X, pady=8, padx=8)
        
        for col, day in enumerate(weekdays):
            header_frame.grid_columnconfigure(col, weight=1)
            day_label = tk.Label(header_frame, text=day, font=("Segoe UI", 12, "bold"), bg=COLOR_ROOT, fg=COLOR_TEXT)
            day_label.grid(row=0, column=col, sticky="ew", padx=2)
        
        # Kalender-Grid
        cal = calendar.monthcalendar(self.displayed_month.year, self.displayed_month.month)
        today = datetime.date.today()
        
        grid_frame = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
        grid_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        cell_min_w = 120
        cell_min_h = 90
        for col in range(7):
            grid_frame.grid_columnconfigure(col, weight=1, minsize=cell_min_w, uniform="calendar")
        for row in range(len(cal)):
            grid_frame.grid_rowconfigure(row, weight=1, minsize=cell_min_h)
        
        for row, week in enumerate(cal):
            for col, day_num in enumerate(week):
                if day_num == 0:
                    # Empty cell
                    empty_frame = tk.Frame(grid_frame, bg=COLOR_ROOT)
                    empty_frame.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
                else:
                    day_date = datetime.date(self.displayed_month.year, self.displayed_month.month, day_num)
                    
                    # Card für jeden Tag - moderneres Design
                    day_card = tk.Frame(grid_frame, bg=COLOR_CARD, relief=tk.FLAT, bd=0)
                    day_card.configure(highlightthickness=1, highlightbackground=COLOR_BORDER)
                    
                    # Styling für heute
                    if day_date == today:
                        day_card.configure(highlightthickness=2, highlightbackground=COLOR_PRIMARY)
                        day_label_color = COLOR_PRIMARY
                    else:
                        day_label_color = COLOR_TEXT
                    
                    # Tag-Nummer - größere Schrift
                    day_num_label = tk.Label(day_card, text=str(day_num), font=("Segoe UI", 14, "bold"), 
                                            bg=day_card.cget("bg"), fg=day_label_color)
                    day_num_label.pack(anchor="ne", padx=6, pady=4)
                    
                    # Events für diesen Tag
                    day_events = [e for e in all_events if e['start'].date() == day_date]
                    if day_events:
                        event_text = day_events[0]['title'][:22]
                        event_label = tk.Label(
                            day_card,
                            text=event_text,
                            font=("Segoe UI", 10),
                            bg=day_card.cget("bg"),
                            fg=COLOR_TEXT,
                            wraplength=90,
                            justify=tk.LEFT,
                        )
                        event_label.pack(anchor="w", padx=6, pady=(2, 1), fill=tk.X)

                    if len(day_events) > 1:
                        more_label = tk.Label(
                            day_card,
                            text=f"+{len(day_events) - 1} mehr",
                            font=("Segoe UI", 9, "italic"),
                            bg=day_card.cget("bg"),
                            fg=COLOR_SUBTEXT,
                        )
                        more_label.pack(anchor="w", padx=6, pady=(0, 2))

                    day_card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)

    def _schedule_refresh(self):
        if not self.alive or self._refresh_inflight:
            return
        self._refresh_inflight = True
        try:
            self.status_var.set("Lade...")
        except Exception:
            pass
        threading.Thread(target=self._load_events_worker, daemon=True).start()

    def _load_events_worker(self):
        try:
            events = self._load_events()
            self._events_queue.put(("ok", events))
        except Exception as e:
            self._events_queue.put(("err", e))

    def _schedule_today_refresh(self):
        if not self.alive or self._today_refresh_inflight:
            return
        self._today_refresh_inflight = True
        threading.Thread(target=self._load_today_events_worker, daemon=True).start()

    def _load_today_events_worker(self):
        try:
            events = self._load_today_events()
            self._today_queue.put(("ok", events))
        except Exception as e:
            self._today_queue.put(("err", e))

    def _poll_today_queue(self):
        if not self.alive:
            return
        try:
            status, payload = self._today_queue.get_nowait()
        except queue.Empty:
            self.root.after(250, self._poll_today_queue)
            return

        if status == "ok":
            self.today_events_data = list(payload) if payload else []
            try:
                self._today_overlay_text = self.build_today_overlay_text(self.today_events_data)
            except Exception:
                self._today_overlay_text = ""
        else:
            # Keep last known overlay; do not overwrite with errors.
            pass

        self._today_refresh_inflight = False
        if self.alive:
            self.root.after(600000, self._schedule_today_refresh)
            self.root.after(250, self._poll_today_queue)

    def _poll_queue(self):
        if not self.alive:
            return
        try:
            status, payload = self._events_queue.get_nowait()
        except queue.Empty:
            self.root.after(200, self._poll_queue)
            return

        if status == "ok":
            self.events_data = list(payload) if payload else []
            try:
                self._render_calendar()
                self.status_var.set("Aktuell")
            except Exception:
                pass
        else:
            try:
                self.status_var.set("Fehler beim Laden")
            except Exception:
                pass
            print(f"Kalender-Fehler: {payload}")

        self._refresh_inflight = False
        if self.alive:
            self.root.after(600000, self._schedule_refresh)
            self.root.after(200, self._poll_queue)
