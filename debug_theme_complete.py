import ttkbootstrap as ttk
import tkinter as tk

root = tk.Tk()
root.withdraw()
s = ttk.Style('darkly')

print('=== VOLLSTÄNDIGE ANALYSE: Wo kommt #00bc8c vor? ===\n')

# Alle möglichen Style-Namen sammeln
all_styles = []
base_widgets = ['TButton', 'TLabel', 'TFrame', 'TScrollbar', 'TScale', 'TProgressbar', 
                'TCheckbutton', 'TRadiobutton', 'TEntry', 'TCombobox', 'TNotebook', 
                'TNotebook.Tab', 'Treeview', 'Horizontal.TScrollbar', 'Vertical.TScrollbar',
                'TLabelframe', 'TLabelframe.Label']

prefixes = ['', 'primary.', 'secondary.', 'success.', 'info.', 'warning.', 'danger.',
            'outline-primary.', 'outline-secondary.', 'outline-success.', 'outline-info.', 
            'outline-warning.', 'outline-danger.', 'primary-outline.', 'secondary-outline.',
            'success-outline.', 'info-outline.', 'warning-outline.', 'danger-outline.']

for prefix in prefixes:
    for widget in base_widgets:
        all_styles.append(prefix + widget)

print(f'Prüfe {len(all_styles)} Style-Varianten...\n')

found_count = 0
for style_name in all_styles:
    try:
        cfg = s.configure(style_name)
        if not cfg:
            continue
        
        green_props = {}
        for key, value in cfg.items():
            val_str = str(value).lower()
            if '#00bc8c' in val_str or '00bc8c' in val_str:
                green_props[key] = value
            elif isinstance(value, (list, tuple)):
                for v in value:
                    if '#00bc8c' in str(v).lower():
                        green_props[key] = value
                        break
        
        if green_props:
            print(f'\n{style_name}:')
            for key, value in green_props.items():
                print(f'  {key} = {value}')
            found_count += 1
    except:
        pass

print(f'\n=== Gefunden: {found_count} Styles mit #00bc8c ===')

# Prüfe auch Theme-Farben direkt
print('\n=== Theme-Farben direkt ===')
try:
    colors = s.colors
    for attr_name in dir(colors):
        if not attr_name.startswith('_'):
            try:
                val = getattr(colors, attr_name)
                if isinstance(val, str) and '00bc8c' in val.lower():
                    print(f'colors.{attr_name} = {val}')
            except:
                pass
except:
    pass

root.destroy()
