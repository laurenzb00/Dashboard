"""
MODERNE WIDGET-BIBLIOTHEK FÜR ENERGIE-DASHBOARD
===============================================
Sammlung von wiederverwendbaren Widgets für verschiedene Datentypen:
- Batterie-Gauge (Plotly & PIL)
- Energiefluss-Sankey (Plotly)
- System-Monitoring-Gauges (Plotly)
- Circular Progress (PIL)
"""

import tkinter as tk
from tkinter import StringVar
from PIL import Image, ImageDraw, ImageTk, ImageFont
import numpy as np
import io

# Check if plotly is available
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    print("[!] Plotly nicht installiert. Installiere mit: pip install plotly")

# --- FARBEN ---
COLOR_DARK_BG = "#0b1220"
COLOR_CARD_BG = "#0f172a"
COLOR_ACCENT = "#1f2a44"
COLOR_PV = "#38bdf8"
COLOR_LOAD = "#f472b6"
COLOR_BATTERY = "#34d399"
COLOR_GRID = "#ef4444"
COLOR_TEXT = "#e5e7eb"
COLOR_SUBTEXT = "#8ba2c7"


# ========== 1. BATTERIE-GAUGE WIDGET ==========
class BatteryGaugeWidget:
    """Moderne Batterie-Anzeige mit Gauge oder Custom Design"""
    
    def __init__(self, parent, width=240, height=240, style="gauge"):
        """
        style: "gauge" (Plotly), "pil" (Custom), oder "simple" (Canvas)
        """
        self.parent = parent
        self.width = width
        self.height = height
        self.style = style
        
        self.frame = tk.Frame(parent, bg=COLOR_CARD_BG)
        
        if style == "gauge" and PLOTLY_AVAILABLE:
            self._create_plotly_gauge()
        elif style == "pil":
            self._create_pil_battery()
        else:
            self._create_simple_canvas()
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
    
    # --- PLOTLY GAUGE ---
    def _create_plotly_gauge(self):
        """Erstellt Plotly Gauge Chart"""
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import matplotlib.pyplot as plt
        
        # Placeholder (wird mit update_soc ersetzt)
        self.gauge_label = tk.Label(
            self.frame,
            text="Lade Gauge...",
            bg=COLOR_CARD_BG,
            fg="white",
            font=("Segoe UI", 10)
        )
        self.gauge_label.pack(fill=tk.BOTH, expand=True)
    
    def _create_plotly_gauge_image(self, soc, charging=False):
        """Erstellt Plotly Gauge als Bild"""
        if not PLOTLY_AVAILABLE:
            return None
        
        # Delta (Ladung/Entladung)
        delta_color = "#10b981" if charging else "#ef4444"
        
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=soc,
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "Batterie SOC", 'font': {'size': 16, 'color': 'white'}},
            delta={'reference': 50, 'increasing': {'color': delta_color}},
            number={'suffix': "%", 'font': {'size': 32, 'color': 'white'}},
            gauge={
                'axis': {
                    'range': [0, 100],
                    'tickwidth': 2,
                    'tickcolor': COLOR_SUBTEXT
                },
                'bar': {'color': COLOR_BATTERY, 'thickness': 0.75},
                'bgcolor': COLOR_DARK_BG,
                'borderwidth': 2,
                'bordercolor': COLOR_ACCENT,
                'steps': [
                    {'range': [0, 20], 'color': '#7f1d1d'},
                    {'range': [20, 50], 'color': '#78350f'},
                    {'range': [50, 80], 'color': '#14532d'},
                    {'range': [80, 100], 'color': '#064e3b'}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 90
                }
            }
        ))
        
        fig.update_layout(
            paper_bgcolor=COLOR_CARD_BG,
            plot_bgcolor=COLOR_CARD_BG,
            font={'color': 'white', 'family': 'Segoe UI'},
            height=self.height,
            width=self.width,
            margin=dict(l=20, r=20, t=40, b=20)
        )
        
        # Als Bild exportieren
        img_bytes = fig.to_image(format="png", width=self.width, height=self.height)
        return Image.open(io.BytesIO(img_bytes))
    
    # --- PIL BATTERY ---
    def _create_pil_battery(self):
        """Custom Batterie mit PIL"""
        self.battery_label = tk.Label(
            self.frame,
            bg=COLOR_CARD_BG,
            relief=tk.FLAT
        )
        self.battery_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self._update_pil_battery(50)
    
    def _update_pil_battery(self, soc):
        """Zeichnet Custom Batterie"""
        w, h = self.width, self.height
        img = Image.new('RGBA', (w, h), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        
        # Battery Outline
        batt_x = w // 4
        batt_y = h // 6
        batt_width = w // 2
        batt_height = 2 * h // 3
        
        draw.rounded_rectangle(
            [(batt_x, batt_y), (batt_x + batt_width, batt_y + batt_height)],
            radius=15,
            outline=COLOR_SUBTEXT,
            width=4
        )
        
        # Terminal
        term_width = batt_width // 4
        term_height = h // 12
        term_x = batt_x + batt_width // 2 - term_width // 2
        draw.rectangle(
            [(term_x, batt_y - term_height), (term_x + term_width, batt_y)],
            fill=COLOR_SUBTEXT
        )
        
        # Fill based on SOC
        fill_height = int((batt_height - 10) * soc / 100)
        fill_y = batt_y + batt_height - fill_height - 5
        
        # Color based on SOC
        if soc < 20:
            color = "#ef4444"
        elif soc < 50:
            color = "#f59e0b"
        elif soc < 80:
            color = "#10b981"
        else:
            color = "#059669"
        
        draw.rounded_rectangle(
            [(batt_x + 5, fill_y), (batt_x + batt_width - 5, batt_y + batt_height - 5)],
            radius=10,
            fill=color
        )
        
        # Text
        text = f"{int(soc)}%"
        bbox = draw.textbbox((0, 0), text, font=None)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        text_x = w // 2 - text_width // 2
        text_y = batt_y + batt_height + 20
        
        draw.text((text_x, text_y), text, fill="white", font=None)
        
        # Zu TkImage
        self.tk_img = ImageTk.PhotoImage(img)
        self.battery_label.configure(image=self.tk_img)
        self.battery_label.image = self.tk_img
    
    # --- SIMPLE CANVAS ---
    def _create_simple_canvas(self):
        """Einfache Canvas-Darstellung"""
        self.canvas = tk.Canvas(
            self.frame,
            width=self.width,
            height=self.height,
            bg=COLOR_CARD_BG,
            highlightthickness=0
        )
        self.canvas.pack(padx=10, pady=10)
        
        self._update_simple_canvas(50)
    
    def _update_simple_canvas(self, soc):
        """Zeichnet einfache Batterie"""
        self.canvas.delete("all")
        
        # Battery frame
        x1, y1 = 40, 60
        x2, y2 = self.width - 40, self.height - 40
        
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=COLOR_SUBTEXT, width=3)
        
        # Terminal
        term_width = (x2 - x1) // 4
        term_x = (x1 + x2) // 2 - term_width // 2
        self.canvas.create_rectangle(term_x, y1 - 15, term_x + term_width, y1, fill=COLOR_SUBTEXT)
        
        # Fill
        fill_height = int((y2 - y1 - 10) * soc / 100)
        fill_y = y2 - fill_height - 5
        
        color = COLOR_BATTERY if soc >= 50 else "#f59e0b" if soc >= 20 else "#ef4444"
        self.canvas.create_rectangle(x1 + 5, fill_y, x2 - 5, y2 - 5, fill=color, outline="")
        
        # Text
        self.canvas.create_text(
            self.width // 2, self.height - 15,
            text=f"{int(soc)}%",
            fill="white",
            font=("Segoe UI", 18, "bold")
        )
    
    # --- PUBLIC UPDATE ---
    def update_soc(self, soc, charging=False):
        """Aktualisiert Batterie-Anzeige"""
        try:
            soc = max(0, min(100, float(soc)))
        except (ValueError, TypeError):
            soc = 0
        
        if self.style == "gauge" and PLOTLY_AVAILABLE:
            # Update Plotly Gauge
            img = self._create_plotly_gauge_image(soc, charging)
            if img:
                tk_img = ImageTk.PhotoImage(img)
                self.gauge_label.configure(image=tk_img, text="")
                self.gauge_label.image = tk_img
        elif self.style == "pil":
            self._update_pil_battery(soc)
        else:
            self._update_simple_canvas(soc)


