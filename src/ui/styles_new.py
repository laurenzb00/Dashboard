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
COLOR_CARD = "#0E0F12"       # Card/Plot-Hintergrund - einheitlich mit Root
COLOR_BORDER = "#0E0F12"     # Auch dunkel für einheitliches Erscheinungsbild
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
    """Applies notebook + button styles on an existing ttkbootstrap Style.
    
    WICHTIG: Überschreibt die hellgrüne success-Farbe #00bc8c aus dem darkly theme!
    """
    # Base defaults - überschreibe ALLE ttkbootstrap darkly theme Farben
    style.configure("TFrame", background=COLOR_ROOT)
    style.configure("TLabel", background=COLOR_ROOT, foreground=COLOR_TEXT)
    style.configure("TLabelframe", background=COLOR_ROOT, bordercolor=COLOR_BORDER)
    style.configure("TLabelframe.Label", background=COLOR_ROOT, foreground=COLOR_SUBTEXT)
    
    # Scrollbar - entferne hellgrüne Theme-Farben
    style.configure("TScrollbar", background=COLOR_BORDER, troughcolor=COLOR_ROOT, bordercolor=COLOR_ROOT, arrowcolor=COLOR_TEXT)
    style.map("TScrollbar", 
        background=[("active", COLOR_PRIMARY), ("!active", COLOR_BORDER)],
        arrowcolor=[("active", COLOR_TEXT), ("!active", COLOR_SUBTEXT)]
    )
    
    # Button - entferne hellgrüne Theme-Farben
    style.configure("TButton", background=COLOR_BORDER, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER, borderwidth=0)
    style.map("TButton",
        background=[("active", COLOR_PRIMARY), ("pressed", COLOR_PRIMARY), ("!active", COLOR_BORDER)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("!active", COLOR_TEXT)]
    )
    
    # Entry - entferne hellgrüne Theme-Farben
    style.configure("TEntry", fieldbackground=COLOR_CARD, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER)
    style.map("TEntry",
        fieldbackground=[("focus", COLOR_CARD), ("!focus", COLOR_CARD)],
        bordercolor=[("focus", COLOR_PRIMARY), ("!focus", COLOR_BORDER)]
    )
    
    # Checkbutton - entferne hellgrüne Theme-Farben
    style.configure("TCheckbutton", background=COLOR_ROOT, foreground=COLOR_TEXT)
    style.map("TCheckbutton",
        background=[("active", COLOR_ROOT), ("!active", COLOR_ROOT)],
        indicatorcolor=[("selected", COLOR_PRIMARY), ("!selected", COLOR_BORDER)]
    )
    
    # Radiobutton - entferne hellgrüne Theme-Farben
    style.configure("TRadiobutton", background=COLOR_ROOT, foreground=COLOR_TEXT)
    style.map("TRadiobutton",
        background=[("active", COLOR_ROOT), ("!active", COLOR_ROOT)],
        indicatorcolor=[("selected", COLOR_PRIMARY), ("!selected", COLOR_BORDER)]
    )
    
    # Scale - entferne hellgrüne Theme-Farben
    style.configure("TScale", background=COLOR_ROOT, troughcolor=COLOR_BORDER, bordercolor=COLOR_BORDER)
    style.map("TScale",
        background=[("active", COLOR_PRIMARY), ("!active", COLOR_PRIMARY)],
        troughcolor=[("active", COLOR_CARD), ("!active", COLOR_CARD)]
    )
    
    # Progressbar - entferne hellgrüne Theme-Farben
    style.configure("TProgressbar", background=COLOR_PRIMARY, troughcolor=COLOR_CARD, bordercolor=COLOR_BORDER)
    
    # === ttkbootstrap bootstyle-Varianten - überschreibe #00bc8c (darkly theme success color) ===
    
    # Secondary Varianten
    style.configure("secondary.TButton", background=COLOR_BORDER, foreground=COLOR_TEXT, 
                    lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER, bordercolor=COLOR_BORDER)
    style.map("secondary.TButton",
        background=[("active", COLOR_SUBTEXT), ("pressed", COLOR_SUBTEXT), ("!active", COLOR_BORDER)],
        foreground=[("active", COLOR_ROOT), ("!active", COLOR_TEXT)]
    )
    
    # Success Varianten - KRITISCH: Diese verwenden #00bc8c im darkly theme!
    style.configure("success.TButton", background=COLOR_SUCCESS, foreground="#ffffff",
                    lightcolor=COLOR_SUCCESS, darkcolor=COLOR_SUCCESS, bordercolor=COLOR_SUCCESS)
    style.map("success.TButton",
        background=[("active", COLOR_SUCCESS), ("pressed", COLOR_SUCCESS), ("!active", COLOR_SUCCESS)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")]
    )
    
    style.configure("success-outline.TButton", background=COLOR_ROOT, foreground=COLOR_SUCCESS, 
                    bordercolor=COLOR_SUCCESS, lightcolor=COLOR_ROOT, darkcolor=COLOR_ROOT)
    style.map("success-outline.TButton",
        background=[("active", COLOR_SUCCESS), ("pressed", COLOR_SUCCESS), ("!active", COLOR_ROOT)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("!active", COLOR_SUCCESS)]
    )
    
    style.configure("outline-success.TButton", background=COLOR_ROOT, foreground=COLOR_SUCCESS, 
                    bordercolor=COLOR_SUCCESS, lightcolor=COLOR_ROOT, darkcolor=COLOR_ROOT)
    style.map("outline-success.TButton",
        background=[("active", COLOR_SUCCESS), ("pressed", COLOR_SUCCESS), ("!active", COLOR_ROOT)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("!active", COLOR_SUCCESS)]
    )
    
    # Alle anderen success-Widgets (gefunden via debug_theme_complete.py)
    style.configure("success.TLabel", foreground=COLOR_SUCCESS, background=COLOR_ROOT)
    style.configure("success.TFrame", background=COLOR_SUCCESS)
    style.configure("success.TEntry", bordercolor=COLOR_SUCCESS, fieldbackground=COLOR_CARD, foreground=COLOR_TEXT)
    style.configure("success.TCombobox", bordercolor=COLOR_SUCCESS, fieldbackground=COLOR_CARD, foreground=COLOR_TEXT)
    style.configure("success.Treeview", bordercolor=COLOR_SUCCESS, background=COLOR_CARD, foreground=COLOR_TEXT)
    style.configure("success.Horizontal.TScrollbar", arrowcolor=COLOR_SUCCESS, background=COLOR_SUCCESS, troughcolor=COLOR_CARD)
    style.configure("success.Vertical.TScrollbar", arrowcolor=COLOR_SUCCESS, background=COLOR_SUCCESS, troughcolor=COLOR_CARD)
    style.configure("success.TLabelframe", bordercolor=COLOR_SUCCESS, background=COLOR_ROOT)
    style.configure("success.TLabelframe.Label", foreground=COLOR_SUCCESS, background=COLOR_ROOT)
    
    # Info Varianten
    style.configure("info.TButton", background=COLOR_INFO, foreground="#ffffff",
                    lightcolor=COLOR_INFO, darkcolor=COLOR_INFO, bordercolor=COLOR_INFO)
    style.configure("info-outline.TButton", background=COLOR_ROOT, foreground=COLOR_INFO, 
                    bordercolor=COLOR_INFO, lightcolor=COLOR_ROOT, darkcolor=COLOR_ROOT)
    style.map("info.TButton",
        background=[("active", COLOR_INFO), ("pressed", COLOR_INFO)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")]
    )
    style.map("info-outline.TButton",
        background=[("active", COLOR_INFO), ("pressed", COLOR_INFO), ("!active", COLOR_ROOT)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("!active", COLOR_INFO)]
    )
    
    # Warning Varianten
    style.configure("warning.TButton", background=COLOR_WARNING, foreground="#ffffff",
                    lightcolor=COLOR_WARNING, darkcolor=COLOR_WARNING, bordercolor=COLOR_WARNING)
    style.map("warning.TButton",
        background=[("active", COLOR_WARNING), ("pressed", COLOR_WARNING)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")]
    )
    
    # Danger Varianten
    style.configure("danger.TButton", background=COLOR_DANGER, foreground="#ffffff",
                    lightcolor=COLOR_DANGER, darkcolor=COLOR_DANGER, bordercolor=COLOR_DANGER)
    style.map("danger.TButton",
        background=[("active", COLOR_DANGER), ("pressed", COLOR_DANGER)],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")]
    )
    
    # Outline Varianten
    style.configure("secondary-outline.TButton", background=COLOR_ROOT, foreground=COLOR_SUBTEXT, 
                    bordercolor=COLOR_SUBTEXT, lightcolor=COLOR_ROOT, darkcolor=COLOR_ROOT)
    style.map("secondary-outline.TButton",
        background=[("active", COLOR_SUBTEXT), ("pressed", COLOR_SUBTEXT), ("!active", COLOR_ROOT)],
        foreground=[("active", COLOR_ROOT), ("pressed", COLOR_ROOT), ("!active", COLOR_SUBTEXT)]
    )
    
    style.configure("outline-secondary.TButton", background=COLOR_ROOT, foreground=COLOR_SUBTEXT, 
                    bordercolor=COLOR_SUBTEXT, lightcolor=COLOR_ROOT, darkcolor=COLOR_ROOT)
    style.map("outline-secondary.TButton",
        background=[("active", COLOR_SUBTEXT), ("pressed", COLOR_SUBTEXT), ("!active", COLOR_ROOT)],
        foreground=[("active", COLOR_ROOT), ("pressed", COLOR_ROOT), ("!active", COLOR_SUBTEXT)]
    )

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
