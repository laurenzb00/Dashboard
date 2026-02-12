import os
DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
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

DEBUG_LOG = False  # Enable for verbose energy-flow debugging

# Feste Größe ohne UI-Scaling: alles bleibt konstant
_EF_SCALE = 1.0

def _s(val: float) -> int:
    return int(round(val * _EF_SCALE))



MISSING_LOG_COOLDOWN = 60.0  # seconds

from core.schema import PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT, LOAD_POWER_KW

class EnergyFlowView(tk.Frame):
    def _request_redraw(self):
        c = getattr(self, "canvas", None)
        if c is None:
            return
        if hasattr(c, "draw_idle"):
            c.draw_idle()
            return
        if hasattr(c, "draw"):
            c.draw()
            return
        try:
            c.update_idletasks()
        except Exception:
            pass

    def update_data(self, data: dict):
        """Update für Energiefluss-View: erwartet dict mit final keys."""
        pv_kw = float(data.get(PV_POWER_KW) or 0.0)
        grid_kw = float(data.get(GRID_POWER_KW) or 0.0)
        raw_batt_kw = float(data.get(BATTERY_POWER_KW) or 0.0)
        soc = float(data.get(BATTERY_SOC_PCT) or 0.0)

        # Fronius-Konvention (PowerFlowRealtimeData) laut Logik hier:
        # - P_Grid:  + = Netzbezug, - = Einspeisung
        # - P_Akku:  + = Batterie entlaedt (liefert), - = Batterie laedt (nimmt)
        # - P_Load:  - = Hausverbrauch
        load_kw_value = None
        if LOAD_POWER_KW in data and data.get(LOAD_POWER_KW) is not None:
            try:
                load_kw_value = float(data.get(LOAD_POWER_KW))
            except Exception:
                load_kw_value = None
        signal_kw = abs(pv_kw) + abs(grid_kw) + abs(raw_batt_kw)
        if load_kw_value is not None and load_kw_value <= 0.05 and signal_kw >= 0.2:
            load_kw_value = None

        if load_kw_value is not None:
            load_kw = abs(load_kw_value)
        else:
            load_kw = pv_kw + grid_kw + raw_batt_kw

        pv_w = pv_kw * 1000
        grid_w = grid_kw * 1000
        batt_w = raw_batt_kw * 1000

        # Hausverbrauch in W (P_Load falls vorhanden, sonst bilanziert)
        load_w = load_kw * 1000

        try:
            self.update_flows(pv_w, load_w, grid_w, batt_w, soc)
        except Exception as e:
            print("[ENERGY_FLOW ERROR]", e, flush=True)

    def update_flows(self, pv_kw, load_kw, grid_kw, batt_kw, soc):
        """Update power flows - nur redraw wenn Werte sich signifikant ändern."""
        values = (pv_kw, load_kw, grid_kw, batt_kw, soc)
        last = getattr(self, "_last_flows", None)
        if last is not None:
            if all(abs(a - b) < 0.01 for a, b in zip(values, last)):
                return
        self._last_flows = values
        # Check for canvas size changes
        cw = max(200, self.canvas.winfo_width())
        ch = max(200, self.canvas.winfo_height())
        # Only recreate layout if size changed by more than 30px
        if abs(cw - self.width) > 30 or abs(ch - self.height) > 30:
            elapsed = time.time() - self._start_time
            self.width, self.height = cw, ch
            self.nodes = self._define_nodes()
            self._base_img = self._render_background()
        try:
            frame = self.render_frame(pv_kw, load_kw, grid_kw, batt_kw, soc)
        except Exception as e:
            return
        try:
            self._tk_img = ImageTk.PhotoImage(frame)
        except Exception as e:
            return
        try:
            self.canvas.itemconfig(self._canvas_img, image=self._tk_img)
        except Exception as e:
            return
        self._request_redraw()

    def __init__(self, parent: tk.Widget, width: int = 420, height: int = 400):
        super().__init__(parent, bg=COLOR_ROOT)
        self._last_missing_log = {"pv": 0.0, "batt": 0.0}
        self._start_time = time.time()
        # UI-only animation to make flow direction clearer (no data changes).
        self._anim_enabled = True
        self._anim_interval_ms = 140
        self._anim_job = None
        self._anim_phase = 0.0
        self.canvas = tk.Canvas(self, width=width, height=height, highlightthickness=0, bg=COLOR_ROOT)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._resize_pending = False  # Debounce Configure events
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.width = width
        self.height = height
        self.node_radius = _s(46)
        self.ring_gap = _s(14)
        self._tk_img = None
        self._font_big = ImageFont.truetype("arial.ttf", _s(54)) if self._has_font("arial.ttf") else None
        self._font_small = ImageFont.truetype("arial.ttf", _s(32)) if self._has_font("arial.ttf") else None
        self._font_tiny = ImageFont.truetype("arial.ttf", _s(22)) if self._has_font("arial.ttf") else None
        self._flow_value_size = _s(24)
        self._flow_unit_size = _s(10)
        self._node_value_size = _s(24)
        self._node_unit_size = _s(10)
        # Emoji font support with multiple fallbacks
        self._font_emoji = self._find_emoji_font(_s(42))
        # Load PNG icons - will be pasted onto PIL image
        self._icons_pil = {}  # PIL Images for embedding
        self._load_icons()

        self.nodes = self._define_nodes()
        self._base_img = self._render_background()
        self._canvas_img = self.canvas.create_image(0, 0, anchor="nw")
        # Performance optimization: track last values to skip rendering when unchanged
        self._last_flows = None
        self._start_animation()

    def _start_animation(self):
        if not self._anim_enabled:
            return
        if self._anim_job is None:
            self._anim_tick()

    def _anim_tick(self):
        if not self._anim_enabled:
            self._anim_job = None
            return
        if not self.winfo_exists():
            self._anim_job = None
            return
        now = time.time()
        # Slow, subtle pulse: ~0.7 Hz
        self._anim_phase = 0.5 + 0.5 * math.sin(now * 2 * math.pi * 0.7)
        if self._last_flows:
            pv, load, grid, batt, soc = self._last_flows
            if max(abs(pv), abs(load), abs(grid), abs(batt)) < 50:
                self._anim_job = self.after(self._anim_interval_ms, self._anim_tick)
                return
            frame = self.render_frame(pv, load, grid, batt, soc)
            self._tk_img = ImageTk.PhotoImage(frame)
            self.canvas.itemconfig(self._canvas_img, image=self._tk_img)
            self._request_redraw()
        self._anim_job = self.after(self._anim_interval_ms, self._anim_tick)

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
                    if DEBUG_LOG:
                        print(f"[ICONS] WARNING: {icon_name} icon not found at {icon_path}")
                    continue
                    
                # Load, convert to RGBA, and resize
                img = Image.open(icon_path).convert("RGBA")
                img = img.resize((icon_size, icon_size), Image.LANCZOS)
                self._icons_pil[icon_name] = img
                if DEBUG_LOG:
                    print(f"[ICONS] Loaded {icon_name} at {elapsed:.3f}s ({icon_size}x{icon_size})")
            except Exception as e:
                if DEBUG_LOG:
                    print(f"[ICONS] Error loading {icon_name}: {e}")
        
        if not self._icons_pil:
            if DEBUG_LOG:
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
        margin_x = int(w * 0.02)
        margin_top = _s(32)
        margin_bottom = _s(56)  # Space for SoC ring at the battery
        usable_h = h - margin_top - margin_bottom
        battery_dx = _s(-200)  # Push battery into lower-left
        return {
            "pv": (margin_x + int((w - 2 * margin_x) * 0.16), margin_top + int(usable_h * 0.14)),
            "grid": (w - margin_x - int((w - 2 * margin_x) * 0.16), margin_top + int(usable_h * 0.14)),
            "home": (w // 2, margin_top + int(usable_h * 0.58)),
            "battery": (w // 2 + battery_dx, margin_top + int(usable_h * 0.96)),
        }

    def _render_background(self) -> Image.Image:
        img = self._draw_bg_gradient()
        draw = ImageDraw.Draw(img)
        # Draw node circles (background + effects)
        for name, (x, y) in self.nodes.items():
            self._draw_node_circle(draw, x, y, name)
        # Paste icons on top
        for name, (x, y) in self.nodes.items():
            # Battery icon is drawn dynamically (shows SOC) in render_frame.
            if name == "battery":
                continue
            if name in self._icons_pil:
                icon = self._icons_pil[name]
                icon_w, icon_h = icon.size
                paste_x = int(x - icon_w / 2)
                paste_y = int(y - icon_h / 2)
                # Home offset to place the icon as high as possible
                if name == "home":
                    paste_y -= 10
                img.paste(icon, (paste_x, paste_y), icon)  # Use alpha channel
        return img

    def _draw_battery_glyph(self, draw: ImageDraw.ImageDraw, center: tuple[int, int], soc: float) -> None:
        """Draw a battery glyph with fill level based on SOC."""
        x, y = center
        # Place glyph slightly above the % text.
        y = int(y - 18)

        soc = max(0.0, min(100.0, float(soc)))
        w = 34
        h = 16
        cap_w = 4
        cap_h = 8
        radius = 4
        outline_w = 2

        left = int(x - (w / 2))
        top = int(y - (h / 2))
        right = left + w
        bottom = top + h

        cap_left = right
        cap_top = int(y - cap_h / 2)
        cap_right = cap_left + cap_w
        cap_bottom = cap_top + cap_h

        # Choose fill color by SOC.
        if soc < 20:
            fill_hex = COLOR_DANGER
        elif soc < 35:
            fill_hex = COLOR_WARNING
        else:
            fill_hex = COLOR_SUCCESS

        outline = self._with_alpha(COLOR_TEXT, 210)
        body_bg = self._with_alpha(COLOR_ROOT, 90)
        fill_col = self._with_alpha(fill_hex, 220)

        # Body
        draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=body_bg, outline=outline, width=outline_w)
        # Cap
        draw.rounded_rectangle([cap_left, cap_top, cap_right, cap_bottom], radius=2, fill=outline, outline=None)

        # Inner fill
        inner_pad = 3
        inner_left = left + inner_pad
        inner_top = top + inner_pad
        inner_right = right - inner_pad
        inner_bottom = bottom - inner_pad

        fill_w = int((inner_right - inner_left) * (soc / 100.0))
        if fill_w > 0:
            draw.rounded_rectangle(
                [inner_left, inner_top, inner_left + fill_w, inner_bottom],
                radius=2,
                fill=fill_col,
                outline=None,
            )

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

    def _get_font(self, size: int, bold: bool = False):
        candidates = [
            "arialbd.ttf" if bold else "arial.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
            "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf",
        ]
        for name in candidates:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _draw_value_unit(self, draw: ImageDraw.ImageDraw, value: str, unit: str, x: int, y: int, value_size: int, unit_size: int, value_color: str, unit_color: str):
        """Draw a dominant value with a smaller unit underneath for clear hierarchy."""
        value_font = self._get_font(value_size, bold=True)
        unit_font = self._get_font(unit_size, bold=False)

        vbox = draw.textbbox((0, 0), value, font=value_font)
        ubox = draw.textbbox((0, 0), unit, font=unit_font)
        vw, vh = vbox[2] - vbox[0], vbox[3] - vbox[1]
        uw, uh = ubox[2] - ubox[0], ubox[3] - ubox[1]

        value_x = x - vw / 2
        value_y = y - (vh + uh + 4) / 2
        unit_x = x - uw / 2
        unit_y = value_y + vh + 4

        draw.text((value_x, value_y), value, font=value_font, fill=value_color)
        draw.text((unit_x, unit_y), unit, font=unit_font, fill=unit_color)

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

    def _draw_arrow(self, draw: ImageDraw.ImageDraw, src, dst, color: str, width: float, pulse: float = 0.0, gap: float = 0.0):
        start, end = self._edge_points(src, dst, self.node_radius + gap)
        x0, y0 = start
        x1, y1 = end
        base_w = int(width)
        pulse_w = int(max(1, base_w + 1 + 2 * pulse))
        glow_alpha = int(18 + 50 * pulse)
        glow_color = self._with_alpha(self._tint(color, 0.35), glow_alpha)
        line_color = self._with_alpha(color, 170)
        draw.line((x0, y0, x1, y1), fill=glow_color, width=pulse_w + 1)
        draw.line((x0, y0, x1, y1), fill=line_color, width=base_w)
        # Start cap to make source/direction clearer (visual-only)
        draw.ellipse([x0 - 3, y0 - 3, x0 + 3, y0 + 3], fill=line_color, outline=None)
        # Arrow head
        vx, vy = x1 - x0, y1 - y0
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        ux, uy = vx / length, vy / length
        size = 14 + width
        left = (x1 - ux * size + uy * size * 0.6, y1 - uy * size - ux * size * 0.6)
        right = (x1 - ux * size - uy * size * 0.6, y1 - uy * size + ux * size * 0.6)
        draw.polygon([left, right, (x1, y1)], fill=line_color)

    def _draw_flow_dots(self, draw: ImageDraw.ImageDraw, src, dst, color: str, strength: float, gap: float = 0.0):
        start, end = self._edge_points(src, dst, self.node_radius + gap)
        x0, y0 = start
        x1, y1 = end
        vx, vy = x1 - x0, y1 - y0
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        ux, uy = vx / length, vy / length

        dot_count = 5
        speed = 0.25 + 0.75 * strength
        radius = 1.2 + 3.2 * strength
        base_alpha = int(120 + 80 * strength)
        dot_color = self._with_alpha(color, base_alpha)

        phase = (self._anim_phase * speed) % 1.0
        for idx in range(dot_count):
            t = (phase + idx / dot_count) % 1.0
            px = x0 + ux * length * t
            py = y0 + uy * length * t
            r = radius * (0.75 + 0.25 * (idx + 1) / dot_count)
            draw.ellipse([px - r, py - r, px + r, py + r], fill=dot_color, outline=None)

    def _draw_flow_label(
        self,
        base_img: Image.Image,
        src,
        dst,
        watts: float,
        offset: int = 8,
        along: int = 0,
        color: str = COLOR_TEXT,
        outside: str | None = None,
        outside_pad: int = 0,
    ):
        start, end = self._edge_points(src, dst, self.node_radius + 6)
        mx = (start[0] + end[0]) / 2
        my = (start[1] + end[1]) / 2

        # Perpendicular offset away from the arrow line
        vx, vy = end[0] - start[0], end[1] - start[1]
        length = max((vx ** 2 + vy ** 2) ** 0.5, 1e-3)
        nx, ny = -vy / length, vx / length
        ux, uy = vx / length, vy / length

        # Pick perpendicular side deterministically in screen space.
        # outside='above' => smaller y, outside='below' => larger y.
        side = 1.0
        if outside == "above" and ny > 0:
            side = -1.0
        elif outside == "below" and ny < 0:
            side = -1.0

        eff_offset = float(offset)
        if outside in ("above", "below"):
            eff_offset += float(max(0, outside_pad))

        px = mx + nx * eff_offset * side + ux * along
        py = my + ny * eff_offset * side + uy * along

        # Render rotated text along arrow direction
        angle = -1 * (180 / math.pi) * (0 if length == 0 else math.atan2(vy, vx))
        # Auto-flip if upside down (keep labels readable)
        if abs(angle) > 90:
            angle += 180
        value_text, unit_text = self._format_power_parts(abs(watts))
        font_val = self._get_font(self._flow_value_size, bold=True)
        font_unit = self._get_font(self._flow_unit_size, bold=False)

        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        ddraw = ImageDraw.Draw(dummy)
        vbox = ddraw.textbbox((0, 0), value_text, font=font_val)
        ubox = ddraw.textbbox((0, 0), unit_text, font=font_unit)
        vw, vh = vbox[2] - vbox[0], vbox[3] - vbox[1]
        uw, uh = ubox[2] - ubox[0], ubox[3] - ubox[1]
        h = max(vh, uh)
        w = vw + 4 + uw

        pad = 8
        txt_img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(txt_img)
        # No background panel (keep labels floating above arrows)
        unit_color = self._tint(color, 0.45)
        text_x = pad
        value_y = pad + (h - vh) / 2
        unit_y = pad + (h - uh) / 2
        # Dark outline for value/unit
        outline_color = "#0a0a0a"
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                tdraw.text((text_x + dx, value_y + dy), value_text, font=font_val, fill=outline_color)
                tdraw.text((text_x + vw + 4 + dx, unit_y + dy), unit_text, font=font_unit, fill=outline_color)
        tdraw.text((text_x, value_y), value_text, font=font_val, fill=color)
        tdraw.text((text_x + vw + 4, unit_y), unit_text, font=font_unit, fill=unit_color)
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

    def _with_alpha(self, color: str, alpha: int) -> tuple[int, int, int, int]:
        r, g, b = self._hex_to_rgb(color)
        return (r, g, b, max(0, min(255, alpha)))

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
        # Neutral in normal range, warn only when low
        if soc < 20:
            color = COLOR_DANGER
        elif soc < 35:
            color = COLOR_WARNING
        elif soc < 60:
            color = COLOR_INFO
        else:
            color = COLOR_SUCCESS
        draw.arc(bbox, start=-90, end=-90 + extent, fill=color, width=5)

    def render_frame(self, pv_w: float, load_w: float, grid_w: float, batt_w: float, soc: float) -> Image.Image:
        img = self._base_img.copy()
        draw = ImageDraw.Draw(img)

        pv = self.nodes["pv"]
        grid = self.nodes["grid"]
        home = self.nodes["home"]
        bat = self.nodes["battery"]

        def clamp(val, lo, hi):
            return max(lo, min(hi, val))

        def flow_strength(watts: float) -> float:
            return clamp(abs(watts) / 3000, 0.0, 1.0)

        def thickness(watts):
            return clamp(2 + abs(watts) / 1500, 2, 8)

        min_flow_w = 50

        # PV -> Haus
        if pv_w > min_flow_w:
            pulse = self._anim_phase * flow_strength(pv_w)
            self._draw_arrow(draw, pv, home, COLOR_SUCCESS, thickness(pv_w), pulse=pulse)
            self._draw_flow_dots(draw, pv, home, COLOR_SUCCESS, flow_strength(pv_w))
            self._draw_flow_label(img, pv, home, pv_w, offset=28, outside_pad=26, along=0, color=COLOR_SUCCESS, outside="above")

        # Grid Import/Export
        if grid_w > min_flow_w:
            pulse = self._anim_phase * flow_strength(grid_w)
            self._draw_arrow(draw, grid, home, COLOR_INFO, thickness(grid_w), pulse=pulse)
            self._draw_flow_dots(draw, grid, home, COLOR_INFO, flow_strength(grid_w))
            self._draw_flow_label(img, grid, home, grid_w, offset=28, along=0, color=COLOR_INFO)
        elif grid_w < -min_flow_w:
            pulse = self._anim_phase * flow_strength(grid_w)
            self._draw_arrow(draw, home, grid, COLOR_INFO, thickness(grid_w), pulse=pulse)
            self._draw_flow_dots(draw, home, grid, COLOR_INFO, flow_strength(grid_w))
            self._draw_flow_label(img, home, grid, grid_w, offset=28, along=0, color=COLOR_INFO)

        # Batterie Laden/Entladen (Richtung dynamisch nach Vorzeichen)
        if batt_w > min_flow_w:
            # Entladen: Batterie -> Haus
            pulse = self._anim_phase * flow_strength(batt_w)
            self._draw_arrow(draw, bat, home, COLOR_SUCCESS, thickness(batt_w), pulse=pulse, gap=8)
            self._draw_flow_dots(draw, bat, home, COLOR_SUCCESS, flow_strength(batt_w), gap=8)
            self._draw_flow_label(img, bat, home, batt_w, offset=15, outside_pad=32, along=0, color=COLOR_SUCCESS, outside="below")
        elif batt_w < -min_flow_w:
            # Laden: Haus -> Batterie
            pulse = self._anim_phase * flow_strength(batt_w)
            self._draw_arrow(draw, home, bat, COLOR_WARNING, thickness(batt_w), pulse=pulse, gap=8)
            self._draw_flow_dots(draw, home, bat, COLOR_WARNING, flow_strength(batt_w), gap=8)
            self._draw_flow_label(img, home, bat, batt_w, offset=15, outside_pad=32, along=0, color=COLOR_WARNING, outside="below")

        # SoC Ring um Batterie
        self._draw_soc_ring(draw, bat, soc)

        # Battery icon with SOC fill
        self._draw_battery_glyph(draw, bat, soc)

        # Hausverbrauch: Zahl dominant, Einheit sekundär
        load_val, load_unit = self._format_power_parts(load_w)
        self._draw_value_unit(
            draw,
            load_val,
            load_unit,
            home[0],
            home[1] + 28,
            value_size=self._node_value_size,
            unit_size=self._node_unit_size,
            value_color=COLOR_TEXT,
            unit_color=COLOR_SUBTEXT,
        )

        # SoC inside battery with outline for readability - moved down to avoid emoji overlap
        soc_color = COLOR_DANGER if soc < 20 else (COLOR_WARNING if soc < 35 else COLOR_TEXT)
        self._text_center(draw, f"{soc:.0f}%", bat[0], bat[1], size=22, color=soc_color, outline=True)
        return img

    def stop(self):
        """Cleanup resources to prevent memory leaks and segfaults."""
        try:
            if self._anim_job is not None:
                try:
                    self.after_cancel(self._anim_job)
                except Exception:
                    pass
                self._anim_job = None
            if hasattr(self, '_tk_img') and self._tk_img:
                self._tk_img = None  # Remove reference to PhotoImage
            if hasattr(self, 'canvas') and self.canvas:
                self.canvas.destroy()
        except Exception:
            pass
