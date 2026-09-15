"""Fine line-art glyph icons for buttons/labels that previously used emoji.

Same "glass" drawing language as the energy-flow node icons (see
resources/icons/pv.png, house.png, grid.png and ui/views/buffer_storage.py's
tank rendering): thin supersampled strokes downsampled with LANCZOS for
crisp edges, plus an optional faint color-matched glow. Icons are drawn
procedurally instead of shipped as static PNG assets so they can be
recolored/resized at call time (e.g. the "Weg" button swapping between its
normal and active tint) without needing extra asset files on disk.
"""
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _soft_glow(img: Image.Image, S: int, color: tuple[int, int, int], blur_frac: float, strength: int) -> Image.Image:
    a = img.split()[3]
    tinted = Image.new("RGBA", (S, S), (*color, 0))
    tinted.putalpha(a.point(lambda v: int(v * (strength / 255))))
    return tinted.filter(ImageFilter.GaussianBlur(S * blur_frac))


def render_glyph(draw_fn, color_hex: str, size: int = 40, scale: int = 10, glow: float = 0.3) -> Image.Image:
    """Render one glyph as a size x size RGBA PIL image with transparent background."""
    S = size * scale
    col = _hex_rgb(color_hex)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(2, S // 60)
    draw_fn(d, S, col, w)
    if glow > 0:
        halo = _soft_glow(img, S, col, blur_frac=0.05, strength=int(90 * glow))
        img = Image.alpha_composite(halo, img)
    return img.resize((size, size), Image.LANCZOS)


# --- individual glyph drawers -------------------------------------------------
# Each draws into a supersampled SxS canvas; d=ImageDraw, col=RGB tuple,
# w=base stroke width already scaled for S.

def draw_door_exit(d, S, col, w):
    fill = (*col, 255)
    # Door frame (open, slightly ajar for a "leaving" feel), with the exit
    # arrow kept clear of the handle so both read distinctly at small size.
    d.line([(S * 0.20, S * 0.14), (S * 0.20, S * 0.86)], fill=fill, width=w)
    d.line([(S * 0.20, S * 0.14), (S * 0.46, S * 0.20)], fill=fill, width=w)
    d.line([(S * 0.46, S * 0.20), (S * 0.46, S * 0.80)], fill=fill, width=w)
    d.line([(S * 0.20, S * 0.86), (S * 0.46, S * 0.80)], fill=fill, width=w)
    d.ellipse([S * 0.38, S * 0.48, S * 0.42, S * 0.52], fill=fill)
    d.line([(S * 0.58, S * 0.50), (S * 0.82, S * 0.50)], fill=fill, width=w)
    d.line([(S * 0.70, S * 0.38), (S * 0.82, S * 0.50), (S * 0.70, S * 0.62)], fill=fill, width=w, joint="curve")


def draw_house(d, S, col, w):
    fill = (*col, 255)
    d.line([(S * 0.16, S * 0.46), (S * 0.50, S * 0.20), (S * 0.84, S * 0.46)], fill=fill, width=w, joint="curve")
    d.line([(S * 0.24, S * 0.42), (S * 0.24, S * 0.80), (S * 0.76, S * 0.80), (S * 0.76, S * 0.42)],
           fill=fill, width=w, joint="curve")
    d.rounded_rectangle([S * 0.44, S * 0.58, S * 0.56, S * 0.80], radius=S * 0.015, outline=fill, width=max(1, w - 1))
    win = [S * 0.30, S * 0.50, S * 0.40, S * 0.60]
    d.rectangle(win, outline=fill, width=max(1, w - 1))
    d.rectangle([win[0] + S * 0.02, win[1] + S * 0.02, win[2] - S * 0.02, win[3] - S * 0.02], fill=(*col, 55))


def draw_shower(d, S, col, w):
    fill = (*col, 255)
    tw = max(1, S // 110)
    d.line([(S * 0.14, S * 0.16), (S * 0.30, S * 0.16)], fill=fill, width=w)
    d.line([(S * 0.30, S * 0.16), (S * 0.44, S * 0.30)], fill=fill, width=w, joint="curve")
    head = [S * 0.30, S * 0.28, S * 0.72, S * 0.42]
    d.rounded_rectangle(head, radius=S * 0.06, outline=fill, width=w)
    face_y = head[3]
    for t in (0.18, 0.38, 0.58, 0.78):
        sx = head[0] + (head[2] - head[0]) * t
        d.line([(sx, face_y), (sx - S * 0.02, face_y + S * 0.18)], fill=fill, width=tw + 1)
    for dx, dy, r in [(-0.02, 0.30, 0.024), (0.18, 0.28, 0.020)]:
        cx = head[0] + (head[2] - head[0]) * 0.5 + S * dx
        cy = face_y + S * dy
        d.ellipse([cx - S * r, cy - S * r * 1.3, cx + S * r, cy + S * r * 1.3], outline=fill, width=tw)


def draw_bulb(d, S, col, w):
    fill = (*col, 255)
    cx, cy, r = S * 0.5, S * 0.36, S * 0.19
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=w)
    d.line([(cx - r * 0.35, cy - r * 0.05), (cx + r * 0.05, cy + r * 0.30), (cx + r * 0.35, cy - r * 0.05)],
           fill=(*col, 200), width=max(1, w - 1), joint="curve")
    neck_top = cy + r * 0.92
    neck_bot = cy + r * 1.35
    d.line([(cx - r * 0.32, neck_top), (cx - r * 0.28, neck_bot)], fill=fill, width=w)
    d.line([(cx + r * 0.32, neck_top), (cx + r * 0.28, neck_bot)], fill=fill, width=w)
    for t in (0.0, 0.35, 0.7, 1.0):
        yy = neck_top + (neck_bot - neck_top) * t
        span = r * 0.32 - (r * 0.04) * t
        d.line([(cx - span, yy), (cx + span, yy)], fill=(*col, 210), width=max(1, w - 1))


def draw_thermometer(d, S, col, w):
    fill = (*col, 255)
    x0, y0 = S * 0.44, S * 0.14
    x1 = S * 0.56
    bulb_cy = S * 0.78
    bulb_r = S * 0.13
    d.line([(x0, y0), (x0, bulb_cy - bulb_r * 0.3)], fill=fill, width=w)
    d.line([(x1, y0), (x1, bulb_cy - bulb_r * 0.3)], fill=fill, width=w)
    d.arc([x0, y0 - (x1 - x0) / 2, x1, y0 + (x1 - x0) / 2], start=180, end=360, fill=fill, width=w)
    d.ellipse([S * 0.5 - bulb_r, bulb_cy - bulb_r, S * 0.5 + bulb_r, bulb_cy + bulb_r], outline=fill, width=w)
    inner_r = bulb_r * 0.55
    d.ellipse([S * 0.5 - inner_r, bulb_cy - inner_r, S * 0.5 + inner_r, bulb_cy + inner_r], fill=(*col, 220))
    d.line([(S * 0.5, y0 + S * 0.06), (S * 0.5, bulb_cy - bulb_r * 0.5)], fill=(*col, 220), width=max(2, w - 1))


def draw_play(d, S, col, w):
    fill = (*col, 255)
    pts = [(S * 0.34, S * 0.20), (S * 0.34, S * 0.80), (S * 0.80, S * 0.50)]
    d.polygon(pts, outline=fill, width=w)
    d.line(pts + [pts[0]], fill=fill, width=w, joint="curve")


def draw_pause(d, S, col, w):
    fill = (*col, 255)
    bar_w = S * 0.14
    d.rounded_rectangle([S * 0.28, S * 0.20, S * 0.28 + bar_w, S * 0.80], radius=S * 0.03, fill=fill)
    d.rounded_rectangle([S * 0.58, S * 0.20, S * 0.58 + bar_w, S * 0.80], radius=S * 0.03, fill=fill)


def draw_skip_prev(d, S, col, w):
    fill = (*col, 255)
    bar_w = S * 0.09
    d.rounded_rectangle([S * 0.22, S * 0.22, S * 0.22 + bar_w, S * 0.78], radius=S * 0.02, fill=fill)
    pts = [(S * 0.76, S * 0.22), (S * 0.76, S * 0.78), (S * 0.34, S * 0.50)]
    d.polygon(pts, fill=fill)


def draw_skip_next(d, S, col, w):
    fill = (*col, 255)
    bar_w = S * 0.09
    d.rounded_rectangle([S * 0.78 - bar_w, S * 0.22, S * 0.78, S * 0.78], radius=S * 0.02, fill=fill)
    pts = [(S * 0.24, S * 0.22), (S * 0.24, S * 0.78), (S * 0.66, S * 0.50)]
    d.polygon(pts, fill=fill)


_GLYPHS = {
    "door_exit": draw_door_exit,
    "house": draw_house,
    "shower": draw_shower,
    "bulb": draw_bulb,
    "thermometer": draw_thermometer,
    "play": draw_play,
    "pause": draw_pause,
    "skip_prev": draw_skip_prev,
    "skip_next": draw_skip_next,
}

_icon_cache: dict = {}


def ctk_icon(name: str, color_hex: str, size: int = 40, glow: float = 0.3) -> "ctk.CTkImage":
    """Return a cached CTkImage for one of the glyphs in _GLYPHS.

    Cached module-globally by (name, color, size, glow) so repeated calls
    (e.g. re-applying the same header state) don't re-render the glyph, and
    so the CTkImage stays referenced (avoiding Tk PhotoImage garbage
    collection) for the lifetime of the process.
    """
    key = (name, color_hex, size, glow)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    draw_fn = _GLYPHS[name]
    pil_img = render_glyph(draw_fn, color_hex, size=size, glow=glow)
    image = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(size, size))
    _icon_cache[key] = image
    return image
