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
    COLOR_SUCCESS,
    COLOR_INFO,
    COLOR_ROOT,
    get_safe_font,
)
from ui.components.glyph_icon import ctk_icon, ctk_icon_rich


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
        # Feinschliff Runde 3: komplett neu aufgebaut nach Nutzer-Feedback
        # ("Layout/Aufbau gefaellt mir nicht" -> "1 Zeile, Uhrzeit in der
        # Mitte, Buttons etwas schoener, klarer Aufbau"). Statt zwei
        # getrennt schwebender Elemente (Uhrzeit-Karte + separate Leiste,
        # Runde 2) jetzt EINE durchgehende Leiste ueber die volle Breite:
        # Aktionen links, Uhrzeit exakt mittig (per place(), unabhaengig
        # von der Breite links/rechts), Licht/Temperatur rechts. Farben
        # bewusst zurueckhaltend (kein Blau mehr - siehe Feedback "weniger
        # Blau im gesamten Programm"): die Leiste selbst ist neutral,
        # einzig "Zuhause" bekommt Gruen als Akzent, Licht/Temperatur
        # bleiben beim bestehenden Warnfarbton.
        super().__init__(parent, height=110, fg_color=COLOR_ROOT, corner_radius=0)
        self.pack_propagate(False)
        self.datastore = datastore

        # Chip-Hintergrund der Aktions-Buttons: einen Schritt heller als die
        # Leiste selbst, damit sie als eigene Flaeche auffallen statt auf
        # der Karte zu verschwimmen (siehe Card()/COLOR_CARD).
        CHIP_BG = "#242A35"
        ACTIVE_FILL = "#3a2c12"
        self._chip_bg = CHIP_BG
        self._active_fill = ACTIVE_FILL

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        bar = ctk.CTkFrame(
            inner,
            fg_color=COLOR_CARD,
            corner_radius=26,
            border_width=2,
            border_color=COLOR_BORDER,
        )
        bar.pack(fill=tk.BOTH, expand=True)
        self._bar = bar

        # --- Links: Aktionen als groessere, klar umrandete Chips statt
        # der vorherigen fast unsichtbaren Transparent-Buttons. ---
        actions_wrap = ctk.CTkFrame(bar, fg_color="transparent")
        actions_wrap.pack(side=tk.LEFT, padx=(18, 0), pady=14)

        leave_wrap = ctk.CTkFrame(actions_wrap, fg_color="transparent")
        leave_wrap.pack(side=tk.LEFT, padx=(0, 10))

        # Feine Glas-Linienicons statt Emoji (siehe glyph_icon.py). "Weg"
        # braucht zwei Varianten (normal/aktiv), die anderen nur eine.
        # Nutzer-Feedback ("keine Farbe, Icons gefallen mir nicht, etwas
        # breiter machen"): alle drei Chips bekommen jetzt einen eigenen
        # staendigen Farbakzent (vorher nur "Zuhause" gruen, Weg/Dusche rein
        # neutral/grau) statt die Akzentfarbe erst beim Aktiv-Zustand zu
        # zeigen - macht die Leiste bunter, ohne wieder in "zu viel Blau"
        # zurueckzufallen (Weg=Amber, Zuhause=Gruen, Dusche=Cyan/Info) - das
        # steuert weiterhin nur den Chip-Rahmen.
        # Die Icons selbst kamen danach durch mehrere Feinschliff-Runden:
        # neu gezeichnet ("Icons gefallen mir nicht"), dann flaechig +
        # Schlagschatten statt Glow ("weniger minimalistisch, nicht
        # neonartig"), zuletzt echte Materialfarben statt Hell/Dunkel-
        # Varianten EINER Akzentfarbe ("duerfen auch nicht einfarbig sein") -
        # siehe ctk_icon_rich()/_RICH_GLYPHS in glyph_icon.py. Dadurch
        # braucht "Weg" auch keine eigene aktiv/normal-Icon-Variante mehr,
        # die feste Holztuer-Palette bleibt gleich; nur die Chip-Fuellung
        # (fg_color, siehe set_leave_home_active) zeigt den Aktiv-Zustand.
        self._icon_leave_normal = ctk_icon_rich("door_exit", size=32)
        self._icon_leave_active = self._icon_leave_normal

        self.leave_btn = ctk.CTkButton(
            leave_wrap,
            text="",
            image=self._icon_leave_normal,
            command=self._on_leave_pressed,
            fg_color=CHIP_BG,
            hover_color=COLOR_BORDER,
            corner_radius=18,
            width=104,
            height=82,
            border_width=2,
            border_color=COLOR_WARNING,
        )
        self.leave_btn.pack()
        self.leave_caption = ctk.CTkLabel(
            leave_wrap, text="Weg", font=get_safe_font("Bahnschrift", 11), text_color=COLOR_SUBTEXT
        )
        self.leave_caption.pack(pady=(4, 0))

        home_wrap = ctk.CTkFrame(actions_wrap, fg_color="transparent")
        home_wrap.pack(side=tk.LEFT, padx=(0, 10))

        # Einziger Farbakzent unter den drei Aktionen: "Zuhause" ist positiv
        # besetzt (alles an/normal) - Gruen statt des frueheren Blaus.
        self.home_btn = ctk.CTkButton(
            home_wrap,
            text="",
            image=ctk_icon_rich("house", size=32),
            command=self._on_home_pressed,
            fg_color=CHIP_BG,
            hover_color=COLOR_BORDER,
            corner_radius=18,
            width=104,
            height=82,
            border_width=2,
            border_color=COLOR_SUCCESS,
        )
        self.home_btn.pack()
        self.home_caption = ctk.CTkLabel(
            home_wrap, text="Zuhause", font=get_safe_font("Bahnschrift", 11), text_color=COLOR_SUBTEXT
        )
        self.home_caption.pack(pady=(4, 0))

        shower_wrap = ctk.CTkFrame(actions_wrap, fg_color="transparent")
        shower_wrap.pack(side=tk.LEFT)

        self.shower_btn = ctk.CTkButton(
            shower_wrap,
            text="",
            image=ctk_icon_rich("shower", size=32),
            command=self._on_shower_pressed,
            fg_color=CHIP_BG,
            hover_color=COLOR_BORDER,
            corner_radius=18,
            width=104,
            height=82,
            border_width=2,
            border_color=COLOR_INFO,
        )
        self.shower_btn.pack()
        self.shower_caption = ctk.CTkLabel(
            shower_wrap, text="Dusche", font=get_safe_font("Bahnschrift", 11), text_color=COLOR_SUBTEXT
        )
        self.shower_caption.pack(pady=(4, 0))

        # --- Mitte: Uhrzeit/Datum, per place() exakt auf der Bar-Mitte
        # zentriert - unabhaengig davon, wie breit links (Aktionen) und
        # rechts (Licht/Temperatur) tatsaechlich sind. Kein eigener
        # Karten-Hintergrund mehr (Runde 2), sitzt direkt auf der Leiste,
        # und keine eigene Akzentfarbe (Runde 2 nutzte Blau) - einfach
        # heller Text, der von selbst als Blickfang wirkt.
        clock_block = ctk.CTkFrame(bar, fg_color="transparent")
        clock_block.place(relx=0.5, rely=0.5, anchor="center")

        self.clock_label = ctk.CTkLabel(
            clock_block,
            text="--:--",
            font=get_safe_font("Bahnschrift", 46, "bold"),
            text_color=COLOR_TEXT,
        )
        self.clock_label.pack(anchor="center")

        date_row = ctk.CTkFrame(clock_block, fg_color="transparent")
        date_row.pack(anchor="center", pady=(4, 0))

        self.weekday_label = ctk.CTkLabel(
            date_row,
            text="",
            font=get_safe_font("Bahnschrift", 13, "bold"),
            text_color=COLOR_SUBTEXT,
        )
        self.weekday_label.pack(side=tk.LEFT)

        self.date_label = ctk.CTkLabel(
            date_row,
            text="--",
            font=get_safe_font("Bahnschrift", 13),
            text_color=COLOR_SUBTEXT,
        )
        self.date_label.pack(side=tk.LEFT, padx=(6, 0))

        # --- Rechts: Licht und Temperatur, wie zuvor mit Trennlinie
        # dazwischen, jetzt etwas groesser. ---
        right_wrap = ctk.CTkFrame(bar, fg_color="transparent")
        right_wrap.pack(side=tk.RIGHT, padx=(0, 20), pady=14)

        def _divider(parent_widget: tk.Widget) -> None:
            ctk.CTkFrame(parent_widget, fg_color=COLOR_BORDER, width=1).pack(
                side=tk.LEFT, fill=tk.Y, pady=8, padx=18
            )

        light_control = ctk.CTkFrame(right_wrap, fg_color="transparent")
        light_control.pack(side=tk.LEFT)

        ctk.CTkLabel(
            light_control,
            text="",
            image=ctk_icon("bulb", COLOR_WARNING, size=26),
            width=26,
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.light_switch = ctk.CTkSwitch(
            light_control,
            text="",
            width=72,
            height=36,
            switch_width=72,
            switch_height=36,
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

        _divider(right_wrap)

        temp_block = ctk.CTkFrame(right_wrap, fg_color="transparent")
        temp_block.pack(side=tk.LEFT)

        top_row = ctk.CTkFrame(temp_block, fg_color="transparent")
        top_row.pack(anchor="e", side=tk.TOP)

        self.out_temp_icon = ctk.CTkLabel(
            top_row,
            text="",
            image=ctk_icon("thermometer", COLOR_WARNING, size=22),
        )
        self.out_temp_icon.pack(side=tk.LEFT, padx=(0, 8))

        self.out_temp_label = ctk.CTkLabel(
            top_row,
            text="--.- °C",
            font=get_safe_font("Bahnschrift", 20, "bold"),
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
            self.configure(height=150)
            self.date_label.configure(font=get_safe_font("Bahnschrift", 16))
            self.weekday_label.configure(font=get_safe_font("Bahnschrift", 16, "bold"))
            self.clock_label.configure(font=get_safe_font("Bahnschrift", 60, "bold"))
            self.out_temp_label.configure(font=get_safe_font("Bahnschrift", 24, "bold"))
            self.out_temp_icon.configure(image=ctk_icon("thermometer", COLOR_WARNING, size=26))
            self.out_temp_time.configure(font=get_safe_font("Bahnschrift", 12))
            # Groessere Icon-Varianten fuer die Touch-Hochformat-Chips (feste
            # Materialfarben-Palette, siehe ctk_icon_rich() weiter oben).
            self._icon_leave_normal = ctk_icon_rich("door_exit", size=40)
            self._icon_leave_active = self._icon_leave_normal
            self.leave_btn.configure(image=self._icon_leave_normal)
            self.home_btn.configure(image=ctk_icon_rich("house", size=40))
            self.shower_btn.configure(image=ctk_icon_rich("shower", size=40))
            for button in (self.leave_btn, self.home_btn, self.shower_btn):
                button.configure(width=124, height=100)
            for caption in (self.leave_caption, self.home_caption, self.shower_caption):
                caption.configure(font=get_safe_font("Bahnschrift", 13))
            self.light_switch.configure(width=80, height=40, switch_width=80, switch_height=40)
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
        """Mark the leave-home button as active when 'all lights are off'.

        Der Button ist jetzt ein Chip mit sichtbarem Hintergrund/Rahmen
        (Feinschliff Runde 3) - die "aktiv"-Markierung faerbt Fuellung UND
        Rahmen warm ein, statt (wie zuvor bei transparentem Hintergrund)
        nur die Fuellung zu setzen.
        """
        try:
            active_fill = getattr(self, "_active_fill", COLOR_WARNING)
            if is_active is True:
                self.leave_btn.configure(
                    image=self._icon_leave_active,
                    fg_color=active_fill,
                    border_color=COLOR_WARNING,
                )
            else:
                # Rahmen/Icon bleiben Amber (staendiger Farbakzent, siehe
                # __init__) - nur die Fuellung geht zurueck auf den
                # neutralen Chip-Hintergrund.
                self.leave_btn.configure(
                    image=self._icon_leave_normal,
                    fg_color=getattr(self, "_chip_bg", "#242A35"),
                    border_color=COLOR_WARNING,
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
