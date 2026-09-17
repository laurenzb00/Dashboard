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

from ui.styles import COLOR_ROOT, COLOR_SUBTEXT, COLOR_BORDER, COLOR_TEXT, get_safe_font


class StandbyOverlay(ctk.CTkFrame):
    """Dunkler Vollbild-Screensaver mit Uhrzeit/Datum.

    Der Inhalt wird periodisch leicht verschoben (dezentes "Pixel-Shifting"),
    damit ein dauerhaft eingeschaltetes LCD-Touchpanel waehrend laengerer
    Abwesenheit kein vollstaendig statisches Bild zeigt.

    Nutzer-Feedback: bei Fern-Zugriff per VPN/VNC (Home Assistant meldet
    dann korrekterweise "nicht zuhause") verdeckt der Screensaver das
    komplette Dashboard, ohne dass man ihn wegklicken kann - die Header-
    Buttons darunter sind ja ebenfalls verdeckt. "on_peek" ist deshalb ein
    kleiner, an fester Position sitzender Button (bewusst NICHT Teil von
    _content, das sich zum Schutz vor Bildeinbrennen periodisch verschiebt),
    der den Screensaver rein lokal/clientseitig fuer eine begrenzte Zeit
    ausblendet - OHNE die echte Home-Assistant-Anwesenheit zu verändern
    (siehe MainApp._standby_peek: das ist bewusst getrennt vom
    "Zuhause erzwingen"-Header-Button, der einen echten HA-Webhook ausloest).
    """

    # Gedaempfter Blauton statt der hellen Header-Uhrzeit-Farbe (#7fb0ff) -
    # der Screensaver soll bewusst unauffaellig/dunkel wirken.
    _CLOCK_COLOR = "#3a4a63"
    _REPOSITION_MS = 45000

    def __init__(self, parent: tk.Widget, on_peek=None):
        super().__init__(parent, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)

        self._reposition_after_id = None
        self._on_peek = on_peek

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

        # Fern-Zugriff-Ausweg: unten rechts, unabhaengig vom "Pixel-
        # Shifting" immer an derselben Stelle zu finden. Bewusst dezent
        # (dunkler Chip, duenner Rand) statt auffaellig, da er auf dem
        # physischen Touch-Display normalerweise gar nicht gebraucht wird.
        self.peek_btn = ctk.CTkButton(
            self,
            text="Kurz anzeigen (30 Min)",
            command=self._on_peek_pressed,
            font=get_safe_font("Bahnschrift", 12),
            fg_color="#1a2030",
            hover_color=COLOR_BORDER,
            text_color=COLOR_SUBTEXT,
            corner_radius=14,
            border_width=1,
            border_color=COLOR_BORDER,
            width=170,
            height=38,
        )
        self.peek_btn.place(relx=1.0, rely=1.0, x=-24, y=-24, anchor="se")

    def _on_peek_pressed(self) -> None:
        try:
            if self._on_peek:
                self._on_peek()
        except Exception:
            pass

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
