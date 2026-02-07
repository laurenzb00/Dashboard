import tkinter as tk
import time

from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_DANGER, COLOR_SUBTEXT
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
        self._start_monotonic = time.monotonic()
        self._uptime_after_id: str | None = None

        rounded = RoundedFrame(self, bg=COLOR_CARD, border=None, radius=18, padding=0)
        rounded.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        inner = rounded.content()

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)
        inner.grid_columnconfigure(2, weight=0)

        # Optional (hidden) label to keep API-compatible state, but not shown.
        self.status_label = tk.Label(inner, text="", fg=COLOR_TEXT, bg=COLOR_CARD, font=("Segoe UI", 11))

        # Laufzeit-Anzeige (Uptime seit Start)
        self.uptime_label = tk.Label(
            inner,
            text="",
            fg=COLOR_SUBTEXT,
            bg=COLOR_CARD,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.uptime_label.grid(row=0, column=0, sticky="w", padx=(10, 6), pady=0)

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

        # Start periodic uptime refresh (3 minutes are enough)
        self._refresh_uptime()
        self.bind("<Destroy>", self._on_destroy, add=True)

    def _on_destroy(self, _event=None) -> None:
        try:
            if self._uptime_after_id is not None:
                self.after_cancel(self._uptime_after_id)
        except Exception:
            pass
        self._uptime_after_id = None

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        total = max(0, int(seconds))
        days, rem = divmod(total, 24 * 3600)
        hours, rem = divmod(rem, 3600)
        minutes, _secs = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours:02d}:{minutes:02d}"
        return f"{hours:02d}:{minutes:02d}"

    def _refresh_uptime(self) -> None:
        try:
            if not getattr(self, "uptime_label", None):
                return
            if not self.winfo_exists():
                return
            uptime = time.monotonic() - self._start_monotonic
            self.uptime_label.config(text=f"Laufzeit: {self._format_uptime(uptime)}")
        except Exception:
            return
        # Update every 3 minutes (180000 ms)
        try:
            self._uptime_after_id = self.after(180_000, self._refresh_uptime)
        except Exception:
            self._uptime_after_id = None

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
