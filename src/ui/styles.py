import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk
from ttkbootstrap import Style

# CustomTkinter Theme-Konfiguration
ctk.set_appearance_mode("dark")  # "dark" oder "light"
ctk.set_default_color_theme("blue")  # "blue", "green", "dark-blue"

# Farbpalette (angepasst für CustomTkinter)
COLOR_ROOT = "#0E0F12"       # Hintergrund/root (neutral dark)
COLOR_HEADER = "#0E0F12"     # Header/Notebook
COLOR_BG = COLOR_HEADER       # alias für bestehende Verwendungen
COLOR_CARD = "#171A20"       # Card/Plot-Hintergrund (neutral dark, leicht heller)
COLOR_BORDER = "#242833"
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
    # Base defaults
    # Viele Tabs verwenden ttk.Frame/ttk.Label ohne explizite bg/fg.
    # Wenn wir das nicht überschreiben, scheint der Theme-Hintergrund (bei "darkly" oft blau) überall durch.
    style.configure("TFrame", background=COLOR_ROOT)
    style.configure("TLabel", background=COLOR_ROOT, foreground=COLOR_TEXT)
    style.configure("TLabelframe", background=COLOR_ROOT)
    style.configure("TLabelframe.Label", background=COLOR_ROOT, foreground=COLOR_SUBTEXT)

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


def init_style(root) -> Style:
    """Initialisiert ttkbootstrap Styles (fallback für legacy widgets), konfiguriert CustomTkinter."""
    # CustomTkinter global theme wurde oben via set_appearance_mode/set_default_color_theme gesetzt
    # Hier nur ttkbootstrap für eventuelle ttk-Widgets (Backward-Compat)
    style = Style(theme="darkly")
    try:
        root.configure(bg=COLOR_ROOT)  # CTk root hat kein bg-Parameter, ignorieren wenn CTk
    except Exception:
        pass
    global EMOJI_OK
    # Cache available fonts early
    get_available_fonts(root)
    EMOJI_OK = detect_emoji_support(root)
    configure_styles(style)
    return style
