import tkinter as tk
import customtkinter as ctk

from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, COLOR_TITLE, get_safe_font


class TabShell(ctk.CTkFrame):
    """Shared page frame for tab content with title, status, and body regions."""

    def __init__(self, parent: tk.Widget, title: str, subtitle: str = ""):
        super().__init__(parent, fg_color=COLOR_ROOT, corner_radius=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=12, border_width=1, border_color=COLOR_BORDER)
        self.header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 12))
        self.header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            self.header,
            text=title,
            font=get_safe_font("Bahnschrift", 21, "bold"),
            text_color=COLOR_TITLE,
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 0))

        self.subtitle_label = ctk.CTkLabel(
            self.header,
            text=subtitle,
            font=get_safe_font("Bahnschrift", 13),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w", padx=18, pady=(2, 14))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

    def set_status(self, text: str) -> None:
        self.subtitle_label.configure(text=text or "")

    def set_portrait_layout(self, portrait: bool) -> None:
        if portrait:
            self.header.grid_configure(padx=16, pady=(16, 10))
            self.body.grid_configure(padx=16, pady=(0, 16))
            self.title_label.configure(font=get_safe_font("Bahnschrift", 24, "bold"))
            self.subtitle_label.configure(font=get_safe_font("Bahnschrift", 15))
        else:
            self.header.grid_configure(padx=20, pady=(20, 12))
            self.body.grid_configure(padx=20, pady=(0, 20))
            self.title_label.configure(font=get_safe_font("Bahnschrift", 21, "bold"))
            self.subtitle_label.configure(font=get_safe_font("Bahnschrift", 13))
