"""
STABILER HUE LICHT TAB - VERSION 2
===================================
Vereinfachte Version mit robustem Error Handling
- Szenen-Modus (max 10 pro Raum)
- Einzellicht-Modus (mit Helligkeitsregler)
- Master Brightness für alle Lampen
- Fehlertoleranz überall
"""

import threading
import time
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from collections import defaultdict
import customtkinter as ctk

try:
    from phue import Bridge
except ImportError:
    Bridge = None

from ui.styles import (
    COLOR_ROOT, COLOR_CARD, COLOR_BORDER, COLOR_PRIMARY,
    COLOR_SUCCESS, COLOR_WARNING, COLOR_TEXT, COLOR_SUBTEXT, COLOR_TITLE, emoji
)

HUE_BRIDGE_IP = "192.168.1.111"

class HueTab:
    def __init__(self, root, notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.bridge = None
        self.mode = tk.StringVar(value="scenes")  # scenes or lights
        self.master_bright_var = tk.IntVar(value=100)
        self.status_var = tk.StringVar(value="🔌 Verbinde...")
        self.last_refresh_var = tk.StringVar(value="")
        self._bridge_lock = threading.Lock()
        # Cache UI/state to avoid unnecessary redraws and flicker.
        self._refresh_inflight = False
        self._scene_snapshot = None
        self._scene_cards = {}
        self._scene_sections = {}
        self._active_scene_by_group = {}
        self._light_snapshot = None
        self._light_structure = None
        self._light_cards = {}
        self._light_state = {}
        
        # UI-Komponenten speichern
        self.status_label = None
        self.scroll_frame = None
        self.scroll_canvas = None
        self.scroll_window = None
        
        # Tab Frame: entweder übergeben (CTkTabview) oder selbst erstellen (Legacy)
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("💡 Licht", "Licht"))
        
        # Initialisiere UI
        self._build_ui()
        
        # Starte Connection Loop im Hintergrund
        self.connect_thread = threading.Thread(target=self._connect_loop, daemon=True)
        self.connect_thread.start()
        
    def _build_ui(self):
        """Erstelle das UI - FEHLERTOLERANTER AUFBAU."""
        try:
            # Frame für Global Controls
            global_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT, height=80)
            global_frame.pack(fill="x", padx=10, pady=10)
            
            # Status
            self.status_label = tk.Label(
                global_frame, textvariable=self.status_var,
                font=("Segoe UI", 10), fg=COLOR_SUBTEXT, bg=COLOR_ROOT
            )
            self.status_label.grid(row=0, column=0, sticky="w", pady=(0, 10))

            self.last_refresh_label = tk.Label(
                global_frame, textvariable=self.last_refresh_var,
                font=("Segoe UI", 9), fg=COLOR_SUBTEXT, bg=COLOR_ROOT
            )
            self.last_refresh_label.grid(row=0, column=1, sticky="e", pady=(0, 10))
            
            # Mode Toggle
            tk.Label(global_frame, text="Modus:", font=("Segoe UI", 9), fg=COLOR_TEXT, bg=COLOR_ROOT).grid(row=1, column=0, sticky="e", padx=(0, 10))
            
            mode_frame = tk.Frame(global_frame, bg=COLOR_ROOT)
            mode_frame.grid(row=1, column=1, sticky="w")
            
            tk.Radiobutton(
                mode_frame, text="Szenen", variable=self.mode, value="scenes",
                command=self._on_mode_changed,
                bg=COLOR_ROOT, fg=COLOR_TEXT, selectcolor=COLOR_CARD,
                font=("Segoe UI", 9)
            ).pack(side="left", padx=(0, 20))
            
            tk.Radiobutton(
                mode_frame, text="Einzelne Lichter", variable=self.mode, value="lights",
                command=self._on_mode_changed,
                bg=COLOR_ROOT, fg=COLOR_TEXT, selectcolor=COLOR_CARD,
                font=("Segoe UI", 9)
            ).pack(side="left")

            # Manuelles Refresh (hilft bei Hue-App Änderungen)
            refresh_btn = tk.Button(
                global_frame, text="↻ Neu laden",
                font=("Segoe UI", 9), bg=COLOR_CARD, fg=COLOR_TEXT,
                activebackground=COLOR_CARD, relief="flat",
                command=self._on_manual_refresh
            )
            refresh_btn.grid(row=1, column=2, sticky="e", padx=(10, 0))

            global_frame.grid_columnconfigure(0, weight=1)
            global_frame.grid_columnconfigure(1, weight=1)
            global_frame.grid_columnconfigure(2, weight=0)
            
            # Master Brightness VERTIKAL links
            main_content = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
            main_content.pack(fill="both", expand=True, padx=10, pady=10)

            # Linke Spalte: Vertikaler Slider
            slider_frame = tk.Frame(main_content, bg=COLOR_ROOT)
            slider_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
            tk.Label(slider_frame, text="Master Helligkeit", font=("Segoe UI", 9), fg=COLOR_TEXT, bg=COLOR_ROOT).pack(pady=(0, 8))
            self.bright_label_var = tk.StringVar(value="100%")
            self.bright_label = tk.Label(
                slider_frame, textvariable=self.bright_label_var,
                font=("Segoe UI", 10, "bold"), fg=COLOR_PRIMARY, bg=COLOR_ROOT, width=5
            )
            self.bright_label.pack(pady=(0, 8))
            
            # Moderner CustomTkinter Slider (vertikal: unten=0, oben=100)
            tk.Label(slider_frame, text="100%", font=("Segoe UI", 9), fg=COLOR_SUBTEXT, bg=COLOR_ROOT, width=5).pack(pady=(0, 6))
            self.bright_slider = ctk.CTkSlider(
                slider_frame,
                from_=0, to=100,
                orientation="vertical",
                variable=self.master_bright_var,
                command=self._on_master_brightness_changed,
                height=320,
                width=30,
                button_color=COLOR_PRIMARY,
                button_hover_color=COLOR_SUCCESS,
                progress_color=COLOR_PRIMARY,
                fg_color=COLOR_CARD
            )
            self.bright_slider.pack(fill="y", expand=True, pady=5)
            # 0%-Label unten
            tk.Label(slider_frame, text="0%", font=("Segoe UI", 9), fg=COLOR_SUBTEXT, bg=COLOR_ROOT, width=5).pack(pady=(8, 0))

            # Rechte Spalte: Szenen/Lichter
            canvas_frame = tk.Frame(main_content, bg=COLOR_ROOT)
            canvas_frame.grid(row=0, column=1, sticky="nsew")
            main_content.grid_columnconfigure(1, weight=1)
            main_content.grid_rowconfigure(0, weight=1)

            self.scroll_canvas = tk.Canvas(
                canvas_frame, bg=COLOR_ROOT,
                highlightthickness=0
            )
            scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.scroll_canvas.yview)

            self.scroll_window = tk.Frame(self.scroll_canvas, bg=COLOR_ROOT)
            self._scroll_window_id = self.scroll_canvas.create_window((0, 0), window=self.scroll_window, anchor="nw")
            self.scroll_canvas.configure(yscrollcommand=scrollbar.set)

            self.scroll_canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            def _on_frame_configure(event):
                try:
                    self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))
                except Exception:
                    pass

            def _on_canvas_configure(event):
                # Keep inner frame width in sync with canvas width
                try:
                    self.scroll_canvas.itemconfigure(self._scroll_window_id, width=event.width)
                except Exception:
                    pass

            self.scroll_window.bind("<Configure>", _on_frame_configure)
            self.scroll_canvas.bind("<Configure>", _on_canvas_configure)

            def _on_mousewheel(event):
                self.scroll_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            self.scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

            # Initial placeholder
            self.scroll_frame = self.scroll_window
            self._show_loading()
            
        except Exception as e:
            print(f"[HUE] UI Build Error: {e}")
            tk.Label(self.notebook, text=f"UI Fehler: {str(e)[:50]}", fg="red", bg=COLOR_ROOT).pack()

    def _on_mode_changed(self):
        """Mode zwischen Szenen und Lichtern gewechselt."""
        try:
            self._refresh_content()
        except Exception as e:
            print(f"[HUE] Mode change error: {e}")

    def _on_manual_refresh(self):
        """Manueller Refresh Button."""
        try:
            self._refresh_content(force=True)
        except Exception as e:
            print(f"[HUE] Manual refresh error: {e}")

    def _ui_call(self, fn, *args, **kwargs):
        """Run UI work on the Tk main thread."""
        try:
            if self.root is None:
                return
            self.root.after(0, lambda: fn(*args, **kwargs))
        except Exception as e:
            print(f"[HUE] ui_call error: {e}")
            
    def _on_master_brightness_changed(self, value):
        """Master brightness slider bewegt."""
        try:
            val = int(value)
            self.bright_label_var.set(f"{val}%")
            self._set_master_brightness(val)
        except Exception as e:
            print(f"[HUE] Brightness change error: {e}")
            
    def _connect_loop(self):
        """Verbinde zur Bridge in stabiler Schleife."""
        retry_count = 0
        max_retries = 10
        
        while self.alive:
            try:
                if self.bridge is None:
                    if retry_count < max_retries:
                        self.status_var.set(f"🔌 Verbinde ({retry_count+1}/{max_retries})...")
                        try:
                            self.bridge = Bridge(HUE_BRIDGE_IP)
                            self.bridge.connect()
                            print(f"[HUE] Bridge connected to {HUE_BRIDGE_IP}")
                            self.status_var.set(f"✓ Verbunden ({len(self.bridge.get_light())} Lichter)")
                            retry_count = 0
                            self._ui_call(self._refresh_content)
                        except Exception as connect_err:
                            print(f"[HUE] Connection attempt failed: {connect_err}")
                            retry_count += 1
                            self.bridge = None
                            time.sleep(2)
                            continue
                    else:
                        self.status_var.set("✗ Verbindung fehlgeschlagen")
                        self.bridge = None
                        time.sleep(10)
                        retry_count = 0
                        continue
                else:
                    # Pi 5: Bridge verbunden - refresh alle 8s (schnellere Updates)
                    time.sleep(8)
                    self._ui_call(self._refresh_content)
                    
            except Exception as e:
                print(f"[HUE] Connection loop error: {e}")
                self.bridge = None
                time.sleep(5)

    def _refresh_content(self, force=False):
        """Refresh Szenen oder Lichter je nach Mode.

        WICHTIG: Keine blockierenden Bridge-Calls im UI-Thread.
        Nur updaten, wenn sich der Zustand aendert oder force=True ist.
        """
        if self._refresh_inflight:
            return
        self._refresh_inflight = True
        mode = self.mode.get()

        def worker():
            try:
                if mode == "scenes":
                    data = self._fetch_scenes_data()
                    self._ui_call(self._apply_scenes, data, force)
                else:
                    data = self._fetch_lights_data()
                    self._ui_call(self._apply_lights, data, force)
            except Exception as e:
                print(f"[HUE] Refresh error: {e}")
            finally:
                self._ui_call(self._set_refresh_done)

        threading.Thread(target=worker, daemon=True).start()

    def _set_refresh_done(self):
        self._refresh_inflight = False

    def _fetch_scenes_data(self):
        """Hole Szenen-Daten in einem Worker-Thread."""
        if not self.bridge:
            return {"error": "Keine Verbindung"}

        scenes = self.bridge.get_scene()
        if not scenes:
            return {"error": "Keine Szenen"}

        try:
            groups = self.bridge.get_group() or {}
        except Exception:
            groups = {}
        try:
            lights = self.bridge.get_light() or {}
        except Exception:
            lights = {}

        light_ids = set(str(k) for k in lights.keys())
        group_names = {}
        for gid, gdata in (groups or {}).items():
            try:
                group_names[str(gid)] = gdata.get("name", f"Gruppe {gid}")
            except Exception:
                group_names[str(gid)] = f"Gruppe {gid}"

        per_group = defaultdict(list)
        other_key = "__other__"
        for scene_id, scene_data in (scenes or {}).items():
            try:
                if scene_data.get("recycle") is True:
                    continue

                name = scene_data.get("name", "Unbenannt")
                scene_group = scene_data.get("group")
                if scene_group is not None:
                    scene_group = str(scene_group)

                scene_lights = scene_data.get("lights") or []
                if scene_lights:
                    if not any(str(lid) in light_ids for lid in scene_lights):
                        continue

                if scene_group and scene_group in group_names:
                    per_group[scene_group].append((name, scene_id, scene_group))
                else:
                    per_group[other_key].append((name, scene_id, None))
            except Exception:
                continue

        for gid in list(per_group.keys()):
            per_group[gid].sort(key=lambda x: x[0])
            per_group[gid] = per_group[gid][:10]

        ordered_group_ids = sorted([gid for gid in per_group.keys() if gid != other_key], key=lambda gid: group_names.get(gid, gid))
        if other_key in per_group and per_group[other_key]:
            ordered_group_ids.append(other_key)

        return {
            "group_names": group_names,
            "per_group": per_group,
            "ordered_group_ids": ordered_group_ids,
        }

    def _scene_structure_key(self, per_group, ordered_group_ids):
        return tuple((gid, tuple(scene_id for _, scene_id, _ in per_group.get(gid, []))) for gid in ordered_group_ids)

    def _apply_scenes(self, data, force=False):
        """Render Szenen nur, wenn sich der Zustand aendert."""
        if not data or data.get("error"):
            self._show_error(data.get("error", "Keine Szenen"))
            return

        group_names = data["group_names"]
        per_group = data["per_group"]
        ordered_group_ids = data["ordered_group_ids"]

        structure_key = self._scene_structure_key(per_group, ordered_group_ids)
        active_key = tuple(sorted(self._active_scene_by_group.items()))
        snapshot = (structure_key, active_key)

        if not force and snapshot == self._scene_snapshot:
            return

        # If only active scene changed, update highlight without rebuild.
        if not force and self._scene_snapshot and self._scene_snapshot[0] == structure_key:
            self._scene_snapshot = snapshot
            self._update_scene_highlights()
            return

        self._scene_snapshot = snapshot

        for w in self.scroll_frame.winfo_children():
            w.destroy()
        self._scene_cards.clear()
        self._scene_sections.clear()

        if not per_group:
            self._show_error("Keine Szenen")
            return

        for gid in ordered_group_ids:
            section = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
            section.pack(fill="x", pady=(0, 12))
            self._scene_sections[gid] = section

            title = group_names.get(gid, "Andere Szenen") if gid != "__other__" else "Andere Szenen"
            tk.Label(
                section,
                text=title,
                font=("Segoe UI", 12, "bold"),
                fg=COLOR_TITLE,
                bg=COLOR_ROOT,
            ).pack(anchor="w", padx=2, pady=(0, 6))

            grid = tk.Frame(section, bg=COLOR_ROOT)
            grid.pack(fill="x")

            cols = 5
            for c in range(cols):
                try:
                    grid.grid_columnconfigure(c, weight=1, uniform="scene")
                except Exception:
                    pass

            items = per_group.get(gid, [])
            for idx, (name, scene_id, scene_group) in enumerate(items):
                row = idx // cols
                col = idx % cols
                self._create_scene_card(grid, name, scene_id, scene_group, row, col)

        self.last_refresh_var.set(time.strftime("%H:%M:%S"))

    def _create_scene_card(self, parent, name, scene_id, group_id, row, col):
        """Erstelle eine Scene Card."""
        try:
            card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=row, column=col, padx=5, pady=5, sticky="ew")
            
            scene_name = tk.Label(
                card, text=name, font=("Segoe UI", 10, "bold"),
                fg=COLOR_TEXT, bg=COLOR_CARD, wraplength=80, justify="center"
            )
            scene_name.pack(pady=(10, 5), padx=10)
            
            btn = tk.Button(
                card, text="Aktivieren", font=("Segoe UI", 9),
                bg=COLOR_PRIMARY, fg=COLOR_ROOT, activebackground=COLOR_SUCCESS,
                command=lambda gid=group_id, sid=scene_id, nm=name: self._threaded_activate_scene(gid, sid, nm),
                relief="flat", padx=15, pady=8
            )
            btn.pack(pady=(5, 10), padx=10, fill="x")

            self._scene_cards[str(scene_id)] = {
                "card": card,
                "button": btn,
                "group_id": str(group_id) if group_id is not None else None,
            }
            self._set_scene_active_style(str(scene_id))
            
        except Exception as e:
            print(f"[HUE] Scene card error: {e}")

    def _set_scene_active_style(self, scene_id: str):
        info = self._scene_cards.get(scene_id)
        if not info:
            return
        gid = info.get("group_id")
        is_active = gid in self._active_scene_by_group and self._active_scene_by_group.get(gid) == scene_id
        try:
            if is_active:
                info["card"].configure(highlightthickness=2, highlightbackground=COLOR_PRIMARY)
                info["button"].configure(bg=COLOR_SUCCESS)
            else:
                info["card"].configure(highlightthickness=1, highlightbackground=COLOR_BORDER)
                info["button"].configure(bg=COLOR_PRIMARY)
        except Exception:
            pass

    def _update_scene_highlights(self):
        for scene_id in list(self._scene_cards.keys()):
            self._set_scene_active_style(scene_id)

    def _threaded_activate_scene(self, group_id, scene_id, scene_name=""):
        """Aktiviere eine Szene (nicht-blockierend, mit UI-Feedback)."""
        # Quick UI feedback
        try:
            label = scene_name.strip() or "Szene"
            self.status_var.set(f"⏱ Aktiviere: {label}...")
        except Exception:
            pass

        def worker():
            if not self.bridge:
                self._ui_call(self.status_var.set, "⚠ Hue: Keine Verbindung")
                return

            gid = 0
            if group_id is not None:
                try:
                    gid = int(group_id)
                except Exception:
                    gid = 0

            try:
                with self._bridge_lock:
                    resp = self.bridge.activate_scene(group_id=gid, scene_id=scene_id)
                print(f"[HUE] Activated scene {scene_id} (group={gid}) -> {resp}")
                self._ui_call(self.status_var.set, "✓ Szene aktiviert")
                # UI-only update: mark active scene without full refresh.
                self._ui_call(self._set_active_scene, str(group_id) if group_id is not None else None, str(scene_id))
            except Exception as e:
                print(f"[HUE] Scene activation error: {e}")
                self._ui_call(self.status_var.set, f"⚠ Szene Fehler: {str(e)[:40]}")

        threading.Thread(target=worker, daemon=True).start()

    def _set_active_scene(self, group_id: str | None, scene_id: str):
        if group_id is None:
            return
        self._active_scene_by_group[str(group_id)] = str(scene_id)
        self._update_scene_highlights()

    def _fetch_lights_data(self):
        """Hole Light-Daten in einem Worker-Thread."""
        if not self.bridge:
            return {"error": "Keine Verbindung"}

        lights = self.bridge.get_light()
        if not lights:
            return {"error": "Keine Lichter"}

        light_list = []
        for light_id, light_data in lights.items():
            state = light_data.get("state", {})
            light_list.append(
                {
                    "id": int(light_id),
                    "name": light_data.get("name", f"Light {light_id}"),
                    "on": bool(state.get("on", False)),
                    "bri": int(state.get("bri", 0)),
                }
            )

        light_list.sort(key=lambda x: x["name"])
        return {"lights": light_list}

    def _light_structure_key(self, light_list):
        return tuple(item["id"] for item in light_list)

    def _apply_lights(self, data, force=False):
        """Render Light Cards nur, wenn sich der Zustand aendert."""
        if not data or data.get("error"):
            self._show_error(data.get("error", "Keine Lichter"))
            return

        light_list = data["lights"]
        snapshot = tuple((l["id"], l["on"], l["bri"]) for l in light_list)
        structure_key = self._light_structure_key(light_list)

        if not force and snapshot == self._light_snapshot:
            return

        # Rebuild only if the set of lights changed.
        if self._light_structure != structure_key:
            for w in self.scroll_frame.winfo_children():
                w.destroy()
            self._light_cards.clear()

            cols = 2
            for idx, light in enumerate(light_list):
                row = idx // cols
                col = idx % cols
                self._create_light_card(light["name"], light["id"], row, col)
            self._light_structure = structure_key

        for light in light_list:
            self._light_state[light["id"]] = light
            self._update_light_card(light["id"], light)

        self._light_snapshot = snapshot
        self.last_refresh_var.set(time.strftime("%H:%M:%S"))

    def _create_light_card(self, name, light_id, row, col):
        """Erstelle eine Licht-Card mit Toggle + Brightness."""
        try:
            card = tk.Frame(self.scroll_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            self.scroll_frame.grid_columnconfigure(col, weight=1)
            
            # Oben: Name und Status
            header = tk.Frame(card, bg=COLOR_CARD)
            header.pack(fill="x", padx=10, pady=(10, 5))
            
            name_label = tk.Label(
                header, text=name, font=("Segoe UI", 10, "bold"),
                fg=COLOR_TEXT, bg=COLOR_CARD
            )
            name_label.pack(side="left")
            
            status_label = tk.Label(
                header, text="[--]", font=("Segoe UI", 9),
                fg=COLOR_SUBTEXT, bg=COLOR_CARD
            )
            status_label.pack(side="right")
            
            # Unten: Toggle Button + Brightness Slider
            control = tk.Frame(card, bg=COLOR_CARD)
            control.pack(fill="x", padx=10, pady=(5, 10))
            
            btn = tk.Button(
                control, text="An/Aus", font=("Segoe UI", 9),
                bg=COLOR_SUBTEXT,
                fg=COLOR_ROOT, activebackground=COLOR_SUCCESS,
                command=lambda lid=light_id: self._toggle_light(lid), relief="flat", width=10
            )
            btn.pack(side="left", padx=(0, 10))

            slider = ctk.CTkSlider(
                control,
                from_=0, to=100,
                orientation="horizontal",
                command=lambda val, lid=light_id: self._on_light_brightness(lid, val),
                width=150,
                height=20,
                button_color=COLOR_PRIMARY,
                button_hover_color=COLOR_SUCCESS,
                progress_color=COLOR_PRIMARY,
                fg_color=COLOR_CARD
            )
            slider.set(0)
            slider.pack(side="left", fill="x", expand=True, padx=5)

            self._light_cards[int(light_id)] = {
                "card": card,
                "name_label": name_label,
                "status_label": status_label,
                "button": btn,
                "slider": slider,
            }
            
        except Exception as e:
            print(f"[HUE] Light card error: {e}")

    def _update_light_card(self, light_id: int, state: dict):
        info = self._light_cards.get(int(light_id))
        if not info:
            return
        is_on = bool(state.get("on", False))
        bri = int(state.get("bri", 0))
        brightness_percent = int((bri / 254) * 100) if bri else 0

        status_text = "An" if is_on else "Aus"
        status_color = COLOR_SUCCESS if is_on else COLOR_SUBTEXT
        try:
            info["status_label"].configure(text=f"[{status_text}]", fg=status_color)
            info["button"].configure(bg=COLOR_PRIMARY if is_on else COLOR_SUBTEXT)
            info["slider"].set(brightness_percent)
            try:
                info["slider"].configure(state="normal" if is_on else "disabled")
            except Exception:
                pass
            if not is_on:
                try:
                    info["slider"].configure(progress_color=COLOR_SUBTEXT)
                except Exception:
                    pass
            else:
                try:
                    info["slider"].configure(progress_color=COLOR_PRIMARY)
                except Exception:
                    pass
        except Exception:
            pass

    def _toggle_light(self, light_id: int):
        state = self._light_state.get(int(light_id), {})
        target = not bool(state.get("on", False))

        def worker():
            if not self.bridge:
                return
            try:
                self.bridge.set_light(light_id, "on", target)
                state["on"] = target
                self._light_state[int(light_id)] = state
                self._ui_call(self._update_light_card, int(light_id), state)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_light_brightness(self, light_id: int, val):
        state = self._light_state.get(int(light_id), {})
        try:
            bri_val = int((int(val) / 100) * 254)
        except Exception:
            return

        def worker():
            if not self.bridge:
                return
            try:
                self.bridge.set_light(light_id, "bri", bri_val)
                state["bri"] = bri_val
                self._light_state[int(light_id)] = state
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _set_master_brightness(self, percent):
        """Setze Helligkeit für alle Lampen (Group 0)."""
        if not self.bridge:
            return
        try:
            bri_val = int((percent / 100) * 254)
            self.bridge.set_group(0, "bri", bri_val)
        except Exception as e:
            print(f"[HUE] Master brightness error: {e}")

    def _show_loading(self):
        """Zeige Loading State."""
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        tk.Label(
            self.scroll_frame, text="⏳ Lädt...",
            font=("Segoe UI", 12), fg=COLOR_SUBTEXT, bg=COLOR_ROOT
        ).pack(pady=50)

    def _show_error(self, message):
        """Zeige Error Message."""
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        tk.Label(
            self.scroll_frame, text=f"⚠ {message}",
            font=("Segoe UI", 11), fg=COLOR_WARNING, bg=COLOR_ROOT
        ).pack(pady=50)

    def _threaded_group_cmd(self, state):
        """Compatibilität mit app.py - Schalte alle Lichter an/aus."""
        if not self.bridge:
            return
        try:
            self.bridge.set_group(0, "on", state)
            print(f"[HUE] Set all lights to {state}")
        except Exception as e:
            print(f"[HUE] Group command error: {e}")

    def cleanup(self):
        """Cleanup."""
        self.alive = False
        if self.connect_thread.is_alive():
            self.connect_thread.join(timeout=2)
