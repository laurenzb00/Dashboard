
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from collections import deque
import math
import traceback

from core.datastore import get_shared_datastore
from core.schema import PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT, BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    emoji,
)
from ui.components.card import Card

class StatusTab(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.datastore = get_shared_datastore()
        self._last_err_db = None
        self._last_err_pv = None
        self._last_err_heat = None

        # Rolling in-memory history of UI fetches (for consistency analysis)
        self._hist_db = deque(maxlen=50)
        self._hist_pv = deque(maxlen=50)
        self._hist_heat = deque(maxlen=50)
        self._build_layout()
        self.after_job = None
        self._schedule_update()

    def _build_layout(self):
        # Hauptlayout: vertikal PanedWindow (oben Health, unten Split)
        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned.pack(fill="both", expand=True)

        # Oben: Health Cards
        self.top_frame = tk.Frame(self, bg=COLOR_ROOT)
        for i in range(4):
            self.top_frame.grid_columnconfigure(i, weight=1)
        self.top_frame.grid_rowconfigure(0, weight=1)
        self.card_db = self._make_health_card(self.top_frame, 0, "DB", "🗄️")
        self.card_pv = self._make_health_card(self.top_frame, 1, "PV", "☀️")
        self.card_heat = self._make_health_card(self.top_frame, 2, "Heizung", "🔥")
        self.card_warn = self._make_health_card(self.top_frame, 3, "Warnung", "⚠️")
        self.paned.add(self.top_frame, weight=0)

        # Unten: horizontal PanedWindow (Snapshot | Details)
        self.bottom_paned = ttk.PanedWindow(self.paned, orient=tk.HORIZONTAL)
        self.paned.add(self.bottom_paned, weight=1)

        # Links: Snapshot
        self.snapshot_card = Card(self.bottom_paned)
        self.snapshot_card.add_title("Current Snapshot", icon="🧾")
        self.snapshot_frame = self.snapshot_card.content()
        self.snapshot_frame.configure(bg=COLOR_CARD)
        for i in range(2):
            self.snapshot_frame.grid_columnconfigure(i, weight=1)
        self.bottom_paned.add(self.snapshot_card, weight=2)

        # Rechts: Details/Errors
        self.details_card = Card(self.bottom_paned)
        self.details_card.add_title("Details / Errors", icon="🧩")
        self.details_frame = self.details_card.content()
        self.details_frame.configure(bg=COLOR_CARD)
        self.details_frame.grid_rowconfigure(0, weight=1)
        self.details_frame.grid_columnconfigure(0, weight=1)
        self.bottom_paned.add(self.details_card, weight=1)

        # Details (scrollable)
        self.details_frame.grid_columnconfigure(0, weight=1)
        self.details_frame.grid_columnconfigure(1, weight=0)

        self.errors_text = tk.Text(
            self.details_frame,
            font=("Consolas", 10),
            bg=COLOR_ROOT,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            height=10,
            wrap="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLOR_CARD,
        )
        self.errors_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(self.details_frame, orient="vertical", command=self.errors_text.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.errors_text.configure(yscrollcommand=sb.set)
        self.errors_text.config(state="disabled")

        # Snapshot-Labels: bessere Spaltenaufteilung
        self.snapshot_frame.grid_columnconfigure(0, weight=3, minsize=120)
        self.snapshot_frame.grid_columnconfigure(1, weight=1, minsize=60, uniform="snapval")
        self.snapshot_labels = {}
        row = 0
        for key, label in [
            (PV_POWER_KW, "PV-Leistung [kW]"),
            (GRID_POWER_KW, "Netz [kW]"),
            (BATTERY_POWER_KW, "Batterie [kW]"),
            (BATTERY_SOC_PCT, "SOC [%]"),
            (BMK_KESSEL_C, "Kessel [°C]"),
            (BMK_WARMWASSER_C, "Warmwasser [°C]"),
            (BUF_TOP_C, "Puffer oben [°C]"),
            (BUF_MID_C, "Puffer mitte [°C]"),
            (BUF_BOTTOM_C, "Puffer unten [°C]"),
        ]:
            tk.Label(
                self.snapshot_frame,
                text=label,
                anchor="w",
                bg=COLOR_CARD,
                fg=COLOR_SUBTEXT,
                font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", padx=8, pady=2)
            val = tk.Label(
                self.snapshot_frame,
                text="--",
                anchor="e",
                bg=COLOR_CARD,
                fg=COLOR_TEXT,
                font=("Segoe UI", 11, "bold"),
            )
            val.grid(row=row, column=1, sticky="e", padx=8, pady=2)
            self.snapshot_labels[key] = val
            row += 1

    def _make_health_card(self, parent, col, title, icon):
        card = Card(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=12, pady=8)
        card.add_title(title, icon=icon)
        content = card.content()
        content.configure(bg=COLOR_CARD)
        content.grid_columnconfigure(1, weight=1)
        lamp = tk.Canvas(content, width=26, height=26, bg=COLOR_CARD, highlightthickness=0)
        lamp.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10), pady=2)
        line1 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_TEXT, font=("Segoe UI", 11, "bold"))
        line1.grid(row=0, column=1, sticky="w")
        line2 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 9))
        line2.grid(row=1, column=1, sticky="w")
        card.lamp = lamp
        card.line1 = line1
        card.line2 = line2
        return card

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
            card.lamp.create_oval(4, 4, 22, 22, fill=color, outline="#222")
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
        errors = []
        tb_full = ""
        now = datetime.now()
        # Default: alle Werte auf "--"
        for lbl in self.snapshot_labels.values():
            lbl.config(text="--")

        # Health-Card Defaults
        self._set_health(self.card_db, "#9a9a9a", "--")
        self._set_health(self.card_pv, "#9a9a9a", "--")
        self._set_health(self.card_heat, "#9a9a9a", "--")
        self._set_health(self.card_warn, "#9a9a9a", "OK")

        # DB
        try:
            latest_ts = self.datastore.get_latest_timestamp()
            db_dt = self._safe_iso_to_dt(latest_ts)
            db_age = self._age_seconds(now, db_dt)
            db_color, db_status, db_line2 = self._lamp_style_ext(db_age, db_dt, now)
            self._set_health(self.card_db, db_color, db_status, db_line2)
            self._hist_db.append({
                "at": now,
                "source_ts": latest_ts,
                "source_dt": db_dt,
                "age_s": db_age,
            })
        except Exception:
            errors.append("DB: Fehler beim Lesen")
            tb_full += traceback.format_exc() + "\n"
            self._hist_db.append({"at": now, "source_ts": None, "source_dt": None, "age_s": None})

        # PV
        try:
            pv_rec = self.datastore.get_last_fronius_record()
            if pv_rec:
                for key in [PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT]:
                    val = pv_rec.get(key)
                    if key == PV_POWER_KW or key == GRID_POWER_KW or key == BATTERY_POWER_KW:
                        self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=2))
                    elif key == BATTERY_SOC_PCT:
                        self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=1))
            pv_dt = self._safe_iso_to_dt(pv_rec.get("timestamp") if pv_rec else None)
            pv_age = self._age_seconds(now, pv_dt)
            pv_color, pv_status, pv_line2 = self._lamp_style_ext(pv_age, pv_dt, now)
            self._set_health(self.card_pv, pv_color, pv_status, pv_line2)
            self._hist_pv.append({
                "at": now,
                "source_ts": (pv_rec or {}).get("timestamp"),
                "source_dt": pv_dt,
                "age_s": pv_age,
                "record": pv_rec,
            })
        except Exception:
            errors.append("PV: Fehler beim Lesen")
            tb_full += traceback.format_exc() + "\n"
            self._hist_pv.append({"at": now, "source_ts": None, "source_dt": None, "age_s": None, "record": None})

        # Heating
        try:
            heat_rec = self.datastore.get_last_heating_record()
            if heat_rec:
                for key in [BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C]:
                    val = heat_rec.get(key)
                    self.snapshot_labels[key].config(text=self._fmt_num(val, decimals=1))
            heat_dt = self._safe_iso_to_dt(heat_rec.get("timestamp") if heat_rec else None)
            heat_age = self._age_seconds(now, heat_dt)
            heat_color, heat_status, heat_line2 = self._lamp_style_ext(heat_age, heat_dt, now)
            self._set_health(self.card_heat, heat_color, heat_status, heat_line2)
            self._hist_heat.append({
                "at": now,
                "source_ts": (heat_rec or {}).get("timestamp"),
                "source_dt": heat_dt,
                "age_s": heat_age,
                "record": heat_rec,
            })
        except Exception:
            errors.append("Heizung: Fehler beim Lesen")
            tb_full += traceback.format_exc() + "\n"
            self._hist_heat.append({"at": now, "source_ts": None, "source_dt": None, "age_s": None, "record": None})

        # Warnings
        pv_last = self._hist_pv[-1] if self._hist_pv else {}
        ht_last = self._hist_heat[-1] if self._hist_heat else {}
        pv_rec_last = pv_last.get("record")
        ht_rec_last = ht_last.get("record")
        consistency_lines = self._build_consistency_lines(pv_rec_last, ht_rec_last)

        if errors:
            self._set_health(self.card_warn, "#cc4444", "Fehler", errors[0] if errors else "Fehler")
        elif consistency_lines and any(line and "OK" not in line for line in consistency_lines):
            self._set_health(self.card_warn, "#d2a106", "Warnung", consistency_lines[0])
        else:
            self._set_health(self.card_warn, "#44cc44", "OK", "Alles gut")

        # Fehlertextfeld immer aktualisieren
        self.errors_text.config(state="normal")
        self.errors_text.delete("1.0", "end")
        if tb_full:
            self.errors_text.insert("1.0", "Fehler/Details:\n" + tb_full)
        else:
            db_last = self._hist_db[-1] if self._hist_db else {}
            info = [
                f"Letztes Update: {now.strftime('%H:%M:%S')}",
                f"DB-Alter: {self._format_age(db_last.get('age_s'))}",
                f"PV-Alter: {self._format_age(pv_last.get('age_s'))}",
                f"Heizung-Alter: {self._format_age(ht_last.get('age_s'))}",
                "",
                "=== Konsistenz ===",
            ]
            if consistency_lines:
                info.extend(consistency_lines)
            else:
                info.append("OK")

            def _fmt_hist_line(entry, kind: str):
                at = entry.get("at")
                age = entry.get("age_s")
                rec = entry.get("record")
                at_s = at.strftime("%H:%M:%S") if at else "--"
                age_s = self._format_age(age)
                ts = entry.get("source_ts") or "--"
                if rec is None:
                    return f"{at_s} age={age_s} ts={ts} (keine Daten)"
                if kind == "pv":
                    return (
                        f"{at_s} age={age_s} ts={ts} "
                        f"pv={self._fmt_num(rec.get(PV_POWER_KW),2)} grid={self._fmt_num(rec.get(GRID_POWER_KW),2)} "
                        f"batt={self._fmt_num(rec.get(BATTERY_POWER_KW),2)} soc={self._fmt_num(rec.get(BATTERY_SOC_PCT),1)}"
                    )
                return (
                    f"{at_s} age={age_s} ts={ts} "
                    f"kessel={self._fmt_num(rec.get(BMK_KESSEL_C),1)} ww={self._fmt_num(rec.get(BMK_WARMWASSER_C),1)} "
                    f"top={self._fmt_num(rec.get(BUF_TOP_C),1)} mid={self._fmt_num(rec.get(BUF_MID_C),1)} bot={self._fmt_num(rec.get(BUF_BOTTOM_C),1)}"
                )

            info.append("")
            info.append("=== Verlauf PV (letzte 5) ===")
            for entry in list(self._hist_pv)[-5:]:
                info.append(_fmt_hist_line(entry, "pv"))

            info.append("")
            info.append("=== Verlauf Heizung (letzte 5) ===")
            for entry in list(self._hist_heat)[-5:]:
                info.append(_fmt_hist_line(entry, "heat"))

            self.errors_text.insert("1.0", "\n".join(info))
        self.errors_text.config(state="disabled")

        self._schedule_update()

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
