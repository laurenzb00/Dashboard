import tkinter as tk
import tkinter.font as tkfont
from ttkbootstrap import Style

# Farbpalette gemäß Vorgabe
COLOR_ROOT = "#0B1320"       # Hintergrund/root
COLOR_HEADER = "#0B1320"     # Header/Notebook
COLOR_BG = COLOR_HEADER       # alias für bestehende Verwendungen
COLOR_CARD = "#121C2B"
COLOR_BORDER = "#1F2A3A"
COLOR_PRIMARY = "#3B82F6"
COLOR_SUCCESS = "#10B981"
COLOR_WARNING = "#F59E0B"
COLOR_INFO = "#38BDF8"
COLOR_DANGER = "#EF4444"
COLOR_TEXT = "#E6ECF5"
COLOR_SUBTEXT = "#9AA3B2"
COLOR_TITLE = "#AAB3C5"

# Emoji support flag (set in init_style)
EMOJI_OK = True

# Safe default fonts
_available_fonts = None

def get_available_fonts(root: tk.Misc = None) -> list:
    """Get list of available fonts, cached."""
    global _available_fonts
    if _available_fonts is not None:
        return _available_fonts
    try:
        if root:
            _available_fonts = set(tkfont.families(root))
        else:
            _available_fonts = set(tkfont.families())
    except Exception:
        _available_fonts = {"Arial", "TkDefaultFont"}
    return _available_fonts

def get_safe_font(family: str = "Arial", size: int = 10, style: str = "") -> tuple:
    """Returns a safe font tuple, falling back to system default if family not available."""
    available = get_available_fonts()
    
    # Check if requested font family exists
    if family not in available:
        # Try to find a fallback
        fallbacks = ["Arial", "Helvetica", "Courier", "TkDefaultFont", "Noto Sans", "DejaVu Sans"]
        family = next((f for f in fallbacks if f in available), "TkDefaultFont")
    
    if style:
        return (family, size, style)
    return (family, size)

def detect_emoji_support(root: tk.Misc) -> bool:
    """Checks if a known emoji font is available in Tk."""
    try:
        families = get_available_fonts(root)
        emoji_fonts = [
            "Segoe UI Emoji",
            "Noto Color Emoji",
            "Apple Color Emoji",
            "Noto Emoji",
            "Symbola",
        ]
        return any(name in families for name in emoji_fonts)
    except Exception:
        return False


def emoji(text: str, fallback: str = "") -> str:
    """Always return emoji text; keep fallback for optional use elsewhere."""
    return text


def configure_styles(style: Style) -> None:
    """Applies notebook + button styles on an existing ttkbootstrap Style."""
    # Notebook (Tabs)
    nb = "TNotebook"
    style.configure(nb, background=COLOR_HEADER, borderwidth=0, padding=0)
    style.configure(
        "TNotebook.Tab",
        background=COLOR_HEADER,
        foreground=COLOR_SUBTEXT,
        padding=[14, 10],
        borderwidth=0
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_PRIMARY)],
        foreground=[("selected", "#ffffff")]
    )

    # Toggle-like Buttons (touch-friendly height via padding)
    style.configure(
        "Card.TButton",
        background=COLOR_BORDER,
        foreground=COLOR_TEXT,
        borderwidth=0,
        padding=(14, 10),
    )
    style.map(
        "Card.TButton",
        background=[("active", COLOR_PRIMARY)],
        foreground=[("active", "#ffffff")]
    )


def init_style(root: tk.Tk) -> Style:
    """Initialisiert ttkbootstrap Styles, setzt Palette und ruft configure_styles."""
    style = Style(theme="darkly")
    root.configure(bg=COLOR_ROOT)
    global EMOJI_OK
    # Cache available fonts early
    get_available_fonts(root)
    EMOJI_OK = detect_emoji_support(root)
    configure_styles(style)
    return style
