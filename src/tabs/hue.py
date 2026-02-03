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
    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.bridge = None
        self.mode = tk.StringVar(value="scenes")  # scenes or lights
        self.master_bright_var = tk.IntVar(value=100)
        self.status_var = tk.StringVar(value="🔌 Verbinde...")
        
        # UI-Komponenten speichern
        self.status_label = None
        self.scroll_frame = None
        self.scroll_canvas = None
        self.scroll_window = None
        
        # Erstelle Tab Frame
        self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
        notebook.add(self.tab_frame, text=emoji("💡 Hue", "Hue"))
        
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
            self.status_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
            
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
            
            # Master Brightness
            tk.Label(global_frame, text="Master Helligkeit:", font=("Segoe UI", 9), fg=COLOR_TEXT, bg=COLOR_ROOT).grid(row=2, column=0, sticky="e", padx=(0, 10), pady=(10, 0))
            
            bright_frame = tk.Frame(global_frame, bg=COLOR_ROOT)
            bright_frame.grid(row=2, column=1, sticky="w", pady=(10, 0))
            
            self.bright_slider = tk.Scale(
                bright_frame, from_=0, to=100, orient="horizontal",
                variable=self.master_bright_var,
                command=self._on_master_brightness_changed,
                bg=COLOR_CARD, fg=COLOR_PRIMARY, length=200,
                highlightthickness=0
            )
            self.bright_slider.pack(side="left")
            
            self.bright_label = tk.Label(
                bright_frame, textvariable=tk.StringVar(value="100%"),
                font=("Segoe UI", 9), fg=COLOR_SUBTEXT, bg=COLOR_ROOT, width=4
            )
            self.bright_label.pack(side="left", padx=(10, 0))
            
            # Scroll Area für Szenen/Lichter
            canvas_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
            canvas_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            self.scroll_canvas = tk.Canvas(
                canvas_frame, bg=COLOR_ROOT,
                highlightthickness=0, height=300
            )
            scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.scroll_canvas.yview)
            
            self.scroll_window = tk.Frame(self.scroll_canvas, bg=COLOR_ROOT)
            self.scroll_canvas.create_window((0, 0), window=self.scroll_window, anchor="nw")
            self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
            
            self.scroll_canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            
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
            
    def _on_master_brightness_changed(self, value):
        """Master brightness slider bewegt."""
        try:
            val = int(value)
            self.bright_label.config(text=f"{val}%")
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
                            self._refresh_content()
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
                    self._refresh_content()
                    
            except Exception as e:
                print(f"[HUE] Connection loop error: {e}")
                self.bridge = None
                time.sleep(5)

    def _refresh_content(self):
        """Refresh Szenen oder Lichter je nach Mode."""
        try:
            if self.mode.get() == "scenes":
                self._refresh_scenes()
            else:
                self._refresh_lights()
        except Exception as e:
            print(f"[HUE] Refresh error: {e}")

    def _refresh_scenes(self):
        """Zeige Szenen."""
        if not self.bridge:
            self._show_error("Keine Verbindung")
            return
            
        try:
            scenes = self.bridge.get_scene()
            if not scenes:
                self._show_error("Keine Szenen")
                return
                
            # Szenen anordnen
            scene_list = []
            for scene_id, scene_data in scenes.items():
                try:
                    name = scene_data.get("name", "Unbenannt")
                    scene_list.append((name, scene_id))
                except:
                    continue
                    
            scene_list.sort(key=lambda x: x[0])
            
            # Max 50 Szenen anzeigen
            scene_list = scene_list[:50]
            
            # UI aufräumen
            for w in self.scroll_frame.winfo_children():
                w.destroy()
                
            if not scene_list:
                self._show_error("Keine Szenen")
                return
            
            # Szenen Grid (3 Spalten)
            for idx, (name, scene_id) in enumerate(scene_list):
                row = idx // 3
                col = idx % 3
                self._create_scene_card(name, scene_id, row, col)
                
        except Exception as e:
            print(f"[HUE] Scene refresh error: {e}")
            self._show_error(f"Fehler: {str(e)[:30]}")

    def _create_scene_card(self, name, scene_id, row, col):
        """Erstelle eine Scene Card."""
        try:
            card = tk.Frame(self.scroll_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=row, column=col, padx=5, pady=5, sticky="ew")
            
            scene_name = tk.Label(
                card, text=name, font=("Segoe UI", 10, "bold"),
                fg=COLOR_TEXT, bg=COLOR_CARD, wraplength=80, justify="center"
            )
            scene_name.pack(pady=(10, 5), padx=10)
            
            btn = tk.Button(
                card, text="Aktivieren", font=("Segoe UI", 9),
                bg=COLOR_PRIMARY, fg=COLOR_ROOT, activebackground=COLOR_SUCCESS,
                command=lambda: self._activate_scene(scene_id),
                relief="flat", padx=15, pady=8
            )
            btn.pack(pady=(5, 10), padx=10, fill="x")
            
        except Exception as e:
            print(f"[HUE] Scene card error: {e}")

    def _activate_scene(self, scene_id):
        """Aktiviere eine Szene."""
        if not self.bridge:
            return
        try:
            self.bridge.activate_scene(group=0, scene=scene_id)
            print(f"[HUE] Activated scene {scene_id}")
        except Exception as e:
            print(f"[HUE] Scene activation error: {e}")

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
            
            for idx, (light_id, name) in enumerate(light_list):
                self._create_light_card(name, light_id, idx)
                
        except Exception as e:
            print(f"[HUE] Lights refresh error: {e}")
            self._show_error(f"Fehler: {str(e)[:30]}")

    def _create_light_card(self, name, light_id, row):
        """Erstelle eine Licht-Card mit Toggle + Brightness."""
        try:
            light_data = self.bridge.get_light(light_id)
            is_on = light_data.get("state", {}).get("on", False)
            brightness = light_data.get("state", {}).get("bri", 128)
            
            card = tk.Frame(self.scroll_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.pack(fill="x", padx=5, pady=5)
            
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
                slider = tk.Scale(
                    control, from_=0, to=100, orient="horizontal",
                    bg=COLOR_CARD, fg=COLOR_PRIMARY,
                    command=set_brightness, length=150,
                    highlightthickness=0
                )
                slider.set(brightness_percent)
                slider.pack(side="left", fill="x", expand=True)
            
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