# ========== 2. CIRCULAR PROGRESS WIDGET ==========
class CircularProgressWidget:
    """Kreisförmiger Fortschrittsbalken (für CPU/RAM/Disk)"""
    
    def __init__(self, parent, size=120, title="Progress"):
        self.parent = parent
        self.size = size
        self.title = title
        
        self.frame = tk.Frame(parent, bg=COLOR_CARD_BG)
        
        self.title_label = tk.Label(
            self.frame, text=title,
            font=("Segoe UI", 10, "bold"),
            fg="white", bg=COLOR_CARD_BG
        )
        self.title_label.pack(pady=(5, 2))
        
        self.progress_label = tk.Label(
            self.frame,
            bg=COLOR_CARD_BG,
            relief=tk.FLAT
        )
        self.progress_label.pack(pady=5)
        
        self._update_progress(0)
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
    
    def _update_progress(self, value):
        """Zeichnet kreisförmigen Fortschritt"""
        size = self.size
        img = Image.new('RGBA', (size, size), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        
        # Background circle
        margin = 10
        draw.arc(
            [(margin, margin), (size - margin, size - margin)],
            0, 360,
            fill=COLOR_ACCENT,
            width=12
        )
        
        # Progress arc
        angle = int(360 * value / 100)
        
        # Color based on value
        if value < 70:
            color = COLOR_PV
        elif value < 90:
            color = "#f59e0b"
        else:
            color = "#ef4444"
        
        draw.arc(
            [(margin, margin), (size - margin, size - margin)],
            -90, -90 + angle,
            fill=color,
            width=12
        )
        
        # Center text
        text = f"{int(value)}%"
        bbox = draw.textbbox((0, 0), text, font=None)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        text_x = size // 2 - text_width // 2
        text_y = size // 2 - text_height // 2
        
        draw.text((text_x, text_y), text, fill="white", font=None)
        
        # Zu TkImage
        self.tk_img = ImageTk.PhotoImage(img)
        self.progress_label.configure(image=self.tk_img)
        self.progress_label.image = self.tk_img
    
    def update_value(self, value, title=None):
        """Aktualisiert Wert"""
        try:
            value = max(0, min(100, float(value)))
        except (ValueError, TypeError):
            value = 0
        
        if title:
            self.title_label.configure(text=title)
        
        self._update_progress(value)


# ========== DEMO ==========
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Widget Library Demo")
    root.geometry("900x500")
    root.configure(bg=COLOR_DARK_BG)
    
    # Header
    tk.Label(
        root, text="🎨 Moderne Widget-Bibliothek",
        font=("Segoe UI", 18, "bold"),
        fg="white", bg=COLOR_DARK_BG
    ).pack(pady=20)
    
    # Container
    container = tk.Frame(root, bg=COLOR_DARK_BG)
    container.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
    
    # Row 1: Batterie-Widgets
    row1 = tk.Frame(container, bg=COLOR_DARK_BG)
    row1.pack(fill=tk.X, pady=10)
    
    for col, (style, title) in enumerate([
        ("gauge", "Gauge (Plotly)"),
        ("pil", "Custom (PIL)"),
        ("simple", "Simple (Canvas)")
    ]):
        card = tk.Frame(row1, bg=COLOR_CARD_BG, relief=tk.FLAT,
                       highlightbackground=COLOR_ACCENT, highlightthickness=2)
        card.pack(side=tk.LEFT, padx=10, expand=True, fill=tk.BOTH)
        
        tk.Label(card, text=title, font=("Segoe UI", 11, "bold"),
                fg="white", bg="#142038", pady=8).pack(fill=tk.X)
        
        widget = BatteryGaugeWidget(card, width=220, height=200, style=style)
        widget.pack(padx=10, pady=10)
        widget.update_soc(75, charging=True)
    
    # Row 2: Circular Progress
    row2 = tk.Frame(container, bg=COLOR_DARK_BG)
    row2.pack(fill=tk.X, pady=10)
    
    for col, (value, title) in enumerate([
        (45, "CPU"),
        (72, "RAM"),
        (88, "Disk")
    ]):
        card = tk.Frame(row2, bg=COLOR_CARD_BG, relief=tk.FLAT,
                       highlightbackground=COLOR_ACCENT, highlightthickness=2)
        card.pack(side=tk.LEFT, padx=10, expand=True, fill=tk.BOTH)
        
        widget = CircularProgressWidget(card, size=100, title=title)
        widget.pack(padx=15, pady=15)
        widget.update_value(value)
    
    root.mainloop()
