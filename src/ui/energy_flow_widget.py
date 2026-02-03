"""
ENERGIEFLUSS WIDGET V2 - Glasmorphism Design
=============================================
Visualisiert Energiefluss mit modernem Glasmorphism-Effekt
"""

import tkinter as tk
from tkinter import Canvas
from PIL import Image, ImageDraw, ImageFont
import io
import os

# Glasmorphism Farbpalette (dunkel & transparent)
COLOR_GLASS_BG = "#1a1f2e"     # Transparente Glass Cards
COLOR_DARK_BG = "#0a0e1a"      # Sehr dunkler Hintergrund
COLOR_SUCCESS = "#10b981"      # Grün für Produktion/OK
COLOR_WARNING = "#f59e0b"      # Orange für Verbrauch
COLOR_DANGER = "#ef4444"       # Rot für Bezug
COLOR_TEXT = "#e2e8f0"         # Hellerer Text
COLOR_SUBTEXT = "#64748b"      # Gedimmter Text
COLOR_BORDER = "#2d3548"       # Glass Border

class EnergyFlowWidgetV2:
    """Moderne Card-basierte Energiefluss-Visualisierung"""
    
    def __init__(self, parent, width=700, height=280, style="modern"):
        self.parent = parent
        self.width = width
        self.height = height
        self.style = style
        
        # Standardwerte (mit sichtbaren Testwerten für initiale Darstellung)
        self.pv_power = 2500  # 2.5 kW
        self.load_power = 1800  # 1.8 kW
        self.battery_power = -500  # -500W (Laden)
        self.grid_power = 200  # 200W Bezug
        self.battery_soc = 65  # 65%
        
        # kWh Werte (Tagesertrag)
        self.pv_kwh = 0
        self.grid_kwh = 0
        self.battery_kwh = 0
        self.load_kwh = 0
        
        # Frame
        self.frame = tk.Frame(parent, width=width, height=height, bg=COLOR_DARK_BG)
        self.frame.pack_propagate(False)
        
        # Canvas
        self.canvas = Canvas(self.frame, width=width, height=height,
                            bg=COLOR_DARK_BG, highlightthickness=0)
        self.canvas.pack()
        
        # Icons laden
        self._load_icons()
        
        self.photo_image = None
        self._draw()
    
    def update(self, pv=0, load=0, battery=0, grid=0, battery_soc=50):
        """Update widget mit neuen Werten"""
        self.pv_power = pv
        self.load_power = load
        self.battery_power = battery
        self.grid_power = grid
        self.battery_soc = battery_soc
        self._draw()
    
    def _load_icons(self):
        """Lädt PNG-Icons oder erstellt Fallbacks"""
        # Nach Reorganisierung: icons in resources/icons
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_path = os.path.join(base_dir, "resources", "icons")
        self.icons = {}
        
        try:
            # Versuche Icons zu laden (mit den richtigen Dateinamen!)
            pv_path = os.path.join(icon_path, "pv.png")
            grid_path = os.path.join(icon_path, "grid.png")
            house_path = os.path.join(icon_path, "house.png")
            battery_path = os.path.join(icon_path, "battery.png")
            
            if os.path.exists(pv_path):
                self.icons['pv'] = self._load_and_remove_background(pv_path, (60, 60))
                print("✓ PV-Icon geladen")
            if os.path.exists(grid_path):
                self.icons['grid'] = self._load_and_remove_background(grid_path, (60, 60))
                print("✓ Grid-Icon geladen")
            if os.path.exists(house_path):
                self.icons['house'] = self._load_and_remove_background(house_path, (80, 80))
                print("✓ House-Icon geladen")
            if os.path.exists(battery_path):
                self.icons['battery'] = self._load_and_remove_background(battery_path, (60, 60))
                print("✓ Battery-Icon geladen")
        except Exception as e:
            print(f"Info: Icons werden gezeichnet (Fallback): {e}")
    
    def _load_and_remove_background(self, path, size):
        """Lädt Icon und entfernt weißen/grauen Hintergrund"""
        img = Image.open(path)
        
        # Konvertiere zu RGBA wenn nicht bereits
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Entferne weißen/hellgrauen Hintergrund durch Alpha-Transparency
        # Pixel die sehr hell sind (> 200 in allen Kanälen) werden transparent
        data = img.getdata()
        new_data = []
        for item in data:
            # Wenn RGB-Werte alle > 200 (sehr hell), mach es transparent
            if len(item) >= 3:
                r, g, b = item[0], item[1], item[2]
                if r > 200 and g > 200 and b > 200:
                    new_data.append((255, 255, 255, 0))  # Transparent
                else:
                    if len(item) == 4:
                        new_data.append(item)  # Behalte Alpha wenn vorhanden
                    else:
                        new_data.append((item[0], item[1], item[2], 255))  # Volles Alpha
        
        img.putdata(new_data)
        return img.resize(size)
    
    def _draw(self):
        """Zeichnet Glasmorphism Energiefluss-Visualisierung"""
        # PIL Image erstellen mit dunklem Hintergrund
        img = Image.new('RGBA', (self.width, self.height), color=(10, 14, 26, 255))
        draw = ImageDraw.Draw(img)
        
        # Layout-Positionen (kompakt, verteilt)
        # Glasmorphism Cards statt harte Boxen

        # ===== GLASMORPHISM CARDS MIT ICONS =====

        # 1. PV (Glass Card) links-oben
        self._draw_glass_card(draw, 120, 40, 260, 100, "Solar")
        if 'pv' in self.icons:
            img.paste(self.icons['pv'], (160, 45), mask=self.icons['pv'])
        else:
            self._draw_sun(draw, 190, 65)
        draw.text((190, 115), f"{int(self.pv_power)}W", fill=COLOR_TEXT, anchor="mm", font=None)

        # 2. GRID (Glass Card) rechts-oben
        self._draw_glass_card(draw, 440, 40, 580, 100, "Netz")
        if 'grid' in self.icons:
            img.paste(self.icons['grid'], (480, 45), mask=self.icons['grid'])
        else:
            self._draw_lightning(draw, 510, 65, COLOR_TEXT)
        draw.text((510, 115), f"{int(abs(self.grid_power))}W", fill=COLOR_TEXT, anchor="mm")

        # 3. HAUS (Glass Card, Mitte)
        self._draw_glass_card(draw, 280, 170, 420, 240, "Verbrauch")
        if 'house' in self.icons:
            img.paste(self.icons['house'], (310, 175), mask=self.icons['house'])
        else:
            self._draw_house(draw, 350, 200)
        draw.text((350, 255), f"{int(self.load_power)}W", fill=COLOR_TEXT, anchor="mm", font=None)

        # 4. BATTERIE (Glass Card) unten mittig
        self._draw_glass_card(draw, 280, 290, 420, 350, f"Batterie {int(self.battery_soc)}%")
        if 'battery' in self.icons:
            img.paste(self.icons['battery'], (310, 295), mask=self.icons['battery'])
        else:
            self._draw_battery(draw, 350, 315, self.battery_soc)
        draw.text((350, 365), f"{int(abs(self.battery_power))}W", fill=COLOR_TEXT, anchor="mm")
        
        # ===== GLOWING SMART ARROWS =====

        # PV → Haus (GRÜN für Produktion)
        if self.pv_power > 10:
            self._draw_glow_arrow(draw, 230, 100, 310, 175, COLOR_SUCCESS, 3)

        # Grid → Haus (ORANGE) oder Haus → Grid (GRÜN)
        if self.grid_power > 10:  # Netzbezug
            self._draw_glow_arrow(draw, 470, 100, 390, 175, COLOR_WARNING, 3)
        elif self.grid_power < -10:  # Einspeisung
            self._draw_glow_arrow(draw, 390, 175, 470, 100, COLOR_SUCCESS, 3)

        # Batterie ↔ Haus
        if self.battery_power > 10:  # Entladen (gut)
            self._draw_glow_arrow(draw, 350, 290, 350, 240, COLOR_SUCCESS, 3)
        elif self.battery_power < -10:  # Laden
            self._draw_glow_arrow(draw, 350, 240, 350, 290, COLOR_WARNING, 3)
        
        # Speichern als PhotoImage
        img_rgb = img.convert('RGB')
        self.photo_image = tk.PhotoImage(data=self._pil_to_ppm(img_rgb))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image)
    
    def _draw_glass_card(self, draw, x1, y1, x2, y2, label=""):
        """Zeichnet Glasmorphism Card mit transparentem Effekt"""
        radius = 16
        
        # Glass Hintergrund (dunkel transparent)
        draw.rounded_rectangle(
            [x1, y1, x2, y2], 
            radius=radius, 
            fill=COLOR_GLASS_BG, 
            outline=COLOR_BORDER, 
            width=2
        )
        
        # Label oben links (klein, gedimmt)
        if label:
            try:
                label_font = ImageFont.truetype("segoeui.ttf", 9)
            except:
                label_font = None
            draw.text((x1 + 10, y1 + 8), label, fill=COLOR_SUBTEXT, anchor="lm", font=label_font)
    
    def _draw_glow_arrow(self, draw, x1, y1, x2, y2, color, width=3):
        """Zeichnet glühende Arrows mit Glow-Effekt"""
        import math
        
        # Zeichne Linie mit fester Breite (sauberer)
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
        
        # Pfeilspitze
        angle = math.atan2(y2 - y1, x2 - x1)
        arrow_size = 15
        
        p1_x = x2 - arrow_size * math.cos(angle - math.pi / 6)
        p1_y = y2 - arrow_size * math.sin(angle - math.pi / 6)
        p2_x = x2 - arrow_size * math.cos(angle + math.pi / 6)
        p2_y = y2 - arrow_size * math.sin(angle + math.pi / 6)
        
        draw.polygon([(x2, y2), (p1_x, p1_y), (p2_x, p2_y)], fill=color)
    
    def _draw_sun(self, draw, x, y):
        """Zeichnet Sonne"""
        import math
        # Strahlen
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = x + 15 * math.cos(rad)
            y1 = y + 15 * math.sin(rad)
            x2 = x + 25 * math.cos(rad)
            y2 = y + 25 * math.sin(rad)
            draw.line([(x1, y1), (x2, y2)], fill="#fbbf24", width=2)
        # Kreis
        draw.ellipse([x-12, y-12, x+12, y+12], fill="#fbbf24")
    
    def _draw_lightning(self, draw, x, y, color):
        """Zeichnet Blitz"""
        points = [
            (x-6, y-15), (x-1, y-3), (x-10, y-1),
            (x, y+15), (x+6, y+3), (x, y-1), (x-6, y-15)
        ]
        draw.polygon(points, fill=color)
    
    def _draw_house(self, draw, x, y):
        """Zeichnet Haus"""
        # Körper
        draw.rectangle([x-20, y-10, x+20, y+20], fill="#f472b6")
        # Dach
        draw.polygon([(x-20, y-10), (x, y-25), (x+20, y-10)], fill="#c2410c")
        # Fenster
        draw.rectangle([x-12, y-5, x-5, y+2], fill="#1f2937")
        draw.rectangle([x+5, y-5, x+12, y+2], fill="#1f2937")
        # Tür
        draw.rectangle([x-5, y+5, x+5, y+18], fill="#1f2937")
    
    def _draw_battery(self, draw, x, y, soc):
        """Zeichnet Batterie mit Ladestand - Orange Stil"""
        # Äußeres Rechteck (Batterie-Körper)
        draw.rectangle([x-14, y-22, x+14, y+22], fill="#1f2937", outline="#f59e0b", width=3)
        
        # Batterie-Kopf (kleine Ausbuchtung oben)
        draw.rectangle([x-7, y-28, x+7, y-22], fill="#1f2937", outline="#f59e0b", width=3)
        
        # Innenfüllung basierend auf SOC
        fill_height = int(40 * (soc / 100))
        if soc < 20:
            fill_color = "#ef4444"  # Rot
        elif soc < 50:
            fill_color = "#f59e0b"  # Orange
        else:
            fill_color = "#34d399"  # Grün
        
        # Füllung von unten nach oben
        draw.rectangle([x-11, y+18-fill_height, x+11, y+18], fill=fill_color, outline=None)
    
    def _pil_to_ppm(self, img):
        """Konvertiert PIL Image zu PPM String für PhotoImage"""
        with io.BytesIO() as output:
            img.save(output, format="PPM")
            data = output.getvalue()
        return data.decode('latin-1')
    
    def update_flows(self, pv_power, load_power, battery_power, grid_power, battery_soc=None):
        """Aktualisiert Energiewerte"""
        self.pv_power = float(pv_power)
        self.load_power = float(load_power)
        self.battery_power = float(battery_power)
        self.grid_power = float(grid_power)
        if battery_soc is not None:
            self.battery_soc = float(max(0, min(100, battery_soc)))
        
        self._draw()
    
    def update_kwh_values(self, pv_kwh=0, grid_kwh=0, battery_kwh=0, load_kwh=0):
        """Aktualisiert kWh-Werte (Tagesertrag)"""
        self.pv_kwh = float(pv_kwh)
        self.grid_kwh = float(grid_kwh)
        self.battery_kwh = float(battery_kwh)
        self.load_kwh = float(load_kwh)
