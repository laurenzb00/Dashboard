import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
from core.datastore import get_shared_datastore
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    emoji,
)
from ui.components.card import Card

class StatusTab:
    """Tab zur Live-Anzeige aller Rohdaten aus dem Datastore (PV, Heizung, etc.)."""
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, datastore=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None
        self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.tab_frame, text=emoji("📊 Status", "Status"))
        if datastore is not None:
            self.datastore = datastore
        else:
            self.datastore = get_shared_datastore()
        self._build_ui()
        self._schedule_update()

    def _build_ui(self):
        self.card = Card(self.tab_frame)
        self.card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.card.add_title("Live-Daten Status", icon="🔎")
        body = self.card.content()
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        # Drei Status-Lampen: DB, PV, Heizung
        self.lamp_frame = tk.Frame(body, bg=COLOR_CARD)
        self.lamp_frame.grid(row=0, column=0, sticky="ew", pady=(0,8))
        self.lamps = {}
        for key, label in [("db", "DB"), ("pv", "PV"), ("heating", "Heizung")]:
            lamp = tk.Canvas(self.lamp_frame, width=24, height=24, bg=COLOR_CARD, highlightthickness=0)
            lamp.pack(side=tk.LEFT, padx=(0,8))
            self.lamps[key] = lamp
            lbl = tk.Label(self.lamp_frame, text=f"{label}:", bg=COLOR_CARD, fg=COLOR_TEXT, font=("Consolas", 11))
            lbl.pack(side=tk.LEFT, padx=(0,4))
        self.lamp_ts_labels = {}
        for key in ["db", "pv", "heating"]:
            ts_lbl = tk.Label(self.lamp_frame, text="--", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Consolas", 10))
            ts_lbl.pack(side=tk.LEFT, padx=(0,12))
            self.lamp_ts_labels[key] = ts_lbl
        # Textbereich
        self.text = tk.Text(body, height=30, width=120, bg=COLOR_CARD, fg=COLOR_TEXT, font=("Consolas", 10))
        self.text.grid(row=1, column=0, sticky="nsew")
        self.text.insert(tk.END, "Status-Tab initialisiert...\n")
        self.text.config(state=tk.DISABLED)

    def _schedule_update(self):
        if not self.alive:
            return
        self._update_status()
        self._update_task_id = self.root.after(3000, self._schedule_update)

    def stop(self):
        self.alive = False
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass

    def _update_status(self):
        if not self.datastore:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # DB Status: show status based on most recent of PV or Heizung
        try:
            pv_rec = self.datastore.get_last_fronius_record()
            pv_ts = pv_rec["timestamp"] if pv_rec else None
        except Exception:
            pv_ts = None
        try:
            heat_rec = self.datastore.get_last_heating_record()
            heat_ts = heat_rec["timestamp"] if heat_rec else None
        except Exception:
            heat_ts = None
        db_color = "#cccccc"
        db_text = "--"
        db_ts_str = "--"
        now_dt = datetime.now()
        latest_dt = None
        if pv_ts and heat_ts:
            try:
                pv_dt = datetime.fromisoformat(pv_ts)
                heat_dt = datetime.fromisoformat(heat_ts)
                if pv_dt > heat_dt:
                    latest_dt = pv_dt
                else:
                    latest_dt = heat_dt
            except Exception:
                latest_dt = None
        elif pv_ts:
            try:
                latest_dt = datetime.fromisoformat(pv_ts)
            except Exception:
                latest_dt = None
        elif heat_ts:
            try:
                latest_dt = datetime.fromisoformat(heat_ts)
            except Exception:
                latest_dt = None
        if latest_dt:
            db_ts_str = latest_dt.strftime('%H:%M:%S')
            age = (now_dt - latest_dt).total_seconds()
            if age <= 60:
                db_color = "#44cc44"
                db_text = "OK"
            else:
                db_color = "#cc4444"
                db_text = "ALT"
        self.lamps["db"].delete("all")
        self.lamps["db"].create_oval(2,2,22,22, fill=db_color, outline="#888", width=2)
        self.lamps["db"].create_text(12,12, text=db_text, fill="#fff", font=("Consolas", 9, "bold"))
        self.lamp_ts_labels["db"].config(text=db_ts_str)

        # PV Status
        try:
            pv_rec = self.datastore.get_last_fronius_record()
            pv_ts = pv_rec["timestamp"] if pv_rec else None
        except Exception:
            pv_ts = None
        pv_color = "#44cc44"
        pv_text = "OK"
        pv_ts_str = "--"
        if pv_ts is None:
            pv_color = "#cccccc"
            pv_text = "--"
        else:
            try:
                pv_dt = datetime.fromisoformat(pv_ts)
                pv_ts_str = pv_dt.strftime("%H:%M:%S")
                delta = datetime.now() - pv_dt
                if delta.total_seconds() > 60:
                    pv_color = "#cc4444"
                    pv_text = "ALT"
            except Exception:
                pv_color = "#cccccc"
                pv_text = "--"
        self.lamps["pv"].delete("all")
        self.lamps["pv"].create_oval(2,2,22,22, fill=pv_color, outline="#888", width=2)
        self.lamps["pv"].create_text(12,12, text=pv_text, fill="#fff", font=("Consolas", 9, "bold"))
        self.lamp_ts_labels["pv"].config(text=pv_ts_str)

        # Heizung Status
        try:
            heat_rec = self.datastore.get_last_heating_record()
            heat_ts = heat_rec["timestamp"] if heat_rec else None
        except Exception:
            heat_ts = None
        heat_color = "#44cc44"
        heat_text = "OK"
        heat_ts_str = "--"
        if heat_ts is None:
            heat_color = "#cccccc"
            heat_text = "--"
        else:
            try:
                heat_dt = datetime.fromisoformat(heat_ts)
                heat_ts_str = heat_dt.strftime("%H:%M:%S")
                delta = datetime.now() - heat_dt
                if delta.total_seconds() > 60:
                    heat_color = "#cc4444"
                    heat_text = "ALT"
            except Exception:
                heat_color = "#cccccc"
                heat_text = "--"
        self.lamps["heating"].delete("all")
        self.lamps["heating"].create_oval(2,2,22,22, fill=heat_color, outline="#888", width=2)
        self.lamps["heating"].create_text(12,12, text=heat_text, fill="#fff", font=("Consolas", 9, "bold"))
        self.lamp_ts_labels["heating"].config(text=heat_ts_str)
        # Textbereich wie bisher
        try:
            pv = self.datastore.get_recent_fronius(hours=24, limit=10)
            heating = self.datastore.get_recent_heating(hours=24, limit=10)
        except Exception as exc:
            pv = []
            heating = []
            error = f"[ERROR] Datastore-Fehler: {exc}"
        self.text.config(state=tk.NORMAL)
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, f"[StatusTab] {now}\n\n")
        self.text.insert(tk.END, f"PV-Daten (letzte 10):\n")
        if pv:
            self.text.insert(tk.END, f"Keys: {list(pv[0].keys())}\n")
            for entry in pv:
                self.text.insert(tk.END, f"{entry}\n")
        else:
            self.text.insert(tk.END, "Keine PV-Daten gefunden!\n")
        self.text.insert(tk.END, "\nHeizungsdaten (letzte 10):\n")
        if heating:
            self.text.insert(tk.END, f"Keys: {list(heating[0].keys())}\n")
            for entry in heating:
                self.text.insert(tk.END, f"{entry}\n")
        else:
            self.text.insert(tk.END, "Keine Heizungsdaten gefunden!\n")
        self.text.config(state=tk.DISABLED)
