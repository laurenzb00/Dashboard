"""Touch-Editor fuer den eigenen Heizungs-Zeitplan (Overlay ueber dem Thermo-Tab).

Bedienung ohne Tastatur: Wochentag waehlen, je Schaltpunkt Uhrzeit in 15-min-Schritten und
Temperatur in 0,5-°C-Schritten per grosse +/- Tasten. Oben die Wochentage, unten eine 24-h-Leiste
als Vorschau. Die Logik (Normalisieren, Verschieben, Kopieren) steckt in core/own_schedule.py.
"""
from __future__ import annotations

import tkinter as tk
from datetime import date
from typing import Callable, Optional

import customtkinter as ctk

from core import own_schedule as S
from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_WARNING,
    get_safe_font,
)

COLOR_ECO = "#818CF8"
COLOR_COMFORT = COLOR_WARNING


def _mix(a: str, b: str, f: float) -> str:
    f = max(0.0, min(1.0, f))
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(pa, pb))


def temp_color(t: float, lo: float, hi: float) -> str:
    """Kalt = bläulich (Absenkung), warm = orange (Komfort), relativ zum Wochenbereich."""
    if hi - lo < 0.5:
        return COLOR_COMFORT
    return _mix(COLOR_ECO, COLOR_COMFORT, (t - lo) / (hi - lo))


