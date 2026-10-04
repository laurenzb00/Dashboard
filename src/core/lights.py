"""Licht-Logik fuer den Licht-Tab (ohne UI, testbar).

* Lampenzustaende aus Home Assistant lesen (Helligkeit, Lichtfarbe, Faehigkeiten)
* Gruppierung nach HA-Bereichen ("Raeume")
* Stimmungen (Film, Essen, Lesen, Arbeiten, Nachtlicht, Party)
* Auto-Modus: Lichtfarbe nach Sonnenstand (morgens kuehl, abends warm)
* Ablaeufe: Aufwachen (Sonnenaufgang), Gute Nacht, Party-Farbwechsel
* Szene aus dem aktuellen Zustand speichern
* Favoriten/Einstellungen in data/light_prefs.json
"""
from __future__ import annotations

import colorsys
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

_PREFS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "light_prefs.json")
OTHER_ROOM = "Weitere"

# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------

@dataclass
class LightInfo:
    entity_id: str
    name: str
    on: bool
    available: bool = True
    brightness_pct: Optional[int] = None
    kelvin: Optional[int] = None
    min_kelvin: int = 2000
    max_kelvin: int = 6500
    supports_brightness: bool = False
    supports_ct: bool = False
    supports_color: bool = False
    rgb: Optional[tuple] = None
    effects: List[str] = field(default_factory=list)
    area: str = OTHER_ROOM


def parse_light(state: Dict[str, Any], area: Optional[str] = None) -> LightInfo:
    attrs = state.get("attributes") or {}
    modes = set(attrs.get("supported_color_modes") or [])
    st = str(state.get("state") or "").lower()
    bri = attrs.get("brightness")
    kelvin = attrs.get("color_temp_kelvin")
    if kelvin is None and attrs.get("color_temp"):
        try:
            kelvin = int(round(1_000_000 / float(attrs["color_temp"])))
        except (TypeError, ValueError, ZeroDivisionError):
            kelvin = None
    rgb = attrs.get("rgb_color")
    return LightInfo(
        entity_id=str(state.get("entity_id")),
        name=str(attrs.get("friendly_name") or state.get("entity_id")),
        on=st == "on",
        available=st not in ("unavailable", "unknown"),
        brightness_pct=int(round(bri / 255 * 100)) if isinstance(bri, (int, float)) and st == "on" else None,
        kelvin=int(kelvin) if kelvin else None,
        min_kelvin=int(attrs.get("min_color_temp_kelvin") or 2000),
        max_kelvin=int(attrs.get("max_color_temp_kelvin") or 6500),
        supports_brightness=bool(modes - {"onoff"}) or "brightness" in modes,
        supports_ct="color_temp" in modes,
        supports_color=bool(modes & {"hs", "rgb", "rgbw", "rgbww", "xy"}),
        rgb=tuple(rgb) if isinstance(rgb, (list, tuple)) and len(rgb) == 3 else None,
        effects=list(attrs.get("effect_list") or []),
        area=area or OTHER_ROOM,
    )


def collect_lights(states: List[Dict[str, Any]], areas: Optional[Dict[str, str]] = None,
                   only: Optional[List[str]] = None) -> List[LightInfo]:
    areas = areas or {}
    wanted = set(only) if only else None
    out = []
    for st in states:
        ent = str(st.get("entity_id") or "")
        if not ent.startswith("light.") or (wanted is not None and ent not in wanted):
            continue
        info = parse_light(st, areas.get(ent))
        if info.available:
            out.append(info)
    out.sort(key=lambda x: x.name.lower())
    return out


def group_by_room(lights: List[LightInfo]) -> Dict[str, List[LightInfo]]:
    rooms: Dict[str, List[LightInfo]] = {}
    for li in lights:
        rooms.setdefault(li.area, []).append(li)
    # Alphabetisch, "Weitere" zuletzt
    return dict(sorted(rooms.items(), key=lambda kv: (kv[0] == OTHER_ROOM, kv[0].lower())))


@dataclass
class RoomSummary:
    on_count: int
    total: int
    brightness_pct: Optional[int]   # Mittel der eingeschalteten
    kelvin: Optional[int]
    supports_ct: bool
    min_kelvin: int
    max_kelvin: int

    @property
    def text(self) -> str:
        if self.on_count == 0:
            return "aus"
        part = "an" if self.on_count == self.total else f"{self.on_count} von {self.total} an"
        if self.brightness_pct is not None:
            part += f" · {self.brightness_pct} %"
        return part


