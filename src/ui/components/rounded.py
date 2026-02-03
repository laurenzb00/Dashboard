import tkinter as tk


class RoundedFrame(tk.Frame):
    """Rounded container with optional border, using a Canvas background."""

    def __init__(self, parent: tk.Widget, bg: str, border: str, radius: int = 12, padding: int = 0, *args, **kwargs):
        super().__init__(parent, bg=bg, *args, **kwargs)
        self._bg = bg
        self._border = border
        self._radius = radius
        self._pad = padding

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.configure(padx=padding, pady=padding)
        self.inner.pack_propagate(False)
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

    def _on_resize(self, event):
        w = max(1, event.width)
        h = max(1, event.height)
        r = min(self._radius, w // 2, h // 2)
        if hasattr(self, "_last_size"):
            last_w, last_h = self._last_size
            if abs(w - last_w) < 2 and abs(h - last_h) < 2:
                return
        self._last_size = (w, h)
        self.canvas.delete("rounded_bg")
        # Border
        if self._border:
            self._round_rect(1, 1, w - 1, h - 1, r, fill=self._border, outline="", tags="rounded_bg")
            inset = 2
        else:
            inset = 0
        # Inner
        self._round_rect(inset, inset, w - inset, h - inset, max(0, r - 1), fill=self._bg, outline="", tags="rounded_bg")

        self.canvas.itemconfigure(self._window, width=w, height=h)

    def content(self) -> tk.Frame:
        return self.inner