class ScheduleEditor(ctk.CTkFrame):
    def __init__(self, parent, sched: S.WeekSchedule, on_save: Callable[[S.WeekSchedule], None],
                 on_close: Callable[[], None], on_import: Optional[Callable[[], Optional[S.WeekSchedule]]] = None):
        super().__init__(parent, fg_color=COLOR_ROOT, corner_radius=0)
        self._s = sched.copy()
        self._on_save, self._on_close, self._on_import = on_save, on_close, on_import
        self._day = min(6, date.today().weekday())
        self._msg = tk.StringVar(value="")
        self._away_var = tk.BooleanVar(value=self._s.away_enabled)
        self._build()
        self._refresh()

    # ----------------------------------------------------------------- Aufbau --

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=0)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="🗓  Eigener Zeitplan", text_color=COLOR_TEXT, anchor="w",
                     font=get_safe_font("Bahnschrift", 26, "bold")).grid(row=0, column=0, sticky="w", padx=20, pady=(14, 0))
        ctk.CTkLabel(head, text="Ab der eingestellten Uhrzeit gilt die Temperatur bis zum nächsten Zeitpunkt.",
                     text_color=COLOR_SUBTEXT, anchor="w", font=get_safe_font("Bahnschrift", 14)
                     ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))
        ctk.CTkButton(head, text="✕", width=64, height=64, corner_radius=32, fg_color=COLOR_ROOT,
                      hover_color=COLOR_BORDER, text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 24, "bold"),
                      command=self._on_close).grid(row=0, column=1, rowspan=2, padx=16, pady=8)

        days = ctk.CTkFrame(self, fg_color="transparent")
        days.grid(row=1, column=0, sticky="ew", padx=14, pady=(14, 6))
        days.grid_columnconfigure(tuple(range(7)), weight=1, uniform="day")
        self._day_btns = []
        for i, name in enumerate(S.DAY_NAMES):
            b = ctk.CTkButton(days, text=name, height=64, corner_radius=20, border_width=2, border_color=COLOR_BORDER,
                              fg_color=COLOR_CARD, hover_color=COLOR_BORDER, text_color=COLOR_TEXT,
                              font=get_safe_font("Bahnschrift", 20, "bold"), command=lambda d=i: self._pick_day(d))
            b.grid(row=0, column=i, padx=3, sticky="ew")
            self._day_btns.append(b)

        copy = ctk.CTkFrame(self, fg_color="transparent")
        copy.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
        copy.grid_columnconfigure((0, 1, 2), weight=1, uniform="cp")
        for i, (txt, tg) in enumerate((("Auf Mo–Fr kopieren", S.WORKDAYS), ("Auf Sa+So kopieren", S.WEEKEND),
                                       ("Auf alle Tage kopieren", tuple(range(7))))):
            ctk.CTkButton(copy, text=txt, height=48, corner_radius=16, fg_color=COLOR_CARD, hover_color=COLOR_BORDER,
                          text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 14, "bold"),
                          command=lambda t=tg, n=txt: self._copy_to(t, n)).grid(row=0, column=i, padx=3, sticky="ew")

        try:
            self._rows = ctk.CTkScrollableFrame(self, fg_color="transparent")
        except Exception:
            self._rows = ctk.CTkFrame(self, fg_color="transparent")
        self._rows.grid(row=3, column=0, sticky="nsew", padx=10, pady=(4, 4))
        self._rows.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=0)
        bottom.grid(row=4, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        self._bar = tk.Canvas(bottom, height=70, bg=COLOR_CARD, highlightthickness=0)
        self._bar.grid(row=0, column=0, columnspan=3, sticky="ew", padx=18, pady=(14, 2))
        self._bar.bind("<Configure>", lambda _e: self._draw_bar())
        self._summary = ctk.CTkLabel(bottom, text="", text_color=COLOR_SUBTEXT, anchor="w", justify="left",
                                     font=get_safe_font("Bahnschrift", 14), wraplength=1000)
        self._summary.grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 4))
        opts = ctk.CTkFrame(bottom, fg_color="transparent")
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(2, 2))
        ctk.CTkSwitch(opts, text="Wenn ich nicht zu Hause bin (Home Assistant): Heizung absenken",
                      variable=self._away_var, command=self._toggle_away, text_color=COLOR_TEXT,
                      font=get_safe_font("Bahnschrift", 15, "bold")).grid(row=0, column=0, columnspan=4, sticky="w",
                                                                          padx=6, pady=(0, 4))
        self._away_temp_lbl = self._mini(opts, 1, 0, "Abwesend:", lambda: self._away_temp(-S.TEMP_STEP),
                                         lambda: self._away_temp(S.TEMP_STEP))
        self._km_lbl = self._mini(opts, 1, 2, "Aufheizen ab:", lambda: self._away_km(-2), lambda: self._away_km(2))
        ctk.CTkLabel(opts, text="5 °C ist das Minimum von Tado: praktisch aus, mit Frostschutz. "
                                "Nähert sich dein Handy dem Haus, läuft der Zeitplan sofort wieder an.",
                     text_color=COLOR_SUBTEXT, anchor="w", justify="left", wraplength=1000,
                     font=get_safe_font("Bahnschrift", 12)).grid(row=2, column=0, columnspan=4, sticky="w", padx=6,
                                                                 pady=(2, 0))
        ctk.CTkLabel(bottom, textvariable=self._msg, text_color=COLOR_WARNING, anchor="w",
                     font=get_safe_font("Bahnschrift", 14, "bold")).grid(row=3, column=0, columnspan=3, sticky="w",
                                                                         padx=20)
        btns = ctk.CTkFrame(bottom, fg_color="transparent")
        btns.grid(row=4, column=0, columnspan=3, sticky="ew", padx=14, pady=(6, 16))
        btns.grid_columnconfigure((0, 1, 2), weight=1, uniform="fb")
        if self._on_import is not None:
            ctk.CTkButton(btns, text="Aus Tado-Verlauf übernehmen", height=64, corner_radius=22, border_width=2,
                          border_color=COLOR_BORDER, fg_color=COLOR_ROOT, hover_color=COLOR_BORDER,
                          text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 15, "bold"),
                          command=self._import).grid(row=0, column=0, padx=4, sticky="ew")
        ctk.CTkButton(btns, text="Abbrechen", height=64, corner_radius=22, border_width=2, border_color=COLOR_BORDER,
                      fg_color=COLOR_ROOT, hover_color=COLOR_BORDER, text_color=COLOR_TEXT,
                      font=get_safe_font("Bahnschrift", 16, "bold"), command=self._on_close
                      ).grid(row=0, column=1, padx=4, sticky="ew")
        self._save_btn = ctk.CTkButton(btns, text="Speichern", height=64, corner_radius=22, fg_color=COLOR_SUCCESS,
                                       hover_color="#0E9F6E", text_color="#0B0D10",
                                       font=get_safe_font("Bahnschrift", 17, "bold"), command=self._save)
        self._save_btn.grid(row=0, column=2, padx=4, sticky="ew")

    # ---------------------------------------------------------------- Zeilen --

    def _pick_day(self, d: int) -> None:
        self._day = d
        self._msg.set("")
        self._refresh()

    def _stepper(self, parent, col: int, text: str, minus: Callable, plus: Callable, width: int) -> None:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, padx=6, pady=8)
        kw = dict(width=64, height=64, corner_radius=32, fg_color=COLOR_ROOT, hover_color=COLOR_BORDER,
                  border_width=2, border_color=COLOR_BORDER, text_color=COLOR_TEXT,
                  font=get_safe_font("Bahnschrift", 26, "bold"))
        ctk.CTkButton(f, text="−", command=minus, **kw).pack(side=tk.LEFT)
        ctk.CTkLabel(f, text=text, width=width, text_color=COLOR_TEXT,
                     font=get_safe_font("Bahnschrift", 28, "bold")).pack(side=tk.LEFT, padx=4)
        ctk.CTkButton(f, text="+", command=plus, **kw).pack(side=tk.LEFT)

    def _mini(self, parent, row: int, col: int, label: str, minus: Callable, plus: Callable):
        """Kleiner Beschriftung-[−]-Wert-[+]-Block; gibt das Wert-Label zurueck."""
        ctk.CTkLabel(parent, text=label, text_color=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 14)
                     ).grid(row=row, column=col, sticky="e", padx=(6, 4), pady=4)
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=col + 1, sticky="w", padx=(0, 18))
        kw = dict(width=52, height=52, corner_radius=26, fg_color=COLOR_ROOT, hover_color=COLOR_BORDER,
                  border_width=2, border_color=COLOR_BORDER, text_color=COLOR_TEXT,
                  font=get_safe_font("Bahnschrift", 22, "bold"))
        ctk.CTkButton(f, text="−", command=minus, **kw).pack(side=tk.LEFT)
        val = ctk.CTkLabel(f, text="", width=80, text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 20, "bold"))
        val.pack(side=tk.LEFT, padx=4)
        ctk.CTkButton(f, text="+", command=plus, **kw).pack(side=tk.LEFT)
        return val

    def _render_rows(self) -> None:
        for w in self._rows.winfo_children():
            w.destroy()
        entries = self._s.days[self._day]
        temps = [t for d in self._s.days for _, t in d]
        lo, hi = (min(temps), max(temps)) if temps else (18.0, 21.0)
        if not entries:
            ctk.CTkLabel(self._rows, text="Keine Zeitpunkte – dieser Tag setzt den Vortag fort.\n"
                                          "Mit „+ Zeitpunkt“ einen anlegen.", text_color=COLOR_SUBTEXT,
                         justify="left", font=get_safe_font("Bahnschrift", 16)).grid(row=0, column=0, padx=14,
                                                                                    pady=24, sticky="w")
        for i, (m, t) in enumerate(entries):
            row = ctk.CTkFrame(self._rows, fg_color=COLOR_CARD, corner_radius=20, border_width=2,
                               border_color=temp_color(t, lo, hi))
            row.grid(row=i, column=0, sticky="ew", pady=5, padx=4)
            row.grid_columnconfigure(2, weight=1)
            ctk.CTkLabel(row, text="ab", text_color=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 18)
                         ).grid(row=0, column=0, padx=(16, 0))
            self._stepper(row, 1, S.fmt_minute(m), lambda i=i: self._time(i, -S.STEP_MIN),
                          lambda i=i: self._time(i, S.STEP_MIN), 90)
            self._stepper(row, 2, S.fmt_t(t), lambda i=i: self._temp(i, -S.TEMP_STEP),
                          lambda i=i: self._temp(i, S.TEMP_STEP), 110)
            ctk.CTkButton(row, text="🗑", width=64, height=64, corner_radius=32, fg_color=COLOR_ROOT,
                          hover_color="#B91C1C", text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 22),
                          command=lambda i=i: self._remove(i)).grid(row=0, column=3, padx=(4, 14))
        add_row = len(entries)
        ctk.CTkButton(self._rows, text="＋  Zeitpunkt hinzufügen", height=60, corner_radius=22, border_width=2,
                      border_color=COLOR_PRIMARY, fg_color=COLOR_ROOT, hover_color=COLOR_BORDER, text_color=COLOR_TEXT,
                      font=get_safe_font("Bahnschrift", 17, "bold"), command=self._add,
                      state="normal" if len(entries) < S.MAX_SWITCHES_PER_DAY else "disabled"
                      ).grid(row=add_row, column=0, sticky="ew", pady=(8, 8), padx=4)

    # ---------------------------------------------------------------- Aktionen --

    def _add(self) -> None:
        self._s.days[self._day] = S.add_switch(self._s.days[self._day])
        self._refresh()

    def _remove(self, i: int) -> None:
        self._s.days[self._day] = S.remove_switch(self._s.days[self._day], i)
        self._refresh()

    def _time(self, i: int, delta: int) -> None:
        self._s.days[self._day] = S.shift_time(self._s.days[self._day], i, delta)
        self._refresh()

    def _temp(self, i: int, delta: float) -> None:
        self._s.days[self._day] = S.shift_temp(self._s.days[self._day], i, delta)
        self._refresh()

    def _copy_to(self, targets, label: str) -> None:
        S.copy_day(self._s, self._day, targets)
        self._msg.set(f"{S.DAY_NAMES[self._day]} übernommen: {label.replace(' kopieren', '')}")
        self._refresh(keep_msg=True)

    def _toggle_away(self) -> None:
        self._s.away_enabled = bool(self._away_var.get())
        self._refresh(keep_msg=True)

    def _away_temp(self, delta: float) -> None:
        self._s.away_temp = max(S.AWAY_TEMP_MIN, min(S.AWAY_TEMP_MAX, round((self._s.away_temp + delta) * 2) / 2))
        self._refresh(keep_msg=True)

    def _away_km(self, delta: float) -> None:
        self._s.approach_km = max(S.APPROACH_KM_MIN, min(S.APPROACH_KM_MAX, self._s.approach_km + delta))
        self._refresh(keep_msg=True)

    def _import(self) -> None:
        imported = self._on_import() if self._on_import else None
        if imported is None or not imported.usable:
            self._msg.set("Aus dem Verlauf lässt sich noch kein fester Plan erkennen.")
            return
        old = self._s
        self._s = imported
        self._s.respect_away, self._s.away_enabled = old.respect_away, old.away_enabled
        self._s.away_temp, self._s.approach_km, self._s.persons = old.away_temp, old.approach_km, old.persons
        self._msg.set("Plan aus dem bisherigen Tado-Verlauf übernommen – bitte prüfen und speichern.")
        self._refresh(keep_msg=True)

    def _save(self) -> None:
        if not self._s.usable:
            self._msg.set("Mindestens ein Zeitpunkt in der Woche nötig.")
            return
        self._s.enabled = True
        self._on_save(self._s)

    # ---------------------------------------------------------------- Anzeige --

    def _refresh(self, keep_msg: bool = False) -> None:
        if not keep_msg:
            self._msg.set("")
        for i, b in enumerate(self._day_btns):
            sel = i == self._day
            filled = bool(self._s.days[i])
            b.configure(fg_color=COLOR_PRIMARY if sel else COLOR_CARD,
                        border_color=COLOR_PRIMARY if sel else (COLOR_BORDER if filled else "#2A2F3A"),
                        text_color=COLOR_TEXT if (sel or filled) else COLOR_SUBTEXT)
        self._render_rows()
        self._save_btn.configure(text="Speichern" if self._s.enabled else "Speichern & einschalten")
        self._away_temp_lbl.configure(text=S.fmt_t(self._s.away_temp))
        self._km_lbl.configure(text=f"{self._s.approach_km:.0f} km")
        dim = COLOR_TEXT if self._s.away_enabled else COLOR_SUBTEXT
        self._away_temp_lbl.configure(text_color=dim)
        self._km_lbl.configure(text_color=dim)
        self._draw_bar()
        self._summary.configure(text=self._day_summary())

    def _day_summary(self) -> str:
        plan = S.day_plan(self._s, self._monday_of_week(self._day))
        if plan is None:
            return f"{S.DAY_NAMES[self._day]}: noch kein Plan"
        parts = [f"{S.fmt_minute(s.start)}–{S.fmt_minute(s.end % 1440) if s.end < 1440 else '24:00'} {S.fmt_t(s.temp)}"
                 for s in plan.segments]
        return f"{S.DAY_NAMES[self._day]}:  " + "   ·   ".join(parts)

    @staticmethod
    def _monday_of_week(weekday: int) -> date:
        today = date.today()
        return today.fromordinal(today.toordinal() - today.weekday() + weekday)

    def _draw_bar(self) -> None:
        c = self._bar
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = 70
        plan = S.day_plan(self._s, self._monday_of_week(self._day))
        top, bot = 6, 44
        c.create_rectangle(0, top, w, bot, fill=COLOR_ROOT, outline="")
        if plan is not None:
            temps = [t for d in self._s.days for _, t in d]
            lo, hi = (min(temps), max(temps)) if temps else (18.0, 21.0)
            for seg in plan.segments:
                x0, x1 = seg.start / 1440 * w, seg.end / 1440 * w
                c.create_rectangle(x0, top, x1, bot, fill=temp_color(seg.temp, lo, hi), outline="")
                if x1 - x0 > 54:
                    c.create_text((x0 + x1) / 2, (top + bot) / 2, text=S.fmt_t(seg.temp), fill="#0B0D10",
                                  font=get_safe_font("Bahnschrift", 14, "bold"))
        for hr in (0, 6, 12, 18, 24):
            x = min(max(hr / 24 * w, 1), w - 1)
            c.create_line(x, bot, x, bot + 6, fill=COLOR_SUBTEXT)
            c.create_text(min(max(x, 14), w - 14), bot + 18, text=f"{hr:02d}", fill=COLOR_SUBTEXT,
                          font=get_safe_font("Bahnschrift", 11))
