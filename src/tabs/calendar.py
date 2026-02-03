import threading
import time
import tkinter as tk
from tkinter import ttk
import requests
import icalendar
import recurring_ical_events
import datetime
import calendar
import pytz
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_TEXT,
    COLOR_SUBTEXT,
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
    
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.displayed_month = datetime.datetime.now().date().replace(day=1)
        
        self.status_var = tk.StringVar(value="Lade Kalender...")
        self.events_data = []
        
        # Tab Frame
        self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
        notebook.add(self.tab_frame, text=emoji("📅 Kalender", "Kalender"))
        
        self.tab_frame.grid_columnconfigure(0, weight=1)
        self.tab_frame.grid_rowconfigure(1, weight=1)

        # Header mit Navigation
        header = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        
        ttk.Button(header, text="◀ Vorheriger", command=self._prev_month, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Label(header, text="Kalender", font=("Arial", 14, "bold")).pack(side=tk.LEFT, padx=20, expand=True)
        ttk.Label(header, textvariable=self.status_var, foreground=COLOR_SUBTEXT, font=("Arial", 9)).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Nächster ▶", command=self._next_month, width=12).pack(side=tk.LEFT, padx=4)

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

        # Start Update Loop
        self.root.after(0, lambda: threading.Thread(target=self._loop, daemon=True).start())

    def stop(self):
        self.alive = False

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

    def _render_calendar(self):
        """Rendere Kalender."""
        # Clear old widgets
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        
        # Load events
        all_events = list(self.events_data)
        
        # Title
        month_name = self.displayed_month.strftime("%B %Y")
        title_label = ttk.Label(self.scroll_frame, text=month_name, font=("Arial", 14, "bold"))
        title_label.pack(pady=12)
        
        # Wochentage Header mit Grid
        weekdays = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
        header_frame = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
        header_frame.pack(fill=tk.X, pady=4, padx=6)
        
        for col, day in enumerate(weekdays):
            header_frame.grid_columnconfigure(col, weight=1)
            day_label = ttk.Label(header_frame, text=day, font=("Arial", 9, "bold"))
            day_label.grid(row=0, column=col, sticky="ew", padx=1)
        
        # Kalender-Grid
        cal = calendar.monthcalendar(self.displayed_month.year, self.displayed_month.month)
        today = datetime.date.today()
        
        grid_frame = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
        grid_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        
        for row, week in enumerate(cal):
            for col, day_num in enumerate(week):
                grid_frame.grid_columnconfigure(col, weight=1)
                grid_frame.grid_rowconfigure(row, weight=1)
                
                if day_num == 0:
                    # Empty cell
                    empty_frame = tk.Frame(grid_frame, bg=COLOR_ROOT)
                    empty_frame.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)
                else:
                    day_date = datetime.date(self.displayed_month.year, self.displayed_month.month, day_num)
                    
                    # Card für jeden Tag
                    day_card = tk.Frame(grid_frame, bg=COLOR_CARD, relief=tk.RAISED, bd=1)
                    day_card.configure(highlightthickness=0)
                    
                    # Styling für heute
                    if day_date == today:
                        day_card.configure(bg=COLOR_PRIMARY, bd=2)
                        day_label_color = "white"
                    else:
                        day_label_color = COLOR_TEXT
                    
                    # Tag-Nummer
                    day_num_label = tk.Label(day_card, text=str(day_num), font=("Arial", 10, "bold"), 
                                            bg=day_card.cget("bg"), fg=day_label_color)
                    day_num_label.pack(anchor="ne", padx=3, pady=2)
                    
                    # Events für diesen Tag
                    day_events = [e for e in all_events if e['start'].date() == day_date]
                    for i, event in enumerate(day_events[:2]):  # Nur erste 2 Events
                        event_text = event['title'][:14]  # Kürzen
                        event_label = tk.Label(day_card, text=event_text, font=("Arial", 7), 
                                             bg=day_card.cget("bg"), fg=COLOR_SUBTEXT, wraplength=45, justify=tk.LEFT)
                        event_label.pack(anchor="w", padx=2, pady=1, fill=tk.X)
                    
                    if len(day_events) > 2:
                        more_label = tk.Label(day_card, text=f"+{len(day_events) - 2}", font=("Arial", 7, "italic"),
                                            bg=day_card.cget("bg"), fg=COLOR_SUBTEXT)
                        more_label.pack(anchor="w", padx=2)
                    
                    day_card.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)

    def _loop(self):
        """Hintergrund-Update Loop."""
        while self.alive:
            try:
                self._ui_set(self.status_var, "Lade...")
                self.events_data = self._load_events()
                self.root.after(0, self._render_calendar)
                self._ui_set(self.status_var, "Aktuell")
            except Exception as e:
                self._ui_set(self.status_var, "Fehler beim Laden")
                print(f"Kalender-Fehler: {e}")
            
            # Update alle 10 Minuten
            for _ in range(600):
                if not self.alive:
                    return
                time.sleep(0.1)
