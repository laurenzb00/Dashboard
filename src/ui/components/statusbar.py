import tkinter as tk
import time
import customtkinter as ctk

from ui.styles import COLOR_CARD, COLOR_BORDER, COLOR_HEADER, COLOR_TEXT, COLOR_SUBTEXT, COLOR_PRIMARY, COLOR_ROOT, get_safe_font


class StatusBar(ctk.CTkFrame):
    """
    Moderne Statusbar mit CustomTkinter - nahtlose Integration.
    """

    def set_status(self, text: str):
        """Set the visible status message text, rendered as small badge chips."""
        new_text = text or ""
        try:
            if new_text == getattr(self, "_status_text", ""):
                return
        except Exception:
            pass
        self._status_text = new_text

        # Highlight calendar part (e.g. "Heute: …") in a separate label.
        parts = [p.strip() for p in new_text.split("•")]
        parts = [p for p in parts if p]

        cal_text = ""
        for p in list(parts):
            if p.startswith("Heute:"):
                cal_text = p
                parts.remove(p)
                break

        self._render_status_chips(parts)

        shown_cal = ""
        if cal_text:
            shown_cal = "📅 " + cal_text
            if len(shown_cal) > 84:
                shown_cal = shown_cal[:83] + "…"

        if hasattr(self, "event_label"):
            try:
                self.event_label.configure(text=shown_cal)
            except Exception:
                pass

    def _render_status_chips(self, parts: list[str]) -> None:
        """Show up to 3 status values (Modus/Einheizen/PV heute) as separate
        badge chips instead of one long '•'-joined line."""
        max_chips = len(self._status_chip_frames)
        chip_max_len = 42
        try:
            for i, frame in enumerate(self._status_chip_frames):
                if i < len(parts) and i < max_chips:
                    text = parts[i]
                    if len(text) > chip_max_len:
                        text = text[: chip_max_len - 1] + "…"
                    self._status_chip_labels[i].configure(text=text)
                    frame.pack(side=tk.LEFT, padx=(0, 6))
                else:
                    frame.pack_forget()
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

    def set_portrait_layout(self, portrait: bool) -> None:
        """Give status text and controls enough room on tall touch displays."""
        if not portrait:
            return
        try:
            self.configure(height=68)
            for label in self._status_chip_labels:
                label.configure(font=get_safe_font("Bahnschrift", 16))
            self.event_label.configure(font=get_safe_font("Bahnschrift", 16, "bold"))
            self.uptime_label.configure(font=get_safe_font("Bahnschrift", 14))
            self.window_btn.configure(width=72, height=46)
            self.exit_btn.configure(width=104, height=46)
        except Exception:
            pass

    def __init__(self, parent: tk.Widget, on_exit=None, on_toggle_fullscreen=None):
        # 16 -> 20: gleiche softere Rundung wie Card()/HeaderBar.
        super().__init__(parent, height=60, fg_color=COLOR_HEADER, corner_radius=20)
        self.pack_propagate(False)
        self._status_text = ""
        self._start_monotonic = time.monotonic()
        self._uptime_after_id: str | None = None
        self._manual_until: float | None = None

        # Innerer Container
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)

        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=0)
        inner.grid_columnconfigure(2, weight=0)
        inner.grid_columnconfigure(3, weight=0)
        inner.grid_columnconfigure(4, weight=0)

        # Visible status message (left) - rendered as a row of badge chips,
        # one per value (Modus/Einheizen/PV heute), instead of one long line.
        self.chips_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self.chips_frame.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self._status_chip_frames: list[ctk.CTkFrame] = []
        self._status_chip_labels: list[ctk.CTkLabel] = []
        for _ in range(3):
            chip = ctk.CTkFrame(
                self.chips_frame,
                fg_color=COLOR_CARD,
                corner_radius=10,
                border_width=1,
                border_color=COLOR_BORDER,
            )
            chip_label = ctk.CTkLabel(
                chip,
                text="",
                text_color=COLOR_TEXT,
                font=get_safe_font("Bahnschrift", 13),
                anchor="w",
            )
            chip_label.pack(padx=10, pady=3)
            self._status_chip_frames.append(chip)
            self._status_chip_labels.append(chip_label)

        # Highlighted calendar/event part (compact, bold)
        self.event_label = ctk.CTkLabel(
            inner,
            text="",
            text_color=COLOR_PRIMARY,
            font=get_safe_font("Bahnschrift", 15, "bold"),
            anchor="w",
        )
        self.event_label.grid(row=0, column=1, sticky="w", padx=(0, 10))

        # Laufzeit-Anzeige (rechts, klein)
        self.uptime_label = ctk.CTkLabel(
            inner,
            text="",
            text_color=COLOR_SUBTEXT,
            font=get_safe_font("Bahnschrift", 13),
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
            corner_radius=12,
            font=get_safe_font("Bahnschrift", 16, "bold"),
            width=64,
            height=42,
            border_width=1,
            border_color=COLOR_BORDER
        )
        self.window_btn.grid(row=0, column=3, sticky="e", padx=(6, 4))

        self.exit_btn = ctk.CTkButton(
            inner,
            text="✕ Beenden",
            command=on_exit,
            fg_color="transparent",
            text_color=COLOR_SUBTEXT,
            hover_color=COLOR_BORDER,
            corner_radius=12,
            font=get_safe_font("Bahnschrift", 14, "bold"),
            width=104,
            height=42,
            border_width=1,
            border_color=COLOR_BORDER
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
