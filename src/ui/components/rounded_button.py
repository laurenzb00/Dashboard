import tkinter as tk
from tkinter import font

class RoundedButton(tk.Canvas):
    """A modern, rounded button for touch dashboards."""
    def __init__(self, parent, text, command=None, bg="#3B82F6", fg="#fff", radius=18, padding=(18, 10), font_name="Segoe UI", font_size=12, active_bg="#2563eb", active_fg="#fff", border=None, borderwidth=0, **kwargs):
        super().__init__(parent, highlightthickness=0, bd=0, bg=parent["bg"] if "bg" in parent.keys() else "#0B1320", **kwargs)
        self._text = text
        self._command = command
        self._bg = bg
        self._fg = fg
        self._radius = radius
        self._padding = padding
        self._font = font.Font(family=font_name, size=font_size, weight="bold")
        self._active_bg = active_bg
        self._active_fg = active_fg
        self._border = border
        self._borderwidth = borderwidth
        self._is_active = False
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        r = min(self._radius, w // 2, h // 2)
        bg = self._active_bg if self._is_active else self._bg
        fg = self._active_fg if self._is_active else self._fg
        # Border
        if self._border:
            self.create_polygon(self._rounded_points(0, 0, w, h, r), fill=self._border, outline="", tags="border", smooth=True)
            inset = self._borderwidth
        else:
            inset = 0
        # Button background
        self.create_polygon(self._rounded_points(inset, inset, w-inset, h-inset, max(0, r-1)), fill=bg, outline="", tags="bg", smooth=True)
        # Text
        self.create_text(w//2, h//2, text=self._text, font=self._font, fill=fg, tags="text")

    def _rounded_points(self, x1, y1, x2, y2, r):
        return [
            x1+r, y1,
            x2-r, y1,
            x2, y1,
            x2, y1+r,
            x2, y2-r,
            x2, y2,
            x2-r, y2,
            x1+r, y2,
            x1, y2,
            x1, y2-r,
            x1, y1+r,
            x1, y1,
        ]

    def _on_click(self, event):
        if self._command:
            self._command()

    def _on_enter(self, event):
        self._is_active = True
        self._draw()

    def _on_leave(self, event):
        self._is_active = False
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_command(self, command):
        self._command = command