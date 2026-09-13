from __future__ import annotations

import tkinter as tk
import customtkinter as ctk

from ui.styles import (
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    FONT_SIZE_TITLE,
    FONT_SIZE_SMALL,
    PADDING_TILE,
    get_safe_font,
)


class MetricTile(ctk.CTkFrame):
    """Small card showing one big value with a caption underneath.

    Used for the portrait-mode metrics panel on the chart tabs, where the
    extra vertical space would otherwise stay empty.
    """

    def __init__(
        self,
        parent: tk.Misc,
        caption: str,
        value: str = "--",
        value_color: str = COLOR_TEXT,
        **kwargs,
    ):
        super().__init__(
            parent,
            fg_color=COLOR_CARD,
            corner_radius=10,
            border_width=1,
            border_color=COLOR_BORDER,
            **kwargs,
        )
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=PADDING_TILE, pady=(8, 10))

        self.value_label = ctk.CTkLabel(
            inner,
            text=value,
            font=get_safe_font("Bahnschrift", FONT_SIZE_TITLE, "bold"),
            text_color=value_color,
            anchor="w",
        )
        self.value_label.pack(anchor="w", fill=tk.X)

        self.caption_label = ctk.CTkLabel(
            inner,
            text=caption,
            font=get_safe_font("Bahnschrift", FONT_SIZE_SMALL),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.caption_label.pack(anchor="w", fill=tk.X)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value_label.configure(text=text)
        if color:
            self.value_label.configure(text_color=color)
