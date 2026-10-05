"""Sonnenstand und Einstrahlung auf geneigte Flaechen (numpy, ohne Zusatzpakete).

* sun_position(unix_utc, lat, lon) -> (Hoehe°, Azimut°) mit Azimut 0 = Sued,
  -90 = Ost, +90 = West (gleiche Konvention wie Open-Meteo/PV-Ausrichtung).
  NOAA-Naeherung, ~0,5° genau - fuer Stundenwerte mehr als ausreichend.
* poa(...) -> Einstrahlung auf die Modulebene (W/m^2), isotropes Himmelsmodell:
      POA = DNI*cos(Einfallswinkel) + DHI*(1+cos b)/2 + GHI*Albedo*(1-cos b)/2
* clearness(...) -> Klarheitsindex kt = GHI / extraterrestrische Horizontal-
  einstrahlung (0 = bedeckt, ~0,75 = wolkenlos).
"""
from __future__ import annotations

import numpy as np

SOLAR_CONST = 1361.0
ALBEDO = 0.2


def sun_position(unix_utc, lat: float, lon: float):
    t = np.asarray(unix_utc, dtype=float)
    # Tag des Jahres und Stunde (UTC) exakt aus dem Kalender
    dt = t.astype("datetime64[s]")
    year_start = dt.astype("datetime64[Y]").astype("datetime64[s]")
    doy = (dt - year_start).astype("timedelta64[s]").astype(float) / 86400.0  # 0-basiert inkl. Tagesbruchteil
    hour = (doy - np.floor(doy)) * 24.0
    g = 2.0 * np.pi / 365.0 * (np.floor(doy) + (hour - 12.0) / 24.0)
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    true_solar_min = hour * 60.0 + eqtime + 4.0 * lon
    ha = np.radians(true_solar_min / 4.0 - 180.0)          # Stundenwinkel, 0 = Mittag
    phi = np.radians(lat)
    cos_zen = np.clip(np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(ha), -1.0, 1.0)
    zen = np.arccos(cos_zen)
    elev = 90.0 - np.degrees(zen)
    # Azimut ab Sued, positiv nach Westen
    az = np.degrees(np.arctan2(np.sin(ha), np.cos(ha) * np.sin(phi) - np.tan(decl) * np.cos(phi)))
    return elev, az


def extraterrestrial_horizontal(unix_utc, elev_deg):
    t = np.asarray(unix_utc, dtype=float)
    doy = (t / 86400.0) % 365.2425
    e0 = SOLAR_CONST * (1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.2425))
    return e0 * np.clip(np.sin(np.radians(elev_deg)), 0.0, None)


def clearness(ghi, unix_utc, elev_deg):
    i0 = extraterrestrial_horizontal(unix_utc, elev_deg)
    with np.errstate(divide="ignore", invalid="ignore"):
        kt = np.where(i0 > 20.0, np.asarray(ghi, dtype=float) / i0, 0.0)
    return np.clip(np.nan_to_num(kt), 0.0, 1.2)


def incidence_cos(elev_deg, az_deg, tilt_deg: float, surf_az_deg: float):
    e, a = np.radians(elev_deg), np.radians(az_deg)
    b, s = np.radians(tilt_deg), np.radians(surf_az_deg)
    return np.sin(e) * np.cos(b) + np.cos(e) * np.sin(b) * np.cos(a - s)


def poa(ghi, dni, dhi, elev_deg, az_deg, tilt_deg: float, surf_az_deg: float):
    """Einstrahlung auf die Modulebene (W/m^2) und Anteil der Direktstrahlung daran."""
    ghi, dni, dhi = (np.nan_to_num(np.asarray(x, dtype=float)) for x in (ghi, dni, dhi))
    up = np.asarray(elev_deg) > 0.5
    cos_i = np.clip(incidence_cos(elev_deg, az_deg, tilt_deg, surf_az_deg), 0.0, None)
    beam = np.where(up, dni * cos_i, 0.0)
    b = np.radians(tilt_deg)
    diffuse = dhi * (1.0 + np.cos(b)) / 2.0 + ghi * ALBEDO * (1.0 - np.cos(b)) / 2.0
    total = beam + diffuse
    return total, beam
