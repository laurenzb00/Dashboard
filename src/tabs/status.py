import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
import traceback

from core.datastore import get_shared_datastore
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    emoji,
)
from ui.components.card import Card


# --- kleine Helpers (UI-only, keine neue Datenlogik) ---

def _safe_iso_to_dt(ts: str | None) -> datetime | None:
    """Parst ISO/SQL-ähnliche Strings robust. Gibt naive datetime zurück (wie bisher im Projekt)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _age_seconds(now: datetime, dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (now - dt).total_seconds()


def _fmt_age(age_s: float | None) -> str:
    if age_s is None:
        return "--"
    sign = "-" if age_s < 0 else ""
    s = abs(age_s)
    if s < 60:
        return f"{sign}{int(s)}s"
    if s < 3600:
        return f"{sign}{int(s // 60)}m"
    return f"{sign}{int(s // 3600)}h"


def _lamp_style(age_s: float | None, stale_s: int = 60, future_s: int = 60):
    """
    Returns: (color, text)
    - OK (green): 0..stale_s
    - ALT (red): > stale_s
    - FUT (amber): < -future_s  (timestamp in the future)
    - -- (grey): no data
    """
    if age_s is None:
        return "#9a9a9a", "--"
    if age_s < -future_s:
        return "#d2a106", "FUT"
    if age_s <= stale_s:
        return "#44cc44", "OK"
    return "#cc4444", "ALT"


def _kv(parent: tk.Widget, row: int, key: str, value: str):
    """Key/value row helper."""
    tk.Label(parent, text=key, bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).grid(
        row=row, column=0, sticky="w", padx=(0, 10), pady=2
    )
    tk.Label(parent, text=value, bg=COLOR_CARD, fg=COLOR_TEXT, font=("Segoe UI", 10, "bold")).grid(
        row=row, column=1, sticky="w", pady=2
    )


class StatusTab:
    """
    StatusTab (neu):
    - Keine Systemwerte (dafür eigener System-Tab)
    - Nutzt bestehende Datastore-Calls:
        get_last_fronius_record, get_last_heating_record, get_recent_fronius, get_recent_heating, get_latest_timestamp
    - UI: 4 Health-Cards + Snapshot + Details
    """

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, datastore=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None

        self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
        # NOTE: Reihenfolge im Notebook entspricht Reihenfolge der Tab-Erstellung!
        self.notebook.add(self.tab_frame, text=emoji("📊 Status", "Status"))

        self.datastore = datastore if datastore is not None else get_shared_datastore()

        # caches für UI
        self._last_err_pv: str | None = None
        self._last_err_heat: str | None = None
        self._last_err_db: str | None = None

        self._build_ui()
        self._schedule_update()

    def stop(self):
        self.alive = False
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass

    # ---------- UI ----------

    def _build_ui(self):
        container = Card(self.tab_frame)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        container.add_title("Status", icon="📊")

        body = container.content()
        body.configure(bg=COLOR_CARD)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        # Top: Health cards row
        top = tk.Frame(body, bg=COLOR_CARD)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for i in range(4):
            top.grid_columnconfigure(i, weight=1, uniform="statuscards")

        self.card_db = self._make_health_card(top, 0, "DB", "🗄️")
        self.card_pv = self._make_health_card(top, 1, "PV / Fronius", "☀️")
        self.card_heat = self._make_health_card(top, 2, "Heizung / BMK", "🔥")
        self.card_warn = self._make_health_card(top, 3, "Warnings", "⚠️")


        # Middle: Snapshot + Debug Text + Details notebook
        mid = tk.Frame(body, bg=COLOR_CARD)
        mid.grid(row=1, column=0, sticky="nsew")
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(1, weight=1)
        mid.grid_rowconfigure(2, weight=1)

        snap_card = Card(mid)
        snap_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        snap_card.add_title("Current Snapshot", icon="🧾")
        self.snapshot_frame = snap_card.content()
        self.snapshot_frame.configure(bg=COLOR_CARD)
        self.snapshot_frame.grid_columnconfigure(0, weight=1)
        self.snapshot_frame.grid_columnconfigure(1, weight=1)

        # Debug-Textfeld (immer sichtbar, monospace, expandiert)
        self.debug_text = tk.Text(mid, font=("Consolas", 11), bg="#181818", fg="#e0e0e0", height=8, wrap="none")
        self.debug_text.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 10))
        self.debug_text.config(state="disabled")

        # Details area
        details_card = Card(mid)
        details_card.grid(row=2, column=0, sticky="nsew")
        details_card.add_title("Details", icon="🧩")
        details_body = details_card.content()
        details_body.configure(bg=COLOR_CARD)
        details_body.grid_rowconfigure(0, weight=1)
        details_body.grid_columnconfigure(0, weight=1)

        self.details_nb = ttk.Notebook(details_body)
        self.details_nb.grid(row=0, column=0, sticky="nsew")

        self.raw_pv = self._make_mono_text_tab("Raw PV (last 10)")
        self.raw_heat = self._make_mono_text_tab("Raw Heating (last 10)")
        self.raw_err = self._make_mono_text_tab("Errors")

    def _make_health_card(self, parent: tk.Widget, col: int, title: str, icon: str):
        card = Card(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0, 10) if col < 3 else 0)
        card.add_title(title, icon=icon)
        content = card.content()
        content.configure(bg=COLOR_CARD)
        content.grid_columnconfigure(1, weight=1)

        lamp = tk.Canvas(content, width=26, height=26, bg=COLOR_CARD, highlightthickness=0)
        lamp.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10), pady=2)

        line1 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_TEXT, font=("Segoe UI", 10, "bold"))
        line1.grid(row=0, column=1, sticky="w")

        line2 = tk.Label(content, text="--", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 9))
        line2.grid(row=1, column=1, sticky="w")

        # optional: extra line (small)
        line3 = tk.Label(content, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 9))
        line3.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        return {"card": card, "lamp": lamp, "l1": line1, "l2": line2, "l3": line3}

    def _set_health(self, card_dict, status_color: str, status_text: str, line1: str, line2: str, line3: str = ""):
        lamp = card_dict["lamp"]
        lamp.delete("all")
        lamp.create_oval(2, 2, 24, 24, fill=status_color, outline="#888", width=2)
        lamp.create_text(13, 13, text=status_text, fill="#fff", font=("Consolas", 9, "bold"))
        card_dict["l1"].configure(text=line1)
        card_dict["l2"].configure(text=line2)
        card_dict["l3"].configure(text=line3)

    def _make_mono_text_tab(self, title: str) -> tk.Text:
        frame = tk.Frame(self.details_nb, bg=COLOR_CARD)
        self.details_nb.add(frame, text=title)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        txt = tk.Text(frame, bg=COLOR_CARD, fg=COLOR_TEXT, font=("Consolas", 10), relief="flat")
        txt.grid(row=0, column=0, sticky="nsew")
        txt.configure(state=tk.DISABLED)
        return txt

    def _set_text(self, widget: tk.Text, text: str):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state=tk.DISABLED)

    # ---------- Update loop ----------

    def _schedule_update(self):
        if not self.alive:
            return
        self._update_status()
        self._update_task_id = self.root.after(3000, self._schedule_update)

    def _update_status(self):
        try:
            if not self.datastore:
                return

            now = datetime.now()

            # --- fetch last records (existing API) ---
            pv_rec = None
            heat_rec = None
            latest_ts = None

            try:
                latest_ts = self.datastore.get_latest_timestamp()
            except Exception as exc:
                self._last_err_db = f"get_latest_timestamp() failed: {exc}"

            try:
                pv_rec = self.datastore.get_last_fronius_record()
                self._last_err_pv = None
            except Exception as exc:
                self._last_err_pv = f"get_last_fronius_record() failed: {exc}"

            try:
                heat_rec = self.datastore.get_last_heating_record()
                self._last_err_heat = None
            except Exception as exc:
                self._last_err_heat = f"get_last_heating_record() failed: {exc}"

            # parse timestamps
            pv_dt = _safe_iso_to_dt(pv_rec.get("timestamp") if pv_rec else None)
            heat_dt = _safe_iso_to_dt(heat_rec.get("timestamp") if heat_rec else None)
            db_dt = _safe_iso_to_dt(latest_ts)

            pv_age = _age_seconds(now, pv_dt)
            heat_age = _age_seconds(now, heat_dt)
            db_age = _age_seconds(now, db_dt)

            # lamps
            db_color, db_text = _lamp_style(db_age, stale_s=60, future_s=60)
            pv_color, pv_text = _lamp_style(pv_age, stale_s=60, future_s=60)
            # ...existing code...
        except Exception:
            try:
                tb = traceback.format_exc()
                if hasattr(self, 'raw_err') and hasattr(self, '_set_text'):
                    self._set_text(self.raw_err, "[StatusTab CRASH]\n\n" + tb)
                if hasattr(self, 'debug_text'):
                    self.debug_text.config(state="normal")
                    self.debug_text.delete("1.0", "end")
                    self.debug_text.insert("1.0", "[StatusTab CRASH]\n\n" + tb)
                    self.debug_text.config(state="disabled")
                if hasattr(self, 'card_warn') and hasattr(self, '_set_health'):
                    self._set_health(self.card_warn, "#cc4444", "ERR", "StatusTab crashed", "siehe Errors-Tab", "")
            except Exception:
                pass
        ht_color, ht_text = _lamp_style(heat_age, stale_s=60, future_s=60)

        # --- Cards content (minimal + useful) ---
        # DB card
        db_line1 = f"{db_text}  •  last: {(db_dt.strftime('%H:%M:%S') if db_dt else '--')}"
        db_line2 = f"age: {_fmt_age(db_age)}"
        if self._last_err_db:
            db_line3 = f"err: {self._last_err_db}"
        else:
            db_line3 = ""
        self._set_health(self.card_db, db_color, db_text, db_line1, db_line2, db_line3)

        # PV card: show key values if present
        pv_line1 = f"{pv_text}  •  last: {(pv_dt.strftime('%H:%M:%S') if pv_dt else '--')}"
        pv_line2 = f"age: {_fmt_age(pv_age)}"
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
        ht_line2 = f"age: {_fmt_age(heat_age)}"
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
                warnings.append(f"DB: timestamp in Zukunft ({_fmt_age(db_age)})")
            elif db_age > 300:
                warnings.append(f"DB: stale ({_fmt_age(db_age)})")

        if pv_age is not None:
            if pv_age < -60:
                warnings.append(f"PV: timestamp in Zukunft ({_fmt_age(pv_age)})")
            elif pv_age > 300:
                warnings.append(f"PV: stale ({_fmt_age(pv_age)})")
        elif pv_rec is None:
            warnings.append("PV: kein letzter Datensatz")

        if heat_age is not None:
            if heat_age < -60:
                warnings.append(f"Heizung: timestamp in Zukunft ({_fmt_age(heat_age)})")
            elif heat_age > 300:
                warnings.append(f"Heizung: stale ({_fmt_age(heat_age)})")
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
        _kv(left, 1, "timestamp", pv_ts_str)
        _kv(left, 2, "age", _fmt_age(pv_age))
        if pv_rec:
            # show a few common keys if present
            _kv(left, 3, "pv", str(pv_rec.get("pv", pv_rec.get("pv_power", "--"))))
            _kv(left, 4, "grid", str(pv_rec.get("grid", pv_rec.get("grid_power", "--"))))
            _kv(left, 5, "batt", str(pv_rec.get("batt", pv_rec.get("batt_power", "--"))))
            _kv(left, 6, "soc", str(pv_rec.get("soc", pv_rec.get("soc_pct", pv_rec.get("BATTERY_SOC_PCT", "--")))))
        else:
            _kv(left, 3, "data", "--")

        # Heating snapshot
        tk.Label(right, text="Heizung", bg=COLOR_CARD, fg=COLOR_PRIMARY, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        heat_ts_str = heat_dt.strftime("%Y-%m-%d %H:%M:%S") if heat_dt else "--"
        _kv(right, 1, "timestamp", heat_ts_str)
        _kv(right, 2, "age", _fmt_age(heat_age))
        if heat_rec:
            _kv(right, 3, "kessel", str(heat_rec.get("kessel", heat_rec.get("kesseltemp", "--"))))
            _kv(right, 4, "warmwasser", str(heat_rec.get("warm", heat_rec.get("warmwasser", "--"))))
            _kv(right, 5, "outdoor", str(heat_rec.get("outdoor", heat_rec.get("außentemp", heat_rec.get("aussentemp", "--")))))
            _kv(right, 6, "puffer_top", str(heat_rec.get("top", heat_rec.get("puffer_top", "--"))))
            _kv(right, 7, "puffer_mid", str(heat_rec.get("mid", heat_rec.get("puffer_mid", "--"))))
            _kv(right, 8, "puffer_bot", str(heat_rec.get("bot", heat_rec.get("puffer_bot", "--"))))
        else:
            _kv(right, 3, "data", "--")

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
