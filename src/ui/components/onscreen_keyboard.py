"""Einfache Bildschirmtastatur (QWERTZ) fuer den Touchscreen ohne echte Tastatur.

    OnscreenKeyboard.ask(root, "Name der Szene", on_done=lambda text: ...)

Zeigt ein Overlay mit Eingabefeld und Tasten; `on_done(None)` bei Abbrechen.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_PRIMARY, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, get_safe_font

_ROWS = ["1234567890ß", "qwertzuiopü", "asdfghjklöä", "yxcvbnm-"]


class OnscreenKeyboard(ctk.CTkFrame):
    def __init__(self, root, title: str, on_done: Callable[[Optional[str]], None], initial: str = "",
                 max_len: int = 30):
        super().__init__(root, fg_color=COLOR_ROOT, corner_radius=0)
        self.on_done = on_done
        self.max_len = max_len
        self.shift = True   # erster Buchstabe gross
        self.text = tk.StringVar(value=initial)
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()

        box = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=18, border_width=1, border_color=COLOR_BORDER)
        box.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.96, relheight=0.9)
        box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(box, text=title, text_color=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 14)).grid(
            row=0, column=0, sticky="w", padx=18, pady=(12, 2))
        self.entry = ctk.CTkLabel(box, textvariable=self.text, anchor="w", height=48, corner_radius=10,
                                  fg_color=COLOR_ROOT, text_color=COLOR_TEXT,
                                  font=get_safe_font("Bahnschrift", 22, "bold"))
        self.entry.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))

        keys = ctk.CTkFrame(box, fg_color="transparent")
        keys.grid(row=2, column=0, sticky="nsew", padx=10)
        box.grid_rowconfigure(2, weight=1)
        self._letter_buttons = []
        for r, row in enumerate(_ROWS):
            fr = ctk.CTkFrame(keys, fg_color="transparent")
            fr.pack(fill="x", expand=True, pady=2)
            for ch in row:
                b = ctk.CTkButton(fr, text=ch, height=46, width=10, corner_radius=10, fg_color=COLOR_BORDER,
                                  text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 18, "bold"),
                                  command=lambda c=ch: self._key(c))
                b.pack(side="left", fill="x", expand=True, padx=2)
                if ch.isalpha() and ch != "ß":
                    self._letter_buttons.append((b, ch))
            if r == 3:
                ctk.CTkButton(fr, text="⌫", height=46, width=10, corner_radius=10, fg_color=COLOR_BORDER,
                              text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 18, "bold"),
                              command=self._backspace).pack(side="left", fill="x", expand=True, padx=2)
        bottom = ctk.CTkFrame(keys, fg_color="transparent")
        bottom.pack(fill="x", expand=True, pady=2)
        ctk.CTkButton(bottom, text="⇧", height=46, width=60, corner_radius=10, fg_color=COLOR_BORDER,
                      text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 18, "bold"),
                      command=self._toggle_shift).pack(side="left", padx=2)
        ctk.CTkButton(bottom, text="Leerzeichen", height=46, corner_radius=10, fg_color=COLOR_BORDER,
                      text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 16),
                      command=lambda: self._key(" ")).pack(side="left", fill="x", expand=True, padx=2)

        actions = ctk.CTkFrame(box, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=18, pady=(6, 12))
        ctk.CTkButton(actions, text="Abbrechen", height=46, width=150, corner_radius=12, fg_color=COLOR_BORDER,
                      text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 16, "bold"),
                      command=lambda: self._finish(None)).pack(side="left")
        ctk.CTkButton(actions, text="Speichern", height=46, width=170, corner_radius=12, fg_color=COLOR_PRIMARY,
                      text_color="#ffffff", font=get_safe_font("Bahnschrift", 16, "bold"),
                      command=lambda: self._finish(self.text.get().strip() or None)).pack(side="right")
        self._update_case()

    @classmethod
    def ask(cls, root, title: str, on_done: Callable[[Optional[str]], None], initial: str = "") -> "OnscreenKeyboard":
        return cls(root, title, on_done, initial=initial)

    def _key(self, ch: str) -> None:
        cur = self.text.get()
        if len(cur) >= self.max_len:
            return
        if self.shift and ch.isalpha() and ch != "ß":
            ch = ch.upper()
            self.shift = False
            self._update_case()
        self.text.set(cur + ch)

    def _backspace(self) -> None:
        self.text.set(self.text.get()[:-1])

    def _toggle_shift(self) -> None:
        self.shift = not self.shift
        self._update_case()

    def _update_case(self) -> None:
        for b, ch in self._letter_buttons:
            b.configure(text=ch.upper() if self.shift and ch != "ß" else ch)

    def _finish(self, value: Optional[str]) -> None:
        try:
            self.destroy()
        finally:
            self.on_done(value)
