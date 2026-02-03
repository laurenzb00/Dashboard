import tkinter as tk
from tkinter import ttk
import os
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    COLOR_WARNING,
    emoji,
)
from ui.components.card import Card

class AnalyseTab:
    """Energie-Effizienz Analyse mit modernem Card-Layout."""
    
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        
        # Working directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_root = os.path.join(project_root, "data")
        self.fronius_csv = os.path.join(data_root, "FroniusDaten.csv")
        self.heating_csv = os.path.join(data_root, "Heizungstemperaturen.csv")
        
        # Tab Frame
        self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
        notebook.add(self.tab_frame, text=emoji("⚡ Analyse", "Analyse"))
        
        self.tab_frame.grid_columnconfigure(0, weight=1)
        self.tab_frame.grid_rowconfigure(1, weight=1)

        # Header
        header = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        
        ttk.Label(header, text="Energie-Effizienz Analyse", font=("Arial", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Aktualisieren", command=self._update_plot).pack(side=tk.RIGHT)

        # Main Card
        card = Card(self.tab_frame)
        card.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        card.add_title("PV vs. Speicherung (3 Tage)", icon="📊")
        
        # Plot
        self.fig, self.ax1 = plt.subplots(figsize=(7.6, 3.8), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax1.set_facecolor(COLOR_CARD)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=card.content())
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._update_plot()

    def stop(self):
        self.alive = False

    def _read_csv_data(self, path: str) -> pd.DataFrame:
        """Lese CSV Daten."""
        if not os.path.exists(path):
            return pd.DataFrame()
        try:
            df = pd.read_csv(path)
            df.columns = df.columns.str.strip()
            df["Zeitstempel"] = pd.to_datetime(df["Zeitstempel"])
            return df
        except Exception as e:
            print(f"CSV-Fehler {path}: {e}")
            return pd.DataFrame()

    def _style_axes(self):
        """Styling für Achsen."""
        self.ax1.set_facecolor(COLOR_CARD)
        for spine in ["top", "right"]:
            self.ax1.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            self.ax1.spines[spine].set_color(COLOR_BORDER)
            self.ax1.spines[spine].set_linewidth(1)
        self.ax1.tick_params(colors=COLOR_TEXT, which='both')

    def _update_plot(self):
        """Update Plot."""
        self.fig.clear()
        self.ax1 = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax1.set_facecolor(COLOR_CARD)
        self._style_axes()
        
        # Load data
        df_pv = self._read_csv_data(self.fronius_csv)
        df_heating = self._read_csv_data(self.heating_csv)
        
        if df_pv.empty or df_heating.empty:
            self.ax1.text(0.5, 0.5, "Zu wenig Daten", color=COLOR_SUBTEXT, ha="center", va="center", 
                         transform=self.ax1.transAxes, fontsize=12)
            self.canvas.draw()
            return

        # Filter last 3 days
        now = pd.Timestamp.now()
        start_date = now - pd.Timedelta(days=3)
        
        df_pv = df_pv[df_pv["Zeitstempel"] >= start_date]
        df_heating = df_heating[df_heating["Zeitstempel"] >= start_date]
        
        if df_pv.empty or df_heating.empty:
            self.ax1.text(0.5, 0.5, "Keine Daten für die letzten 3 Tage", color=COLOR_SUBTEXT, ha="center", 
                         va="center", transform=self.ax1.transAxes, fontsize=11)
            self.canvas.draw()
            return

        # Plot 1: PV Leistung (Linke Achse)
        self.ax1.set_ylabel("PV Leistung (kW)", color=COLOR_PRIMARY, fontsize=10)
        self.ax1.plot(df_pv["Zeitstempel"], df_pv["PV-Leistung (kW)"], color=COLOR_PRIMARY, 
                     label="PV-Leistung", linewidth=1.8)
        self.ax1.fill_between(df_pv["Zeitstempel"], df_pv["PV-Leistung (kW)"], 
                             color=COLOR_PRIMARY, alpha=0.1)
        self.ax1.tick_params(axis='y', labelcolor=COLOR_PRIMARY, labelsize=9)
        
        # Plot 2: Puffer Oben (Rechte Achse) 
        ax2 = self.ax1.twinx()
        ax2.set_ylabel("Puffer Oben (°C)", color=COLOR_WARNING, fontsize=10)
        ax2.plot(df_heating["Zeitstempel"], df_heating.get("Pufferspeicher Oben", 
                df_heating.get("Puffer_Top", df_heating.get("PufferTop", pd.Series([0])))),
                color=COLOR_WARNING, label="Puffer Oben", linewidth=1.6, linestyle="--")
        ax2.tick_params(axis='y', labelcolor=COLOR_WARNING, labelsize=9)
        
        # Styling
        self.ax1.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=8)
        self.ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d.%m"))
        self.ax1.grid(True, color=COLOR_BORDER, alpha=0.2, linewidth=0.8)
        
        self.fig.suptitle("Zusammenhang: Sonneneinstrahlung vs. Wärmespeicher", 
                         color=COLOR_TEXT, fontsize=11, fontweight='bold', y=0.98)
        
        self.fig.autofmt_xdate()
        self.canvas.draw_idle()