def summarize(lights: List[LightInfo]) -> RoomSummary:
    on = [li for li in lights if li.on]
    bris = [li.brightness_pct for li in on if li.brightness_pct is not None]
    kels = [li.kelvin for li in on if li.kelvin]
    ct = [li for li in lights if li.supports_ct]
    return RoomSummary(
        on_count=len(on), total=len(lights),
        brightness_pct=int(round(sum(bris) / len(bris))) if bris else (100 if on else None),
        kelvin=int(round(sum(kels) / len(kels))) if kels else None,
        supports_ct=bool(ct),
        min_kelvin=min((li.min_kelvin for li in ct), default=2000),
        max_kelvin=max((li.max_kelvin for li in ct), default=6500),
    )


# ---------------------------------------------------------------------------
# Farben (fuer Vorschau-Kacheln und Kelvin-Slider)
# ---------------------------------------------------------------------------

def kelvin_to_rgb(kelvin: float) -> tuple:
    """Naeherung (Tanner Helland) Farbtemperatur -> RGB."""
    t = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    return tuple(int(max(0, min(255, c))) for c in (r, g, b))


def rgb_hex(rgb: tuple, brightness: float = 1.0) -> str:
    f = max(0.15, min(1.0, brightness))
    return "#" + "".join(f"{int(c * f):02x}" for c in rgb)


def scene_preview_color(entities: Dict[str, Any]) -> Optional[str]:
    """Mittlere Farbe einer Szene aus ihrer HA-Konfiguration (fuer die Kachel)."""
    cols, bris = [], []
    for ent, cfg in (entities or {}).items():
        if not str(ent).startswith("light.") or not isinstance(cfg, dict):
            continue
        if str(cfg.get("state", "on")).lower() != "on":
            continue
        rgb = cfg.get("rgb_color")
        if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
            cols.append(tuple(rgb))
        elif cfg.get("color_temp_kelvin"):
            cols.append(kelvin_to_rgb(cfg["color_temp_kelvin"]))
        elif cfg.get("color_temp"):
            try:
                cols.append(kelvin_to_rgb(1_000_000 / float(cfg["color_temp"])))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        else:
            cols.append(kelvin_to_rgb(3000))
        if cfg.get("brightness") is not None:
            bris.append(float(cfg["brightness"]) / 255.0)
    if not cols:
        return None
    avg = tuple(sum(c[i] for c in cols) / len(cols) for i in range(3))
    return rgb_hex(avg, sum(bris) / len(bris) if bris else 0.9)


# ---------------------------------------------------------------------------
# Stimmungen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mood:
    key: str
    name: str
    icon: str
    brightness_pct: int
    kelvin: Optional[int] = None
    rgb: Optional[tuple] = None
    party: bool = False

    @property
    def preview(self) -> str:
        if self.rgb:
            return rgb_hex(self.rgb, 0.9)
        return rgb_hex(kelvin_to_rgb(self.kelvin or 3000), 0.35 + self.brightness_pct / 160)


MOODS: List[Mood] = [
    Mood("film", "Film", "🎬", 15, kelvin=2200),
    Mood("essen", "Essen", "🍽", 60, kelvin=2700),
    Mood("lesen", "Lesen", "📖", 80, kelvin=3500),
    Mood("arbeiten", "Arbeiten", "💻", 100, kelvin=5000),
    Mood("nachtlicht", "Nachtlicht", "🌙", 5, kelvin=2000, rgb=(255, 120, 40)),
    Mood("party", "Party", "🎉", 80, rgb=(255, 0, 180), party=True),
]


