"""Gemeinsame Temperatur-Farbskala fuer Puffer/Boiler (Energie- und Waerme-Tab).

Die Farbstopps haengen direkt an Grad Celsius statt an Anteilen einer Spanne -
so bleibt die Wahrnehmung gleich, auch wenn die Skala-Enden geaendert werden.

Nutzer-Feedback: unter 45 °C ist kalt, 50-55 °C mittel, ab ~57 °C ist der
Puffer schon "relativ warm", 65 °C ziemlich warm.
"""
from __future__ import annotations

TEMP_MIN = 30.0
TEMP_MAX = 80.0

# (°C, Farbe) - kalt: Navy -> Ozean -> Cyan, mittel: Gelb, warm: Bernstein -> Rot
STOPS_C: list[tuple[float, str]] = [
    (30.0, "#0a2540"),
    (37.0, "#0e3f6b"),
    (42.0, "#0f7ea8"),
    (46.0, "#22c3d6"),
    (49.0, "#8fe3e0"),
    (52.0, "#f2e07a"),   # mittel
    (56.0, "#f4a53d"),   # schon warm
    (62.0, "#e8542f"),
    (70.0, "#c81e3a"),
    (80.0, "#7a0f2a"),
]

# Beschriftung des Farbbalkens
TICKS_C = (30.0, 45.0, 55.0, 65.0, 80.0)

NO_DATA = "#2a2f3a"


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def temp_color(temp: float | None) -> str:
    """Temperatur (°C) -> #rrggbb."""
    if temp is None:
        return NO_DATA
    t = max(STOPS_C[0][0], min(STOPS_C[-1][0], float(temp)))
    for (t0, c0), (t1, c1) in zip(STOPS_C, STOPS_C[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            a, b = _hex(c0), _hex(c1)
            return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))
    return STOPS_C[-1][1]


def build_cmap(vmin: float = TEMP_MIN, vmax: float = TEMP_MAX):
    """Matplotlib-Colormap passend zu Normalize(vmin, vmax)."""
    from matplotlib.colors import LinearSegmentedColormap

    span = vmax - vmin
    stops = [(min(1.0, max(0.0, (t - vmin) / span)), c) for t, c in STOPS_C]
    stops[0] = (0.0, stops[0][1])
    stops[-1] = (1.0, stops[-1][1])
    return LinearSegmentedColormap.from_list("dashboard_temp", stops, N=512)


# --- Warmwasser-Boiler -------------------------------------------------------
# Der Boiler erreicht kaum mehr als 60 °C und wird anders "gelesen" als der
# Puffer: Duschwasser hat ~38-40 °C an der Brause. Liegt der Boiler nur knapp
# darueber, muss kaum kaltes Wasser beigemischt werden -> der Vorrat ist schnell
# weg. Nutzer: 45 °C im Boiler = mittelwarm.
#   < 38 °C  kalt - Duschen nicht mehr angenehm          -> blau
#   ~40 °C   lauwarm - nur noch kurz duschen             -> tuerkis
#   ~45 °C   mittel - eine Dusche geht gut               -> gelb
#   ~50 °C   warm - mehrere Duschen                      -> orange
#   55-60 °C heiss/voll (60 °C auch Legionellen-Schutz)  -> rot
# Umgesetzt als Umrechnung auf die Puffer-Skala, damit dieselben Farben
# dieselbe Bedeutung haben ("gelb = mittel", "rot = voll").
BOILER_TO_PUFFER_C: list[tuple[float, float]] = [
    (25.0, 30.0),
    (35.0, 40.0),
    (40.0, 46.0),
    (45.0, 52.0),
    (50.0, 57.0),
    (55.0, 63.0),
    (60.0, 70.0),
    (65.0, 78.0),
]


def boiler_equivalent(temp: float) -> float:
    pts = BOILER_TO_PUFFER_C
    t = max(pts[0][0], min(pts[-1][0], float(temp)))
    for (a0, b0), (a1, b1) in zip(pts, pts[1:]):
        if t <= a1:
            return b0 + (b1 - b0) * (t - a0) / (a1 - a0)
    return pts[-1][1]


def boiler_color(temp: float | None) -> str:
    """Boilertemperatur (°C) -> #rrggbb, auf Warmwasser-Komfort abgestimmt."""
    if temp is None:
        return NO_DATA
    return temp_color(boiler_equivalent(temp))
