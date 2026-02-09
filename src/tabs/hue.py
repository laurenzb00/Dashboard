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
    COLOR_SUCCESS, COLOR_WARNING, COLOR_TEXT, COLOR_SUBTEXT, emoji
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
            
            # Moderner CustomTkinter Slider
            self.bright_slider = ctk.CTkSlider(
                slider_frame,
                from_=100, to=0,
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
        """Refresh Szenen oder Lichter je nach Mode."""
        try:
            # Timestamp für UI
            try:
                self.last_refresh_var.set(time.strftime("%H:%M:%S"))
            except Exception:
                pass
            if self.mode.get() == "scenes":
                self._refresh_scenes(force=force)
            else:
                self._refresh_lights()
        except Exception as e:
            print(f"[HUE] Refresh error: {e}")

    def _refresh_scenes(self, force=False):
        """Zeige Szenen."""
        if not self.bridge:
            self._show_error("Keine Verbindung")
            return
            
        try:
            scenes = self.bridge.get_scene()
            if not scenes:
                self._show_error("Keine Szenen")
                return

            # Gruppen (Rooms/Zones) + Lights für Validierung
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

            # Szenen pro Gruppe sammeln (max 10 pro Raum)
            per_group = defaultdict(list)  # gid -> [(name, scene_id, gid)]
            other_key = "__other__"
            for scene_id, scene_data in (scenes or {}).items():
                try:
                    if scene_data.get("recycle") is True:
                        continue

                    name = scene_data.get("name", "Unbenannt")
                    scene_group = scene_data.get("group")
                    if scene_group is not None:
                        scene_group = str(scene_group)

                    # Manche Szenen referenzieren Lights direkt (validiere, um "alte" Szenen zu vermeiden)
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

            # Sortieren + pro Gruppe limitieren
            for gid in list(per_group.keys()):
                per_group[gid].sort(key=lambda x: x[0])
                if gid != other_key:
                    per_group[gid] = per_group[gid][:10]
                else:
                    per_group[gid] = per_group[gid][:10]
            
            # UI aufräumen
            for w in self.scroll_frame.winfo_children():
                w.destroy()

            if not per_group:
                self._show_error("Keine Szenen")
                return

            # Gruppen-Order: nach Name, dann "Andere"
            ordered_group_ids = sorted([gid for gid in per_group.keys() if gid != other_key], key=lambda gid: group_names.get(gid, gid))
            if other_key in per_group and per_group[other_key]:
                ordered_group_ids.append(other_key)

            # Render: pro Gruppe ein Abschnitt
            for section_idx, gid in enumerate(ordered_group_ids):
                section = tk.Frame(self.scroll_frame, bg=COLOR_ROOT)
                section.pack(fill="x", pady=(0, 12))

                title = group_names.get(gid, "Andere Szenen") if gid != other_key else "Andere Szenen"
                tk.Label(
                    section, text=title,
                    font=("Segoe UI", 10, "bold"), fg=COLOR_TEXT, bg=COLOR_ROOT
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
                if not items:
                    continue

                for idx, (name, scene_id, scene_group) in enumerate(items):
                    row = idx // cols
                    col = idx % cols
                    self._create_scene_card(grid, name, scene_id, scene_group, row, col)
                
        except Exception as e:
            print(f"[HUE] Scene refresh error: {e}")
            self._show_error(f"Fehler: {str(e)[:30]}")

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
            
        except Exception as e:
            print(f"[HUE] Scene card error: {e}")

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
            except Exception as e:
                print(f"[HUE] Scene activation error: {e}")
                self._ui_call(self.status_var.set, f"⚠ Szene Fehler: {str(e)[:40]}")

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_lights(self):
        """Zeige einzelne Lichter mit Helligkeitsreglern."""
        if not self.bridge:
            self._show_error("Keine Verbindung")
            return
            
        try:
            lights = self.bridge.get_light()
            if not lights:
                self._show_error("Keine Lichter")
                return
            
            # UI aufräumen
            for w in self.scroll_frame.winfo_children():
                w.destroy()
            
            # Lichter als Liste
            light_list = sorted([(light_id, light_data.get("name", f"Light {light_id}")) for light_id, light_data in lights.items()])
            
            # Lichter Grid (2 Spalten - genug Platz für Slider)
            cols = 2
            for idx, (light_id, name) in enumerate(light_list):
                row = idx // cols
                col = idx % cols
                self._create_light_card(name, light_id, row, col)
                
        except Exception as e:
            print(f"[HUE] Lights refresh error: {e}")
            self._show_error(f"Fehler: {str(e)[:30]}")

    def _create_light_card(self, name, light_id, row, col):
        """Erstelle eine Licht-Card mit Toggle + Brightness."""
        try:
            light_data = self.bridge.get_light(light_id)
            is_on = light_data.get("state", {}).get("on", False)
            brightness = light_data.get("state", {}).get("bri", 128)

            card = tk.Frame(self.scroll_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            self.scroll_frame.grid_columnconfigure(col, weight=1)
            
            # Oben: Name und Status
            header = tk.Frame(card, bg=COLOR_CARD)
            header.pack(fill="x", padx=10, pady=(10, 5))
            
            tk.Label(
                header, text=name, font=("Segoe UI", 10, "bold"),
                fg=COLOR_TEXT, bg=COLOR_CARD
            ).pack(side="left")
            
            status_text = "An" if is_on else "Aus"
            status_color = COLOR_SUCCESS if is_on else COLOR_SUBTEXT
            tk.Label(
                header, text=f"[{status_text}]", font=("Segoe UI", 9),
                fg=status_color, bg=COLOR_CARD
            ).pack(side="right")
            
            # Unten: Toggle Button + Brightness Slider
            control = tk.Frame(card, bg=COLOR_CARD)
            control.pack(fill="x", padx=10, pady=(5, 10))
            
            def toggle_light():
                try:
                    self.bridge.set_light(light_id, "on", not is_on)
                except:
                    pass
            
            btn = tk.Button(
                control, text="An/Aus", font=("Segoe UI", 9),
                bg=COLOR_PRIMARY if is_on else COLOR_SUBTEXT,
                fg=COLOR_ROOT, activebackground=COLOR_SUCCESS,
                command=toggle_light, relief="flat", width=10
            )
            btn.pack(side="left", padx=(0, 10))
            
            if is_on:
                def set_brightness(val):
                    try:
                        bri_val = int((int(val) / 100) * 254)
                        self.bridge.set_light(light_id, "bri", bri_val)
                    except:
                        pass
                
                brightness_percent = int((brightness / 254) * 100)
                
                # Moderner horizontaler CustomTkinter Slider
                slider = ctk.CTkSlider(
                    control,
                    from_=0, to=100,
                    orientation="horizontal",
                    command=set_brightness,
                    width=150,
                    height=20,
                    button_color=COLOR_PRIMARY,
                    button_hover_color=COLOR_SUCCESS,
                    progress_color=COLOR_PRIMARY,
                    fg_color=COLOR_CARD
                )
                slider.set(brightness_percent)
                slider.pack(side="left", fill="x", expand=True, padx=5)
            
        except Exception as e:
            print(f"[HUE] Light card error: {e}")

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