def mood_commands(mood: Mood, lights: List[LightInfo]) -> List[tuple[List[str], Dict[str, Any]]]:
    """Service-Aufrufe (entity_ids, daten) fuer eine Stimmung - je Lampentyp passend."""
    groups: Dict[str, List[str]] = {"color": [], "ct": [], "dim": [], "onoff": []}
    for li in lights:
        if mood.rgb and li.supports_color:
            groups["color"].append(li.entity_id)
        elif li.supports_ct:
            groups["ct"].append(li.entity_id)
        elif li.supports_brightness:
            groups["dim"].append(li.entity_id)
        else:
            groups["onoff"].append(li.entity_id)
    cmds = []
    if groups["color"]:
        cmds.append((groups["color"], {"brightness_pct": mood.brightness_pct, "rgb_color": list(mood.rgb), "transition": 1}))
    if groups["ct"]:
        k = mood.kelvin or 3000
        cmds.append((groups["ct"], {"brightness_pct": mood.brightness_pct, "color_temp_kelvin": k, "transition": 1}))
    if groups["dim"]:
        cmds.append((groups["dim"], {"brightness_pct": mood.brightness_pct, "transition": 1}))
    if groups["onoff"]:
        cmds.append((groups["onoff"], {}))
    return cmds


def clamp_kelvin(k: int, lights: List[LightInfo]) -> int:
    ct = [li for li in lights if li.supports_ct]
    if not ct:
        return k
    lo = max(li.min_kelvin for li in ct)
    hi = min(li.max_kelvin for li in ct)
    if lo > hi:
        lo, hi = min(li.min_kelvin for li in ct), max(li.max_kelvin for li in ct)
    return int(max(lo, min(hi, k)))


# ---------------------------------------------------------------------------
# Auto-Modus (Lichtfarbe nach Sonnenstand)
# ---------------------------------------------------------------------------

def circadian_kelvin(sun_elevation_deg: float) -> int:
    """Nacht 2200 K, Daemmerung ~2700 K, hohe Sonne bis 5000 K."""
    e = sun_elevation_deg
    if e <= -6:
        return 2200
    if e <= 0:
        return int(2200 + (e + 6) / 6 * 500)          # 2200 -> 2700
    if e >= 35:
        return 5000
    return int(2700 + e / 35 * 2300)                   # 2700 -> 5000


# ---------------------------------------------------------------------------
# Szene speichern
# ---------------------------------------------------------------------------

def snapshot_entities(states: List[Dict[str, Any]], entity_ids: List[str]) -> Dict[str, Any]:
    """Aktuellen Zustand der Lampen als Szenen-Konfiguration (Format des HA-Szenen-Editors)."""
    wanted = set(entity_ids)
    out: Dict[str, Any] = {}
    for st in states:
        ent = str(st.get("entity_id") or "")
        if ent not in wanted:
            continue
        attrs = st.get("attributes") or {}
        state = str(st.get("state") or "off")
        cfg: Dict[str, Any] = {"state": state}
        if state == "on":
            mode = attrs.get("color_mode")
            if attrs.get("brightness") is not None:
                cfg["brightness"] = attrs["brightness"]
            if mode == "color_temp" and attrs.get("color_temp_kelvin"):
                cfg["color_mode"] = "color_temp"
                cfg["color_temp_kelvin"] = attrs["color_temp_kelvin"]
            elif mode in ("hs", "xy", "rgb", "rgbw", "rgbww") and attrs.get("rgb_color"):
                cfg["color_mode"] = mode
                if attrs.get("hs_color"):
                    cfg["hs_color"] = attrs["hs_color"]
                cfg["rgb_color"] = attrs["rgb_color"]
        out[ent] = cfg
    return out


def scene_config_id(name: str, existing: Optional[set] = None) -> str:
    base = "dashboard_" + (re.sub(r"[^a-z0-9]+", "_", name.lower().replace("ä", "ae").replace("ö", "oe")
                                  .replace("ü", "ue").replace("ß", "ss")).strip("_") or "szene")
    sid, n = base, 2
    while existing and sid in existing:
        sid, n = f"{base}_{n}", n + 1
    return sid


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

