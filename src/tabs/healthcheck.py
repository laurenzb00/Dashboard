import os
import time
from datetime import datetime, timezone
from pathlib import Path
import threading
import requests
import subprocess
import shutil
import shlex

import tkinter as tk
import customtkinter as ctk

from ui.styles import (
    COLOR_ROOT,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_TITLE,
    COLOR_PRIMARY,
    COLOR_CARD,
    COLOR_BORDER,
    emoji,
)
from ui.components.card import Card
from ui.components.tab_shell import TabShell
from core.health import (
    get_health_snapshot,
    update_source_health,
    check_tcp_reachable,
    host_port_from_url,
)
from core import Wechselrichter
from core import BMKDATEN


def _fmt_age_minutes(dt: datetime | None) -> str:
    if not dt:
        return "–"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_s = max(0.0, (now - dt).total_seconds())
    if age_s < 120:
        return f"{int(age_s)}s"
    age_m = age_s / 60.0
    if age_m < 120:
        return f"{age_m:.0f}m"
    age_h = age_m / 60.0
    return f"{age_h:.1f}h"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        # By convention in this project, naive timestamps are UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_gap_minutes(timestamps: list[str]) -> float | None:
    dts = [d for d in (_parse_ts(t) for t in timestamps) if d is not None]
    if len(dts) < 3:
        return None
    dts.sort()
    max_gap_s = 0.0
    for a, b in zip(dts, dts[1:]):
        max_gap_s = max(max_gap_s, (b - a).total_seconds())
    return max_gap_s / 60.0


def _fmt_age_local(dt: datetime | None) -> str:
    """Wie _fmt_age_minutes(), aber fuer core.health-Zeitstempel.

    core/health.py schreibt seine Zeitstempel mit dem lokalen, naiven
    datetime.now() (nicht UTC wie die DB-Zeitstempel). Wuerde man hier
    _fmt_age_minutes() wiederverwenden, wuerde das Alter um die lokale
    UTC-Verschiebung falsch berechnet (in Oesterreich 1-2h daneben).
    """
    if not dt:
        return "–"
    now = datetime.now()
    age_s = max(0.0, (now - dt).total_seconds())
    if age_s < 120:
        return f"{int(age_s)}s"
    age_m = age_s / 60.0
    if age_m < 120:
        return f"{age_m:.0f}m"
    age_h = age_m / 60.0
    return f"{age_h:.1f}h"


def _source_status_line(entry) -> tuple[str, str]:
    """Formatiert einen core.health.SourceHealth-Eintrag zu (Icon, Text).

    "Aktueller" Status = welches Ereignis (letzter Erfolg oder letzter
    Fehler) zeitlich zuletzt aufgetreten ist. error_count zaehlt dagegen
    kumulativ seit Programmstart und sagt fuer sich allein nichts darueber
    aus, ob die Quelle gerade jetzt noch klemmt oder sich laengst wieder
    erholt hat.
    """
    if entry is None or (entry.last_ok is None and entry.last_error is None):
        return "⚪", "noch keine Daten seit Programmstart"

    is_currently_ok = entry.last_ok is not None and (
        entry.last_error is None or entry.last_ok >= entry.last_error
    )
    if is_currently_ok:
        lat = f", {entry.last_latency_ms}ms" if entry.last_latency_ms is not None else ""
        return "🟢", f"OK, letzte Daten vor {_fmt_age_local(entry.last_ok)}{lat}"

    msg = f" – {entry.last_error_msg}" if entry.last_error_msg else ""
    return "🔴", (
        f"seit {_fmt_age_local(entry.last_error)} keine Verbindung{msg} "
        f"({entry.error_count}x Fehler seit Programmstart)"
    )


