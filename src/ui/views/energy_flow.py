import tkinter as tk
import math
import time
import os
from PIL import Image, ImageDraw, ImageFont, ImageTk
from ui.styles import (
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_ROOT,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_INFO,
    COLOR_DANGER,
)

# Feste Größe ohne UI-Scaling: alles bleibt konstant
_EF_SCALE = 1.0

def _s(val: float) -> int:
    return int(round(val * _EF_SCALE))


DEBUG_LOG = os.getenv("DASH_DEBUG", "0") == "1"

class EnergyFlowView(tk.Frame):
    """PIL-basierter, flimmerfreier Energiefluss. Ein Canvas-Image pro Update."""

    def __init__(self, parent: tk.Widget, width: int = 420, height: int = 400):
        super().__init__(parent, bg=COLOR_CARD)
        self._start_time = time.time()
        self.canvas = tk.Canvas(self, width=width, height=height, highlightthickness=0, bg=COLOR_CARD)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._resize_pending = False  # Debounce Configure events
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.width = width
        self.height = height
        self.node_radius = _s(38)
        self.ring_gap = _s(10)
        self._tk_img = None
        self._font_big = ImageFont.truetype("arial.ttf", _s(40)) if self._has_font("arial.ttf") else None
        self._font_small = ImageFont.truetype("arial.ttf", _s(24)) if self._has_font("arial.ttf") else None
        self._font_tiny = ImageFont.truetype("arial.ttf", _s(17)) if self._has_font("arial.ttf") else None
        # Emoji font support with multiple fallbacks
        self._font_emoji = self._find_emoji_font(_s(36))
        
        # Load PNG icons - will be pasted onto PIL image
        self._icons_pil = {}  # PIL Images for embedding
        self._load_icons()

        self.nodes = self._define_nodes()
        self._base_img = self._render_background()
        self._canvas_img = self.canvas.create_image(0, 0, anchor="nw")
        
        # Performance optimization: track last values to skip rendering when unchanged
        self._last_flows = None

    def _on_canvas_resize(self, event):
        """Re-render background and last frame when the canvas grows."""
        # Debounce rapid resize events
        if self._resize_pending:
            return
        
        new_w = max(240, int(event.width))
        new_h = max(200, int(event.height))
        if abs(new_w - self.width) < 10 and abs(new_h - self.height) < 10:  # 10px threshold
            return

        self._resize_pending = True
        self.width = new_w
        self.height = new_h
        self.nodes = self._define_nodes()
        self._base_img = self._render_background()
        self.canvas.config(width=new_w, height=new_h)

        if self._last_flows:
            pv, load, grid, batt, soc = self._last_flows
            frame = self.render_frame(pv, load, grid, batt, soc)
        else:
            frame = self._base_img

        self._tk_img = ImageTk.PhotoImage(frame)
        self.canvas.itemconfig(self._canvas_img, image=self._tk_img)
        self._resize_pending = False

    def resize(self, width: int, height: int):
        """FIXED: Only update canvas size and dimensions, don't recreate background."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[ENERGY] resize() called at {elapsed:.3f}s with {width}x{height}")
        
        old_w, old_h = self.width, self.height
        width = max(240, int(width))
        height = max(200, int(height))
        
        # Only update canvas config and internal dimensions
        self.canvas.config(width=width, height=height)
        self.width = width
        self.height = height
        self.nodes = self._define_nodes()
        
        # Only recreate background if size changed significantly (>20px)
        if abs(width - old_w) > 20 or abs(height - old_h) > 20:
            if DEBUG_LOG:
                print(f"[ENERGY] Large size change, recreating background")
            self._base_img = self._render_background()
        else:
            if DEBUG_LOG:
                print(f"[ENERGY] Small change, skipping background recreate")

    def _has_font(self, name: str) -> bool:
        try:
            ImageFont.truetype(name, 12)
            return True
        except Exception:
            return False

    def _load_icons(self):
        """Load and cache PNG icons from icons directory."""
        elapsed = time.time() - self._start_time
        
        # Nach Reorganisierung: icons in resources/icons
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))  # views -> ui -> src -> root
        icon_dir = os.path.join(project_root, "resources", "icons")
        
        icon_files = {
            "pv": "pv.png",
            "grid": "grid.png",
            "home": "house.png",
            "battery": "battery.png",
        }
        
        icon_size = int(self.node_radius * 1.15)  # Dynamic sizing based on node radius
        
        for icon_name, filename in icon_files.items():
            try:
                icon_path = os.path.join(icon_dir, filename)
                if not os.path.exists(icon_path):
                    print(f"[ICONS] WARNING: {icon_name} icon not found at {icon_path}")
                    continue
                    
                # Load, convert to RGBA, and resize
                img = Image.open(icon_path).convert("RGBA")
                img = img.resize((icon_size, icon_size), Image.LANCZOS)
                self._icons_pil[icon_name] = img
                print(f"[ICONS] Loaded {icon_name} at {elapsed:.3f}s ({icon_size}x{icon_size})")
            except Exception as e:
                print(f"[ICONS] Error loading {icon_name}: {e}")
        
        if not self._icons_pil:
            print(f"[ICONS] WARNING: No icons loaded! Falling back to text labels.")

    def _find_emoji_font(self, size: int):
        """Find emoji font with multiple fallback paths for cross-platform support."""
        # Common emoji font names and paths
        emoji_fonts = [
            "seguiemj.ttf",  # Windows
            "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",  # Linux
            "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",  # Linux alternative
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Fallback
            "DejaVuSans.ttf",  # Generic fallback
        ]
        
        for font_path in emoji_fonts:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                pass
        
        # If no emoji font found, return None and use default
        return None

    def _define_nodes(self):
        w, h = self.width, self.height
        margin_x = int(w * 0.06)
        margin_top = _s(40)
        margin_bottom = _s(90)  # Platz für SoC-Text unter der Batterie
        usable_h = h - margin_top - margin_bottom
        battery_dx = _s(-160)  # weit nach links versetzen, damit Haus/Batterie genügend Abstand haben
        return {
            "pv": (margin_x + int((w - 2 * margin_x) * 0.20), margin_top + int(usable_h * 0.15)),
            "grid": (w - margin_x - int((w - 2 * margin_x) * 0.20), margin_top + int(usable_h * 0.15)),
            "home": (w // 2, margin_top + int(usable_h * 0.55)),
            "battery": (w // 2 + battery_dx, margin_top + int(usable_h * 0.94)),
        }

    def _render_background(self) -> Image.Image:
        img = self._draw_bg_gradient()
        draw = ImageDraw.Draw(img)
        # Draw node circles (background + effects)
        for name, (x, y) in self.nodes.items():
            self._draw_node_circle(draw, x, y, name)
        # Paste icons on top
        for name, (x, y) in self.nodes.items():
            if name in self._icons_pil:
                icon = self._icons_pil[name]
                icon_w, icon_h = icon.size
                paste_x = int(x - icon_w / 2)
                paste_y = int(y - icon_h / 2)
                # Battery offset to avoid SoC overlap
                if name == "battery":
                    paste_y -= 8
                img.paste(icon, (paste_x, paste_y), icon)  # Use alpha channel
        return img

    def _draw_bg_gradient(self) -> Image.Image:
        """Elliptical gradient: matches widget shape, very transparent at edges."""
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        
        center_x = self.width // 2
        center_y = self.height // 2
        
        # Subtle blueish color
        color = (14, 24, 40)
        
        for y in range(self.height):
            for x in range(self.width):
                # Elliptical distance - scales with widget dimensions
                dx = (x - center_x) / (self.width / 2)
                dy = (y - center_y) / (self.height / 2)
                
                # Normalized elliptical distance (0=center, 1=edge)
                norm_dist = min(1.0, (dx ** 2 + dy ** 2) ** 0.5)
                
                # Alpha falloff: center ~220, edges ~5 (nearly invisible)
                # Smoother power function for seamless blend
                alpha_val = int(220 * (1.0 - norm_dist ** 0.7))
                
                d.point((x, y), fill=(color[0], color[1], color[2], alpha_val))
        
        return img

    def _draw_node_circle(self, draw: ImageDraw.ImageDraw, x: int, y: int, name: str):
        """Draw node circle background with effects (no text/icons)."""
        r = self.node_radius + (6 if name == "home" else 0)
        fill = COLOR_BORDER
        if name == "home":
            fill = COLOR_PRIMARY
        elif name == "pv":
            fill = COLOR_SUCCESS
        elif name == "grid":
            fill = COLOR_INFO
        elif name == "battery":
            fill = COLOR_WARNING
        # Beautiful soft shadow + subtle glow
        self._draw_soft_shadow(draw, x, y, r, fill)
        self._draw_subtle_glow(draw, x, y, r, fill)
        # Radial gradient (subtle)
        self._draw_radial(draw, x, y, r, fill)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill, outline=None, width=0)

    def _text_center(self, draw: ImageDraw.ImageDraw, text: str, x: int, y: int, size: int, color: str = COLOR_TEXT, fontweight: str = "normal", outline: bool = False):
        # Use emoji font for emoji characters, otherwise use bold font
        is_emoji = any(ord(c) > 0x1F000 for c in text)
        
        if is_emoji and self._font_emoji:
            font = self._font_emoji
        else:
            try:
                font = ImageFont.truetype("arial.ttf", size, weight="bold" if fontweight == "bold" else "normal")
            except Exception:
                font = self._font_big if size > 20 and self._font_big else ImageFont.load_default()
                if size <= 20:
                    font = self._font_small if self._font_small else ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        text_x = x - tw / 2
        text_y = y - th / 2
        
        # Draw black outline for better readability
        if outline:
            outline_color = "#000000"
            # Draw outline in 8 directions + thicker center
            for dx in [-2, -1, 0, 1, 2]:
                for dy in [-2, -1, 0, 1, 2]:
                    if dx != 0 or dy != 0:  # Skip center
                        draw.text((text_x + dx, text_y + dy), text, font=font, fill=outline_color)
        
        # Draw main text on top
        draw.text((text_x, text_y), text, font=font, fill=color)

    def _edge_points(self, src, dst, offset: float):
        x0, y0 = src
        x1, y1 = dst
        vx, vy = x1 - x0, y1 - y0
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        ux, uy = vx / length, vy / length
        return (
            (x0 + ux * offset, y0 + uy * offset),
            (x1 - ux * offset, y1 - uy * offset),
        )

    def _draw_arrow(self, draw: ImageDraw.ImageDraw, src, dst, color: str, width: float):
        start, end = self._edge_points(src, dst, self.node_radius)
        x0, y0 = start
        x1, y1 = end
        draw.line((x0, y0, x1, y1), fill=color, width=int(width))
        # Arrow head
        vx, vy = x1 - x0, y1 - y0
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        ux, uy = vx / length, vy / length
        size = 12 + width
        left = (x1 - ux * size + uy * size * 0.6, y1 - uy * size - ux * size * 0.6)
        right = (x1 - ux * size - uy * size * 0.6, y1 - uy * size + ux * size * 0.6)
        draw.polygon([left, right, (x1, y1)], fill=color)

    def _draw_flow_label(self, base_img: Image.Image, src, dst, watts: float, offset: int = 8, along: int = 0, color: str = COLOR_TEXT, flip_text: bool = False):
        start, end = self._edge_points(src, dst, self.node_radius + 6)
        mx = (start[0] + end[0]) / 2
        my = (start[1] + end[1]) / 2

        # Perpendicular offset away from the arrow line
        vx, vy = end[0] - start[0], end[1] - start[1]
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        nx, ny = -vy / length, vx / length
        ux, uy = vx / length, vy / length
        px = mx + nx * offset + ux * along
        py = my + ny * offset + uy * along

        # Render rotated text along arrow direction
        angle = -1 * (180 / math.pi) * (0 if length == 0 else math.atan2(vy, vx))
        # Auto-flip if upside down (keep labels readable)
        if abs(angle) > 90:
            angle += 180
        # Optional extra flip
        if flip_text:
            angle += 180
        value_text, unit_text = self._format_power_parts(abs(watts))
        font_val = self._font_small if self._font_small else ImageFont.load_default()
        font_unit = self._font_tiny if self._font_tiny else ImageFont.load_default()

        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        ddraw = ImageDraw.Draw(dummy)
        vbox = ddraw.textbbox((0, 0), value_text, font=font_val)
        ubox = ddraw.textbbox((0, 0), unit_text, font=font_unit)
        vw, vh = vbox[2] - vbox[0], vbox[3] - vbox[1]
        uw, uh = ubox[2] - ubox[0], ubox[3] - ubox[1]
        h = max(vh, uh)
        w = vw + 4 + uw

        txt_img = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(txt_img)
        unit_color = self._tint(color, 0.45)
        tdraw.text((6, 6 + (h - vh) / 2), value_text, font=font_val, fill=color)
        tdraw.text((6 + vw + 4, 6 + (h - uh) / 2), unit_text, font=font_unit, fill=unit_color)
        rotated = txt_img.rotate(angle, resample=Image.BICUBIC, expand=True)

        rx, ry = rotated.size
        base_img.paste(rotated, (int(px - rx / 2), int(py - ry / 2)), rotated)

    def _format_power(self, watts: float) -> str:
        if abs(watts) < 1000:
            return f"{watts:.0f} W"
        return f"{watts/1000:.2f} kW"

    def _format_power_parts(self, watts: float) -> tuple[str, str]:
        if abs(watts) < 1000:
            return f"{watts:.0f}", "W"
        return f"{watts/1000:.2f}", "kW"

    def _hex_to_rgb(self, color: str) -> tuple[int, int, int]:
        c = color.lstrip("#")
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))

    def _tint(self, color: str, amount: float) -> str:
        r, g, b = self._hex_to_rgb(color)
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_soft_shadow(self, draw: ImageDraw.ImageDraw, x: int, y: int, r: int, color: str):
        """Very soft multi-layer shadow with smooth falloff."""
        # Shadow offset slightly down and right
        offset_x = 2
        offset_y = 4
        
        # More shadow layers with very smooth alpha falloff
        shadow_layers = [
            (r + 16, 4),   # Outermost, barely visible
            (r + 12, 6),   # Outer
            (r + 9, 9),    # Mid-outer
            (r + 6, 12),   # Mid
            (r + 3, 15),   # Inner
        ]
        
        for shadow_r, alpha in shadow_layers:
            draw.ellipse(
                [x - shadow_r + offset_x, y - shadow_r + offset_y, 
                 x + shadow_r + offset_x, y + shadow_r + offset_y],
                fill=(0, 0, 0, alpha)
            )

    def _draw_subtle_glow(self, draw: ImageDraw.ImageDraw, x: int, y: int, r: int, color: str):
        """Very subtle color-matched glow with smooth falloff."""
        base = self._hex_to_rgb(color)
        
        # More glow layers for smoother transition
        glow_layers = [
            (r + 14, 4),   # Outermost glow
            (r + 11, 6),   # Outer glow
            (r + 8, 8),    # Mid-outer glow
            (r + 5, 11),   # Mid glow
            (r + 2, 14),   # Inner glow
        ]
        
        for glow_r, alpha in glow_layers:
            draw.ellipse(
                [x - glow_r, y - glow_r, x + glow_r, y + glow_r],
                fill=base + (alpha,)
            )

    def _draw_radial(self, draw: ImageDraw.ImageDraw, x: int, y: int, r: int, color: str):
        for i in range(r, 0, -4):
            t = 1 - (i / r)
            c = self._tint(color, 0.18 + t * 0.25)
            draw.ellipse([x - i, y - i, x + i, y + i], fill=c)

    def _draw_soc_ring(self, draw: ImageDraw.ImageDraw, center, soc: float):
        x, y = center
        r = self.node_radius + self.ring_gap
        bbox = [x - r, y - r, x + r, y + r]
        extent = max(0, min(360, 360 * soc / 100))
        color = COLOR_SUCCESS if soc >= 70 else (COLOR_WARNING if soc >= 35 else COLOR_DANGER)
        draw.arc(bbox, start=-90, end=-90 + extent, fill=color, width=4)

    def render_frame(self, pv_w: float, load_w: float, grid_w: float, batt_w: float, soc: float) -> Image.Image:
        img = self._base_img.copy()
        draw = ImageDraw.Draw(img)

        pv = self.nodes["pv"]
        grid = self.nodes["grid"]
        home = self.nodes["home"]
        bat = self.nodes["battery"]

        def clamp(val, lo, hi):
            return max(lo, min(hi, val))

        def thickness(watts):
            return clamp(2 + abs(watts) / 1500, 2, 8)

        # PV -> Haus
        if pv_w > 0:
            self._draw_arrow(draw, pv, home, COLOR_SUCCESS, thickness(pv_w))
            self._draw_flow_label(img, pv, home, pv_w, offset=28, along=0, color=COLOR_SUCCESS)

        # Grid Import/Export
        if grid_w > 0:
            self._draw_arrow(draw, grid, home, COLOR_INFO, thickness(grid_w))
            self._draw_flow_label(img, grid, home, grid_w, offset=28, along=0, color=COLOR_INFO, flip_text=True)
        elif grid_w < 0:
            self._draw_arrow(draw, home, grid, COLOR_INFO, thickness(grid_w))
            self._draw_flow_label(img, home, grid, grid_w, offset=28, along=0, color=COLOR_INFO)

        # Batterie Laden/Entladen (batt_w > 0 = Entladen)
        if batt_w > 0:
            # Entladen: Batterie -> Haus (rot)
            self._draw_arrow(draw, bat, home, COLOR_DANGER, thickness(batt_w))
            self._draw_flow_label(img, bat, home, batt_w, offset=15, along=0, color=COLOR_DANGER)
        elif batt_w < 0:
            # Laden: Haus -> Batterie (grün)
            self._draw_arrow(draw, home, bat, COLOR_SUCCESS, thickness(batt_w))
            self._draw_flow_label(img, home, bat, batt_w, offset=15, along=0, color=COLOR_SUCCESS)

        # SoC Ring um Batterie
        self._draw_soc_ring(draw, bat, soc)

        # Werte anzeigen mit Einheiten
        self._text_center(draw, f"Haus {self._format_power(load_w)}", home[0], home[1] + 70, size=16, color=COLOR_PRIMARY)

        # SoC inside battery with outline for readability - moved down to avoid emoji overlap
        self._text_center(draw, f"{soc:.0f}%", bat[0], bat[1] + 8, size=20, color=COLOR_TEXT, outline=True)
        self._text_center(draw, "SoC", bat[0], bat[1] + 28, size=12, color=COLOR_TEXT, outline=True)
        return img

    def update_flows(self, pv_w: float, load_w: float, grid_w: float, batt_w: float, soc: float):
        """Update power flows - only re-render if values changed significantly (save CPU)."""
        values = (pv_w, load_w, grid_w, batt_w, soc)
        
        # Skip rendering if values haven't changed significantly (saves PIL rendering CPU)
        if self._last_flows:
            last_pv, last_load, last_grid, last_batt, last_soc = self._last_flows
            # Only re-render if: delta > 1% OR absolute change > 50W
            if (abs(pv_w - last_pv) < 0.05 * max(1, last_pv + pv_w) or abs(pv_w - last_pv) < 50) and \
                (abs(load_w - last_load) < 0.05 * max(1, last_load + load_w) or abs(load_w - last_load) < 50) and \
                (abs(grid_w - last_grid) < 0.05 * max(1, last_grid + grid_w) or abs(grid_w - last_grid) < 50) and \
                (abs(batt_w - last_batt) < 0.05 * max(1, last_batt + batt_w) or abs(batt_w - last_batt) < 50) and \
                (abs(soc - last_soc) < 2):
                return  # Skip render - values too similar, saves CPU
        
        self._last_flows = values
        
        # Check for canvas size changes
        cw = max(200, self.canvas.winfo_width())
        ch = max(200, self.canvas.winfo_height())
        
        # Only recreate layout if size changed by more than 30px
        if abs(cw - self.width) > 30 or abs(ch - self.height) > 30:
            elapsed = time.time() - self._start_time
            if DEBUG_LOG:
                print(f"[ENERGY] update_flows detected SIGNIFICANT size change at {elapsed:.3f}s: {self.width}x{self.height} -> {cw}x{ch}")
            self.width, self.height = cw, ch
            self.nodes = self._define_nodes()
            self._base_img = self._render_background()

        frame = self.render_frame(pv_w, load_w, grid_w, batt_w, soc)
        self._tk_img = ImageTk.PhotoImage(frame)
        self.canvas.itemconfig(self._canvas_img, image=self._tk_img)