def load_prefs() -> Dict[str, Any]:
    try:
        with open(_PREFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
        tmp = _PREFS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=1)
        os.replace(tmp, _PREFS_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Ablaeufe (laufen im Hintergrund, abbrechbar)
# ---------------------------------------------------------------------------

class Routine:
    """Basis: Thread mit Stop-Event. `on_done(name)` wird nach Ende/Abbruch gerufen."""

    name = "routine"

    def __init__(self, client, on_done: Optional[Callable[[str], None]] = None):
        self.client = client
        self.on_done = on_done
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_at: Optional[float] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._wrap, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _sleep(self, seconds: float) -> bool:
        """True = weiter, False = abgebrochen."""
        return not self._stop.wait(seconds)

    def _wrap(self) -> None:
        try:
            self.run()
        except Exception:
            pass
        finally:
            if self.on_done:
                try:
                    self.on_done(self.name)
                except Exception:
                    pass

    def run(self) -> None:  # pragma: no cover - ueberschrieben
        raise NotImplementedError


class WakeUp(Routine):
    """Sonnenaufgang: von 1 % sehr warm auf 100 % neutral ueber `minutes`."""

    name = "wakeup"

    def __init__(self, client, lights: List[LightInfo], minutes: int = 20, on_done=None):
        super().__init__(client, on_done)
        self.lights = lights
        self.minutes = max(1, int(minutes))

    def run(self) -> None:
        ids = [li.entity_id for li in self.lights]
        ct_ids = [li.entity_id for li in self.lights if li.supports_ct]
        steps = max(4, self.minutes * 2)            # alle 30 s ein Schritt
        dt = self.minutes * 60.0 / steps
        for i in range(steps + 1):
            f = i / steps
            bri = max(1, int(round(1 + 99 * f ** 1.6)))
            kelvin = int(2000 + 2000 * f)
            if ct_ids:
                self.client.light_turn_on(ct_ids, brightness_pct=bri, color_temp_kelvin=kelvin, transition=dt)
            other = [e for e in ids if e not in ct_ids]
            if other:
                self.client.light_turn_on(other, brightness_pct=bri, transition=dt)
            if i < steps and not self._sleep(dt):
                return


class GoodNight(Routine):
    """Alles aus - ein Licht (z.B. Flur/Vorraum) bleibt noch `keep_minutes` an."""

    name = "goodnight"

    def __init__(self, client, all_lights: List[str], keep_entity: Optional[str], keep_minutes: float = 2.0,
                 on_done=None):
        super().__init__(client, on_done)
        self.all_lights = all_lights
        self.keep_entity = keep_entity
        self.keep_minutes = keep_minutes

    def run(self) -> None:
        others = [e for e in self.all_lights if e != self.keep_entity]
        if others:
            self.client.call_service("light", "turn_off", {"entity_id": others, "transition": 3})
        if self.keep_entity:
            domain = self.keep_entity.split(".", 1)[0]
            if domain == "light":
                self.client.light_turn_on([self.keep_entity], brightness_pct=10)
            else:
                self.client.call_service("homeassistant", "turn_on", {"entity_id": self.keep_entity})
            if not self._sleep(self.keep_minutes * 60):
                return
            self.client.call_service("homeassistant", "turn_off", {"entity_id": self.keep_entity})


class Party(Routine):
    """Farbwechsel. Nutzt den 'colorloop'-Effekt der Lampe, sonst wechselt das Dashboard die Farben."""

    name = "party"

    def __init__(self, client, lights: List[LightInfo], period_s: float = 4.0, on_done=None):
        super().__init__(client, on_done)
        self.lights = [li for li in lights if li.supports_color]
        self.period_s = period_s

    def run(self) -> None:
        # Lampen mit eingebautem Farbwechsel-Effekt, gruppiert nach Effektname
        by_effect: Dict[str, List[str]] = {}
        for li in self.lights:
            eff = next((e for e in li.effects if e.lower() == "colorloop"), None)
            if eff:
                by_effect.setdefault(eff, []).append(li.entity_id)
        loop = [e for ids in by_effect.values() for e in ids]
        manual = [li.entity_id for li in self.lights if li.entity_id not in loop]
        for eff, ids in by_effect.items():
            self.client.light_turn_on(ids, effect=eff, brightness_pct=80)
        hue = 0.0
        while manual and not self._stop.is_set():
            r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            self.client.light_turn_on(manual, rgb_color=[int(r * 255), int(g * 255), int(b * 255)],
                                      brightness_pct=80, transition=self.period_s * 0.9)
            hue = (hue + 0.13) % 1.0
            if not self._sleep(self.period_s):
                break
        if not manual:
            self._stop.wait()
        if loop:
            try:
                self.client.light_turn_on(loop, effect="none")
            except Exception:
                pass
