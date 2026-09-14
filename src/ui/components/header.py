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
        # Kompletter Umbau: statt einer durchgehenden Leiste (ein einziges
        # abgerundetes Rechteck über die volle Breite) jetzt einzelne
        # Karten-Segmente mit kleinen Lücken dazwischen (Datum | Uhrzeit,
        # hervorgehoben | Aktionen | Licht+Temperatur), die direkt auf dem
        # App-Hintergrund sitzen - passt zum Karten-Look, den der Rest des
        # Dashboards (Card-Komponente) schon hat, statt einer eigenen,
        # separaten "Balken"-Optik nur für den Header.
        super().__init__(parent, height=98, fg_color=COLOR_ROOT, corner_radius=0)
        self.pack_propagate(False)
        self.datastore = datastore

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=6)
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=0)  # Datum
        inner.grid_columnconfigure(1, weight=0)  # Uhrzeit
        inner.grid_columnconfigure(2, weight=0)  # Aktionen
        inner.grid_columnconfigure(3, weight=1)  # Spacer
        inner.grid_columnconfigure(4, weight=0)  # Licht + Temp

        def _seg_card(col: int, border_color: str = COLOR_BORDER, padx=(0, 10)) -> ctk.CTkFrame:
            card = ctk.CTkFrame(
                inner,
                fg_color=COLOR_CARD,
                corner_radius=14,
                border_width=1,
                border_color=border_color,
            )
            card.grid(row=0, column=col, sticky="ns", padx=padx)
            return card

        # --- Segment: Datum ---
        date_card = _seg_card(0)
        date_inner = ctk.CTkFrame(date_card, fg_color="transparent")
        date_inner.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)

        self.date_label = ctk.CTkLabel(
            date_inner,
            text="--",
            font=get_safe_font("Bahnschrift", 18, "bold"),
            text_color=COLOR_TEXT,
            anchor="w",
        )
        self.date_label.pack(anchor="w", side=tk.TOP)

        self.weekday_label = ctk.CTkLabel(
            date_inner,
            text="",
            font=get_safe_font("Bahnschrift", 12),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.weekday_label.pack(anchor="w", side=tk.TOP, pady=(2, 0))

        # --- Segment: Uhrzeit (hervorgehoben durch farbigen Rand statt
        # neutralem Rahmen, damit sie als "wichtigstes" Segment auffaellt) ---
        clock_card = _seg_card(1, border_color=COLOR_PRIMARY)
        self.clock_label = ctk.CTkLabel(
            clock_card,
            text="--:--",
            font=get_safe_font("Bahnschrift", 40, "bold"),
            text_color=COLOR_PRIMARY,
        )
        self.clock_label.pack(padx=24, pady=8)

        # --- Segment: Aktionen (Weg/Zuhause/Dusche) ---
        actions_card = _seg_card(2)
        actions_inner = ctk.CTkFrame(actions_card, fg_color="transparent")
        actions_inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        leave_wrap = ctk.CTkFrame(actions_inner, fg_color="transparent")
        leave_wrap.pack(side=tk.LEFT, padx=(0, 10))

        self.leave_btn = ctk.CTkButton(
            leave_wrap,
            text="🏃",
            command=self._on_leave_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=44,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.leave_btn.pack()
        self.leave_caption = ctk.CTkLabel(
            leave_wrap, text="Weg", font=get_safe_font("Bahnschrift", 10), text_color=COLOR_SUBTEXT
        )
        self.leave_caption.pack(pady=(2, 0))

        self._leave_btn_text_inactive = "🏃"
        self._leave_btn_text_active = "🏃✓"

        home_wrap = ctk.CTkFrame(actions_inner, fg_color="transparent")
        home_wrap.pack(side=tk.LEFT, padx=(0, 10))

        self.home_btn = ctk.CTkButton(
            home_wrap,
            text="🏠",
            command=self._on_home_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=44,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.home_btn.pack()
        self.home_caption = ctk.CTkLabel(
            home_wrap, text="Zuhause", font=get_safe_font("Bahnschrift", 10), text_color=COLOR_SUBTEXT
        )
        self.home_caption.pack(pady=(2, 0))

        shower_wrap = ctk.CTkFrame(actions_inner, fg_color="transparent")
        shower_wrap.pack(side=tk.LEFT)

        self.shower_btn = ctk.CTkButton(
            shower_wrap,
            text="🚿",
            command=self._on_shower_pressed,
            fg_color="transparent",
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 20, "bold"),
            width=64,
            height=44,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.shower_btn.pack()
        self.shower_caption = ctk.CTkLabel(
            shower_wrap, text="Dusche", font=get_safe_font("Bahnschrift", 10), text_color=COLOR_SUBTEXT
        )
        self.shower_caption.pack(pady=(2, 0))

        # --- Segment: Licht-Schalter + Außentemperatur ---
        right_card = _seg_card(4, padx=(0, 0))
        right_inner = ctk.CTkFrame(right_card, fg_color="transparent")
        right_inner.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        # Licht: Icon bleibt direkt am Schalter (wie zuvor) - nur jetzt Teil
        # dieses gemeinsamen Segments statt einer eigenen Spalte in der Mitte.
        light_control = ctk.CTkFrame(right_inner, fg_color="transparent")
        light_control.pack(side=tk.LEFT, padx=(0, 16))

        ctk.CTkLabel(
            light_control,
            text="💡",
            font=get_safe_font("Bahnschrift", 20),
            text_color=COLOR_WARNING,
            width=24,
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.light_switch = ctk.CTkSwitch(
            light_control,
            text="",
            width=68,
            height=34,
            switch_width=68,
            switch_height=34,
            fg_color=COLOR_BORDER,
            progress_color=COLOR_WARNING,
            button_color="#FFFFFF",
            button_hover_color="#E0E0E0",
            command=self._on_light_switch_toggle,
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

        temp_block = ctk.CTkFrame(right_inner, fg_color="transparent")
        temp_block.pack(side=tk.LEFT)

        top_row = ctk.CTkFrame(temp_block, fg_color="transparent")
        top_row.pack(anchor="e", side=tk.TOP)

        # Kleines Thermometer-Icon vor dem Wert - passend zum Rest des
        # Headers, wo jede Aktion/jeder Wert (Weg/Zuhause/Dusche, Licht)
        # bereits ein eigenes Icon hat statt nur nacktem Text.
        self.out_temp_icon = ctk.CTkLabel(
            top_row,
            text="🌡️",
            font=get_safe_font("Bahnschrift", 14),
            text_color=COLOR_WARNING,
        )
        self.out_temp_icon.pack(side=tk.LEFT, padx=(0, 6))

        self.out_temp_label = ctk.CTkLabel(
            top_row,
            text="--.- °C",
            font=get_safe_font("Bahnschrift", 16, "bold"),
            text_color=COLOR_WARNING,
            anchor="e",
        )
        self.out_temp_label.pack(side=tk.LEFT)

        # Ungenutztes Sub-Label (Zeitstempel) beibehalten fuer Kompatibilitaet
        # mit update_header(), aber nicht mehr sichtbar gepackt - stand immer
        # leer da und hat im neuen kompakten Segment nur Platz verschwendet.
        self.out_temp_time = ctk.CTkLabel(
            temp_block,
            text="",
            font=get_safe_font("Bahnschrift", 9),
            text_color=COLOR_SUBTEXT,
            anchor="e",
        )

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

    def set_portrait_layout(self, portrait: bool) -> None:
        """Increase the header hierarchy for the tall touch display."""
        if not portrait:
            return
        try:
            self.configure(height=132)
            self.date_label.configure(font=get_safe_font("Bahnschrift", 22, "bold"))
            self.weekday_label.configure(font=get_safe_font("Bahnschrift", 15))
            self.clock_label.configure(font=get_safe_font("Bahnschrift", 56, "bold"))
            self.out_temp_label.configure(font=get_safe_font("Bahnschrift", 20, "bold"))
            self.out_temp_icon.configure(font=get_safe_font("Bahnschrift", 17))
            self.out_temp_time.configure(font=get_safe_font("Bahnschrift", 12))
            for button in (self.leave_btn, self.home_btn, self.shower_btn):
                button.configure(width=82, height=56, font=get_safe_font("Bahnschrift", 23, "bold"))
            for caption in (self.leave_caption, self.home_caption, self.shower_caption):
                caption.configure(font=get_safe_font("Bahnschrift", 12))
            self.light_switch.configure(width=76, height=38, switch_width=76, switch_height=38)
        except Exception:
            pass

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
