import logging
import threading
import queue
import os
import webbrowser
import time
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

class SpotifyTab:
    """Spotify Integration Tab - Minimalist Working Version"""
    
    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        
        self.tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_frame, text="Spotify")
        
        label = ttk.Label(
            self.tab_frame, 
            text="Spotify Integration Ready", 
            font=("Arial", 12)
        )
        label.pack(expand=True)
        
        # Start OAuth initialization in background
        threading.Thread(target=self._init, daemon=True).start()
    
    def _init(self):
        """Initialize Spotify OAuth in background"""
        try:
            import sys, os
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import spotifylogin
            logging.info("[SPOTIFY] OAuth init completed")
        except Exception as e:
            logging.error(f"[SPOTIFY] OAuth init failed: {e}")
    
    def stop(self):
        """Stop the Spotify tab"""
        self.alive = False
