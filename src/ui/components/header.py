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
    get_safe_font,
)


class HeaderBar(ctk.CTkFrame):
    """Moderner Header mit CustomTkinter - nahtlose Integration."""


    def __init__(
        self,
        parent: tk.Widget,
        datastore=None,
        on_toggle_a=None,
        on_toggle_b=None,
        on_leave=None,
        on_come_home=None,
        on_shower=None,
        on_exit=None,
    ):
        super().__init__(parent, height=74, fg_color=COLOR_HEADER, corner_radius=16)
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
            font=get_safe_font("Bahnschrift", 16, "bold"), 
            text_color=COLOR_TEXT,
            anchor="w"
        )
        self.date_label.pack(anchor="w", side=tk.TOP)
        
        self.weekday_label = ctk.CTkLabel(
            left, 
            text="", 
            font=get_safe_font("Bahnschrift", 11), 
            text_color=COLOR_SUBTEXT,
            anchor="w"
        )
        self.weekday_label.pack(anchor="w", side=tk.TOP, pady=(2, 0))

        # Mitte: Uhrzeit + Light Switch - horizontal, vertikal zentriert
        center = ctk.CTkFrame(inner, fg_color="transparent")
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_columnconfigure(0, weight=0)
        center.grid_columnconfigure(1, weight=1)
        center.grid_columnconfigure(2, weight=0)
        center.grid_columnconfigure(3, weight=0)

        # Actions (zwischen Datum und Uhrzeit)
        actions = ctk.CTkFrame(center, fg_color="transparent")
        actions.grid(row=0, column=0, sticky="w", padx=(0, 12))

        self.leave_btn = ctk.CTkButton(
            actions,
            text="🏃",
            command=self._on_leave_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=40,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.leave_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._leave_btn_text_inactive = "🏃"
        self._leave_btn_text_active = "🏃✓"

        self.home_btn = ctk.CTkButton(
            actions,
            text="🏠",
            command=self._on_home_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=40,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.home_btn.pack(side=tk.LEFT)

        self.clock_label = ctk.CTkLabel(
            center, 
            text="--:--", 
            font=get_safe_font("Bahnschrift", 38, "bold"), 
            text_color=COLOR_PRIMARY
        )
        self.clock_label.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        self.shower_btn = ctk.CTkButton(
            center,
            text="🚿",
            command=self._on_shower_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=40,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.shower_btn.grid(row=0, column=2, sticky="e", padx=(0, 12))

        # Light Control - Icon und Switch horizontal nebeneinander
        light_control = ctk.CTkFrame(center, fg_color="transparent")
        light_control.grid(row=0, column=3, sticky="ns", padx=8)
        
        ctk.CTkLabel(
            light_control,
            text="💡",
            font=get_safe_font("Bahnschrift", 20),
            text_color=COLOR_WARNING,
            width=24
        ).pack(side=tk.LEFT, padx=(0, 6))
        
        self.light_switch = ctk.CTkSwitch(
            light_control,
            text="",
            width=58,
            height=28,
            switch_width=58,
            switch_height=28,
            fg_color=COLOR_BORDER,
            progress_color=COLOR_WARNING,
            button_color="#FFFFFF",
            button_hover_color="#E0E0E0",
            command=self._on_light_switch_toggle
        )
        self.light_switch.pack(side=tk.LEFT)
        self._suppress_light_switch_event = False
        self.light_switch.select()
        
        # Callbacks speichern
        self._on_toggle_a = on_toggle_a
        self._on_toggle_b = on_toggle_b
        self._on_leave = on_leave
        self._on_come_home = on_come_home
        self._on_shower = on_shower
        self._on_exit = on_exit  # fallback only

        # Rechts: Außentemp mit modernem Style
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.grid(row=0, column=2, sticky="ne")

        top_row = ctk.CTkFrame(right, fg_color="transparent")
        top_row.pack(anchor="ne", side=tk.TOP, fill=tk.X)
        top_row.grid_columnconfigure(0, weight=1)
        top_row.grid_columnconfigure(1, weight=0)
        top_row.grid_columnconfigure(2, weight=0)

        self.out_temp_label = ctk.CTkLabel(
            top_row,
            text="--.- °C",
            font=get_safe_font("Bahnschrift", 16, "bold"),
            text_color=COLOR_WARNING,
            anchor="e",
        )
        self.out_temp_label.grid(row=0, column=0, sticky="e")
        
        self.out_temp_time = ctk.CTkLabel(
            right, 
            text="", 
            font=get_safe_font("Bahnschrift", 9), 
            text_color=COLOR_SUBTEXT,
            anchor="e"
        )
        self.out_temp_time.pack(anchor="ne", side=tk.TOP, pady=(2, 0))

        # Outdoor temp is updated via MainApp.update_header(...), single source of truth.

    def _on_light_switch_toggle(self):
        """Handler für Light Switch - ruft entsprechenden Callback auf."""
        if getattr(self, "_suppress_light_switch_event", False):
            return
        if self.light_switch.get():
            # Switch ist An
            if self._on_toggle_a:
                self._on_toggle_a()
        else:
            # Switch ist Aus
            if self._on_toggle_b:
                self._on_toggle_b()

    def _on_leave_pressed(self) -> None:
        try:
            if self._on_leave:
                self._on_leave()
                return
            # Backward-compatible fallback
            if self._on_exit:
                self._on_exit()
        except Exception:
            pass

    def _on_home_pressed(self) -> None:
        try:
            if self._on_come_home:
                self._on_come_home()
        except Exception:
            pass

    def _on_shower_pressed(self) -> None:
        try:
            if self._on_shower:
                self._on_shower()
        except Exception:
            pass

    def set_light_switch_state(self, is_on: bool) -> None:
        """Set switch UI state without triggering callbacks."""
        try:
            self._suppress_light_switch_event = True
            if is_on:
                self.light_switch.select()
            else:
                self.light_switch.deselect()
        except Exception:
            pass
        finally:
            self._suppress_light_switch_event = False

    def set_leave_home_active(self, is_active: bool | None) -> None:
        """Mark the leave-home button as active when 'all lights are off'."""
        try:
            if is_active is True:
                self.leave_btn.configure(
                    text=self._leave_btn_text_active,
                    border_color=COLOR_WARNING,
                    text_color=COLOR_WARNING,
                )
            else:
                self.leave_btn.configure(
                    text=self._leave_btn_text_inactive,
                    border_color=COLOR_BORDER,
                    text_color=COLOR_TEXT,
                )
        except Exception:
            pass

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
