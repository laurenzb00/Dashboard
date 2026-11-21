"""Arbeit nur dann erledigen, wenn der Tab auch zu sehen ist.

Messung vom 16.11.2026 (perf_log.jsonl, 108,5 h, 1758 Haenger ueber einer
Sekunde): matplotlib-Zeichnen 923 s = 27 % der Haengezeit, Ertrag/PV 686 s,
Waerme 212 s. Der Loewenanteil davon entsteht an Tabs, die gerade niemand
ansieht - alle Tabs werden beim Start gebaut und laufen danach mit ihren
eigenen Zeitgebern weiter.

Das ist auch der Grund, warum sich das Umschalten zaeh anfuehlt, obwohl im
Protokoll nur 8 Haenger ueberhaupt einen Resize im Stapel haben: der
Tabwechsel selbst ist nicht teuer, aber er landet auf einem Thread, der nie
frei ist. Wird die Arbeit der verborgenen Tabs eingestellt, ist der Thread im
Moment des Umschaltens leer.

Die Gegenprobe: die Arbeit einfach auf den Moment des Sichtbarwerdens zu
verlegen, wuerde den Wechsel selbst blockieren - also genau das verschlimmern,
was besser werden soll. Darum `ANZEIGE_VERZUG_MS`: beim Sichtbarwerden wird
nachgeholt, aber erst nachdem Tk den Tab gezeichnet hat.
"""

from __future__ import annotations

# Kurz genug, dass es niemand als Verzoegerung wahrnimmt, lang genug, dass Tk
# den sichtbar gewordenen Tab vorher fertig zeichnet.
ANZEIGE_VERZUG_MS = 30


def ist_sichtbar(widget) -> bool:
    """Ob das Widget gerade auf dem Bildschirm liegt.

    Im Zweifel True: eine fehlgeschlagene Abfrage darf nie dazu fuehren, dass
    ein Tab stillsteht. Lieber einmal zu viel gezeichnet als ein Tab, der
    leer bleibt.
    """
    try:
        return bool(widget.winfo_ismapped())
    except Exception:
        return True


def eigenes_map_event(event, widget) -> bool:
    """Ob ein <Map>-Ereignis zu diesem Widget gehoert und nicht zu einem Kind.

    Tk liefert <Map> auch fuer jedes Kind-Widget. Ohne diese Pruefung loest
    ein einziger Tabwechsel das Nachholen dutzendfach aus.
    """
    if event is None:
        return True
    try:
        return event.widget is widget
    except Exception:
        return True


def darf_ueberspringen(besitzer, widget, merker: str = "_je_sichtbar_gewesen") -> bool:
    """Ob die Arbeit dieses Durchlaufs entfallen darf.

    Zwei Bedingungen, und die zweite ist die wichtige: der Tab muss verborgen
    sein UND schon mindestens einmal sichtbar gewesen sein.

    Grund: ob `<Map>` in einer bestimmten Widget-Verschachtelung wirklich beim
    Tab-Rahmen ankommt, laesst sich nicht garantieren. Kommt es nicht an und
    wuerde trotzdem uebersprungen, bliebe ein Tab fuer immer leer - genau das
    ist am 21.11.2026 im Ertrag- und Waerme-Tab passiert. Mit dieser Regel ist
    der schlimmste Fall, dass einmal beim Start unsichtbar gezeichnet wird;
    danach ist bewiesen, dass die Sichtbarkeitserkennung fuer dieses Widget
    funktioniert, und erst dann wird gespart.
    """
    if ist_sichtbar(widget):
        try:
            setattr(besitzer, merker, True)
        except Exception:
            pass
        return False
    return bool(getattr(besitzer, merker, False))


def naechster_takt(sichtbar: bool, aktiv_ms: int, ruhe_ms: int | None = None) -> int:
    """Wartezeit bis zum naechsten Durchlauf.

    Verborgen wird seltener nachgesehen - der Durchlauf selbst kostet dann ja
    nichts mehr, aber jeder Weckruf ist ein Tk-Zeitgeber. `ruhe_ms` kleiner
    als `aktiv_ms` waere unsinnig und wird auf `aktiv_ms` angehoben.
    """
    if sichtbar:
        return int(aktiv_ms)
    if ruhe_ms is None:
        return int(aktiv_ms)
    return int(max(aktiv_ms, ruhe_ms))
