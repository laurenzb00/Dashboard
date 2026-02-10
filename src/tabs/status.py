
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from collections import deque
import math
import traceback
from types import SimpleNamespace
import customtkinter as ctk

from core.datastore import get_shared_datastore
from core.schema import PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT, BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_TITLE,
    emoji,
)
from ui.components.card import Card
from ui.components.rounded import RoundedFrame

class StatusTab(ctk.CTkFrame):
    def _on_light_on(self):
        # TODO: Hier Logik für Licht AN einfügen
        self.light_icon.config(fg=COLOR_PRIMARY)
        print("Licht AN")

    def _on_light_off(self):
        # TODO: Hier Logik für Licht AUS einfügen
        self.light_icon.config(fg=COLOR_SUBTEXT)
        print("Licht AUS")
    def __init__(self, parent, tab_frame=None, *args, **kwargs):
        # Use provided tab_frame as parent or parent parameter (legacy)
        frame_parent = tab_frame if tab_frame is not None else parent
        super().__init__(frame_parent, *args, **kwargs)
        self.parent = parent
        self.datastore = get_shared_datastore()
        self._last_err_db = None
        self._last_err_pv = None
        self._last_err_heat = None

        # Rolling in-memory history of UI fetches (for consistency analysis)
        self._hist_db = deque(maxlen=50)
        self._hist_pv = deque(maxlen=50)
        self._hist_heat = deque(maxlen=50)
        # --- Fix: initialize snapshot_labels dict for all relevant keys ---
        self.snapshot_labels = {}
        
        # Pack self into the provided frame if using CustomTkinter
        if tab_frame is not None:
            self.pack(fill=tk.BOTH, expand=True)
        self._build_layout()
        self.after_job = None
        self._schedule_update()



    def _build_layout(self):
        """Minimalistisches Status-Dashboard mit großen Kacheln."""
        self.configure(fg_color=COLOR_ROOT)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X, padx=12, pady=(12, 6))
        ctk.CTkLabel(
            header,
            text="Systemstatus",
            font=("Segoe UI", 16, "bold"),
            text_color=COLOR_TITLE,
        ).pack(anchor="w")
        self.summary_label = ctk.CTkLabel(
            header,
            text="Warte auf Daten...",
            font=("Segoe UI", 11),
            text_color=COLOR_SUBTEXT,
        )
        self.summary_label.pack(anchor="w", pady=(2, 0))
        self.detail_label = ctk.CTkLabel(
            header,
            text="",
            font=("Segoe UI", 9),
            text_color=COLOR_SUBTEXT,
        )
        self.detail_label.pack(anchor="w")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        
        # Grid: 3 Zeilen × 4 Spalten
        for i in range(4):
            main.grid_columnconfigure(i, weight=1, uniform="status")
        for i in range(3):
            main.grid_rowconfigure(i, weight=1, uniform="status")
        
        # Zeile 1: System-Status Ampeln (DB, PV, Heizung, Warnung)
        self.ampel_cards = []
        ampel_specs = [
            ("DB", "🗄️", 0),
            ("PV", "☀️", 1),
            ("Heizung", "🔥", 2),
            ("Status", "✓", 3),
        ]
        for label, icon, col in ampel_specs:
            card = Card(main, padding=12)
            card.grid(row=0, column=col, sticky="nsew", padx=4, pady=4)
            inner = card.content()
            
            # Icon
            icon_lbl = ctk.CTkLabel(inner, text=icon, font=("Segoe UI", 28), text_color=COLOR_TEXT)
            icon_lbl.pack(pady=(8, 4))
            # Label
            ctk.CTkLabel(inner, text=label, font=("Segoe UI", 10, "bold"), text_color=COLOR_TITLE).pack(pady=(0, 4))
            # Status-Ampel
            lamp = tk.Canvas(inner, width=24, height=24, bg=COLOR_ROOT, highlightthickness=0)
            lamp.pack(pady=(4, 8))

            status_lbl = ctk.CTkLabel(inner, text="--", font=("Segoe UI", 11, "bold"), text_color=COLOR_TEXT)
            status_lbl.pack(pady=(0, 2))
            age_lbl = ctk.CTkLabel(inner, text="--", font=("Segoe UI", 9), text_color=COLOR_SUBTEXT)
            age_lbl.pack(pady=(0, 2))
            
            self.ampel_cards.append({"label": label, "lamp": lamp, "icon": icon_lbl, "status": status_lbl, "age": age_lbl})
        
        # Zeile 2: Energie-Werte (PV, Grid, Batterie, Batterie-SOC)
        self.snapshot_labels = {}
        energy_specs = [
            (PV_POWER_KW, "PV", "☀️", "kW", 0),
            (GRID_POWER_KW, "Netz", "🔌", "kW", 1),
            (BATTERY_POWER_KW, "Batt", "🔋", "kW", 2),
            (BATTERY_SOC_PCT, "SOC", "🔋", "%", 3),
        ]
        for key, label, icon, unit, col in energy_specs:
            card = Card(main, padding=12)
            card.grid(row=1, column=col, sticky="nsew", padx=4, pady=4)
            inner = card.content()
            
            ctk.CTkLabel(inner, text=icon, font=("Segoe UI", 22), text_color=COLOR_PRIMARY).pack(pady=(6, 2))
            val = ctk.CTkLabel(inner, text="--", font=("Segoe UI", 20, "bold"), text_color=COLOR_TEXT)
            val.pack(pady=(0, 2))
            ctk.CTkLabel(inner, text=f"{label} ({unit})", font=("Segoe UI", 9), text_color=COLOR_SUBTEXT).pack(pady=(0, 6))
            
            self.snapshot_labels[key] = val
        
        # Zeile 3: Heizungs-Werte (Kessel, Warmwasser, Puffer oben/mitte/unten kombiniert, Außentemp)
        heating_specs = [
            (BMK_KESSEL_C, "Kessel", "🔥", "°C", 0),
            (BMK_WARMWASSER_C, "Warmwasser", "💧", "°C", 1),
            (BUF_TOP_C, "Puffer", "⬆️", "°C", 2),  # Zeigt Top-Wert
        ]
        for key, label, icon, unit, col in heating_specs:
            card = Card(main, padding=12)
            card.grid(row=2, column=col, sticky="nsew", padx=4, pady=4)
            inner = card.content()
            
            ctk.CTkLabel(inner, text=icon, font=("Segoe UI", 22), text_color=COLOR_WARNING).pack(pady=(6, 2))
            val = ctk.CTkLabel(inner, text="--", font=("Segoe UI", 20, "bold"), text_color=COLOR_TEXT)
            val.pack(pady=(0, 2))
            ctk.CTkLabel(inner, text=f"{label} ({unit})", font=("Segoe UI", 9), text_color=COLOR_SUBTEXT).pack(pady=(0, 6))
            
            self.snapshot_labels[key] = val
        
        # Zusätzliche Puffer-Werte (versteckt in den Daten, werden aber nicht explizit angezeigt)
        # Wir initialisieren die Labels trotzdem für update_data
        for key in [BUF_MID_C, BUF_BOTTOM_C]:
            dummy_label = ctk.CTkLabel(main, text="--")
            self.snapshot_labels[key] = dummy_label

    def _make_health_tile(self, parent, col, title, icon):
        outer = RoundedFrame(parent, bg=COLOR_ROOT, border=None, radius=18, padding=0)
        outer.grid(row=0, column=col, sticky="nsew", padx=8, pady=0)
        inner = outer.content()
        inner.grid_columnconfigure(0, weight=0)
        inner.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 2))
        ctk.CTkLabel(header, text=icon, text_color=COLOR_TEXT, font=("Segoe UI", 12)).pack(side=tk.LEFT)
        ctk.CTkLabel(header, text=title, text_color=COLOR_SUBTEXT, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(6, 0))

        lamp = tk.Canvas(inner, width=18, height=18, bg=COLOR_ROOT, highlightthickness=0)
        lamp.grid(row=1, column=0, sticky="w", padx=(12, 8), pady=(2, 10))
        line1 = ctk.CTkLabel(inner, text="--", text_color=COLOR_TEXT, font=("Segoe UI", 12, "bold"))
        line1.grid(row=1, column=1, sticky="w", pady=(2, 0), padx=(0, 12))
        line2 = ctk.CTkLabel(inner, text="--", text_color=COLOR_SUBTEXT, font=("Segoe UI", 9))
        line2.grid(row=2, column=1, sticky="w", pady=(0, 10), padx=(0, 12))

        return SimpleNamespace(lamp=lamp, line1=line1, line2=line2)

    def _build_consistency_lines(self, pv_rec_last, ht_rec_last) -> list[str]:
        consistency: list[str] = []
        pv_key_warn = self._key_warnings(pv_rec_last, self._expected_keys_pv(), allowed_extras={"timestamp"})
        pv_range_warn = self._range_warnings(pv_rec_last, "pv")
        pv_ts_warn = self._monotonic_ts_warnings(self._hist_pv)
        if pv_key_warn or pv_range_warn or pv_ts_warn:
            consistency.append("PV: " + " | ".join(pv_key_warn + pv_range_warn + pv_ts_warn))

        ht_key_warn = self._key_warnings(ht_rec_last, self._expected_keys_heat(), allowed_extras={"timestamp", "outdoor"})
        ht_range_warn = self._range_warnings(ht_rec_last, "heat")
        ht_ts_warn = self._monotonic_ts_warnings(self._hist_heat)
        if ht_key_warn or ht_range_warn or ht_ts_warn:
            consistency.append("Heizung: " + " | ".join(ht_key_warn + ht_range_warn + ht_ts_warn))

        return consistency

    def _set_health(self, card, color, status_text, line2=""):
        try:
            card.lamp.delete("all")
            card.lamp.create_oval(4, 4, 22, 22, fill=color, outline=COLOR_ROOT)
            card.line1.config(text=status_text)
            card.line2.config(text=line2 if line2 else "")
        except Exception:
            pass

    @staticmethod
    def _safe_float(v):
        if v is None:
            return None
        try:
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except Exception:
            return None

    @staticmethod
    def _fmt_num(v, decimals=1):
        f = StatusTab._safe_float(v)
        if f is None:
            return "--"
        return f"{f:.{decimals}f}"

    @staticmethod
    def _expected_keys_pv():
        return {PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT}

    @staticmethod
    def _expected_keys_heat():
        return {BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C}

    @staticmethod
    def _range_warnings(record, kind: str):
        warns = []
        if not record:
            return warns
        if kind == "pv":
            soc = StatusTab._safe_float(record.get(BATTERY_SOC_PCT))
            if soc is not None and not (0.0 <= soc <= 100.0):
                warns.append(f"SOC außerhalb 0..100: {soc:.1f}")
            for key, lim in [(PV_POWER_KW, 80.0), (GRID_POWER_KW, 80.0), (BATTERY_POWER_KW, 80.0)]:
                val = StatusTab._safe_float(record.get(key))
                if val is not None and abs(val) > lim:
                    warns.append(f"{key} unplausibel (|x|>{lim:g}): {val:.2f}")
        if kind == "heat":
            for key in [BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C]:
                val = StatusTab._safe_float(record.get(key))
                if val is not None and not (-40.0 <= val <= 120.0):
                    warns.append(f"{key} unplausibel (-40..120): {val:.1f}")
        return warns

    @staticmethod
    def _key_warnings(record, expected_keys: set, allowed_extras: set):
        if record is None:
            return ["Keine Daten"]
        keys = set(record.keys())
        missing = sorted(expected_keys - keys)
        extra = sorted(keys - expected_keys - allowed_extras)
        warns = []
        if missing:
            warns.append("Fehlende Keys: " + ", ".join(missing))
        if extra:
            warns.append("Unerwartete Keys: " + ", ".join(extra))
        return warns

    @staticmethod
    def _monotonic_ts_warnings(history_deque):
        if len(history_deque) < 2:
            return []
        last = history_deque[-1].get("source_dt")
        prev = history_deque[-2].get("source_dt")
        if last is None or prev is None:
            return []
        if last < prev:
            return ["Zeitstempel rückwärts (neu < alt)"]
        return []

    def _schedule_update(self):
        if self.after_job is not None:
            try:
                self.after_cancel(self.after_job)
            except Exception:
                pass
        self.after_job = self.after(3000, self._update_status)

    def _update_status(self):
        """Minimales Update: nur Ampeln und Live-Werte."""
        now = datetime.now()
        
        # Default: alle Werte auf "--"
        for lbl in self.snapshot_labels.values():
            try:
                lbl.config(text="--")
            except Exception:
                pass
        
        # Ampel-Defaults (grau)
        for card in self.ampel_cards:
            card["lamp"].delete("all")
            card["lamp"].create_oval(6, 6, 18, 18, fill="#555", outline=COLOR_ROOT)
        
        # Initialize age variables
        db_age = None
        pv_age = None
        heat_age = None
        
        # DB-Status
        try:
            latest_ts = self.datastore.get_latest_timestamp()
            db_dt = self._safe_iso_to_dt(latest_ts)
            db_age = self._age_seconds(now, db_dt)
            db_color, db_status, db_line = self._lamp_style_ext(db_age, db_dt, now, stale_s=60)
            self.ampel_cards[0]["lamp"].delete("all")
            self.ampel_cards[0]["lamp"].create_oval(6, 6, 18, 18, fill=db_color, outline=COLOR_ROOT)
            self.ampel_cards[0]["status"].configure(text=db_status)
            self.ampel_cards[0]["age"].configure(text=db_line)
        except Exception:
            pass
        
        # PV-Daten
        try:
            pv_rec = self.datastore.get_last_fronius_record()
            if pv_rec:
                for key in [PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT]:
                    val = pv_rec.get(key)
                    if key in self.snapshot_labels:
                        if key == BATTERY_SOC_PCT:
                            self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=0))
                        else:
                            self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=2))
            
            pv_dt = self._safe_iso_to_dt(pv_rec.get("timestamp") if pv_rec else None)
            pv_age = self._age_seconds(now, pv_dt)
            pv_color, pv_status, pv_line = self._lamp_style_ext(pv_age, pv_dt, now, stale_s=60)
            self.ampel_cards[1]["lamp"].delete("all")
            self.ampel_cards[1]["lamp"].create_oval(6, 6, 18, 18, fill=pv_color, outline=COLOR_ROOT)
            self.ampel_cards[1]["status"].configure(text=pv_status)
            self.ampel_cards[1]["age"].configure(text=pv_line)
        except Exception:
            pass
        
        # Heizungs-Daten
        try:
            heat_rec = self.datastore.get_last_heating_record()
            if heat_rec:
                for key in [BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C]:
                    val = heat_rec.get(key)
                    if key in self.snapshot_labels:
                        self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=1))
                
                # Puffer-Werte (versteckte Labels)
                for key in [BUF_MID_C, BUF_BOTTOM_C]:
                    val = heat_rec.get(key)
                    if key in self.snapshot_labels:
                        self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=1))
            
            heat_dt = self._safe_iso_to_dt(heat_rec.get("timestamp") if heat_rec else None)
            heat_age = self._age_seconds(now, heat_dt)
            heat_color, heat_status, heat_line = self._lamp_style_ext(heat_age, heat_dt, now, stale_s=60)
            self.ampel_cards[2]["lamp"].delete("all")
            self.ampel_cards[2]["lamp"].create_oval(6, 6, 18, 18, fill=heat_color, outline=COLOR_ROOT)
            self.ampel_cards[2]["status"].configure(text=heat_status)
            self.ampel_cards[2]["age"].configure(text=heat_line)
        except Exception:
            pass
        
        # Gesamt-Status (4. Ampel)
        ages = [a for a in (db_age, pv_age, heat_age) if a is not None]
        if not ages:
            overall_color = "#9a9a9a"
            overall_status = "Keine Daten"
            overall_line = "--"
        else:
            worst = max(ages)
            if worst < 60:
                overall_color = COLOR_SUCCESS
                overall_status = "OK"
            elif worst < 300:
                overall_color = COLOR_WARNING
                overall_status = "Veraltet"
            else:
                overall_color = COLOR_DANGER
                overall_status = "Stark veraltet"
            overall_line = f"vor {self._format_age(worst)}"
        self.ampel_cards[3]["lamp"].delete("all")
        self.ampel_cards[3]["lamp"].create_oval(6, 6, 18, 18, fill=overall_color, outline=COLOR_ROOT)
        self.ampel_cards[3]["status"].configure(text=overall_status)
        self.ampel_cards[3]["age"].configure(text=overall_line)

        try:
            db_line = self.ampel_cards[0]["age"].cget("text")
            pv_line = self.ampel_cards[1]["age"].cget("text")
            heat_line = self.ampel_cards[2]["age"].cget("text")
            self.summary_label.configure(text=f"DB {db_line} | PV {pv_line} | Heizung {heat_line}")
            self.detail_label.configure(text=f"Letztes Update: {now.strftime('%H:%M:%S')}")
        except Exception:
            pass
        
        self._schedule_update()
    
    @staticmethod
    def _simple_status_color(age_s):
        """Einfache Ampel: Grün < 60s, Gelb < 300s, Rot sonst."""
        if age_s is None:
            return "#555"
        if age_s < 60:
            return COLOR_SUCCESS
        elif age_s < 300:
            return COLOR_WARNING
        else:
            return COLOR_DANGER

    @staticmethod
    def _safe_iso_to_dt(ts):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts))
        except Exception:
            return None

    @staticmethod
    def _age_seconds(now, dt):
        if dt is None:
            return None
        # Normalize both datetimes to naive (no tzinfo) to avoid TypeError
        if hasattr(now, 'tzinfo') and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return (now - dt).total_seconds()

    @staticmethod
    def _lamp_style_ext(age_s, dt, now, stale_s=60, future_s=60):
        # Liefert (Farbe, Status, Kontextzeile)
        if dt is None:
            return ("#9a9a9a", "Keine Daten", "--")
        if age_s is None:
            return ("#9a9a9a", "Keine Daten", "--")
        if age_s < -future_s:
            return ("#d2a106", "Zeitfehler", f"+{abs(int(age_s))//60}m")
        if age_s <= stale_s:
            return ("#44cc44", "OK", f"vor {StatusTab._format_age(age_s)}")
        if age_s <= 10*stale_s:
            return ("#d2a106", "Veraltet", f"vor {StatusTab._format_age(age_s)}")
        return ("#cc4444", "Stark veraltet", f"vor {StatusTab._format_age(age_s)}")

    @staticmethod
    def _format_age(age_s):
        if age_s is None:
            return "--"
        age_s = int(abs(age_s))
        if age_s < 60:
            return f"{age_s}s"
        m, s = divmod(age_s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m"
