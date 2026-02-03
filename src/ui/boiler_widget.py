"""
MODERNE BOILER/PUFFERSPEICHER VISUALISIERUNG
=============================================
Features:
- 3D Heatmap mit Temperatur-Gradient
- PIL-basierte thermische Darstellung
- Matplotlib imshow für detaillierte Wärmeverteilung
- Animierte Wellen-Effekte
- Touch-optimiert
- Moderne Chip-Style Temperature Labels
"""

import tkinter as tk
from tkinter import StringVar
import numpy as np
from PIL import Image, ImageDraw, ImageTk, ImageFilter
import matplotlib
matplotlib.use('Agg')  # Für Embedding
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.cm as cm

# --- GLASMORPHISM FARBEN ---
COLOR_DARK_BG = "#0a0e1a"
COLOR_GLASS_BG = "#1a1f2e"
COLOR_PRIMARY = "#3b82f6"
COLOR_SUCCESS = "#10b981"
COLOR_WARNING = "#f59e0b"
COLOR_TEXT = "#e2e8f0"
COLOR_SUBTEXT = "#64748b"
COLOR_BORDER = "#2d3548"

class ModernBoilerWidget:
    """Moderne Pufferspeicher-Visualisierung mit Heatmap"""
    
    def __init__(self, parent, width=200, height=180, style="heatmap"):
        """
        style: "heatmap" (Matplotlib), "gradient" (PIL), oder "blocks" (klassisch)
        """
        self.parent = parent
        self.width = width
        self.height = height
        self.style = style
        
        self.temp_top_var = StringVar(value="0")
        self.temp_mid_var = StringVar(value="0")
        self.temp_bot_var = StringVar(value="0")
        
        # Container mit Glasmorphism
        self.frame = tk.Frame(parent, bg=COLOR_GLASS_BG)
        
        if style == "heatmap":
            self._create_matplotlib_heatmap()
        elif style == "gradient":
            self._create_pil_gradient()
        else:
            self._create_classic_blocks()
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
    
    # ========== MATPLOTLIB HEATMAP VERSION ==========
    def _create_matplotlib_heatmap(self):
        """Erstellt detaillierte Heatmap mit Glasmorphism"""
        
        # Figure erstellen (klein und kompakt)
        self.fig, self.ax = plt.subplots(figsize=(2.2, 2.8), dpi=85)
        self.fig.patch.set_facecolor(COLOR_GLASS_BG)
        self.ax.set_facecolor(COLOR_GLASS_BG)
        
        # Canvas
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Initial leere Heatmap
        self._update_heatmap(0, 0, 0)
    
    def _update_heatmap(self, temp_top, temp_mid, temp_bot, temp_kessel=None):
        """Aktualisiert die Heatmap mit modernen Chip-Style Labels"""
        self.ax.clear()
        
        # Erstelle simulierte Temperaturverteilung (24 Schichten - glatterer Gradient)
        layers = 24
        temps = np.linspace(temp_bot, temp_top, layers)
        
        # 2D Array für Heatmap (etwas breiter für bessere Visualisierung)
        heatmap_data = np.tile(temps[:, np.newaxis], (1, 5))
        
        # Verbesserte Normalisierung für besseren Kontrast
        from matplotlib.colors import TwoSlopeNorm
        norm = TwoSlopeNorm(vmin=35, vcenter=57, vmax=75)
        
        # Imshow mit verbesserter Colormap
        self.ax.imshow(
            heatmap_data,
            aspect='auto',
            cmap='RdYlBu_r',  # Rot=heiß, Blau=kalt
            norm=norm,
            interpolation='gaussian',  # Weicher als bilinear
            origin='lower'
        )
        
        # Moderne Chip-Style Temperature Labels (kompakt & elegant)
        mid_layer = layers // 2
        
        # Funktion für Chip-Style Badge
        def add_temp_chip(y_pos, temp, label):
            """Fügt moderne Chip-Style Temperature Badge hinzu"""
            # Hintergrund-Box (rounded)
            from matplotlib.patches import FancyBboxPatch
            
            # Farbe basierend auf Temperatur
            if temp >= 65:
                chip_color = COLOR_SUCCESS
                text_color = 'white'
            elif temp >= 55:
                chip_color = COLOR_WARNING
                text_color = 'white'
            else:
                chip_color = COLOR_BORDER
                text_color = COLOR_SUBTEXT
            
            # Chip Position rechts neben Heatmap
            chip_x = 5.5
            chip_width = 1.8
            chip_height = 1.6
            
            box = FancyBboxPatch(
                (chip_x, y_pos - chip_height/2),
                chip_width,
                chip_height,
                boxstyle="round,pad=0.05",
                facecolor=chip_color,
                edgecolor='none',
                alpha=0.9
            )
            self.ax.add_patch(box)
            
            # Temperatur-Text (fett, weiß)
            self.ax.text(
                chip_x + chip_width/2, y_pos,
                f"{temp:.0f}°",
                ha='center', va='center',
                fontsize=9, fontweight='bold',
                color=text_color
            )
            
            # Label (klein, gedimmt) - links von der Heatmap
            self.ax.text(
                -0.5, y_pos,
                label,
                ha='right', va='center',
                fontsize=8,
                color=COLOR_SUBTEXT
            )
        
        # Temperature Chips hinzufügen
        add_temp_chip(layers - 1.5, temp_top, "Oben")
        add_temp_chip(mid_layer, temp_mid, "Mitte")
        add_temp_chip(1.5, temp_bot, "Unten")
        
        # Kesseltemperatur (wenn vorhanden) - als Badge unten
        if temp_kessel is not None:
            from matplotlib.patches import FancyBboxPatch
            box = FancyBboxPatch(
                (1, -3.5), 3, 1.3,
                boxstyle="round,pad=0.05",
                facecolor=COLOR_PRIMARY,
                edgecolor='none',
                alpha=0.85
            )
            self.ax.add_patch(box)
            self.ax.text(
                2.5, -2.9,
                f"🔥 Kessel {temp_kessel:.0f}°",
                ha='center', va='center',
                fontsize=8, fontweight='bold',
                color='white'
            )
        
        # Saubere Achsen (keine Labels)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        
        # Alle Spines unsichtbar
        for spine in self.ax.spines.values():
            spine.set_visible(False)
        
        # Leichtes Padding
        self.ax.set_xlim(-1.5, 7.5)
        self.ax.set_ylim(-4, layers)
        
        self.fig.tight_layout(pad=0.1)
        self.canvas.draw()
    
    # ========== PIL GRADIENT VERSION ==========
    def _create_pil_gradient(self):
        """Erstellt smooth gradient mit PIL"""
        
        self.gradient_label = tk.Label(
            self.frame,
            bg=COLOR_CARD_BG,
            relief=tk.FLAT
        )
        self.gradient_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self._update_gradient(0, 0, 0)
    
    def _update_gradient(self, temp_top, temp_mid, temp_bot):
        """Erstellt gradient image mit Temperaturen"""
        
        # Größe
        w, h = 180, 200
        
        # Bild erstellen
        img = Image.new('RGB', (w, h))
        draw = ImageDraw.Draw(img)
        
        # Temperatur zu Farbe
        def temp_to_color(temp):
            if temp < 20:
                return (59, 130, 246)  # Blau
            elif temp < 35:
                return (16, 185, 129)  # Grün
            elif temp < 50:
                return (245, 158, 11)  # Orange
            elif temp < 65:
                return (239, 68, 68)   # Rot
            else:
                return (220, 38, 38)   # Dunkelrot
        
        # 3 Zonen-Gradient
        zones = [
            (temp_bot, 0, h // 3),
            (temp_mid, h // 3, 2 * h // 3),
            (temp_top, 2 * h // 3, h)
        ]
        
        for temp, y_start, y_end in zones:
            color = temp_to_color(temp)
            
            for y in range(y_start, y_end):
                # Leichter Gradient innerhalb der Zone
                factor = (y - y_start) / (y_end - y_start) if y_end > y_start else 1
                r = int(color[0] * (0.7 + 0.3 * factor))
                g = int(color[1] * (0.7 + 0.3 * factor))
                b = int(color[2] * (0.7 + 0.3 * factor))
                draw.rectangle([(0, y), (w, y + 1)], fill=(r, g, b))
        
        # Border
        draw.rectangle([(0, 0), (w - 1, h - 1)], outline=(36, 51, 84), width=3)
        
        # Text-Overlay
        draw.text((w // 2, h // 6), f"{temp_top:.0f}°C",
                  fill='white', anchor='mm', font=None)
        draw.text((w // 2, h // 2), f"{temp_mid:.0f}°C",
                  fill='white', anchor='mm', font=None)
        draw.text((w // 2, 5 * h // 6), f"{temp_bot:.0f}°C",
                  fill='white', anchor='mm', font=None)
        
        # Blur für smooth look
        img = img.filter(ImageFilter.GaussianBlur(radius=2))
        
        # Zu TkImage
        self.tk_img = ImageTk.PhotoImage(img)
        self.gradient_label.configure(image=self.tk_img)
        self.gradient_label.image = self.tk_img
    
    # ========== CLASSIC BLOCKS VERSION ==========
    def _create_classic_blocks(self):
        """Klassische Block-Darstellung mit Canvas"""
        
        self.canvas = tk.Canvas(
            self.frame,
            width=self.width,
            height=self.height,
            bg=COLOR_CARD_BG,
            highlightthickness=0
        )
        self.canvas.pack(pady=5, expand=True)
        
        self._update_blocks(0, 0, 0)
    
    def _update_blocks(self, temp_top, temp_mid, temp_bot):
        """Klassische 3-Block Darstellung"""
        self.canvas.delete("all")
        
        x_start, y_start = 20, 10
        width, height = self.width - 40, self.height - 20
        
        # Shadow
        self.canvas.create_rectangle(
            x_start + 3, y_start + 3,
            x_start + width + 3, y_start + height + 3,
            fill="#000000", outline="", stipple="gray50"
        )
        
        # Container
        self.canvas.create_rectangle(
            x_start, y_start,
            x_start + width, y_start + height,
            fill="#0a0f1a", outline="#243354", width=2
        )
        
        section_height = height / 3
        temps = [temp_top, temp_mid, temp_bot]
        labels = ["Oben", "Mitte", "Unten"]
        
        for i, (temp, label) in enumerate(zip(temps, labels)):
            y_pos = y_start + i * section_height
            
            # Farbe basierend auf Temperatur
            if temp < 20:
                color = "#3b82f6"
            elif temp < 35:
                color = "#10b981"
            elif temp < 50:
                color = "#f59e0b"
            elif temp < 65:
                color = "#ef4444"
            else:
                color = "#dc2626"
            
            # Block
            self.canvas.create_rectangle(
                x_start + 2, y_pos,
                x_start + width - 2, y_pos + section_height,
                fill=color, outline="#1f2a44", width=1
            )
            
            # Text
            self.canvas.create_text(
                x_start + width // 2, y_pos + section_height // 2,
                text=f"{temp:.0f}°",
                fill="white", font=("Segoe UI", 14, "bold")
            )
            
            # Label klein
            self.canvas.create_text(
                x_start + 15, y_pos + 10,
                text=label,
                fill="#8ba2c7", font=("Segoe UI", 7), anchor="nw"
            )
    
    # ========== PUBLIC UPDATE METHODE ==========
    def update_temperatures(self, temp_top, temp_mid, temp_bot, temp_kessel=None):
        """Aktualisiert die Visualisierung mit neuen Temperaturen"""
        try:
            t_top = float(temp_top)
            t_mid = float(temp_mid)
            t_bot = float(temp_bot)
            t_kessel = float(temp_kessel) if temp_kessel is not None else None
        except (ValueError, TypeError):
            t_top, t_mid, t_bot = 0, 0, 0
            t_kessel = None
        
        if self.style == "heatmap":
            self._update_heatmap(t_top, t_mid, t_bot, t_kessel)
        elif self.style == "gradient":
            self._update_gradient(t_top, t_mid, t_bot)
        else:
            self._update_blocks(t_top, t_mid, t_bot)


# ========== DEMO / TEST ==========
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Boiler Widget Demo")
    root.geometry("800x400")
    root.configure(bg=COLOR_DARK_BG)
    
    # Container
    container = tk.Frame(root, bg=COLOR_DARK_BG)
    container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
    
    # 3 verschiedene Styles nebeneinander
    for col, (style, title) in enumerate([
        ("heatmap", "Heatmap (Matplotlib)"),
        ("gradient", "Gradient (PIL)"),
        ("blocks", "Blocks (Classic)")
    ]):
        frame = tk.Frame(container, bg=COLOR_CARD_BG, relief=tk.FLAT,
                        highlightbackground=COLOR_ACCENT, highlightthickness=2)
        frame.grid(row=0, column=col, padx=10, pady=10, sticky="nsew")
        
        tk.Label(frame, text=title, font=("Segoe UI", 11, "bold"),
                fg="white", bg="#142038", pady=8).pack(fill=tk.X)
        
        widget = ModernBoilerWidget(frame, width=220, height=240, style=style)
        widget.pack(padx=10, pady=10)
        
        # Simulierte Temperaturen
        widget.update_temperatures(65, 45, 25)
    
    container.grid_columnconfigure(0, weight=1)
    container.grid_columnconfigure(1, weight=1)
    container.grid_columnconfigure(2, weight=1)
    
    root.mainloop()
