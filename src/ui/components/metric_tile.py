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
        icon: str | None = None,
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

        # "Datenreich/Dashboard"-Stil: kleiner farbiger Akzent (Icon oder
        # Punkt in der Wertfarbe) VOR der GROSSGESCHRIEBENEN Beschriftung,
        # statt nur nackter Zahl + Beschriftung ohne visuelle Verknuepfung
        # zueinander. Icon ist optional (Rueckwaerts-kompatibel zu
        # bestehenden Aufrufen ohne icon=) - ohne eigenes Icon faellt es auf
        # einen schlichten Farbpunkt in value_color zurueck.
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(anchor="w", fill=tk.X)

        self.accent_label = ctk.CTkLabel(
            header,
            text=(icon or "●"),
            font=get_safe_font("Segoe UI", 11),
            text_color=value_color,
            width=16,
        )
        self.accent_label.pack(side=tk.LEFT, padx=(0, 4))

        self.caption_label = ctk.CTkLabel(
            header,
            text=caption.upper(),
            font=get_safe_font("Bahnschrift", max(9, FONT_SIZE_SMALL - 1), "bold"),
            text_color=COLOR_SUBTEXT,
            anchor="w",
        )
        self.caption_label.pack(side=tk.LEFT, anchor="w")

        self.value_label = ctk.CTkLabel(
            inner,
            text=value,
            font=get_safe_font("Bahnschrift", FONT_SIZE_TITLE, "bold"),
            text_color=value_color,
            anchor="w",
        )
        self.value_label.pack(anchor="w", fill=tk.X, pady=(3, 0))

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value_label.configure(text=text)
        if color:
            self.value_label.configure(text_color=color)
            try:
                self.accent_label.configure(text_color=color)
            except Exception:
                pass