class HealthTab:
    """Simple health check + self-healing tools."""

    def __init__(self, root: tk.Tk, notebook, datastore=None, app=None, tab_frame=None, help_tab_cls=None, homeassistant_actions_cls=None):
        self.root = root
        self.notebook = notebook
        self.datastore = datastore
        self.app = app
        self.help_tab = None
        # help_tab_cls/homeassistant_actions_cls werden von app.py durch-
        # gereicht (dort schon per try/except importiert), statt hier selbst
        # zu importieren - ein fehlgeschlagener Import von tabs.help oder
        # tabs.homeassistant_actions soll den Rest des Health-Tabs nicht mit
        # reissen (gleiche Begruendung wie in tabs/help.py fuer
        # homeassistant_actions_cls).
        self._help_tab_cls = help_tab_cls
        self._homeassistant_actions_cls = homeassistant_actions_cls

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("🩺 Health", "Health"))

        try:
            self.tab_frame.configure(fg_color=COLOR_ROOT)
        except Exception:
            pass

        self._build_ui()
        self._refresh_after_id = None
        self.root.after(500, self.refresh)

    def stop(self):
        try:
            if self._refresh_after_id is not None:
                self.root.after_cancel(self._refresh_after_id)
        except Exception:
            pass
        self._refresh_after_id = None

    def _build_ui(self) -> None:
        container = TabShell(self.tab_frame, "Health Check", "Datenqualität und Integrationen")
        self._shell = container
        container.pack(fill=tk.BOTH, expand=True)

        self._refresh_btn = ctk.CTkButton(
            container.header,
            text="Refresh",
            fg_color=COLOR_PRIMARY,
            command=self.refresh,
            width=144,
            height=48,
            font=("Segoe UI", 13, "bold"),
        )
        self._refresh_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=18, pady=14)

        # Sub-Tab-Leiste im Health-Tab: "Übersicht" (der bisherige Inhalt
        # dieses Tabs) und "Help" (vorher ein eigener oberster Tab, jetzt
        # hier als Reiter untergebracht - siehe tabs/help.py). Gleiches
        # nested-CTkTabview-Muster wie im Spotify-Tab und im Help-Tab selbst.
        self.content_notebook = ctk.CTkTabview(
            container.body,
            fg_color=COLOR_ROOT,
            border_color=COLOR_ROOT,
            segmented_button_fg_color=COLOR_ROOT,
            segmented_button_selected_color=COLOR_PRIMARY,
            segmented_button_selected_hover_color=COLOR_PRIMARY,
            segmented_button_unselected_color=COLOR_CARD,
            segmented_button_unselected_hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            text_color_disabled=COLOR_SUBTEXT,
        )
        self.content_notebook.pack(fill=tk.BOTH, expand=True)

        self.content_notebook.add("Übersicht")
        uebersicht_frame = self.content_notebook.tab("Übersicht")

        self.content_notebook.add("Help")
        help_frame = self.content_notebook.tab("Help")

        try:
            segmented = getattr(self.content_notebook, "_segmented_button", None)
            if segmented is not None:
                segmented.configure(
                    font=("Segoe UI", 13, "bold"),
                    height=44,
                    corner_radius=14,
                    border_width=1,
                    border_color=COLOR_BORDER,
                )
        except Exception:
            pass

        if self._help_tab_cls is not None:
            try:
                self.help_tab = self._help_tab_cls(
                    self.root,
                    self.content_notebook,
                    tab_frame=help_frame,
                    homeassistant_actions_cls=self._homeassistant_actions_cls,
                )
            except Exception:
                self.help_tab = None

        grid = ctk.CTkFrame(uebersicht_frame, fg_color="transparent")
        grid.pack(fill=tk.BOTH, expand=True)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        self.card_data = Card(grid)
        self.card_data.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self.card_data.add_title("Datenqualität", icon="🧰")

        self.card_int = Card(grid)
        self.card_int.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        self.card_int.add_title("Integrationen", icon="🔌")
        self._health_grid = grid

        # Integrations-Labels (Home Assistant/Tado/Spotify). War vorher aus
        # Versehen in set_portrait_layout() statt hier - dadurch wurden bei
        # jedem Wechsel Portrait/Landscape neue StringVars + Labels erzeugt
        # und zusaetzlich in card_int gepackt, ohne die alten zu entfernen
        # (unbegrenzt wachsende doppelte Zeilen bei jeder Drehung).
        self.var_hue = tk.StringVar(value="Home Assistant: –")
        self.var_tado = tk.StringVar(value="Tado: –")
        self.var_spotify = tk.StringVar(value="Spotify: –")

        body2 = ctk.CTkFrame(self.card_int.content(), fg_color="transparent")
        body2.pack(fill=tk.BOTH, expand=True)
        for v in (self.var_hue, self.var_tado, self.var_spotify):
            ctk.CTkLabel(body2, textvariable=v, font=("Segoe UI", 12), text_color=COLOR_TEXT).pack(anchor="w", pady=2)

        # Data labels
        self.var_db = tk.StringVar(value="DB ingest: –")
        self.var_pv = tk.StringVar(value="PV: –")
        self.var_heat = tk.StringVar(value="Heizung: –")
        self.var_pv_status = tk.StringVar(value="⚪ noch keine Daten seit Programmstart")
        self.var_heat_status = tk.StringVar(value="⚪ noch keine Daten seit Programmstart")
        self.var_gap_pv = tk.StringVar(value="PV gap(24h): –")
        self.var_gap_heat = tk.StringVar(value="Heizung gap(24h): –")
        self.var_cache = tk.StringVar(value="Sparkline cache: –")
        self.var_selfheal = tk.StringVar(value="Self-Heal: –")
        self.var_update = tk.StringVar(value="Update: –")
        self.var_last_update = tk.StringVar(value="Letztes Update: –")

        body = ctk.CTkFrame(self.card_data.content(), fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(body, textvariable=self.var_db, font=("Segoe UI", 14), text_color=COLOR_TEXT).pack(anchor="w", pady=4)

        # PV/Heizung: Alterszeile + Live-Verbindungsstatus (core.health,
        # von main.py's Polling-Threads befuellt) + "Jetzt prüfen"-Button,
        # der einen echten Verbindungs-/Abrufversuch auf Knopfdruck anstoesst.
        self._build_source_row(body, self.var_pv, self.var_pv_status, self._check_pv_now, "pv_check")
        self._build_source_row(body, self.var_heat, self.var_heat_status, self._check_heating_now, "heat_check")

        for v in (
            self.var_gap_pv,
            self.var_gap_heat,
            self.var_cache,
            self.var_selfheal,
            self.var_update,
            self.var_last_update,
        ):
            ctk.CTkLabel(body, textvariable=v, font=("Segoe UI", 14), text_color=COLOR_TEXT).pack(anchor="w", pady=4)

        # Load last update info on startup
        self._load_last_update_info()

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill=tk.X, pady=(10, 0))
        btn_row.grid_columnconfigure(0, weight=0)
        btn_row.grid_columnconfigure(1, weight=0)
        btn_row.grid_columnconfigure(2, weight=0)
        btn_row.grid_columnconfigure(3, weight=1)
        self._rebuild_cache_btn = ctk.CTkButton(
            btn_row,
            text="Cache neu bauen",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=self._rebuild_spark_cache,
            width=190,
            height=48,
        )
        self._rebuild_cache_btn.grid(row=0, column=0, sticky="w")

        self._selfheal_btn = ctk.CTkButton(
            btn_row,
            text="Self-Heal",
            fg_color=COLOR_PRIMARY,
            command=self._self_heal,
            width=160,
            height=48,
        )
        self._selfheal_btn.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self._update_btn = ctk.CTkButton(
            btn_row,
            text="Update + Neustart",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=self._git_pull_and_restart,
            width=210,
            height=48,
        )
        self._update_btn.grid(row=0, column=2, sticky="w", padx=(10, 0))

    def set_portrait_layout(self, portrait: bool) -> None:
        """Stack health cards for portrait screens; forward to the nested
        Help-Reiter (der wiederum an homeassistant_actions_tab weiterreicht -
        siehe tabs/help.py) so beide Ebenen der Verschachtelung reagieren."""
        try:
            if hasattr(self, "_shell"):
                self._shell.set_portrait_layout(portrait)
            if portrait:
                self._health_grid.grid_columnconfigure(0, weight=1)
                self._health_grid.grid_columnconfigure(1, weight=0)
                self.card_data.grid_configure(row=0, column=0, columnspan=2, padx=0, pady=(0, 12))
                self.card_int.grid_configure(row=1, column=0, columnspan=2, padx=0, pady=0)
            else:
                self._health_grid.grid_columnconfigure(0, weight=1)
                self._health_grid.grid_columnconfigure(1, weight=1)
                self.card_data.grid_configure(row=0, column=0, columnspan=1, padx=(0, 6), pady=0)
                self.card_int.grid_configure(row=0, column=1, columnspan=1, padx=(6, 0), pady=0)
        except Exception:
            pass

        setter = getattr(self.help_tab, "set_portrait_layout", None)
        if callable(setter):
            try:
                setter(portrait)
            except Exception:
                pass

    def _build_source_row(self, parent, age_var: tk.StringVar, status_var: tk.StringVar, check_cmd, btn_attr: str) -> None:
        """Baut eine Zeile fuer eine Datenquelle (PV/Heizung): DB-Alter,
        Live-Verbindungsstatus aus core.health und einen "Jetzt prüfen"-
        Button, der einen echten Erreichbarkeits-/Abrufversuch anstoesst."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill=tk.X, pady=4)
        row.grid_columnconfigure(0, weight=1)

        labels = ctk.CTkFrame(row, fg_color="transparent")
        labels.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(labels, textvariable=age_var, font=("Segoe UI", 14), text_color=COLOR_TEXT).pack(anchor="w")
        ctk.CTkLabel(labels, textvariable=status_var, font=("Segoe UI", 12), text_color=COLOR_SUBTEXT).pack(anchor="w")

        btn = ctk.CTkButton(
            row,
            text="Jetzt prüfen",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=check_cmd,
            width=130,
            height=36,
            font=("Segoe UI", 12),
        )
        btn.grid(row=0, column=1, sticky="e", padx=(10, 0))
        setattr(self, btn_attr + "_btn", btn)

    def _check_pv_now(self) -> None:
        self._check_source_now(
            status_var=self.var_pv_status,
            btn=getattr(self, "pv_check_btn", None),
            host_url=Wechselrichter.FRONIUS_URL,
            fetch_fn=Wechselrichter.abrufen_und_speichern,
            health_name="pv",
            label="PV",
        )

    def _check_heating_now(self) -> None:
        self._check_source_now(
            status_var=self.var_heat_status,
            btn=getattr(self, "heat_check_btn", None),
            host_url=BMKDATEN.BMK_URL,
            fetch_fn=BMKDATEN.abrufen_und_speichern,
            health_name="heating",
            label="Heizung",
        )

    def _check_source_now(self, status_var: tk.StringVar, btn, host_url: str, fetch_fn, health_name: str, label: str) -> None:
        """Manueller "Jetzt prüfen"-Check: erst TCP-Erreichbarkeit testen,
        dann bei Erreichbarkeit einen echten Abrufversuch ueber dieselbe
        Funktion wie das normale Polling ausfuehren. So bekommt der Nutzer
        sofort eine Antwort ("Geraet nicht im Netz erreichbar" vs. "Geraet
        erreichbar, aber Abruf/Parsing schlaegt fehl") statt nur auf den
        naechsten 10s-Polling-Zyklus warten zu muessen."""
        running_attr = f"_{health_name}_check_running"
        if getattr(self, running_attr, False):
            return
        setattr(self, running_attr, True)
        try:
            status_var.set("⏳ Verbindungstest läuft…")
        except Exception:
            pass
        try:
            if btn is not None:
                btn.configure(state="disabled")
        except Exception:
            pass

        def worker() -> None:
            # abrufen_und_speichern() selbst ruft core.health NICHT auf -
            # das macht sonst nur der main.py-Polling-Thread. Ein manueller
            # Check muss den core.health-Eintrag also in jedem Zweig
            # (Erfolg wie Fehlschlag) selbst nachtragen, sonst wuerde die
            # Statuszeile nach einem erfolgreichen "Jetzt prüfen" weiterhin
            # den alten (roten) Zustand zeigen.
            host, port = host_port_from_url(host_url)
            reachable, latency_ms = check_tcp_reachable(host, port, timeout=3.0)
            fetch_ok = False
            fetch_error = None
            if reachable:
                try:
                    result = fetch_fn()
                    fetch_ok = result is not None
                    if not fetch_ok:
                        fetch_error = "Abruf lieferte keine Daten (siehe Log)"
                except Exception as exc:
                    fetch_error = f"{type(exc).__name__}: {exc}"
            else:
                fetch_error = f"Host {host}:{port} nicht erreichbar"

            if fetch_ok:
                update_source_health(health_name, ok=True, latency_ms=latency_ms)
            else:
                update_source_health(health_name, ok=False, error=fetch_error)

            def apply() -> None:
                try:
                    if reachable and fetch_ok:
                        lat = f", {latency_ms}ms" if latency_ms is not None else ""
                        status_var.set(f"🟢 Verbindungstest OK{lat}, Abruf erfolgreich")
                    elif reachable and not fetch_ok:
                        status_var.set(f"🟡 Host erreichbar, aber Abruf fehlgeschlagen – {fetch_error}")
                    else:
                        status_var.set(f"🔴 {fetch_error}")
                except Exception:
                    pass
                try:
                    if btn is not None:
                        btn.configure(state="normal")
                except Exception:
                    pass
                setattr(self, running_attr, False)
                self.refresh()

            try:
                self.root.after(0, apply)
            except Exception:
                setattr(self, running_attr, False)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_homeassistant_async(self) -> None:
        if getattr(self, "_ha_check_running", False):
            return
        self._ha_check_running = True

        try:
            self.var_hue.set("Home Assistant: prüfe…")
        except Exception:
            pass

        def worker() -> None:
            msg = "Home Assistant: –"
            try:
                tab = getattr(self.app, "hue_tab", None) if self.app else None
                client = getattr(tab, "_ha_client", None) if tab else None
                cfg = getattr(tab, "_ha_cfg", None) if tab else None
                if client is None or cfg is None:
                    msg = "Home Assistant: –"
                else:
                    t0 = time.perf_counter()
                    states = client.get_states()
                    ms = int((time.perf_counter() - t0) * 1000)

                    lights_total = 0
                    lights_on = 0
                    scenes = 0
                    for st in states:
                        try:
                            ent = str(st.get("entity_id") or "")
                            state = str(st.get("state") or "").lower()
                            if ent.startswith("light."):
                                if state not in ("unknown", "unavailable"):
                                    lights_total += 1
                                    if state == "on":
                                        lights_on += 1
                            elif ent.startswith("scene."):
                                scenes += 1
                        except Exception:
                            continue

                    msg = f"Home Assistant: OK ({ms}ms), lights_on={lights_on}/{lights_total}, scenes={scenes}"
            except requests.exceptions.RequestException as exc:
                msg = f"Home Assistant: Fehler ({type(exc).__name__})"
            except Exception as exc:
                msg = f"Home Assistant: Fehler ({type(exc).__name__})"

            def apply() -> None:
                try:
                    self.var_hue.set(msg)
                except Exception:
                    pass
                self._ha_check_running = False

            try:
                self.root.after(0, apply)
            except Exception:
                self._ha_check_running = False

        threading.Thread(target=worker, daemon=True).start()

    def _rebuild_spark_cache(self) -> None:
        try:
            view = getattr(self.app, "sparkline_view", None) if self.app else None
            if view and hasattr(view, "rebuild_cache_now"):
                view.rebuild_cache_now()
        except Exception:
            pass
        self.root.after(300, self.refresh)

    def _self_heal(self) -> None:
        """One-click repair: rebuild sparkline cache + repair PV yield data."""
        if getattr(self, "_selfheal_running", False):
            return
        self._selfheal_running = True
        try:
            self.var_selfheal.set("Self-Heal: läuft…")
        except Exception:
            pass

        def worker() -> None:
            ok_cache = False
            ok_ertrag = False
            err = None
            ds = self.datastore
            if ds is None and self.app is not None:
                ds = getattr(self.app, "datastore", None)
            try:
                view = getattr(self.app, "sparkline_view", None) if self.app else None
                if view and hasattr(view, "rebuild_cache_now"):
                    try:
                        view.rebuild_cache_now()
                        ok_cache = True
                    except Exception:
                        ok_cache = False

                try:
                    from core.ertrag_validator import validate_and_repair_ertrag
                    validate_and_repair_ertrag(ds, verbose=False)
                    ok_ertrag = True
                except Exception:
                    ok_ertrag = False
            except Exception as exc:
                err = exc

            def done() -> None:
                ts = datetime.now().strftime("%H:%M")
                if err is not None:
                    self.var_selfheal.set(f"Self-Heal: Fehler ({type(err).__name__})")
                else:
                    parts = []
                    parts.append("Cache OK" if ok_cache else "Cache –")
                    parts.append("Ertrag OK" if ok_ertrag else "Ertrag –")
                    self.var_selfheal.set(f"Self-Heal {ts}: " + " | ".join(parts))
                self._selfheal_running = False
                self.refresh()

            try:
                self.root.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _git_pull_and_restart(self) -> None:
        """Pull latest changes and quit so the service can restart the app."""
        if getattr(self, "_update_running", False):
            return
        self._update_running = True

        try:
            self.var_update.set("Update: läuft…")
        except Exception:
            pass

        try:
            if hasattr(self, "_update_btn"):
                self._update_btn.configure(state="disabled")
        except Exception:
            pass

        def worker() -> None:
            ok = False
            msg = "Update: –"
            # Prefer the service repo path (user request). Fallback to current workspace root.
            preferred_repo = Path("/home/laurenz/Dashboard")
            repo_root = preferred_repo if (preferred_repo / ".git").exists() else Path(__file__).resolve().parents[2]
            log_dir = repo_root / "data"
            log_path = log_dir / "update_last.log"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _write_log(header: str, body: str) -> None:
                try:
                    log_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n==== {ts} {header} ====\n")
                        f.write((body or "").rstrip() + "\n")
                except Exception:
                    pass

            def _find_git_exe() -> str | None:
                try:
                    p = shutil.which("git")
                    if p:
                        return p
                except Exception:
                    pass
                # Common Windows Git installs
                candidates = [
                    r"C:\\Program Files\\Git\\cmd\\git.exe",
                    r"C:\\Program Files\\Git\\bin\\git.exe",
                    r"C:\\Program Files (x86)\\Git\\cmd\\git.exe",
                    r"C:\\Program Files (x86)\\Git\\bin\\git.exe",
                ]
                for c in candidates:
                    try:
                        if Path(c).exists():
                            return c
                    except Exception:
                        continue
                return None

            def _discard_local_changes(git_exe: str, files: list[str]) -> None:
                """Discard local modifications to specific tracked files (e.g. runtime-generated caches)."""
                for f in files:
                    try:
                        subprocess.run(
                            [git_exe, "checkout", "--", f],
                            cwd=str(repo_root),
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                    except Exception:
                        pass

            def _parse_overwrite_conflict_files(output: str) -> list[str]:
                """Extract file paths from git's "local changes would be overwritten" error."""
                if "would be overwritten by merge" not in output:
                    return []
                files: list[str] = []
                capture = False
                for line in output.splitlines():
                    if "would be overwritten by merge" in line:
                        capture = True
                        continue
                    if capture:
                        stripped = line.strip()
                        if not stripped:
                            break
                        if stripped.startswith(("Please commit", "Aborting")):
                            break
                        files.append(stripped)
                return files

            def _run_git_pull_once(git_exe: str, env: dict) -> tuple[int, str]:
                # On Linux services, PATH might be minimal; bash -lc emulates a user terminal better.
                if os.name != "nt":
                    bash = "/bin/bash"
                    try:
                        if Path(bash).exists():
                            cmd = f"cd {shlex.quote(str(repo_root))} && {shlex.quote(git_exe)} pull"
                            p = subprocess.run(
                                [bash, "-lc", cmd],
                                cwd=str(repo_root),
                                capture_output=True,
                                text=True,
                                env=env,
                                timeout=180,
                            )
                            out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
                            return p.returncode, out
                    except Exception:
                        pass

                # Fallback: run directly with cwd.
                p = subprocess.run(
                    [git_exe, "pull"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=180,
                )
                out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
                return p.returncode, out

            def _run_pull_like_terminal(git_exe: str) -> tuple[int, str]:
                """Run git pull in repo_root in a way similar to manual terminal usage.

                Locally-modified runtime files (e.g. data/sparkline_cache.json, which the
                app itself rewrites continuously) would otherwise permanently block every
                future pull with "local changes would be overwritten by merge". If that
                specific error occurs, discard local changes to just those files and retry
                once instead of leaving the update stuck forever.
                """
                env = os.environ.copy()
                env.setdefault("GIT_TERMINAL_PROMPT", "0")
                env.setdefault("GCM_INTERACTIVE", "Never")

                code, out = _run_git_pull_once(git_exe, env)
                if code != 0:
                    conflict_files = _parse_overwrite_conflict_files(out)
                    if conflict_files:
                        _discard_local_changes(git_exe, conflict_files)
                        code, out = _run_git_pull_once(git_exe, env)
                return code, out

            try:
                if not (repo_root / ".git").exists():
                    msg = "Update: kein Git-Repo (.git fehlt)"
                    _write_log("NO_GIT_REPO", f"repo_root={repo_root}")
                    raise RuntimeError("no-git")

                git_exe = _find_git_exe()
                if not git_exe:
                    msg = "Update: git nicht gefunden (siehe Log)"
                    _write_log("NO_GIT_BIN", f"repo_root={repo_root}\nPATH={os.environ.get('PATH','')}")
                    raise RuntimeError("no-git-bin")

                # Mirror typical terminal behavior: log dirty state but still try `git pull`.
                dirty = ""
                try:
                    st = subprocess.run(
                        [git_exe, "status", "--porcelain"],
                        cwd=str(repo_root),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    dirty = (st.stdout or "").strip()
                except Exception:
                    dirty = ""

                if dirty:
                    _write_log("DIRTY", dirty)
                    msg = "Update: lokale Änderungen (versuche pull…)"

                code, out = _run_pull_like_terminal(git_exe)
                _write_log("PULL", out or "(no output)")
                if code == 0:
                    ok = True
                    # Detect if files were actually updated
                    files_changed = self._parse_git_pull_changes(out)
                    if "Already up" in out or "Already up-to-date" in out:
                        msg = "Update: aktuell – Neustart…"
                        _write_log("RESULT", "NO_CHANGES")
                    else:
                        if files_changed:
                            msg = f"Update: {files_changed} Dateien geändert – Neustart…"
                            _write_log("RESULT", f"UPDATED:{files_changed}")
                        else:
                            msg = "Update: OK – Neustart…"
                            _write_log("RESULT", "UPDATED")
                else:
                    # Keep it short for the UI.
                    short = out.replace("\r", " ").replace("\n", " ")
                    short = " ".join(short.split())
                    # Provide a short hint, but keep full output in the log.
                    hint = "Auth?" if ("authentication" in short.lower() or "permission" in short.lower()) else ""
                    msg = f"Update: Fehler ({code}) {hint} (Log)".strip()
            except Exception:
                pass

            def done() -> None:
                try:
                    self.var_update.set(msg)
                except Exception:
                    pass

                try:
                    if hasattr(self, "_update_btn"):
                        self._update_btn.configure(state="normal")
                except Exception:
                    pass

                self._update_running = False

                if ok:
                    def _restart() -> None:
                        try:
                            if self.app is not None and hasattr(self.app, "on_exit"):
                                self.app.on_exit()
                            else:
                                self.root.quit()
                        except Exception:
                            try:
                                self.root.quit()
                            except Exception:
                                pass

                    try:
                        self.root.after(800, _restart)
                    except Exception:
                        _restart()

            try:
                self.root.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _parse_git_pull_changes(self, output: str) -> int | None:
        """Parse git pull output to extract number of changed files."""
        if not output:
            return None
        import re
        # Match patterns like "5 files changed" or "1 file changed"
        match = re.search(r'(\d+)\s+files?\s+changed', output)
        if match:
            return int(match.group(1))
        # Match "Updating abc123..def456" followed by file list
        if "Updating" in output and "|" in output:
            # Count lines with | (file changes)
            lines = [l for l in output.split('\n') if '|' in l and not l.strip().startswith('|')]
            if lines:
                return len(lines)
        return None

    def _load_last_update_info(self) -> None:
        """Load and display the last update result from log file."""
        def worker():
            result_text = "Letztes Update: –"
            try:
                preferred_repo = Path("/home/laurenz/Dashboard")
                repo_root = preferred_repo if (preferred_repo / ".git").exists() else Path(__file__).resolve().parents[2]
                log_path = repo_root / "data" / "update_last.log"
                
                if not log_path.exists():
                    result_text = "Letztes Update: kein Log"
                else:
                    content = log_path.read_text(encoding="utf-8", errors="ignore")
                    lines = content.strip().split('\n')
                    
                    # Find last RESULT entry and its timestamp
                    last_ts = None
                    last_result = None
                    for i, line in enumerate(lines):
                        if "====" in line and "RESULT" in line:
                            # Extract timestamp from header: ==== 2026-03-20 15:30:00 RESULT ====
                            parts = line.replace("=", "").strip().split()
                            if len(parts) >= 2:
                                last_ts = f"{parts[0]} {parts[1]}"
                            # Next line is the result
                            if i + 1 < len(lines):
                                last_result = lines[i + 1].strip()
                    
                    if last_result and last_ts:
                        if last_result == "NO_CHANGES":
                            result_text = f"Letztes Update ({last_ts}): ✅ Keine Änderungen"
                        elif last_result.startswith("UPDATED:"):
                            num = last_result.split(":")[1]
                            result_text = f"Letztes Update ({last_ts}): 📦 {num} Dateien geändert"
                        elif last_result == "UPDATED":
                            result_text = f"Letztes Update ({last_ts}): 📦 Dateien aktualisiert"
                        else:
                            result_text = f"Letztes Update ({last_ts}): {last_result}"
                    elif last_ts:
                        result_text = f"Letztes Update ({last_ts}): Ergebnis unbekannt"
                    else:
                        # Try to find any timestamp from PULL entries
                        for line in reversed(lines):
                            if "====" in line and "PULL" in line:
                                parts = line.replace("=", "").strip().split()
                                if len(parts) >= 2:
                                    result_text = f"Letztes Update ({parts[0]} {parts[1]}): Log vorhanden"
                                    break
            except Exception as e:
                result_text = f"Letztes Update: Fehler ({type(e).__name__})"
            
            def apply():
                try:
                    self.var_last_update.set(result_text)
                except Exception:
                    pass
            
            try:
                self.root.after(0, apply)
            except Exception:
                pass
        
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self) -> None:
        ds = self.datastore
        if ds is None and self.app is not None:
            ds = getattr(self.app, "datastore", None)

        # DB ingest freshness
        try:
            if ds is None:
                self.var_db.set("DB ingest: –")
            else:
                dt = None
                try:
                    dt = ds.get_last_ingest_datetime()
                except Exception:
                    dt = None
                if dt is None:
                    try:
                        dt = _parse_ts(ds.get_latest_timestamp())
                    except Exception:
                        dt = None
                if dt is None:
                    self.var_db.set("DB ingest: –")
                else:
                    local = dt.astimezone() if dt.tzinfo is not None else dt
                    self.var_db.set(f"DB ingest: {_fmt_age_minutes(dt)} ({local.strftime('%H:%M')})")
        except Exception:
            self.var_db.set("DB ingest: –")

        try:
            fr = ds.get_last_fronius_record() if ds else None
            dt = _parse_ts((fr or {}).get("timestamp"))
            self.var_pv.set(f"PV last: {_fmt_age_minutes(dt)}")
        except Exception:
            self.var_pv.set("PV last: –")

        try:
            hr = ds.get_last_heating_record() if ds else None
            dt = _parse_ts((hr or {}).get("timestamp"))
            self.var_heat.set(f"Heizung last: {_fmt_age_minutes(dt)}")
        except Exception:
            self.var_heat.set("Heizung last: –")

        # Live-Verbindungsstatus aus core.health (von main.py's Polling-
        # Threads bei jedem Zyklus befuellt) - zeigt WARUM eine Quelle ggf.
        # keine frischen Daten liefert (Verbindung verloren, Timeout, ...),
        # statt nur wie alt der letzte DB-Eintrag ist.
        try:
            snapshot = get_health_snapshot()
        except Exception:
            snapshot = {}
        try:
            icon, detail = _source_status_line(snapshot.get("pv"))
            self.var_pv_status.set(f"{icon} {detail}")
        except Exception:
            pass
        try:
            icon, detail = _source_status_line(snapshot.get("heating"))
            self.var_heat_status.set(f"{icon} {detail}")
        except Exception:
            pass

        # Gap detection (24h)
        try:
            pv_rows = ds.get_recent_fronius(hours=24, limit=4000) if ds else []
            gap = _max_gap_minutes([r.get("timestamp") for r in pv_rows if r.get("timestamp")])
            self.var_gap_pv.set("PV gap(24h): –" if gap is None else f"PV gap(24h): {gap:.0f}m")
        except Exception:
            self.var_gap_pv.set("PV gap(24h): –")

        try:
            h_rows = ds.get_recent_heating(hours=24, limit=4000) if ds else []
            gap = _max_gap_minutes([r.get("timestamp") for r in h_rows if r.get("timestamp")])
            self.var_gap_heat.set("Heizung gap(24h): –" if gap is None else f"Heizung gap(24h): {gap:.0f}m")
        except Exception:
            self.var_gap_heat.set("Heizung gap(24h): –")

        # Sparkline cache file
        try:
            cache_path = Path(__file__).resolve().parents[2] / "data" / "sparkline_cache.json"
            if cache_path.exists():
                age_s = max(0.0, time.time() - cache_path.stat().st_mtime)
                kb = cache_path.stat().st_size / 1024.0
                self.var_cache.set(f"Sparkline cache: {kb:.1f}KB, {age_s/60:.0f}m alt")
            else:
                self.var_cache.set("Sparkline cache: fehlt")
        except Exception:
            self.var_cache.set("Sparkline cache: –")

        # Integrations
        self._refresh_homeassistant_async()

        try:
            tab = getattr(self.app, "tado_tab", None) if self.app else None
            api = getattr(tab, "api", None) if tab else None
            if api is None:
                self.var_tado.set("Tado: –")
            else:
                # Best-effort check
                try:
                    api.getHomeState()
                    self.var_tado.set("Tado: OK")
                except Exception:
                    self.var_tado.set("Tado: OK (ohne HomeState)")
        except Exception as exc:
            self.var_tado.set(f"Tado: Fehler ({type(exc).__name__})")

        # Lightweight periodic refresh (keeps freshness values current)
        try:
            if self._refresh_after_id is not None:
                self.root.after_cancel(self._refresh_after_id)
        except Exception:
            pass
        try:
            self._refresh_after_id = self.root.after(30_000, self.refresh)
        except Exception:
            self._refresh_after_id = None

        try:
            tab = getattr(self.app, "spotify_tab", None) if self.app else None
            client = getattr(tab, "client", None) if tab else None
            self.var_spotify.set("Spotify: OK" if client else "Spotify: –")
        except Exception:
            self.var_spotify.set("Spotify: –")

        # Refresh last update info
        self._load_last_update_info()
