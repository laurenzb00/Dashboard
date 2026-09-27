import tkinter as tk
import customtkinter as ctk
from ui.styles import COLOR_ROOT, COLOR_CARD, COLOR_TEXT, COLOR_TITLE, emoji, get_safe_font, COLOR_BORDER


class Card(ctk.CTkFrame):
    """Shared elevated panel used to give dashboard content a clear hierarchy."""

    def __init__(self, parent: tk.Widget, padding: int = 16, *args, **kwargs):
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            # 12 -> 18: Teil der app-weiten "Glas"-Designsprache (siehe
            # buffer_storage.py Tank-Rendering) - softere, rundere Flaechen
            # statt der vorherigen eher technischen 12px-Ecken. Card ist die
            # gemeinsame Basis fast aller Tab-Panels, dieser eine Wert
            # propagiert die Rundung dadurch automatisch ueberall dort mit.
            corner_radius=18,
            border_width=1,
            border_color=COLOR_BORDER,
            *args,
            **kwargs,
        )
        
        # Direkter innerer Frame - transparent
        self.inner = ctk.CTkFrame(self, fg_color="transparent")
        self.inner.pack(fill=tk.BOTH, expand=True, padx=padding, pady=padding)

    def content(self) -> tk.Frame:
        """Gibt den inneren Container zurück."""
        return self.inner

    def add_title(
        self,
        text: str,
        icon: str | None = None,
        glyph: str | None = None,
        glyph_color: str | None = None,
    ) -> ctk.CTkFrame:
        """Baut die Titel-Zeile einer Card.

        `icon` ist ein rohes Emoji-Zeichen (Rueckwaerts-kompatibel zu allen
        bestehenden Aufrufen). `glyph` nimmt stattdessen den Namen einer
        gezeichneten Glyphe aus glyph_icon.py (gleiche "Glas"-Optik wie die
        Header-Aktions-Icons) - hochwertiger als ein rohes Systememoji und
        in `glyph_color` einfaerbbar (Default: COLOR_TITLE, wie der Text).
        Beide Label-Referenzen haengen als header.icon_label/.title_label
        am zurueckgegebenen Frame, falls ein Aufrufer sie spaeter (z.B. zum
        Umfaerben) braucht.
        """
        header = ctk.CTkFrame(self.inner, fg_color="transparent")
        header.pack(fill=tk.X, pady=0, padx=0)

        icon_label = None
        if glyph:
            from ui.components.glyph_icon import ctk_icon
            image = ctk_icon(glyph, glyph_color or COLOR_TITLE, size=20)
            icon_label = ctk.CTkLabel(header, image=image, text="")
            icon_label.pack(side=tk.LEFT, padx=(0, 6))
        elif icon:
            icon_text = emoji(icon, "")
            if icon_text:
                icon_label = ctk.CTkLabel(header, text=icon_text, font=get_safe_font("Bahnschrift", 17), text_color=COLOR_TITLE)
                icon_label.pack(side=tk.LEFT, padx=(0, 6))

        title_label = ctk.CTkLabel(
            header,
            text=text,
            font=get_safe_font("Bahnschrift", 17, "bold"),
            text_color=COLOR_TITLE,
        )
        title_label.pack(side=tk.LEFT)
        header.icon_label = icon_label
        header.title_label = title_label
        return header
