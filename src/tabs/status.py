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
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.text = tk.Text(body, height=30, width=120, bg=COLOR_CARD, fg=COLOR_TEXT, font=("Consolas", 10))
        self.text.grid(row=0, column=0, sticky="nsew")
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
