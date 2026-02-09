import ttkbootstrap as ttk
import tkinter as tk

root = tk.Tk()
s = ttk.Style('darkly')

print('=== DARKLY THEME - FINDE HELLGRÜNE FARBE #00bc8c ===\n')

# Alle Widget-Typen prüfen
widgets = ['TButton', 'TLabel', 'TFrame', 'TScrollbar', 'TScale', 'TProgressbar', 
           'TCheckbutton', 'TRadiobutton', 'TEntry', 'TCombobox', 'TNotebook', 
           'TNotebook.Tab', 'Treeview', 'Treeview.Heading', 'TLabelframe', 
           'TLabelframe.Label', 'Horizontal.TScrollbar', 'Vertical.TScrollbar',
           'Horizontal.TScale', 'Vertical.TScale', 'Horizontal.TProgressbar',
           'success.TButton', 'info.TButton', 'primary.TButton']

for widget_name in widgets:
    try:
        cfg = s.configure(widget_name)
        if cfg:
            found_green = False
            for key, value in cfg.items():
                val_str = str(value).lower()
                if '#00bc8c' in val_str or '00bc8c' in val_str or (isinstance(value, (list, tuple)) and any('#00bc8c' in str(v).lower() for v in value)):
                    print(f'{widget_name}.{key} = {value}')
                    found_green = True
    except Exception as e:
        pass

print('\n=== STYLE MAPS (active, hover, etc.) ===\n')

# Prüfe auch style maps
for widget_name in ['TButton', 'TScrollbar', 'TScale', 'TProgressbar', 'TCheckbutton', 'TEntry']:
    try:
        for state in ['active', 'pressed', 'selected', 'focus', 'hover', 'disabled']:
            for option in ['background', 'foreground', 'troughcolor', 'bordercolor', 'selectcolor', 'indicatorcolor']:
                try:
                    value = s.map(widget_name, query_opt=option)
                    if value:
                        val_str = str(value).lower()
                        if '#00bc8c' in val_str or '00bc8c' in val_str:
                            print(f'{widget_name} map {option}: {value}')
                except:
                    pass
    except:
        pass

root.destroy()
