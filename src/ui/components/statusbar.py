import tkinter as tk
import time
import customtkinter as ctk

from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_DANGER, COLOR_SUBTEXT, COLOR_PRIMARY, COLOR_ROOT, get_safe_font


class StatusBar(ctk.CTkFrame):
    """
    Moderne Statusbar mit CustomTkinter - nahtlose Integration.
    """

    def set_status(self, text: str):
        """Set the visible status message text."""
        new_text = text or ""
        try:
            if new_text == getattr(self, "_status_text", ""):
                return
        except Exception:
            pass
        self._status_text = new_text

        # Highlight calendar part (e.g. "Heute: …") in a separate label.
        base_text = self._status_text
        cal_text = ""
        try:
            parts = [p.strip() for p in base_text.split("•")]
            parts = [p for p in parts if p]
            for p in list(parts):
                if p.startswith("Heute:"):
                    cal_text = p
                    parts.remove(p)
                    break
            base_text = " • ".join(parts)
        except Exception:
            base_text = self._status_text
            cal_text = ""

        # Keep the status bar compact on 1024x600 screens.
        # If calendar exists, reserve space for it and shorten the base text first.
        max_total = 140
        base_max = 140
        cal_max = 0
        if cal_text:
            cal_max = 48
            base_max = max(40, max_total - (min(len(cal_text) + 2, cal_max) + 3))

        shown_base = base_text
        if len(shown_base) > base_max:
            shown_base = shown_base[: max(0, base_max - 1)] + "…" if base_max > 1 else "…"

        shown_cal = ""
        if cal_text:
            shown_cal = "📅 " + cal_text
            if len(shown_cal) > cal_max:
                shown_cal = shown_cal[: max(0, cal_max - 1)] + "…" if cal_max > 1 else "…"

        for attr in ("message_label", "status_label"):
            if hasattr(self, attr):
                try:
                    getattr(self, attr).configure(text=shown_base)
                except Exception:
                    pass

        if hasattr(self, "event_label"):
            try:
                self.event_label.configure(text=shown_cal)
            except Exception:
                pass

    def set_auto_status(self, text: str) -> None:
        """Set status text only if no recent manual status is active."""
        try:
            if time.monotonic() < (self._manual_until or 0.0):
                return
        except Exception:
            pass
        self.set_status(text)

    def __init__(self, parent: tk.Widget, on_exit=None, on_toggle_fullscreen=None):
        super().__init__(parent, height=36, fg_color=COLOR_HEADER, corner_radius=16)
        self.pack_propagate(False)
        self._status_text = ""
        self._start_monotonic = time.monotonic()
        self._uptime_after_id: str | None = None
        self._manual_until: float | None = None

        # Innerer Container
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)
        inner.grid_columnconfigure(2, weight=0)
        inner.grid_columnconfigure(3, weight=0)
        inner.grid_columnconfigure(4, weight=0)

        # Visible status message (left)
        self.message_label = ctk.CTkLabel(
            inner,
            text="",
            text_color=COLOR_TEXT,
            font=get_safe_font("Bahnschrift", 13),
            anchor="w",
        )
        self.message_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

        # Optional (hidden) label to keep API-compatible state (some tabs call update_status)
        self.status_label = ctk.CTkLabel(inner, text="", text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 13))

        # Highlighted calendar/event part (compact, bold)
        self.event_label = ctk.CTkLabel(
            inner,
            text="",
            text_color=COLOR_PRIMARY,
            font=get_safe_font("Bahnschrift", 13, "bold"),
            anchor="w",
        )
        self.event_label.grid(row=0, column=1, sticky="w", padx=(0, 10))

        # Laufzeit-Anzeige (rechts, klein)
        self.uptime_label = ctk.CTkLabel(
            inner,
            text="",
            text_color=COLOR_SUBTEXT,
            font=get_safe_font("Bahnschrift", 11),
            anchor="e",
        )
        self.uptime_label.grid(row=0, column=2, sticky="e", padx=(6, 6))

        # Window and Exit Buttons - schönerer moderner Style
        self.window_btn = ctk.CTkButton(
            inner, 
            text="⧖",
            command=on_toggle_fullscreen,
            fg_color="transparent",
            text_color=COLOR_PRIMARY,
            hover_color=COLOR_BORDER,
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 16, "bold"),
            width=52,
            height=28,
            border_width=1,
            border_color=COLOR_BORDER
        )
        self.window_btn.grid(row=0, column=3, sticky="e", padx=(6, 4))

        self.exit_btn = ctk.CTkButton(
            inner, 
            text="✕",
            command=on_exit,
            fg_color=COLOR_DANGER,
            text_color="#FFFFFF",
            hover_color="#DC2626",
            corner_radius=10,
            font=get_safe_font("Bahnschrift", 14, "bold"),
            width=52,
            height=28,
            border_width=0
        )
        self.exit_btn.grid(row=0, column=4, sticky="e", padx=(4, 0))

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
            self.uptime_label.configure(text=f"⏱ {self._format_uptime(uptime)}")
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
        # Manual status should temporarily override auto-status updates.
        try:
            self._manual_until = time.monotonic() + 6.0
        except Exception:
            self._manual_until = None
        self.set_status(text)

    def update_center(self, text: str):
        return

    def update_data_freshness(self, text: str, alert: bool = False):
        return

    def update_sparkline(self, values: list[float], color: str):
        return
