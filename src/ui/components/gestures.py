"""Wiederverwendbare Touch-Gesten fuer CustomTkinter-Widgets.

Baut nur auf Standard-Tkinter-Events (<ButtonPress-1>/<B1-Motion>/
<ButtonRelease-1>) auf, die der Touchscreen-Treiber auf dem Pi als normale
Maus-Events an Tk weiterreicht - es braucht dafuer keine Zusatzbibliothek.

Zwei Gesten werden angeboten:

- bind_swipe(): erkennt ein schnelles horizontales Wischen (z.B. fuer
  Tab-Navigation), siehe TabShell.enable_swipe()/HeaderBar.enable_swipe().
- bind_long_press(): erkennt laengeres Gedrueckthalten ohne nennenswerte
  Bewegung (z.B. fuer eine Detailansicht), siehe MetricTile.enable_detail().

Beide binden bewusst NICHT auf ganze Tab-Inhalte, sondern nur auf klar
umrissene Widgets (Kopfzeilen, einzelne Kacheln) - ein globales bind_all()
wuerde sonst mit bestehenden Drag-Interaktionen kollidieren (z.B. dem
Lautstaerke-/Fortschritts-Slider im Spotify-Tab oder scrollbaren Listen).
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Callable, List, Optional


def _all_descendants(widget: tk.Misc) -> List[tk.Misc]:
    """Sammelt `widget` selbst plus alle Kind-Widgets rekursiv.

    Noetig, weil ein <ButtonPress-1> auf einem Kind-Widget (z.B. einem
    CTkLabel innerhalb einer Kachel) NICHT automatisch an eine Bindung auf
    dem Eltern-Widget weitergereicht wird - Tk liefert Maus-Events immer an
    das konkrete Widget unter dem Zeiger.
    """
    widgets = [widget]
    try:
        children = widget.winfo_children()
    except Exception:
        children = []
    for child in children:
        widgets.extend(_all_descendants(child))
    return widgets


class _SwipeState:
    __slots__ = ("start_x", "start_y", "start_t", "active")

    def __init__(self) -> None:
        self.start_x = 0
        self.start_y = 0
        self.start_t = 0.0
        self.active = False


def bind_swipe(
    widget: tk.Misc,
    on_left: Optional[Callable[[], None]] = None,
    on_right: Optional[Callable[[], None]] = None,
    threshold_px: int = 70,
    max_off_axis_px: int = 60,
    max_duration_s: float = 0.8,
    include_children: bool = False,
) -> None:
    """Erkennt ein horizontales Wischen auf `widget`.

    on_left wird bei einem Wisch nach LINKS ausgeloest (Finger bewegt sich
    von rechts nach links -> "naechste" Seite), on_right entsprechend bei
    einem Wisch nach RECHTS ("vorherige" Seite). Ein Wisch zaehlt nur, wenn
    er ueberwiegend horizontal verlaeuft (max_off_axis_px) und schnell genug
    ist (max_duration_s) - ein langsames, absichtliches Scrollen/Ziehen soll
    keine Tab-Navigation ausloesen.

    `include_children=True` bindet zusaetzlich rekursiv auf alle Kind-
    Widgets (siehe _all_descendants) - nur fuer klar begrenzte Container
    wie eine Kopfzeile mit ein paar Labels sinnvoll, NICHT fuer einen ganzen
    Tab-Inhalt mit Buttons/Slidern/Charts.
    """
    state = _SwipeState()

    def _press(event: tk.Event) -> None:
        state.start_x = event.x_root
        state.start_y = event.y_root
        state.start_t = time.monotonic()
        state.active = True

    def _release(event: tk.Event) -> None:
        if not state.active:
            return
        state.active = False
        dx = event.x_root - state.start_x
        dy = event.y_root - state.start_y
        dt = time.monotonic() - state.start_t
        if dt > max_duration_s:
            return
        if abs(dy) > max_off_axis_px:
            return
        if dx <= -threshold_px and on_left:
            on_left()
        elif dx >= threshold_px and on_right:
            on_right()

    targets = _all_descendants(widget) if include_children else [widget]
    for w in targets:
        try:
            w.bind("<ButtonPress-1>", _press, add="+")
            w.bind("<ButtonRelease-1>", _release, add="+")
        except Exception:
            pass


class _LongPressState:
    __slots__ = ("job", "start_x", "start_y", "fired")

    def __init__(self) -> None:
        self.job = None
        self.start_x = 0
        self.start_y = 0
        self.fired = False


def bind_long_press(
    widget: tk.Misc,
    callback: Callable[[], None],
    duration_ms: int = 550,
    move_cancel_px: int = 18,
    feedback_widget: Optional[tk.Misc] = None,
    press_color: Optional[str] = None,
    release_color: Optional[str] = None,
    include_children: bool = True,
) -> None:
    """Loest `callback` aus, wenn `widget` mindestens `duration_ms` lang
    gedrueckt gehalten wird, ohne sich mehr als `move_cancel_px` zu bewegen
    (sonst zaehlt es als Wischen/Verrutschen statt Long-Press).

    Optionales visuelles Press-Feedback: `feedback_widget` (Default: das
    Widget selbst) bekommt beim Druecken `press_color` als border_color,
    beim Loslassen/Abbrechen wieder `release_color` - simuliert einen
    "gedrueckt"-Zustand auf einem sonst unveraenderlichen CTkFrame, damit
    auf dem Touchscreen sofort sichtbar ist, dass die Beruehrung erkannt
    wurde (nicht erst nach den vollen `duration_ms`).

    `include_children=True` (Default) bindet rekursiv auf alle Kind-Widgets
    (siehe _all_descendants), damit ein Long-Press ueberall auf der
    sichtbaren Kachel funktioniert, nicht nur auf unbeschrifteten Randpixeln.
    """
    state = _LongPressState()
    target = feedback_widget if feedback_widget is not None else widget

    def _apply_feedback(color: Optional[str]) -> None:
        if color is None:
            return
        try:
            target.configure(border_color=color)
        except Exception:
            pass

    def _cancel() -> None:
        if state.job is not None:
            try:
                widget.after_cancel(state.job)
            except Exception:
                pass
            state.job = None
        if not state.fired:
            _apply_feedback(release_color)
        state.fired = False

    def _fire() -> None:
        state.job = None
        state.fired = True
        _apply_feedback(release_color)
        try:
            callback()
        except Exception:
            pass

    def _press(event: tk.Event) -> None:
        state.start_x = event.x_root
        state.start_y = event.y_root
        state.fired = False
        _apply_feedback(press_color)
        try:
            state.job = widget.after(duration_ms, _fire)
        except Exception:
            state.job = None

    def _motion(event: tk.Event) -> None:
        if state.job is None:
            return
        if (
            abs(event.x_root - state.start_x) > move_cancel_px
            or abs(event.y_root - state.start_y) > move_cancel_px
        ):
            _cancel()

    def _release(event: tk.Event) -> None:
        _cancel()

    targets = _all_descendants(widget) if include_children else [widget]
    for w in targets:
        try:
            w.bind("<ButtonPress-1>", _press, add="+")
            w.bind("<B1-Motion>", _motion, add="+")
            w.bind("<ButtonRelease-1>", _release, add="+")
        except Exception:
            pass
