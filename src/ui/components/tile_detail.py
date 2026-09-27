"""Long-Press-Detailansicht fuer MetricTile: 24h-Verlauf einer Kennzahl.

Liegt (wie StandbyOverlay) als eigenstaendiges place()-Widget direkt auf dem
Fenster-Root statt als separates Toplevel-Fenster - auf dem Kiosk-Display
(meist ohne echten Fenstermanager) verhalten sich zusaetzliche Toplevels
unzuverlaessig (koennen hinter dem Vollbildfenster landen oder keinen Fokus
bekommen), ein place()-Overlay dagegen liegt garantiert sichtbar obenauf
innerhalb desselben Fensters.

Pro Fenster wird nur eine einzige Overlay-Instanz angelegt und von allen
Kacheln wiederverwendet (siehe get_shared_overlay()), damit nicht jede
MetricTile ihr eigenes, staendig im Speicher gehaltenes Popup-Widget braucht.
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional, Sequence, Tuple

import customtkinter as ctk
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from ui.components.chart_style import apply_chart_style
from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    get_safe_font,
)

_OVERLAY_ATTR = "_tile_detail_overlay"


class TileDetailOverlay(ctk.CTkFrame):
    """Vollflaechiges, halbmodales Popover mit einem kleinen 24h-Diagramm."""

    def __init__(self, root: tk.Misc):
        super().__init__(root, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)

        # Tap auf den Hintergrund (ausserhalb der Karte) schliesst das
        # Popover wieder - derselbe Grundgedanke wie bei einem klassischen
        # modalen Dialog, nur ohne echtes zweites Fenster.
        self.bind("<Button-1>", lambda e: self.hide())

        self.card = ctk.CTkFrame(
            self, fg_color=COLOR_CARD, corner_radius=18, border_width=2, border_color=COLOR_BORDER
        )
        self.card.place(relx=0.5, rely=0.5, anchor="center")
        # Klicks auf die Karte selbst duerfen NICHT zum Hintergrund
        # durchschlagen, sonst wuerde jeder Tap auf die Karte sie sofort
        # wieder schliessen.
        self.card.bind("<Button-1>", lambda e: "break")

        header = ctk.CTkFrame(self.card, fg_color="transparent")
        header.pack(fill=tk.X, padx=20, pady=(16, 4))
        header.bind("<Button-1>", lambda e: "break")
        self.title_label = ctk.CTkLabel(
            header, text="", font=get_safe_font("Bahnschrift", 17, "bold"), text_color=COLOR_TEXT
        )
        self.title_label.pack(side=tk.LEFT)
        self.range_label = ctk.CTkLabel(
            header, text="Letzte 24h", font=get_safe_font("Bahnschrift", 11), text_color=COLOR_SUBTEXT
        )
        self.range_label.pack(side=tk.RIGHT)

        self.chart_frame = ctk.CTkFrame(self.card, fg_color="transparent", width=460, height=220)
        self.chart_frame.pack(padx=20, pady=(4, 4))
        self.chart_frame.pack_propagate(False)
        self.chart_frame.bind("<Button-1>", lambda e: "break")

        self.stats_label = ctk.CTkLabel(
            self.card, text="", font=get_safe_font("Bahnschrift", 12), text_color=COLOR_SUBTEXT
        )
        self.stats_label.pack(pady=(0, 4))

        self.close_btn = ctk.CTkButton(
            self.card,
            text="Schließen",
            command=self.hide,
            fg_color=COLOR_BORDER,
            hover_color=COLOR_PRIMARY,
            height=38,
            font=get_safe_font("Bahnschrift", 12, "bold"),
        )
        self.close_btn.pack(fill=tk.X, padx=20, pady=(4, 18))

        self._auto_close_id: Optional[str] = None

    def show(
        self,
        title: str,
        unit: str,
        color: str,
        series: Sequence[Tuple[object, float]],
    ) -> None:
        """Zeigt das Popover mit dem Verlauf `series` ([(datetime, wert), ...])."""
        self.title_label.configure(text=title)
        for child in self.chart_frame.winfo_children():
            child.destroy()

        series = list(series or [])
        if len(series) >= 2:
            times = [t for t, _ in series]
            values = [v for _, v in series]
            fig = Figure(figsize=(4.4, 2.1), dpi=100)
            fig.patch.set_facecolor(COLOR_ROOT)
            ax = fig.add_subplot(111)
            ax.plot(times, values, color=color, linewidth=1.8)
            ax.fill_between(times, values, min(values), color=color, alpha=0.12)
            apply_chart_style(ax, grid_axis="y")
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=5))
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
            fig.tight_layout(pad=0.6)
            canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            last_val, min_val, max_val = values[-1], min(values), max(values)
            self.stats_label.configure(
                text=f"Aktuell {last_val:.1f}{unit}  ·  Min {min_val:.1f}{unit}  ·  Max {max_val:.1f}{unit}"
            )
        else:
            ctk.CTkLabel(
                self.chart_frame,
                text="Noch nicht genug Daten für einen Verlauf.",
                font=get_safe_font("Bahnschrift", 12),
                text_color=COLOR_SUBTEXT,
            ).pack(expand=True)
            self.stats_label.configure(text="")

        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()

        if self._auto_close_id is not None:
            try:
                self.after_cancel(self._auto_close_id)
            except Exception:
                pass
        # Auto-close nach 20s, falls die Karte auf dem Kiosk-Display (ohne
        # Tastatur/Maus) versehentlich offen bleibt.
        self._auto_close_id = self.after(20000, self.hide)

    def hide(self) -> None:
        if self._auto_close_id is not None:
            try:
                self.after_cancel(self._auto_close_id)
            except Exception:
                pass
            self._auto_close_id = None
        try:
            self.place_forget()
        except Exception:
            pass


def get_shared_overlay(root: tk.Misc) -> TileDetailOverlay:
    """Liefert das (pro Fenster einmalige) TileDetailOverlay, legt es bei
    Bedarf an."""
    overlay = getattr(root, _OVERLAY_ATTR, None)
    alive = False
    if overlay is not None:
        try:
            alive = bool(overlay.winfo_exists())
        except Exception:
            alive = False
    if overlay is None or not alive:
        overlay = TileDetailOverlay(root)
        setattr(root, _OVERLAY_ATTR, overlay)
    return overlay
