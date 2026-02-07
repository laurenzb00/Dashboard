
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

    @staticmethod
    def _fmt_age(age):
        if age is None:
            return "--"
        if age < 0:
            return f"-{abs(int(age))}s"
        return f"{int(age)}s"

    @staticmethod
    def _kv(parent: tk.Widget, row: int, key: str, value: str):
        tk.Label(parent, text=key, bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=2
        )
        tk.Label(parent, text=value, bg=COLOR_CARD, fg=COLOR_TEXT, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=1, sticky="w", pady=2
        )

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.datastore = get_shared_datastore()
        self._last_err_db = None
        self._last_err_pv = None
        self._last_err_heat = None
        self._build_layout()
        self.after_job = None
        self._schedule_update()
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
        self.top_frame.grid_columnconfigure((0,1,2,3), weight=1)
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
        self.snapshot_frame.grid_columnconfigure(0, weight=1)

        # --- Cards content (minimal + useful) ---
        # DB card
        db_line1 = f"{db_text}  •  last: {(db_dt.strftime('%H:%M:%S') if db_dt else '--')}"
        db_line2 = f"age: {self._fmt_age(db_age)}"
        if self._last_err_db:
            db_line3 = f"err: {self._last_err_db}"
        else:
            db_line3 = ""
        self._set_health(self.card_db, db_color, db_text, db_line1, db_line2, db_line3)

        # PV card: show key values if present
        pv_line1 = f"{pv_text}  •  last: {(pv_dt.strftime('%H:%M:%S') if pv_dt else '--')}"
        pv_line2 = f"age: {self._fmt_age(pv_age)}"
        pv_line3 = ""
        if pv_rec:
            # keys are from schema in your datastore (pv, grid, batt, soc) in recent list,
            # but last record uses schema keys; keep it generic:
            # show the 2 most useful values when present
            pv_val = pv_rec.get("pv") or pv_rec.get("pv_power") or pv_rec.get("pv_power_kw") or pv_rec.get("PV_POWER_KW")
            soc_val = pv_rec.get("soc") or pv_rec.get("BATTERY_SOC_PCT") or pv_rec.get("soc_pct")
            pieces = []
            if pv_val is not None:
                pieces.append(f"PV: {pv_val}")
            if soc_val is not None:
                pieces.append(f"SOC: {soc_val}")
            pv_line3 = "   ".join(pieces) if pieces else ""
        if self._last_err_pv:
            pv_line3 = f"err: {self._last_err_pv}"
        self._set_health(self.card_pv, pv_color, pv_text, pv_line1, pv_line2, pv_line3)

        # Heating card
        ht_line1 = f"{ht_text}  •  last: {(heat_dt.strftime('%H:%M:%S') if heat_dt else '--')}"
        ht_line2 = f"age: {self._fmt_age(heat_age)}"
        ht_line3 = ""
        if heat_rec:
            # in your datastore code you return keys: BMK_KESSEL_C, BMK_WARMWASSER_C, 'outdoor', BUF_TOP/MID/BOTTOM
            # but we keep it resilient:
            kessel = heat_rec.get("kessel") or heat_rec.get("kesseltemp") or heat_rec.get("BMK_KESSEL_C")
            warm = heat_rec.get("warm") or heat_rec.get("warmwasser") or heat_rec.get("BMK_WARMWASSER_C")
            out = heat_rec.get("outdoor") or heat_rec.get("außentemp") or heat_rec.get("aussentemp")
            pieces = []
            if kessel is not None:
                pieces.append(f"Kessel: {kessel}")
            if warm is not None:
                pieces.append(f"WW: {warm}")
            if out is not None:
                pieces.append(f"Außen: {out}")
            ht_line3 = "   ".join(pieces) if pieces else ""
        if self._last_err_heat:
            ht_line3 = f"err: {self._last_err_heat}"
        self._set_health(self.card_heat, ht_color, ht_text, ht_line1, ht_line2, ht_line3)

        # Warnings card (future/stale/missing)
        warnings = []
        if db_age is None:
            warnings.append("DB: keine Daten / timestamp fehlt")
        else:
            if db_age < -60:
                warnings.append(f"DB: timestamp in Zukunft ({self._fmt_age(db_age)})")
            elif db_age > 300:
                warnings.append(f"DB: stale ({self._fmt_age(db_age)})")

        if pv_age is not None:
            if pv_age < -60:
                warnings.append(f"PV: timestamp in Zukunft ({self._fmt_age(pv_age)})")
            elif pv_age > 300:
                warnings.append(f"PV: stale ({self._fmt_age(pv_age)})")
        elif pv_rec is None:
            warnings.append("PV: kein letzter Datensatz")

        if heat_age is not None:
            if heat_age < -60:
                warnings.append(f"Heizung: timestamp in Zukunft ({self._fmt_age(heat_age)})")
            elif heat_age > 300:
                warnings.append(f"Heizung: stale ({self._fmt_age(heat_age)})")
        elif heat_rec is None:
            warnings.append("Heizung: kein letzter Datensatz")

        # decide warning lamp
        if not warnings:
            w_color, w_text = "#44cc44", "OK"
            w1, w2, w3 = "OK  •  keine Warnungen", "—", ""
        else:
            # amber if only warnings; red if errors exist
            has_err = any([self._last_err_db, self._last_err_pv, self._last_err_heat])
            w_color, w_text = ("#cc4444", "ERR") if has_err else ("#d2a106", "WARN")
            w1 = f"{w_text}  •  {len(warnings)}"
            w2 = warnings[0]
            w3 = warnings[1] if len(warnings) > 1 else ""
        self._set_health(self.card_warn, w_color, w_text, w1, w2, w3)

        # --- Snapshot (compact, no spam) ---
        for w in self.snapshot_frame.winfo_children():
            w.destroy()

        left = tk.Frame(self.snapshot_frame, bg=COLOR_CARD)
        right = tk.Frame(self.snapshot_frame, bg=COLOR_CARD)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right.grid(row=0, column=1, sticky="nsew")

        # PV snapshot
        tk.Label(left, text="PV", bg=COLOR_CARD, fg=COLOR_PRIMARY, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        pv_ts_str = pv_dt.strftime("%Y-%m-%d %H:%M:%S") if pv_dt else "--"
        self._kv(left, 1, "timestamp", pv_ts_str)
        self._kv(left, 2, "age", self._fmt_age(pv_age))
        if pv_rec:
            # show a few common keys if present
            self._kv(left, 3, "pv", str(pv_rec.get("pv", pv_rec.get("pv_power", "--"))))
            self._kv(left, 4, "grid", str(pv_rec.get("grid", pv_rec.get("grid_power", "--"))))
            self._kv(left, 5, "batt", str(pv_rec.get("batt", pv_rec.get("batt_power", "--"))))
            self._kv(left, 6, "soc", str(pv_rec.get("soc", pv_rec.get("soc_pct", pv_rec.get("BATTERY_SOC_PCT", "--")))))
        else:
            self._kv(left, 3, "data", "--")

        # Heating snapshot
        tk.Label(right, text="Heizung", bg=COLOR_CARD, fg=COLOR_PRIMARY, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        heat_ts_str = heat_dt.strftime("%Y-%m-%d %H:%M:%S") if heat_dt else "--"
        self._kv(right, 1, "timestamp", heat_ts_str)
        self._kv(right, 2, "age", self._fmt_age(heat_age))
        if heat_rec:
            self._kv(right, 3, "kessel", str(heat_rec.get("kessel", heat_rec.get("kesseltemp", "--"))))
            self._kv(right, 4, "warmwasser", str(heat_rec.get("warm", heat_rec.get("warmwasser", "--"))))
            self._kv(right, 5, "outdoor", str(heat_rec.get("outdoor", heat_rec.get("außentemp", heat_rec.get("aussentemp", "--")))))
            self._kv(right, 6, "puffer_top", str(heat_rec.get("top", heat_rec.get("puffer_top", "--"))))
            self._kv(right, 7, "puffer_mid", str(heat_rec.get("mid", heat_rec.get("puffer_mid", "--"))))
            self._kv(right, 8, "puffer_bot", str(heat_rec.get("bot", heat_rec.get("puffer_bot", "--"))))
        else:
            self._kv(right, 3, "data", "--")

        # --- Details tabs: raw last10 + errors ---
        # (calls already exist; we just display)
        try:
            pv_last10 = self.datastore.get_recent_fronius(hours=24, limit=10)
        except Exception as exc:
            pv_last10 = []
            self._last_err_pv = self._last_err_pv or f"get_recent_fronius() failed: {exc}"

        try:
            heat_last10 = self.datastore.get_recent_heating(hours=24, limit=10)
        except Exception as exc:
            heat_last10 = []
            self._last_err_heat = self._last_err_heat or f"get_recent_heating() failed: {exc}"

        raw_pv_text = "[PV last 10]\n"
        if pv_last10:
            raw_pv_text += f"keys: {list(pv_last10[0].keys())}\n\n"
            raw_pv_text += "\n".join(str(x) for x in pv_last10)
        else:
            raw_pv_text += "Keine PV-Daten gefunden.\n"
        self._set_text(self.raw_pv, raw_pv_text)

        raw_heat_text = "[Heating last 10]\n"
        if heat_last10:
            raw_heat_text += f"keys: {list(heat_last10[0].keys())}\n\n"
            raw_heat_text += "\n".join(str(x) for x in heat_last10)
        else:
            raw_heat_text += "Keine Heizungsdaten gefunden.\n"
        self._set_text(self.raw_heat, raw_heat_text)

        err_lines = []
        if self._last_err_db:
            err_lines.append(f"[DB] {self._last_err_db}")
        if self._last_err_pv:
            err_lines.append(f"[PV] {self._last_err_pv}")
        if self._last_err_heat:
            err_lines.append(f"[HEAT] {self._last_err_heat}")
        if warnings:
            err_lines.append("\n[WARNINGS]")
            err_lines.extend(f"- {w}" for w in warnings)

        self._set_text(self.raw_err, "\n".join(err_lines) if err_lines else "Keine Errors.\n")
