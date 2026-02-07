import os
import sys
from pathlib import Path

# Ensure src on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Keep logs quiet
os.environ.pop("DASHBOARD_DEBUG", None)

import tkinter as tk
from tkinter import ttk

from core.datastore import DataStore
from tabs.historical import HistoricalTab
from ui.styles import COLOR_ROOT


def main() -> None:
    out = ROOT / "historie_preview.png"

    root = tk.Tk()
    root.withdraw()

    nb = ttk.Notebook(root)
    nb.pack_forget()

    store = DataStore()

    tab = HistoricalTab(root, nb, datastore=store)
    tab._period_var.set("24h")
    tab._update_plot()

    # Save figure to PNG
    tab.fig.savefig(out, dpi=140, facecolor=COLOR_ROOT, bbox_inches="tight")

    try:
        root.destroy()
    except Exception:
        pass

    print(str(out))


if __name__ == "__main__":
    main()
