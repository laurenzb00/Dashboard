import tkinter as tk
from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_DANGER
from ui.components.rounded import RoundedFrame



class StatusBar(tk.Frame):
    """
    32px Statusbar (minimal): nur Fenster/Fullscreen und Exit.
    """

    def set_status(self, text: str):
        """Set the status label text (API-consistent)."""
        self._status_text = text
        if hasattr(self, "status_label"):
            try:
                self.status_label.config(text=text)
            except Exception:
                pass

    def __init__(self, parent: tk.Widget, on_exit=None, on_toggle_fullscreen=None):
        super().__init__(parent, height=32, bg=COLOR_HEADER)
        self.pack_propagate(False)
        self._status_text = ""

        rounded = RoundedFrame(self, bg=COLOR_CARD, border=None, radius=18, padding=0)
        rounded.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        inner = rounded.content()

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)
        inner.grid_columnconfigure(2, weight=0)

        # Optional (hidden) label to keep API-compatible state, but not shown.
        self.status_label = tk.Label(inner, text="", fg=COLOR_TEXT, bg=COLOR_CARD, font=("Segoe UI", 11))

        # Window and Exit Buttons (once)
        from ui.components.rounded_button import RoundedButton
        self.window_btn = RoundedButton(
            inner, text="⊡", command=on_toggle_fullscreen,
            bg=COLOR_BORDER, fg=COLOR_TEXT,
            radius=10, padding=(10, 4), font_size=11, width=44, height=26
        )
        self.window_btn.grid(row=0, column=1, sticky="e", padx=(6, 4), pady=0)

        self.exit_btn = RoundedButton(
            inner, text="⏻", command=on_exit,
            bg=COLOR_DANGER, fg="#fff",
            radius=10, padding=(10, 4), font_size=11, width=44, height=26
        )
        self.exit_btn.grid(row=0, column=2, sticky="e", padx=(4, 8), pady=0)

    # --- API-compatible methods (no UI) ---
    def update_lamps(self, db_status, pv_status, heating_status):
        return

    def update_status(self, text: str):
        self.set_status(text)

    def update_center(self, text: str):
        return

    def update_data_freshness(self, text: str, alert: bool = False):
        return

    def update_sparkline(self, values: list[float], color: str):
        return
