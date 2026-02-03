import tkinter as tk
from ui.styles import COLOR_ROOT, COLOR_CARD, COLOR_TEXT, COLOR_TITLE, emoji


class Card(tk.Frame):
    """Glasmorph-ähnlicher Card-Container mit Border und Innenbereich."""

    def __init__(self, parent: tk.Widget, padding: int = 16, *args, **kwargs):
        super().__init__(parent, bg=COLOR_ROOT, *args, **kwargs)

        self._pad = padding
        self._radius = 18
        self._bg = COLOR_CARD
        self._shadow_color = "#000000"

        self.canvas = tk.Canvas(self, bg=COLOR_ROOT, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self.canvas, bg=COLOR_CARD)
        self.inner.pack_propagate(False)
        self.inner.configure(padx=padding, pady=padding)

        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._on_resize)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    def _blend(self, c1: str, c2: str, t: float) -> str:
        def _hex(c):
            c = c.lstrip("#")
            return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
        r1, g1, b1 = _hex(c1)
        r2, g2, b2 = _hex(c2)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_resize(self, event):
        w = max(1, event.width)
        h = max(1, event.height)
        # Only redraw if size changed significantly (prevents flicker from minor updates)
        if hasattr(self, '_last_size'):
            last_w, last_h = self._last_size
            if abs(w - last_w) < 4 and abs(h - last_h) < 4:
                return  # Skip redraw for tiny changes
        self._last_size = (w, h)

        # Debounce redraw to avoid rapid flicker
        if hasattr(self, '_redraw_after_id') and self._redraw_after_id:
            try:
                self.after_cancel(self._redraw_after_id)
            except Exception:
                pass
        self._redraw_after_id = self.after(80, lambda: self._redraw_card(w, h))

    def _redraw_card(self, w: int, h: int):
        r = min(self._radius, w // 2, h // 2)
        self.canvas.delete("card_bg")

        # Shadow layers (soft)
        for i in range(4, 0, -1):
            offset = 2 + i
            shade = self._blend(self._shadow_color, COLOR_ROOT, 0.65)
            self._round_rect(
                2,
                2 + offset,
                w - 2,
                h - 2 + offset,
                r,
                fill=shade,
                outline="",
                tags="card_bg",
            )

        # Main card
        self._round_rect(2, 2, w - 2, h - 2, r, fill=self._bg, outline="", tags="card_bg")

        self.canvas.itemconfigure(self._window, width=w, height=h)

    def content(self) -> tk.Frame:
        """Gibt den inneren Container zurück."""
        return self.inner

    def add_title(self, text: str, icon: str | None = None) -> tk.Frame:
        header = tk.Frame(self.inner, bg=COLOR_CARD)
        header.pack(fill=tk.X, pady=(0, 8))

        if icon:
            icon_text = emoji(icon, "")
            if icon_text:
                tk.Label(header, text=icon_text, font=("Segoe UI", 14), bg=COLOR_CARD, fg=COLOR_TITLE).pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(
            header,
            text=text,
            font=("Segoe UI", 14, "bold"),
            bg=COLOR_CARD,
            fg=COLOR_TITLE,
        ).pack(side=tk.LEFT)
        return header
