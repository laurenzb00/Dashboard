from __future__ import annotations

import re
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

# Matcht die erste Zahl in einem formatierten Anzeigetext (z.B. "42.3 kWh",
# "-1.500 W", "62%") - Vorzeichen und ein Dezimaltrenner (Punkt ODER Komma)
# werden erkannt, damit set_value() Prefix/Zahl/Suffix trennen und nur die
# Zahl animieren kann.
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


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

        # Fuer set_value()'s Hochzaehl-Animation: der zuletzt angezeigte
        # numerische Wert plus Prefix/Suffix/Nachkommastellen des zuletzt
        # gesetzten Textformats. None = noch kein animierbarer (numerischer)
        # Wert gesetzt - der naechste Aufruf zeigt dann direkt an, statt von
        # 0 hochzuzaehlen.
        self._display_value: float | None = None
        self._value_prefix = ""
        self._value_suffix = ""
        self._value_decimals = 0
        self._value_anim_job = None

    def set_value(self, text: str, color: str | None = None, animate: bool = True) -> None:
        """Setzt den angezeigten Wert.

        Springt vorher immer hart auf den neuen Text. Wenn sowohl der
        bisherige als auch der neue Text eine Zahl enthalten (gleiches
        Prefix/Suffix, z.B. nur "42.3 kWh" -> "45.1 kWh"), zaehlt der Wert
        jetzt stattdessen sanft dorthin hoch/runter - fuehlt sich weniger
        "roboterhaft" an als der harte Sprung. Bei der allerersten Anzeige
        eines Wertes (vorher "--" o.ae.) oder wenn sich das Format aendert
        (andere Einheit/Prefix), wird weiterhin direkt angezeigt, sonst
        wuerde z.B. beim Wechsel von "-- kWh" auf "42 kWh" sinnlos von 0
        hochgezaehlt.
        """
        if color:
            self.value_label.configure(text_color=color)
            try:
                self.accent_label.configure(text_color=color)
            except Exception:
                pass

        match = _NUMBER_RE.search(text) if animate else None
        if match is None:
            self._cancel_value_animation()
            self._display_value = None
            self.value_label.configure(text=text)
            return

        num_str = match.group(0)
        new_value = float(num_str.replace(",", "."))
        prefix = text[: match.start()]
        suffix = text[match.end() :]
        sep_idx = max(num_str.find("."), num_str.find(","))
        decimals = len(num_str) - sep_idx - 1 if sep_idx >= 0 else 0

        if (
            self._display_value is None
            or prefix != self._value_prefix
            or suffix != self._value_suffix
        ):
            self._display_value = new_value
            self._value_prefix = prefix
            self._value_suffix = suffix
            self._value_decimals = decimals
            self._cancel_value_animation()
            self.value_label.configure(text=text)
            return

        self._value_prefix = prefix
        self._value_suffix = suffix
        self._value_decimals = decimals
        self._animate_value_to(new_value)

    def _animate_value_to(self, target: float) -> None:
        self._cancel_value_animation()
        threshold = (10 ** -self._value_decimals) / 2

        def step() -> None:
            try:
                if not self.winfo_exists():
                    self._value_anim_job = None
                    return
            except Exception:
                self._value_anim_job = None
                return
            delta = target - self._display_value
            if abs(delta) <= threshold:
                self._display_value = target
                self._render_display_value()
                self._value_anim_job = None
                return
            self._display_value += delta * 0.35
            self._render_display_value()
            try:
                self._value_anim_job = self.after(40, step)
            except Exception:
                self._value_anim_job = None

        step()

    def _render_display_value(self) -> None:
        formatted = f"{self._display_value:.{self._value_decimals}f}"
        try:
            self.value_label.configure(text=f"{self._value_prefix}{formatted}{self._value_suffix}")
        except Exception:
            pass

    def _cancel_value_animation(self) -> None:
        if self._value_anim_job is not None:
            try:
                self.after_cancel(self._value_anim_job)
            except Exception:
                pass
            self._value_anim_job = None
