from __future__ import annotations

"""Help/Verwaltungs-Tab.

Sammelstelle fuer Hilfs-/Verwaltungsfunktionen, die keinen eigenen Platz in
der ohnehin schon vollen obersten Tab-Leiste brauchen (10 Haupt-Tabs waren
mit der zweizeiligen Icon+Name-Darstellung bereits an der Kapazitaetsgrenze
der Bildschirmbreite). Erster Bewohner: die Home-Assistant-Automationen/
Skripte, vorher ein eigener oberster Tab ("HomeA") - jetzt als eigener
Reiter hier drin (nested CTkTabview, gleiches Muster wie die Sub-Tabs im
Spotify-Tab). Weitere Hilfs-Inhalte koennen spaeter als zusaetzliche Reiter
dazukommen, ohne die Haupt-Tableiste weiter zu fuellen.
"""

import tkinter as tk
from tkinter import BOTH

import customtkinter as ctk

from ui.components.tab_shell import TabShell
from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    emoji,
    get_safe_font,
)


class HelpTab:
    """Oberster Tab, der intern eine eigene Sub-Tab-Leiste (Reiter) fuehrt."""

    def __init__(self, root: tk.Tk, notebook, tab_frame=None, homeassistant_actions_cls=None):
        self.root = root
        self.notebook = notebook
        self.homeassistant_actions_tab = None

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = ctk.CTkFrame(self.notebook, fg_color=COLOR_ROOT)
            self.notebook.add(self.tab_frame, text=emoji("❓\nHelp", "Help"))

        self._shell = TabShell(self.tab_frame, "Help", "Automationen, Skripte und weitere Hilfsfunktionen")
        self._shell.pack(fill=BOTH, expand=True)

        wrapper = tk.Frame(self._shell.body, bg=COLOR_ROOT)
        wrapper.pack(fill=BOTH, expand=True)

        # Gleiches Muster wie SpotifyTab._build_ui()'s content_notebook: ein
        # nested CTkTabview statt des alten ttk.Notebook-Looks, damit die
        # Sub-Tab-Leiste zum App-weiten CTkTabview-Look passt.
        self.content_notebook = ctk.CTkTabview(
            wrapper,
            fg_color=COLOR_ROOT,
            border_color=COLOR_ROOT,
            segmented_button_fg_color=COLOR_ROOT,
            segmented_button_selected_color=COLOR_PRIMARY,
            segmented_button_selected_hover_color=COLOR_PRIMARY,
            segmented_button_unselected_color=COLOR_CARD,
            segmented_button_unselected_hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            text_color_disabled=COLOR_SUBTEXT,
        )
        self.content_notebook.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        # Plain-Text-Label ohne Icon/Zeilenumbruch, gleiche Konvention wie
        # die Sub-Tab-Reiter im Spotify-Tab ("Now Playing", "Playlists", ...)
        # - die Icon+Zeilenumbruch-Behandlung gilt nur fuer die oberste
        # Tab-Leiste (siehe MainApp._style_tabview_buttons()).
        self.content_notebook.add("Home Assistant")
        ha_frame = self.content_notebook.tab("Home Assistant")

        try:
            segmented = getattr(self.content_notebook, "_segmented_button", None)
            if segmented is not None:
                segmented.configure(
                    font=get_safe_font("Bahnschrift", 13, "bold"),
                    height=44,
                    corner_radius=14,
                    border_width=1,
                    border_color=COLOR_BORDER,
                )
        except Exception:
            pass

        # homeassistant_actions_cls wird von app.py durchgereicht (dort schon
        # per try/except importiert) statt hier nochmal eigenstaendig zu
        # importieren - ein fehlgeschlagener Import von tabs.homeassistant_
        # actions soll den restlichen Help-Tab nicht mit reissen.
        if homeassistant_actions_cls is not None:
            try:
                self.homeassistant_actions_tab = homeassistant_actions_cls(
                    self.root, self.content_notebook, tab_frame=ha_frame
                )
            except Exception:
                self.homeassistant_actions_tab = None

    def set_portrait_layout(self, portrait: bool) -> None:
        """Reicht die Ausrichtung an die Inhalte der Sub-Tabs weiter."""
        setter = getattr(self.homeassistant_actions_tab, "set_portrait_layout", None)
        if callable(setter):
            try:
                setter(portrait)
            except Exception:
                pass
