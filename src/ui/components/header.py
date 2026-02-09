import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from ui.styles import (
    COLOR_CARD,
    COLOR_HEADER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    COLOR_BORDER,
    COLOR_WARNING,
)
from ui.components.rounded import RoundedFrame

# Import für moderne Buttons
from ui.components.rounded_button import RoundedButton


class HeaderBar(tk.Frame):
    """Schlanker Header mit Datum, Uhrzeit, Toggles und Exit."""


    def __init__(self, parent: tk.Widget, datastore=None, on_toggle_a=None, on_toggle_b=None, on_exit=None):
        super().__init__(parent, height=36, bg=COLOR_HEADER)
        self.pack_propagate(False)
        self.datastore = datastore

        # Rounded container
        rounded = RoundedFrame(self, bg=COLOR_CARD, border=None, radius=16, padding=0)
        rounded.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        inner = rounded.content()

        inner.grid_columnconfigure(0, weight=1, minsize=140, uniform="hdr")
        inner.grid_columnconfigure(1, weight=2, uniform="hdr")
        inner.grid_columnconfigure(2, weight=1, minsize=140, uniform="hdr")

        # Links: Datum
        left = tk.Frame(inner, bg=COLOR_CARD)
        left.grid(row=0, column=0, sticky="nsew", padx=10, pady=6)
        self.date_label = tk.Label(left, text="--", font=("Segoe UI", 14, "bold"), fg=COLOR_TEXT, bg=COLOR_CARD)
        self.date_label.pack(anchor="w")
        self.weekday_label = tk.Label(left, text="", font=("Segoe UI", 11), fg=COLOR_SUBTEXT, bg=COLOR_CARD)
        self.weekday_label.pack(anchor="w", pady=(2, 0))

        # Mitte: Uhrzeit + Buttons (zwischen Uhrzeit und Außentemp)
        center = tk.Frame(inner, bg=COLOR_CARD)
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_columnconfigure(0, weight=1)
        center.grid_columnconfigure(1, weight=0)
        center.grid_columnconfigure(2, weight=0)

        self.clock_label = tk.Label(center, text="--:--", font=("Segoe UI", 36, "bold"), fg=COLOR_PRIMARY, bg=COLOR_CARD)
        self.clock_label.grid(row=0, column=0, sticky="nsew")

        # Rechts: Außentemp
        right = tk.Frame(inner, bg=COLOR_CARD)
        right.grid(row=0, column=2, sticky="ne", padx=4, pady=2)

        self.out_temp_label = tk.Label(right, text="--.- °C", font=("Segoe UI", 14, "bold"), fg=COLOR_WARNING, bg=COLOR_CARD)
        self.out_temp_label.pack(anchor="ne")
        self.out_temp_time = tk.Label(right, text="", font=("Segoe UI", 9), fg=COLOR_SUBTEXT, bg=COLOR_CARD)
        self.out_temp_time.pack(anchor="ne", pady=(0, 4))

        btn_row = tk.Frame(center, bg=COLOR_CARD, height=36)
        btn_row.grid(row=0, column=2, sticky="n", padx=(8, 8), pady=0)
        btn_row.grid_propagate(False)

        tk.Label(
            btn_row,
            text="💡 Licht",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.btn_a = RoundedButton(
            btn_row, text="An", command=on_toggle_a,
            bg=COLOR_PRIMARY, fg="#fff",
            radius=12, padding=(12, 4), font_size=12, width=64, height=30
        )
        self.btn_a.pack(side=tk.LEFT, padx=4, pady=0)
        self.btn_b = RoundedButton(
            btn_row, text="Aus", command=on_toggle_b,
            bg=COLOR_BORDER, fg=COLOR_TEXT,
            radius=12, padding=(12, 4), font_size=12, width=64, height=30
        )
        self.btn_b.pack(side=tk.LEFT, padx=4, pady=0)

        # Outdoor temp is updated via MainApp.update_header(...), single source of truth.

    def update_header(self, date_text: str, weekday: str, time_text: str, out_temp: str):
        self.date_label.config(text=date_text)
        self.weekday_label.config(text=weekday)
        self.clock_label.config(text=time_text)
        self.out_temp_label.config(text=out_temp)
        # Keep the small sub-label unused unless you want to show a timestamp.
        self.out_temp_time.config(text="")

    def update_time(self, time_text: str):
        self.clock_label.config(text=time_text)

    def update_date(self, date_text: str, weekday: str):
        self.date_label.config(text=date_text)
        self.weekday_label.config(text=weekday)

    def update_outside_temp(self, out_temp: str):
        self.out_temp_label.config(text=out_temp)
