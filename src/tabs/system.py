import threading
import time
import tkinter as tk
from tkinter import ttk
import psutil
import platform
import os
from datetime import datetime, timedelta
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_TITLE,
    emoji,
)
from ui.components.card import Card


class SystemTab:
    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.start_time = datetime.now()
        
        # Variablen
        self.var_cpu = tk.IntVar(value=0)
        self.var_ram = tk.IntVar(value=0)
        self.var_disk = tk.IntVar(value=0)
        self.var_temp = tk.StringVar(value="-- °C")
        self.var_uptime = tk.StringVar(value="00:00:00")
        self.var_network_tx = tk.StringVar(value="0 MB/s")
        self.var_network_rx = tk.StringVar(value="0 MB/s")
        
        self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.tab_frame, text=emoji("⚙️ System", "System"))
        
        self._build_ui()
        self.root.after(0, lambda: threading.Thread(target=self._loop, daemon=True).start())

    def stop(self):
        self.alive = False

    def _build_ui(self):
        """Modern Dashboard-style UI mit Cards."""
        # Main container with padding
        main = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        
        # Grid layout: 2 rows x 3 columns
        main.grid_rowconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)
        main.grid_columnconfigure(2, weight=1)
        
        # Row 1: CPU, RAM, Disk
        self._create_cpu_card(main).grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._create_ram_card(main).grid(row=0, column=1, sticky="nsew", padx=2, pady=2)
        self._create_disk_card(main).grid(row=0, column=2, sticky="nsew", padx=2, pady=2)
        
        # Row 2: Temperature, Uptime, Network
        self._create_temp_card(main).grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self._create_uptime_card(main).grid(row=1, column=1, sticky="nsew", padx=2, pady=2)
        self._create_network_card(main).grid(row=1, column=2, sticky="nsew", padx=2, pady=2)

    def _create_cpu_card(self, parent) -> Card:
        """CPU Usage Card with circular progress."""
        card = Card(parent, padding=8)
        card.add_title("CPU", icon="⚙️")
        
        # Canvas for circular progress
        self.cpu_canvas = tk.Canvas(card.content(), width=100, height=100, 
                                     bg=COLOR_CARD, highlightthickness=0)
        self.cpu_canvas.pack(pady=4)
        
        # Value label
        self.cpu_label = tk.Label(card.content(), textvariable=self.var_cpu, 
                                   font=("Segoe UI", 20, "bold"), fg=COLOR_TEXT, bg=COLOR_CARD)
        self.cpu_label.pack()
        
        tk.Label(card.content(), text="%", font=("Segoe UI", 10), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack()
        
        return card

    def _create_ram_card(self, parent) -> Card:
        """RAM Usage Card with circular progress."""
        card = Card(parent, padding=8)
        card.add_title("RAM", icon="💾")
        
        # Canvas for circular progress
        self.ram_canvas = tk.Canvas(card.content(), width=100, height=100, 
                                     bg=COLOR_CARD, highlightthickness=0)
        self.ram_canvas.pack(pady=4)
        
        # Value label
        self.ram_label = tk.Label(card.content(), textvariable=self.var_ram, 
                                   font=("Segoe UI", 20, "bold"), fg=COLOR_TEXT, bg=COLOR_CARD)
        self.ram_label.pack()
        
        tk.Label(card.content(), text="%", font=("Segoe UI", 10), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack()
        
        return card

    def _create_disk_card(self, parent) -> Card:
        """Disk Usage Card with circular progress."""
        card = Card(parent, padding=8)
        card.add_title("SD-Karte", icon="💿")
        
        # Canvas for circular progress
        self.disk_canvas = tk.Canvas(card.content(), width=100, height=100, 
                                      bg=COLOR_CARD, highlightthickness=0)
        self.disk_canvas.pack(pady=4)
        
        # Value label
        self.disk_label = tk.Label(card.content(), textvariable=self.var_disk, 
                                    font=("Segoe UI", 20, "bold"), fg=COLOR_TEXT, bg=COLOR_CARD)
        self.disk_label.pack()
        
        tk.Label(card.content(), text="%", font=("Segoe UI", 10), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack()
        
        return card

    def _create_temp_card(self, parent) -> Card:
        """Temperature Card."""
        card = Card(parent, padding=8)
        card.add_title("Temperatur", icon="🌡️")
        
        temp_label = tk.Label(card.content(), textvariable=self.var_temp, 
                              font=("Segoe UI", 28, "bold"), fg=COLOR_WARNING, bg=COLOR_CARD)
        temp_label.pack(expand=True, pady=15)
        
        return card

    def _create_uptime_card(self, parent) -> Card:
        """Uptime Card."""
        card = Card(parent, padding=8)
        card.add_title("Uptime", icon="⏱️")
        
        uptime_label = tk.Label(card.content(), textvariable=self.var_uptime, 
                                font=("Segoe UI", 18, "bold"), fg=COLOR_SUCCESS, bg=COLOR_CARD)
        uptime_label.pack(expand=True, pady=12)
        
        # System info
        info_frame = tk.Frame(card.content(), bg=COLOR_CARD)
        info_frame.pack(pady=3)
        
        system_info = f"{platform.system()} {platform.machine()}"
        tk.Label(info_frame, text=system_info, font=("Segoe UI", 8), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack()
        
        return card

    def _create_network_card(self, parent) -> Card:
        """Network Traffic Card."""
        card = Card(parent, padding=8)
        card.add_title("Netzwerk", icon="📡")
        
        # Upload
        upload_frame = tk.Frame(card.content(), bg=COLOR_CARD)
        upload_frame.pack(pady=5, fill=tk.X)
        
        tk.Label(upload_frame, text="↑ Upload", font=("Segoe UI", 9), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack(side=tk.LEFT, padx=3)
        tk.Label(upload_frame, textvariable=self.var_network_tx, font=("Segoe UI", 11, "bold"), 
                 fg=COLOR_PRIMARY, bg=COLOR_CARD).pack(side=tk.RIGHT, padx=3)
        
        # Download
        download_frame = tk.Frame(card.content(), bg=COLOR_CARD)
        download_frame.pack(pady=5, fill=tk.X)
        
        tk.Label(download_frame, text="↓ Download", font=("Segoe UI", 9), 
                 fg=COLOR_SUBTEXT, bg=COLOR_CARD).pack(side=tk.LEFT, padx=3)
        tk.Label(download_frame, textvariable=self.var_network_rx, font=("Segoe UI", 11, "bold"), 
                 fg=COLOR_SUCCESS, bg=COLOR_CARD).pack(side=tk.RIGHT, padx=3)
        
        return card

    def _draw_circular_progress(self, canvas, value, max_value=100, color=COLOR_PRIMARY):
        """Draw circular progress indicator."""
        canvas.delete("all")
        
        w = canvas.winfo_reqwidth() or 160
        h = canvas.winfo_reqheight() or 160
        cx, cy = w // 2, h // 2
        r = min(cx, cy) - 15
        
        # Background circle
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, 
                          outline=COLOR_BORDER, width=8, fill="")
        
        # Progress arc
        if value > 0:
            extent = -(value / max_value) * 360
            # Color based on value
            if value < 50:
                arc_color = COLOR_SUCCESS
            elif value < 80:
                arc_color = COLOR_WARNING
            else:
                arc_color = COLOR_DANGER
            
            canvas.create_arc(cx - r, cy - r, cx + r, cy + r, 
                             start=90, extent=extent, 
                             outline=arc_color, width=8, style=tk.ARC)

    def _loop(self):
        """Background thread for updating system stats."""
        last_net = psutil.net_io_counters()
        
        while self.alive:
            try:
                # CPU
                cpu = int(psutil.cpu_percent(interval=0.5))
                self.root.after(0, self.var_cpu.set, cpu)
                
                # RAM
                ram = psutil.virtual_memory()
                ram_percent = int(ram.percent)
                self.root.after(0, self.var_ram.set, ram_percent)
                
                # Disk
                disk = psutil.disk_usage('/')
                disk_percent = int(disk.percent)
                self.root.after(0, self.var_disk.set, disk_percent)
                
                # Temperature
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        temp_raw = int(f.read().strip())
                        temp_c = temp_raw / 1000.0
                        self.root.after(0, self.var_temp.set, f"{temp_c:.1f}°C")
                except Exception:
                    self.root.after(0, self.var_temp.set, "N/A")
                
                # Uptime
                uptime = datetime.now() - self.start_time
                hours = int(uptime.total_seconds() // 3600)
                minutes = int((uptime.total_seconds() % 3600) // 60)
                seconds = int(uptime.total_seconds() % 60)
                self.root.after(0, self.var_uptime.set, f"{hours:02d}:{minutes:02d}:{seconds:02d}")
                
                # Network
                net = psutil.net_io_counters()
                tx_rate = (net.bytes_sent - last_net.bytes_sent) / 1024 / 1024 / 2  # MB/s
                rx_rate = (net.bytes_recv - last_net.bytes_recv) / 1024 / 1024 / 2  # MB/s
                self.root.after(0, self.var_network_tx.set, f"{tx_rate:.2f} MB/s")
                self.root.after(0, self.var_network_rx.set, f"{rx_rate:.2f} MB/s")
                last_net = net
                
                # Update circular progress indicators
                self.root.after(0, lambda: self._draw_circular_progress(self.cpu_canvas, cpu))
                self.root.after(0, lambda: self._draw_circular_progress(self.ram_canvas, ram_percent))
                self.root.after(0, lambda: self._draw_circular_progress(self.disk_canvas, disk_percent))
                
                time.sleep(2)
            except Exception as e:
                print(f"[SYSTEM] Error: {e}")
                time.sleep(5)
