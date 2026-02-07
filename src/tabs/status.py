
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
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
        self._build_layout()
        self.after_job = None
        self._schedule_update()

    def _build_layout(self):
        # Hauptlayout: vertikal PanedWindow (oben Health, unten Split)
        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned.pack(fill="both", expand=True)

        # Oben: Health Cards
        self.top_frame = tk.Frame(self, bg=COLOR_CARD)
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

        # Fehlertextfeld (immer sichtbar)
        self.errors_text = tk.Text(self.details_frame, font="TkFixedFont", bg="#181818", fg="#e0e0e0", height=10, wrap="none")
        self.errors_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.errors_text.config(state="disabled")

        # Snapshot-Labels
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
            tk.Label(self.snapshot_frame, text=label, anchor="w", bg=COLOR_CARD, fg=COLOR_TEXT).grid(row=row, column=0, sticky="w", padx=8, pady=2)
            val = tk.Label(self.snapshot_frame, text="--", anchor="e", bg=COLOR_CARD, fg=COLOR_TEXT)
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
        line1 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_TEXT)
        line1.grid(row=0, column=1, sticky="w")
        line2 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_SUBTEXT)
        line2.grid(row=1, column=1, sticky="w")
        card.lamp = lamp
        card.line1 = line1
        card.line2 = line2
        return card

    def _set_health(self, card, color, text, title, line1, line2):
        try:
            card.lamp.delete("all")
            card.lamp.create_oval(4, 4, 22, 22, fill=color, outline="#222")
            card.line1.config(text=text)
            card.line2.config(text=line1 if line1 else "")
        except Exception:
            pass

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
        self._set_health(self.card_db, "#9a9a9a", "--", "DB", "", "")
        self._set_health(self.card_pv, "#9a9a9a", "--", "PV", "", "")
        self._set_health(self.card_heat, "#9a9a9a", "--", "Heizung", "", "")
        self._set_health(self.card_warn, "#9a9a9a", "--", "OK", "", "")

        # DB
        try:
            latest_ts = self.datastore.get_latest_timestamp()
            db_dt = self._safe_iso_to_dt(latest_ts)
            db_age = self._age_seconds(now, db_dt)
            db_color, db_text = self._lamp_style(db_age)
            self._set_health(self.card_db, db_color, db_text, "DB", "", "")
        except Exception:
            err = "[DB] " + traceback.format_exc().splitlines()[-1]
            errors.append(err)
            tb_full += traceback.format_exc() + "\n"

        # PV
        try:
            pv_rec = self.datastore.get_last_fronius_record()
            if pv_rec:
                for key in [PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT]:
                    val = pv_rec.get(key)
                    self.snapshot_labels[key].config(text=f"{val:.2f}" if val is not None else "--")
            pv_dt = self._safe_iso_to_dt(pv_rec.get("timestamp") if pv_rec else None)
            pv_age = self._age_seconds(now, pv_dt)
            pv_color, pv_text = self._lamp_style(pv_age)
            self._set_health(self.card_pv, pv_color, pv_text, "PV", "", "")
        except Exception:
            err = "[PV] " + traceback.format_exc().splitlines()[-1]
            errors.append(err)
            tb_full += traceback.format_exc() + "\n"

        # Heating
        try:
            heat_rec = self.datastore.get_last_heating_record()
            if heat_rec:
                for key in [BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C]:
                    val = heat_rec.get(key)
                    self.snapshot_labels[key].config(text=f"{val:.1f}" if val is not None else "--")
            heat_dt = self._safe_iso_to_dt(heat_rec.get("timestamp") if heat_rec else None)
            heat_age = self._age_seconds(now, heat_dt)
            heat_color, heat_text = self._lamp_style(heat_age)
            self._set_health(self.card_heat, heat_color, heat_text, "Heizung", "", "")
        except Exception:
            err = "[Heizung] " + traceback.format_exc().splitlines()[-1]
            errors.append(err)
            tb_full += traceback.format_exc() + "\n"

        # Warnings
        if errors:
            self._set_health(self.card_warn, "#cc4444", "ERR", "Fehler", "\n".join(errors[:2]), "")
        else:
            self._set_health(self.card_warn, "#44cc44", "OK", "Alles gut", "", "")

        # Fehlertextfeld immer aktualisieren
        self.errors_text.config(state="normal")
        if tb_full:
            self.errors_text.delete("1.0", "end")
            self.errors_text.insert("1.0", "[StatusTab Errors]\n\n" + tb_full)
        else:
            self.errors_text.delete("1.0", "end")
            self.errors_text.insert("1.0", "Keine Fehler.")
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
    def _lamp_style(age_s, stale_s=60, future_s=60):
        if age_s is None:
            return "#9a9a9a", "--"
        if age_s < -future_s:
            return "#d2a106", "FUT"
        if age_s <= stale_s:
            return "#44cc44", "OK"
        return "#cc4444", "ALT"
