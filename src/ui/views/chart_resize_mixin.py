"""Gemeinsame Matplotlib-Canvas-Resize-Logik fuer die Chart-Views/-Tabs.

Extrahiert aus drei bisher fast wortgleichen Implementierungen
(`tabs/historical.py`, `tabs/tagesproduktion.py`, `ui/views/energy_chart.py`),
die alle denselben, ueber Trial-and-Error gefundenen Fix fuer denselben Bug
enthielten (siehe Docstring von `_sync_size` unten) - inklusive praktisch
identischem Erklaerungs-Kommentar dreifach kopiert. Eine Aenderung an dieser
Logik musste bisher an drei Stellen synchron nachgezogen werden; das ist die
Quelle der Duplikation, die hier beseitigt wird.

Bewusst NICHT hierher verschoben: `_apply_layout()` (unterschiedliche
Subplot-Raender je Chart), `_resize_canvas_now()`/`_on_resize()` (leicht
unterschiedliche Trigger-Quellen: `chart_frame`-Groesse bei den Tabs vs.
Event-Groesse bei `EnergyChart`, unterschiedliche Log-Praefixe). Diese
bleiben bewusst je Klasse bestehen, um das laufende, bereits fein
abgestimmte Resize-/Layout-Verhalten nicht anzufassen.

Erwartet von der nutzenden Klasse (wie in allen drei bisherigen
Implementierungen bereits vorhanden): `self.canvas` (FigureCanvasTkAgg) und
ein beschreibbares `self._last_synced_wh`-Attribut.
"""

from __future__ import annotations

from ui.styles import COLOR_ROOT


class MatplotlibCanvasResizeMixin:
    """Mixin mit der gemeinsamen Groessen-Sync-/Redraw-Hilfslogik.

    Kein eigener __init__ - reine Methoden-Sammlung, daher unproblematisch
    per Mehrfachvererbung mit tk.Frame oder als einzige Basisklasse nutzbar.
    """

    def _sync_size(self, w: int, h: int) -> bool:
        """Sync die Matplotlib-Figure-Groesse auf eine gegebene Tk-Widget-Groesse.

        CTk/Tk-Layouts koennen waehrend eines Relayouts (z.B. Tab-Wechsel)
        kurzzeitig sehr kleine/veraltete Groessen melden; solche werden
        ignoriert statt ein gestauchtes/verzerrtes Bild zu rendern.

        WICHTIG (Ursache des "Diagramm bleibt klein"-Bugs): Vorher wurde
        hier nur fig.set_size_inches(..., forward=True) + canvas.draw_idle()
        aufgerufen. Das vergroessert zwar den intern von Matplotlib
        gerenderten Bild-Buffer, aendert aber NICHT die Groesse des
        zugrunde liegenden Tk PhotoImage (_tkphoto) und auch nicht
        Position/Groesse des Canvas-Image-Items (_tkcanvas_image_region) -
        das erledigt normalerweise automatisch FigureCanvasTk.resize(), das
        intern per <Configure> ans Canvas-Widget gebunden wird. Weil dieses
        <Configure> in den nutzenden Klassen mit einem eigenen Handler
        (ohne add="+") ueberschrieben wird, ERSETZT Tkinter die eingebaute
        Bindung dadurch komplett - resize() wurde also nie mehr aufgerufen.
        Sichtbares Symptom: das Diagramm blieb dauerhaft klein oben links,
        mit schwarzer Flaeche drumherum, obwohl das Canvas-Widget selbst
        (laut Logs) korrekt auf volle Groesse mitgewachsen ist. Fix: die
        eingebaute resize()-Logik direkt mit einem synthetischen Event
        aufrufen statt sie unvollstaendig nachzubauen - das aktualisiert
        Figure-Groesse, PhotoImage-Groesse und Canvas-Image-Item in einem
        Schritt korrekt.
        """
        try:
            if w < 50 or h < 50:
                return False
            from types import SimpleNamespace
            self.canvas.resize(SimpleNamespace(width=int(w), height=int(h)))
            self._last_synced_wh = (w, h)
            return True
        except Exception:
            return False

    def _clear_tk_canvas(self) -> None:
        """Stellt sicher, dass das zugrunde liegende Tk-Canvas fuer saubere
        Redraws konfiguriert ist.

        Auf manchen Tk/Matplotlib-Backends kann ein alter Render-Buffer nach
        schnellen Resizes/Layout-Wechseln unter dem neuen sichtbar bleiben.
        """
        try:
            tk_canvas = getattr(self.canvas, "_tkcanvas", None)
            if tk_canvas is not None:
                # WICHTIG: nicht delete("all") aufrufen. FigureCanvasTkAgg
                # zeichnet ueber Items auf diesem Canvas (PhotoImage) -
                # alles zu loeschen kann das Render-Target entfernen und zu
                # einem leeren Diagramm fuehren.
                try:
                    tk_canvas.configure(bg=COLOR_ROOT, highlightthickness=0, bd=0)
                except Exception:
                    pass
        except Exception:
            pass
