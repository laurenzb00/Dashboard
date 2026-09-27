"""Gemeinsamer Matplotlib-Achsen-Stil fuer alle Chart-Tabs.

historical.py und tagesproduktion.py hatten bisher fast identischen, aber
leicht divergierten Style-Code fuer ihre Achsen (Grid-Alpha 0.20 vs. 0.08,
Ticklabel-Groesse 11 vs. 9, Spine-Linienbreite 0.5 vs. 0.6) - bei sonst
nahezu gleichem Card-/Achsen-Aufbau wirkte die eine Grafik dadurch deutlich
"dichter"/kontrastreicher als die andere, obwohl beide dieselbe Design-
sprache (Card, COLOR_ROOT-Hintergrund, dezente COLOR_BORDER-Gitterlinien)
verwenden sollen. apply_chart_style() vereinheitlicht das an einer Stelle,
damit neue Chart-Tabs automatisch denselben Look erben.
"""
from __future__ import annotations

from ui.styles import COLOR_ROOT, COLOR_BORDER, COLOR_SUBTEXT

# Einheitliche Werte - liegen zwischen den beiden bisher divergierten
# Chart-Tabs, statt einen der beiden alten Werte einfach zu "gewinnen".
GRID_ALPHA = 0.14
GRID_LINEWIDTH = 0.6
TICK_LABELSIZE = 10
TICK_LENGTH = 3
TICK_WIDTH = 0.5
SPINE_LINEWIDTH = 0.6


def apply_chart_style(ax, grid_axis: str = "both") -> None:
    """Wendet den einheitlichen Achsen-Stil auf ein Matplotlib-Axes-Objekt an.

    `grid_axis` steuert, auf welchen Achsen Gitterlinien erscheinen:
    "both" (Standard), "x", "y" oder "none" fuer kein Gitter. Ruft weder
    set_ylabel/set_xlabel noch tick_params(axis="x", pad=...) auf - das
    bleibt Sache des jeweiligen Tabs, da Achsenbeschriftungen fachlich
    unterschiedlich sind (z.B. "°C" vs. "PV (kWh / Tag)").
    """
    ax.set_facecolor(COLOR_ROOT)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(COLOR_BORDER)
    ax.spines["bottom"].set_color(COLOR_BORDER)
    ax.spines["left"].set_linewidth(SPINE_LINEWIDTH)
    ax.spines["bottom"].set_linewidth(SPINE_LINEWIDTH)
    ax.tick_params(
        axis="both",
        which="major",
        labelsize=TICK_LABELSIZE,
        colors=COLOR_SUBTEXT,
        length=TICK_LENGTH,
        width=TICK_WIDTH,
    )

    if grid_axis == "none":
        ax.grid(False)
        return

    ax.grid(True, axis=grid_axis, color=COLOR_BORDER, alpha=GRID_ALPHA, linewidth=GRID_LINEWIDTH)
    if grid_axis == "y":
        ax.grid(False, axis="x")
    elif grid_axis == "x":
        ax.grid(False, axis="y")
