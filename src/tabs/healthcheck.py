import os
import time
from datetime import datetime, timezone
from pathlib import Path
import threading
import subprocess
import shutil

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


class HealthTab:
    """Simple health check + self-healing tools."""

    def __init__(self, root: tk.Tk, notebook, datastore=None, app=None, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.datastore = datastore
        self.app = app

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
        container = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill=tk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text=emoji("🩺 Health Check", "Health Check"),
            font=("Segoe UI", 16, "bold"),
            text_color=COLOR_TITLE,
        ).pack(side=tk.LEFT)

        self._refresh_btn = ctk.CTkButton(
            header,
            text="Refresh",
            fg_color=COLOR_PRIMARY,
            command=self.refresh,
            width=120,
        )
        self._refresh_btn.pack(side=tk.RIGHT)

        grid = ctk.CTkFrame(container, fg_color="transparent")
        grid.pack(fill=tk.BOTH, expand=True)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        self.card_data = Card(grid)
        self.card_data.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self.card_data.add_title("Datenqualität", icon="🧰")

        self.card_int = Card(grid)
        self.card_int.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        self.card_int.add_title("Integrationen", icon="🔌")

        # Data labels
        self.var_db = tk.StringVar(value="DB ingest: –")
        self.var_pv = tk.StringVar(value="PV: –")
        self.var_heat = tk.StringVar(value="Heizung: –")
        self.var_gap_pv = tk.StringVar(value="PV gap(24h): –")
        self.var_gap_heat = tk.StringVar(value="Heizung gap(24h): –")
        self.var_cache = tk.StringVar(value="Sparkline cache: –")
        self.var_selfheal = tk.StringVar(value="Self-Heal: –")
        self.var_update = tk.StringVar(value="Update: –")

        body = ctk.CTkFrame(self.card_data.content(), fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True)
        for v in (
            self.var_db,
            self.var_pv,
            self.var_heat,
            self.var_gap_pv,
            self.var_gap_heat,
            self.var_cache,
            self.var_selfheal,
            self.var_update,
        ):
            ctk.CTkLabel(body, textvariable=v, font=("Segoe UI", 12), text_color=COLOR_TEXT).pack(anchor="w", pady=2)

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill=tk.X, pady=(10, 0))
        self._rebuild_cache_btn = ctk.CTkButton(
            btn_row,
            text="Sparkline Cache neu bauen",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=self._rebuild_spark_cache,
        )
        self._rebuild_cache_btn.pack(anchor="w")

        self._selfheal_btn = ctk.CTkButton(
            btn_row,
            text="Self-Heal (Cache + Ertrag)",
            fg_color=COLOR_PRIMARY,
            command=self._self_heal,
        )
        self._selfheal_btn.pack(anchor="w", pady=(8, 0))

        self._update_btn = ctk.CTkButton(
            btn_row,
            text="Update (git pull + Neustart)",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=self._git_pull_and_restart,
        )
        self._update_btn.pack(anchor="w", pady=(8, 0))

        # Integration labels
        self.var_hue = tk.StringVar(value="Hue: –")
        self.var_tado = tk.StringVar(value="Tado: –")
        self.var_spotify = tk.StringVar(value="Spotify: –")

        body2 = ctk.CTkFrame(self.card_int.content(), fg_color="transparent")
        body2.pack(fill=tk.BOTH, expand=True)
        for v in (self.var_hue, self.var_tado, self.var_spotify):
            ctk.CTkLabel(body2, textvariable=v, font=("Segoe UI", 12), text_color=COLOR_TEXT).pack(anchor="w", pady=2)

        hint = ctk.CTkLabel(
            container,
            text="Hinweis: Timestamps werden standardmäßig als UTC interpretiert. Optional: env DASHBOARD_TS_ASSUME_LOCAL=1",
            font=("Segoe UI", 10),
            text_color=COLOR_SUBTEXT,
        )
        hint.pack(anchor="w", pady=(10, 0))

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
            repo_root = Path(__file__).resolve().parents[2]

            try:
                if not (repo_root / ".git").exists():
                    msg = "Update: kein Git-Repo (.git fehlt)"
                    raise RuntimeError("no-git")

                if shutil.which("git") is None:
                    msg = "Update: git nicht gefunden"
                    raise RuntimeError("no-git-bin")

                # Safety: do not pull on a dirty working tree.
                st = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if (st.stdout or "").strip():
                    msg = "Update: lokale Änderungen – abbrechen"
                    raise RuntimeError("dirty")

                # Fast-forward only to avoid merge prompts on a service.
                res = subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                out = (res.stdout or "") + "\n" + (res.stderr or "")
                out = out.strip()
                if res.returncode == 0:
                    ok = True
                    if "Already up" in out or "Already up-to-date" in out:
                        msg = "Update: aktuell – Neustart…"
                    else:
                        msg = "Update: OK – Neustart…"
                else:
                    # Keep it short for the UI.
                    short = out.replace("\r", " ").replace("\n", " ")
                    short = " ".join(short.split())
                    msg = f"Update: Fehler ({res.returncode}) {short[:60]}".strip()
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
        try:
            tab = getattr(self.app, "hue_tab", None) if self.app else None
            bridge = getattr(tab, "bridge", None) if tab else None
            lock = getattr(tab, "_bridge_lock", None) if tab else None
            if bridge and lock:
                t0 = time.perf_counter()
                with lock:
                    group = bridge.get_group(0)
                any_on = bool((group or {}).get("state", {}).get("any_on", False))
                ms = int((time.perf_counter() - t0) * 1000)
                self.var_hue.set(f"Hue: OK ({ms}ms), any_on={any_on}")
            else:
                self.var_hue.set("Hue: –")
        except Exception as exc:
            self.var_hue.set(f"Hue: Fehler ({type(exc).__name__})")

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
