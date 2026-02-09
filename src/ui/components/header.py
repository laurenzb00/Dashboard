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
    COLOR_ROOT,
)


class HeaderBar(ctk.CTkFrame):
    """Moderner Header mit CustomTkinter - nahtlose Integration."""


    def __init__(self, parent: tk.Widget, datastore=None, on_toggle_a=None, on_toggle_b=None, on_exit=None):
        super().__init__(parent, height=44, fg_color=COLOR_HEADER, corner_radius=16)
        self.pack_propagate(False)
        self.datastore = datastore

        # Innerer Container mit Grid-Layout
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        inner.grid_columnconfigure(0, weight=1, minsize=160, uniform="hdr")
        inner.grid_columnconfigure(1, weight=2, uniform="hdr")
        inner.grid_columnconfigure(2, weight=1, minsize=160, uniform="hdr")

        # Links: Datum mit modernem Style
        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew")
        
        self.date_label = ctk.CTkLabel(
            left, 
            text="--", 
            font=("Segoe UI", 16, "bold"), 
            text_color=COLOR_TEXT,
            anchor="w"
        )
        self.date_label.pack(anchor="w", side=tk.TOP)
        
        self.weekday_label = ctk.CTkLabel(
            left, 
            text="", 
            font=("Segoe UI", 11), 
            text_color=COLOR_SUBTEXT,
            anchor="w"
        )
        self.weekday_label.pack(anchor="w", side=tk.TOP, pady=(2, 0))

        # Mitte: Uhrzeit + Light Switch - kompakter
        center = ctk.CTkFrame(inner, fg_color="transparent")
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_columnconfigure(0, weight=1)
        center.grid_columnconfigure(1, weight=0)

        self.clock_label = ctk.CTkLabel(
            center, 
            text="--:--", 
            font=("Segoe UI", 38, "bold"), 
            text_color=COLOR_PRIMARY
        )
        self.clock_label.grid(row=0, column=0, sticky="ew", padx=(0, 16))

        # Light Control - kompakter mit Switch
        light_control = ctk.CTkFrame(center, fg_color="transparent")
        light_control.grid(row=0, column=1, sticky="ns", padx=8)
        
        ctk.CTkLabel(
            light_control,
            text="💡",
            font=("Segoe UI", 16),
            text_color=COLOR_WARNING,
            width=30
        ).pack(side=tk.TOP, pady=(0, 4))
        
        self.light_switch = ctk.CTkSwitch(
            light_control,
            text="",
            width=46,
            height=24,
            switch_width=46,
            switch_height=24,
            fg_color=COLOR_BORDER,
            progress_color=COLOR_WARNING,
            button_color="#FFFFFF",
            button_hover_color="#E0E0E0",
            command=self._on_light_switch_toggle
        )
        self.light_switch.pack(side=tk.TOP)
        self.light_switch.select()
        
        # Callbacks speichern
        self._on_toggle_a = on_toggle_a
        self._on_toggle_b = on_toggle_b

        # Rechts: Außentemp mit modernem Style
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.grid(row=0, column=2, sticky="ne")

        self.out_temp_label = ctk.CTkLabel(
            right, 
            text="--.- °C", 
            font=("Segoe UI", 16, "bold"), 
            text_color=COLOR_WARNING,
            anchor="e"
        )
        self.out_temp_label.pack(anchor="ne", side=tk.TOP)
        
        self.out_temp_time = ctk.CTkLabel(
            right, 
            text="", 
            font=("Segoe UI", 9), 
            text_color=COLOR_SUBTEXT,
            anchor="e"
        )
        self.out_temp_time.pack(anchor="ne", side=tk.TOP, pady=(2, 0))

        # Outdoor temp is updated via MainApp.update_header(...), single source of truth.

    def _on_light_switch_toggle(self):
        """Handler für Light Switch - ruft entsprechenden Callback auf."""
        if self.light_switch.get():
            # Switch ist An
            if self._on_toggle_a:
                self._on_toggle_a()
        else:
            # Switch ist Aus
            if self._on_toggle_b:
                self._on_toggle_b()

    def update_header(self, date_text: str, weekday: str, time_text: str, out_temp: str):
        self.date_label.configure(text=date_text)
        self.weekday_label.configure(text=weekday)
        self.clock_label.configure(text=time_text)
        self.out_temp_label.configure(text=out_temp)
        # Keep the small sub-label unused unless you want to show a timestamp.
        self.out_temp_time.configure(text="")

    def update_time(self, time_text: str):
        self.clock_label.configure(text=time_text)

    def update_date(self, date_text: str, weekday: str):
        self.date_label.configure(text=date_text)
        self.weekday_label.configure(text=weekday)

    def update_outside_temp(self, out_temp: str):
        self.out_temp_label.configure(text=out_temp)
