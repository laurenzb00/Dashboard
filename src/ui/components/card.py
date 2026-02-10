import tkinter as tk
import customtkinter as ctk
from ui.styles import COLOR_ROOT, COLOR_TEXT, COLOR_TITLE, emoji, get_safe_font, COLOR_BORDER


class Card(ctk.CTkFrame):
    """Vereinfachter Card-Container - transparent, damit Hintergrundfarbe durchscheint."""

    def __init__(self, parent: tk.Widget, padding: int = 16, *args, **kwargs):
        # Transparenter Hintergrund - nutzt parent fg_color
        super().__init__(
            parent,
            fg_color=COLOR_ROOT,
            corner_radius=10,
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

    def add_title(self, text: str, icon: str | None = None) -> ctk.CTkFrame:
        header = ctk.CTkFrame(self.inner, fg_color="transparent")
        header.pack(fill=tk.X, pady=0, padx=0)

        if icon:
            icon_text = emoji(icon, "")
            if icon_text:
                ctk.CTkLabel(header, text=icon_text, font=get_safe_font("Bahnschrift", 14), text_color=COLOR_TITLE).pack(side=tk.LEFT, padx=0)

        ctk.CTkLabel(
            header,
            text=text,
            font=get_safe_font("Bahnschrift", 14, "bold"),
            text_color=COLOR_TITLE,
        ).pack(side=tk.LEFT)
        return header
