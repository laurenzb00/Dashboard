import tkinter as tk
from tkinter import ttk
from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_SUBTEXT, COLOR_WARNING, COLOR_DANGER
from ui.components.rounded import RoundedFrame


class StatusBar(tk.Frame):
    """32px Statusbar mit Zeitstempel, Fenster- und Exit-Button."""

    def __init__(self, parent: tk.Widget, on_exit=None, on_toggle_fullscreen=None):
        super().__init__(parent, height=20, bg=COLOR_HEADER)
        self.pack_propagate(False)

        rounded = RoundedFrame(self, bg=COLOR_CARD, border=None, radius=18, padding=0)
        rounded.pack(fill=tk.BOTH, expand=True, padx=6, pady=3)
        inner = rounded.content()

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(2, weight=0)
        inner.grid_columnconfigure(3, weight=0)

        self.status_label = tk.Label(inner, text="Updated --:--:--", fg=COLOR_TEXT, bg=COLOR_CARD, font=("Segoe UI", 10))
        self.status_label.grid(row=0, column=0, sticky="w", padx=12)

        self.center_frame = tk.Frame(inner, bg=COLOR_CARD)
        self.center_frame.grid(row=0, column=1, sticky="nsew")

        self.center_label = tk.Label(self.center_frame, text="", fg=COLOR_SUBTEXT, bg=COLOR_CARD, font=("Segoe UI", 10))
        self.center_label.pack(side=tk.LEFT, padx=(6, 8))

        self.fresh_label = tk.Label(self.center_frame, text="Daten: --", fg=COLOR_SUBTEXT, bg=COLOR_CARD, font=("Segoe UI", 9))
        self.fresh_label.pack(side=tk.LEFT, padx=(0, 8))

        self.spark_canvas = tk.Canvas(self.center_frame, width=110, height=16, bg=COLOR_CARD, highlightthickness=0)
        self.spark_canvas.pack(side=tk.LEFT, padx=(0, 6))

        from ui.components.rounded_button import RoundedButton
        self.window_btn = RoundedButton(
            inner, text="⊡", command=on_toggle_fullscreen,
            bg=COLOR_BORDER, fg=COLOR_TEXT,
            radius=10, padding=(10, 3), font_size=11, width=44, height=24
        )
        self.window_btn.grid(row=0, column=2, sticky="e", padx=(6, 4), pady=0)

        # Kleiner Exit-Button
        self.exit_btn = RoundedButton(
            inner, text="⏻", command=on_exit,
            bg=COLOR_DANGER, fg="#fff",
            radius=10, padding=(10, 3), font_size=11, width=44, height=24
        )
        self.exit_btn.grid(row=0, column=3, sticky="e", padx=(4, 8), pady=0)

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
