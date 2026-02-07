import os
import sys

def get_phue_conf_path():
    # Windows: C:\Users\<user>\phue.conf
    # Linux: /home/<user>/phue.conf
    home = os.path.expanduser("~")
    conf = os.path.join(home, "phue.conf")
    return conf

def main():
    conf = get_phue_conf_path()
    if os.path.exists(conf):
        os.remove(conf)
        print(f"[HUE] Cache-Datei {conf} wurde gelöscht.")
    else:
        print(f"[HUE] Keine phue.conf im Home-Verzeichnis gefunden.")

if __name__ == "__main__":
    main()
