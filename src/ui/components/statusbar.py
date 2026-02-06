import tkinter as tk
from tkinter import ttk
from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_SUBTEXT, COLOR_WARNING, COLOR_DANGER
from ui.components.rounded import RoundedFrame


class StatusBar(tk.Frame):
        def set_status(self, text: str):
            """Set the status label text (API-consistent)."""
            self.status_label.config(text=text)
    """32px Statusbar mit Zeitstempel, Fenster- und Exit-Button."""

    def __init__(self, parent: tk.Widget, on_exit=None, on_toggle_fullscreen=None):
        super().__init__(parent, height=32, bg=COLOR_HEADER)
        self.pack_propagate(False)

        rounded = RoundedFrame(self, bg=COLOR_CARD, border=None, radius=18, padding=0)
        rounded.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        inner = rounded.content()

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)
        inner.grid_columnconfigure(2, weight=1)
        inner.grid_columnconfigure(3, weight=0)
        inner.grid_columnconfigure(4, weight=0)
        inner.grid_columnconfigure(5, weight=0)

        self.status_label = tk.Label(inner, text="Updated --:--:--", fg=COLOR_TEXT, bg=COLOR_CARD, font=("Segoe UI", 11))
        self.status_label.grid(row=0, column=0, sticky="w", padx=12)

        self.center_frame = tk.Frame(inner, bg=COLOR_CARD)
        self.center_frame.grid(row=0, column=2, sticky="nsew")

        self.center_label = tk.Label(self.center_frame, text="", fg=COLOR_SUBTEXT, bg=COLOR_CARD, font=("Segoe UI", 11))
        self.center_label.pack(side=tk.LEFT, padx=(6, 8))

        # Drei Lampen für DB, PV, Heizung
        self.lamps = {}
        self.lamp_ts_labels = {}
        for key, label in [("db", "DB"), ("pv", "PV"), ("heating", "Heizung")]:
            lamp = tk.Canvas(self.center_frame, width=18, height=18, bg=COLOR_CARD, highlightthickness=0)
            lamp.pack(side=tk.LEFT, padx=(0,4))
            self.lamps[key] = lamp
            lbl = tk.Label(self.center_frame, text=f"{label}:", bg=COLOR_CARD, fg=COLOR_TEXT, font=("Segoe UI", 9))
            lbl.pack(side=tk.LEFT, padx=(0,2))
            ts_lbl = tk.Label(self.center_frame, text="--", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=("Segoe UI", 8))
            ts_lbl.pack(side=tk.LEFT, padx=(0,6))
            self.lamp_ts_labels[key] = ts_lbl

        # Sparkline Canvas (once)
        self.spark_canvas = tk.Canvas(self.center_frame, width=110, height=18, bg=COLOR_CARD, highlightthickness=0)
        self.spark_canvas.pack(side=tk.LEFT, padx=(0, 6))

        # Window and Exit Buttons (once)
        from ui.components.rounded_button import RoundedButton
        self.window_btn = RoundedButton(
            inner, text="⊡", command=on_toggle_fullscreen,
            bg=COLOR_BORDER, fg=COLOR_TEXT,
            radius=10, padding=(10, 4), font_size=11, width=44, height=26
        )
        self.window_btn.grid(row=0, column=4, sticky="e", padx=(6, 4), pady=0)

        self.exit_btn = RoundedButton(
            inner, text="⏻", command=on_exit,
            bg=COLOR_DANGER, fg="#fff",
            radius=10, padding=(10, 4), font_size=11, width=44, height=26
        )
        self.exit_btn.grid(row=0, column=5, sticky="e", padx=(4, 8), pady=0)
    def update_lamps(self, db_status, pv_status, heating_status):
        """Update all three lamps and timestamps. Status: {color, text, ts}"""
        for key, status in zip(["db", "pv", "heating"], [db_status, pv_status, heating_status]):
            color, text, ts_str = status
            self.lamps[key].delete("all")
            self.lamps[key].create_oval(2,2,16,16, fill=color, outline="#888", width=1)
            self.lamps[key].create_text(9,9, text=text, fill="#fff", font=("Segoe UI", 7, "bold"))
            self.lamp_ts_labels[key].config(text=ts_str)

    def update_status(self, text: str):
        self.status_label.config(text=text)

    def update_center(self, text: str):
        self.center_label.config(text=text)

    def update_data_freshness(self, text: str, alert: bool = False):
        self.fresh_label.config(text=text, fg=COLOR_WARNING if alert else COLOR_SUBTEXT)

    def update_sparkline(self, values: list[float], color: str):
        self.spark_canvas.delete("all")
        if not values or len(values) < 2:
            return
        w = int(self.spark_canvas.winfo_width() or 110)
        h = int(self.spark_canvas.winfo_height() or 16)
        vmin = min(values)
        vmax = max(values)
        if vmax == vmin:
            vmax += 1.0
        pts = []
        for i, v in enumerate(values):
            x = int(i * (w - 2) / (len(values) - 1)) + 1
            y = int((1 - (v - vmin) / (vmax - vmin)) * (h - 2)) + 1
            pts.extend([x, y])
        self.spark_canvas.create_line(*pts, fill=color, width=1)
