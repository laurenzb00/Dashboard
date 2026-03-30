"""Wrapper für CTkTabview, der die alte ttk.Notebook API emuliert."""

from __future__ import annotations

import tkinter as tk
import customtkinter as ctk


class TabviewWrapper:
    """Wrapper für CTkTabview, der die alte ttk.Notebook API emuliert.
    
    This provides backward compatibility with code that was written for
    ttk.Notebook but now uses CTkTabview.
    
    Attributes:
        _tabview: The underlying CTkTabview widget.
        _tabs: Dictionary mapping tab names to their frame widgets.
    """
    
    def __init__(self, tabview: ctk.CTkTabview) -> None:
        """Initialize the wrapper.
        
        Args:
            tabview: The CTkTabview widget to wrap.
        """
        self._tabview = tabview
        self._tabs: dict[str, tk.Frame] = {}
    
    @property
    def tk(self):
        """Tkinter root für Kompatibilität mit Tabs.
        
        Returns:
            The Tk instance from the root window.
        """
        return self._tabview.winfo_toplevel().tk
    
    def add(self, frame: tk.Frame, text: str = "") -> str:
        """Emuliert notebook.add(frame, text='...').
        
        Creates a tab in CTkTabview and packs the frame content into it.
        
        Args:
            frame: The frame widget to add as tab content.
            text: The tab label text.
            
        Returns:
            The tab name/text.
        """
        self._tabview.add(text)
        tab_frame = self._tabview.tab(text)
        frame.pack(in_=tab_frame, fill=tk.BOTH, expand=True)
        self._tabs[text] = frame
        return text
    
    def tabs(self) -> list[str]:
        """Gibt Liste aller Tab-Namen zurück.
        
        Returns:
            List of tab names.
        """
        return list(self._tabs.keys())
    
    def forget(self, tab_id: str) -> None:
        """Entfernt einen Tab (für CTkTabview nicht implementiert).
        
        Note: CTkTabview does not support dynamic tab removal,
        so this is a no-op.
        
        Args:
            tab_id: The tab identifier to remove.
        """
        pass
    
    def winfo_height(self) -> int:
        """Get the height of the wrapped tabview.
        
        Returns:
            Height in pixels.
        """
        return self._tabview.winfo_height()
    
    def winfo_width(self) -> int:
        """Get the width of the wrapped tabview.
        
        Returns:
            Width in pixels.
        """
        return self._tabview.winfo_width()
