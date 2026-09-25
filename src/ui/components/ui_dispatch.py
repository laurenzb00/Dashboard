"""Gemeinsame Thread->Tk-Queue-Pump-Logik fuer App/Tab-Klassen.

Extrahiert aus sechs bisher unabhaengig voneinander kopierten
Implementierungen (ui/app.py, tabs/hue.py, tabs/ertrag.py,
tabs/historical.py, tabs/tagesproduktion.py,
tabs/homeassistant_actions.py), die alle denselben Zweck erfuellen:
Tkinter ist nicht thread-safe, Hintergrund-Worker duerfen keine Tk-APIs
direkt aufrufen, sondern posten Callbacks in eine Queue, die hier im
Main-Thread (per `after()`) periodisch abgearbeitet wird.

Der Preis der Duplikation zeigte sich konkret: hue.py und
homeassistant_actions.py liefen zuletzt mit 50ms Poll-Intervall,
ui/app.py/tabs/ertrag.py/tabs/historical.py/tabs/tagesproduktion.py mit
200ms - und BEIDE Seiten hatten einen Code-Kommentar, der behauptete,
"konsistent mit den anderen Tabs" zu sein. Eine Aenderung musste bisher
von Hand an bis zu sechs Stellen synchron nachgezogen werden; genau das
ist irgendwann nicht mehr passiert. Ausserdem wurde jeder Fehler in
einem gerade ausgefuehrten UI-Callback bisher mit nacktem
`except Exception: pass` verschluckt, ganz ohne Logging - ein Bug in
so einem Handler verschwand spurlos.

Erwartet von der nutzenden Klasse: `_init_ui_queue()` in __init__
aufrufen, bevor `_start_ui_pump()`/`_post_ui()` benutzt werden. Fuer den
Scheduler wird standardmaessig `self.root.after` verwendet (passt fuer
App-/Tab-Klassen, die selbst KEIN Tk-Widget sind); Klassen, die selbst
von tk.Frame/tk.Widget erben und stattdessen `self.after()` nutzen
wollen (historical.py, tagesproduktion.py), uebergeben explizit
`after_func=self.after` an `_start_ui_pump()`. Fuer den Liveness-Check
vor dem Ausfuehren geposteter Callbacks wird `self.alive` verwendet,
falls vorhanden, sonst `self.winfo_exists()`, falls vorhanden, sonst
wird immer weitergepumpt (passt fuer ui.app.MainApp, das nie "stirbt",
solange der Prozess laeuft - identisch zum bisherigen Verhalten dort).
"""

from __future__ import annotations

import logging
import queue

logger = logging.getLogger(__name__)

# Vereinheitlicht auf 200ms: war der bereits an vier von sechs Stellen
# genutzte Wert, mit einer nachvollziehbaren Begruendung (Pi hatte laut
# Task-Manager reichlich Luft, aber die UI-Queues sollen Updates nicht
# unnoetig verzoegern). 50ms lief an den anderen beiden Stellen zwar
# ebenfalls ohne beobachtete Probleme, aber ohne einen erkennbaren Grund,
# warum es dort schneller sein musste als ueberall sonst.
UI_PUMP_INTERVAL_MS = 200


class UiQueuePumpMixin:
    """Mixin mit der gemeinsamen Thread->Tk-Dispatch-Logik.

    Kein eigener __init__ - reine Methoden-Sammlung, daher unproblematisch
    per Mehrfachvererbung mit tk.Frame/tk.Widget oder als einzige
    Basisklasse (wie bei ui.app.MainApp) nutzbar.
    """

    def _init_ui_queue(self) -> None:
        """Muss vor _start_ui_pump()/_post_ui() aufgerufen werden."""
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()

    def _is_alive_for_ui_pump(self) -> bool:
        alive = getattr(self, "alive", None)
        if alive is not None:
            return bool(alive)
        winfo_exists = getattr(self, "winfo_exists", None)
        if callable(winfo_exists):
            try:
                return bool(winfo_exists())
            except Exception:
                return False
        return True

    def _start_ui_pump(self, interval_ms: int = UI_PUMP_INTERVAL_MS, after_func=None) -> None:
        """Startet die Queue-Pump-Schleife (idempotent - mehrfacher Aufruf
        ist ein No-Op, spiegelt das bisherige Verhalten in ui/app.py)."""
        if getattr(self, "_ui_pump_started", False):
            return
        self._ui_pump_started = True

        scheduler = after_func if after_func is not None else self.root.after
        cls_name = type(self).__name__

        def pump() -> None:
            if not self._is_alive_for_ui_pump():
                return
            try:
                while True:
                    cb = self._ui_queue.get_nowait()
                    try:
                        cb()
                    except Exception:
                        logger.exception(
                            "[UI-PUMP:%s] Fehler in gepostetem UI-Callback", cls_name
                        )
            except queue.Empty:
                pass
            try:
                scheduler(interval_ms, pump)
            except Exception:
                pass

        try:
            scheduler(0, pump)
        except Exception:
            pass

    def _post_ui(self, callback) -> None:
        """Postet einen Callback zur Ausfuehrung im Main-Thread."""
        try:
            if not self._is_alive_for_ui_pump():
                return
            self._ui_queue.put(callback)
        except Exception:
            pass
