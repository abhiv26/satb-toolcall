"""Parse partwriter.com's MEI export into per-solution note spellings.

The site emits one ``<measure>`` per realization, with every note carrying an
``xml:id`` of the form ``m{measure}c{chord}v{voice}`` — voice 0 is the bass and
voice 3 the soprano, and ``oct`` is already scientific pitch notation. Verovio
reuses those same ids as SVG element ids, so a measure id joins a rendered
image to its exact pitches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

MEI_NS = "{http://www.music-encoding.org/ns/mei}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# From On.mr (printed) and On.pr (gestural) in partwriter.min.js. A note with
# neither attribute is natural.
ACCIDENTALS = {"ff": "bb", "f": "b", "n": "", "s": "#", "x": "##", "ss": "##"}

VOICE_NAMES = ["bass", "tenor", "alto", "soprano"]

_ID_RE = re.compile(r"^m(?P<measure>[^c]+)c(?P<chord>\d+)v(?P<voice>\d+)")

REST = "rest"


@dataclass
class Solution:
    """One complete SATB realization of the progression."""

    index: int
    """1-based position in the order the site presents them."""
    mei_id: str
    """The MEI/SVG ``xml:id`` of this realization's measure, e.g. ``"m3"``."""
    opening: int
    """Which starting voicing group this realization belongs to (1-based)."""
    chords: list[list[str]] = field(default_factory=list)
    """Per chord, the four voices bass -> soprano, e.g. ``["Eb3","Bb3","G4","Eb5"]``."""
    png: bytes | None = None


def _spell(element: ElementTree.Element) -> str:
    pname = (element.get("pname") or "?").upper()
    accid = element.get("accid") or element.get("accid.ges") or ""
    return f"{pname}{ACCIDENTALS.get(accid, '')}{element.get('oct') or ''}"


def notes_by_measure(mei: str) -> dict[str, list[list[str]]]:
    """Map each measure ``xml:id`` to its chords, each bass -> soprano.

    Returns an empty dict rather than raising if the export is unusable — the
    caller still has images to fall back on.
    """
    try:
        root = ElementTree.fromstring(mei)
    except ElementTree.ParseError:
        return {}

    out: dict[str, list[list[str]]] = {}
    for measure in root.iter(f"{MEI_NS}measure"):
        measure_id = measure.get(XML_ID)
        if not measure_id:
            continue
        # {chord index: {voice index: spelling}} — the MEI interleaves voices
        # across staves and layers, so collect by id and sort afterwards.
        grid: dict[int, dict[int, str]] = {}
        for tag, render in ((f"{MEI_NS}note", _spell), (f"{MEI_NS}rest", lambda _: REST)):
            for element in measure.iter(tag):
                match = _ID_RE.match(element.get(XML_ID) or "")
                if not match:
                    continue
                grid.setdefault(int(match["chord"]), {})[int(match["voice"])] = render(element)
        if grid:
            out[measure_id] = [
                [voices.get(v, REST) for v in range(len(VOICE_NAMES))]
                for _, voices in sorted(grid.items())
            ]
    return out


def format_solution(solution: Solution, chord_labels: list[str]) -> str:
    """Render one solution as aligned ``RN (bass tenor alto soprano)`` lines."""
    if not solution.chords:
        return f"--- Setting {solution.index} --- (note data unavailable)"

    label_width = max((len(c) for c in chord_labels), default=0)
    note_width = max(
        (len(n) for chord in solution.chords for n in chord),
        default=0,
    )
    lines = [f"--- Setting {solution.index} ---"]
    for i, chord in enumerate(solution.chords):
        label = chord_labels[i] if i < len(chord_labels) else "?"
        notes = " ".join(n.ljust(note_width) for n in chord).rstrip()
        lines.append(f"{label.ljust(label_width)}  ({notes})")
    return "\n".join(lines)
