"""Help -> Diagnose: ein Knopfdruck erstellt das Diagnose-Paket (src/diagnose.py).

Laeuft als eigener Prozess mit niedriger Prioritaet, damit das Dashboard
fluessig bleibt; die Ausgabe erscheint live im Textfeld. Ergebnis liegt in
~/Desktop/analyse/diagnose_<Datum>/ (die 3 neuesten werden behalten).
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_SUCCESS,
    COLOR_TEXT,
    get_safe_font,
)

SCRIPT = Path(__file__).resolve().parents[1] / "diagnose.py"


class DiagnosePanel:
    def __init__(self, root: tk.Misc, parent):
        self.root = root
        self._proc = None
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self.with_data = tk.BooleanVar(value=True)
        self.quick = tk.BooleanVar(value=False)

        box = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=16)
        box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        ctk.CTkLabel(box, text="🩺  Diagnose-Paket erstellen", text_color=COLOR_TEXT, anchor="w",
                     font=get_safe_font("Bahnschrift", 16, "bold")).pack(fill=tk.X, padx=16, pady=(12, 2))
        ctk.CTkLabel(box, justify="left", anchor="w", wraplength=900, text_color=COLOR_SUBTEXT,
                     font=get_safe_font("Bahnschrift", 12),
                     text="Sammelt Systemzustand, Leistungsprotokoll, Logs, Datenbank-Statistik und Messungen in einem "
                          "Ordner auf dem Desktop (analyse/diagnose_…). Passwörter und Tokens werden entfernt. "
                          "Den Ordner dann auf den Laptop kopieren – der Befehl steht am Ende unten im Feld."
                     ).pack(fill=tk.X, padx=16)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill=tk.X, padx=12, pady=10)
        self.btn = ctk.CTkButton(row, text="Diagnose starten", height=48, width=220, corner_radius=24,
                                 fg_color=COLOR_PRIMARY, hover_color=COLOR_SUCCESS,
                                 font=get_safe_font("Bahnschrift", 15, "bold"), command=self.start)
        self.btn.pack(side=tk.LEFT, padx=4)
        for text, var in (("Datenbanken mitkopieren (~250 MB, für Auswertungen)", self.with_data),
                          ("Schnell (ohne Rechen-Benchmarks)", self.quick)):
            ctk.CTkCheckBox(row, text=text, variable=var, text_color=COLOR_TEXT, checkbox_height=28,
                            checkbox_width=28, font=get_safe_font("Bahnschrift", 12),
                            border_color=COLOR_BORDER, fg_color=COLOR_PRIMARY).pack(side=tk.LEFT, padx=12)
        self.status = ctk.CTkLabel(box, text="", anchor="w", text_color=COLOR_SUBTEXT,
                                   font=get_safe_font("Bahnschrift", 12, "bold"))
        self.status.pack(fill=tk.X, padx=16)
        self.out = ctk.CTkTextbox(box, fg_color=COLOR_ROOT, text_color=COLOR_TEXT, wrap="char",
                                  font=("DejaVu Sans Mono", 11), height=260)
        self.out.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        self.out.configure(state="disabled")

    # ------------------------------------------------------------------

    def _append(self, text: str) -> None:
        self.out.configure(state="normal")
        self.out.insert("end", text + "\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    def start(self) -> None:
        if self._proc is not None:
            return
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")
        args = [sys.executable, "-u", str(SCRIPT)]
        if self.with_data.get():
            args.append("--with-data")
        if self.quick.get():
            args.append("--quick")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SCRIPT.parent) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
                cwd=str(SCRIPT.parent.parent),
                preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)   # Dashboard bleibt fluessig
        except Exception as exc:
            self.status.configure(text=f"Start fehlgeschlagen: {exc}")
            return
        self.btn.configure(state="disabled", text="läuft …")
        self.status.configure(text="Diagnose läuft – dauert 1–3 Minuten (mit Datenbanken etwas länger) …")
        threading.Thread(target=self._reader, daemon=True, name="diagnose-reader").start()
        self.root.after(150, self._poll)

    def _reader(self) -> None:
        proc = self._proc
        for line in proc.stdout:
            self._q.put(line.rstrip("\n"))
        proc.wait()
        self._q.put(None)

    def _poll(self) -> None:
        done = False
        try:
            for _ in range(200):
                item = self._q.get_nowait()
                if item is None:
                    done = True
                    break
                self._append(item)
        except queue.Empty:
            pass
        if not done:
            self.root.after(150, self._poll)
            return
        code = self._proc.returncode if self._proc else -1
        self._proc = None
        self.btn.configure(state="normal", text="Diagnose starten")
        if code == 0:
            self.status.configure(text="✓ Fertig – Ordner liegt auf dem Desktop unter analyse/. "
                                       "Kopier-Befehl siehe unten.", text_color=COLOR_SUCCESS)
        else:
            self.status.configure(text=f"⚠ Diagnose mit Fehler beendet (Code {code}) – Ausgabe siehe unten.",
                                  text_color=COLOR_SUBTEXT)
