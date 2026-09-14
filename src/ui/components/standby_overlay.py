"""Vollflaechiger Bildschirmschoner-Overlay fuer den Standby-Modus.

Wird von MainApp eingeblendet, sobald die echte Home-Assistant-Anwesenheit
(person.laurenz, siehe MainApp._sync_presence_standby_state) "nicht zuhause"
meldet - NICHT bei Touch-Inaktivitaet. Liegt als eigenstaendiges place()-
Widget direkt auf root (Geschwister von main_container), damit es das
komplette Dashboard unabhaengig von dessen Grid-Layout ueberdecken kann,
ohne dieses Layout selbst anzufassen.
"""

import random
import tkinter as tk
import customtkinter as ctk

from ui.styles import COLOR_ROOT, COLOR_SUBTEXT, get_safe_font


class StandbyOverlay(ctk.CTkFrame):
    """Dunkler Vollbild-Screensaver mit Uhrzeit/Datum.

    Der Inhalt wird periodisch leicht verschoben (dezentes "Pixel-Shifting"),
    damit ein dauerhaft eingeschaltetes LCD-Touchpanel waehrend laengerer
    Abwesenheit kein vollstaendig statisches Bild zeigt.
    """

    # Gedaempfter Blauton statt der hellen Header-Uhrzeit-Farbe (#7fb0ff) -
    # der Screensaver soll bewusst unauffaellig/dunkel wirken.
    _CLOCK_COLOR = "#3a4a63"
    _REPOSITION_MS = 45000

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)

        self._reposition_after_id = None

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        # Position wird erst in show()/_reposition() gesetzt.

        self.clock_label = ctk.CTkLabel(
            self._content,
            text="--:--",
            font=get_safe_font("Bahnschrift", 96, "bold"),
            text_color=self._CLOCK_COLOR,
        )
        self.clock_label.pack()

        self.date_label = ctk.CTkLabel(
            self._content,
            text="--",
            font=get_safe_font("Bahnschrift", 20),
            text_color=COLOR_SUBTEXT,
        )
        self.date_label.pack(pady=(6, 0))

        self.hint_label = ctk.CTkLabel(
            self._content,
            text="🌙  Nicht zuhause – Bildschirmschoner aktiv",
            font=get_safe_font("Bahnschrift", 13),
            text_color=COLOR_SUBTEXT,
        )
        self.hint_label.pack(pady=(18, 0))

    def update_time(self, time_text: str) -> None:
        try:
            self.clock_label.configure(text=time_text)
        except Exception:
            pass

    def update_date(self, date_text: str, weekday: str) -> None:
        try:
            self.date_label.configure(text=f"{weekday}, {date_text}")
        except Exception:
            pass

    def show(self) -> None:
        try:
            self.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.lift()
        except Exception:
            pass
        self._reposition()

    def hide(self) -> None:
        if self._reposition_after_id is not None:
            try:
                self.after_cancel(self._reposition_after_id)
            except Exception:
                pass
            self._reposition_after_id = None
        try:
            self.place_forget()
        except Exception:
            pass

    def _reposition(self) -> None:
        try:
            relx = random.uniform(0.30, 0.70)
            rely = random.uniform(0.28, 0.62)
            self._content.place(relx=relx, rely=rely, anchor="center")
        except Exception:
            pass
        try:
            self._reposition_after_id = self.after(self._REPOSITION_MS, self._reposition)
        except Exception:
            self._reposition_after_id = None
