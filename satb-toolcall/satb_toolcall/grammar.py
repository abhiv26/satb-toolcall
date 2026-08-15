"""Roman-numeral text -> partwriter.com button-click sequence.

partwriter.com has no API. Its solver reads an internal token array (`De`) that is
built up purely by button clicks; the on-page textarea is a read-only mirror of it.
An illegal click is silently dropped (it only flashes a transient alert), so a
mis-ordered sequence yields a *wrong but plausible* progression. This module
therefore reproduces the site's own grammar and validation rules up front, and
also computes the exact textarea string the clicks should produce so the driver
can verify entry afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FLAT = "♭"   # ♭
SHARP = "♯"  # ♯
HALF_DIM = "⌀"  # ⌀
NEXT_RN = "‒"  # ‒ (the site's separator glyph, figure dash)

UPPER = ["VII", "VI", "V", "IV", "III", "II", "I"]
LOWER = ["vii", "vi", "v", "iv", "iii", "ii", "i"]
AUG_SIXTH = ["Fr", "Gr", "It"]          # these auto-append figures "6"
FIGURES = ["64", "65", "43", "42", "6", "7"]  # two-digit first: longest match wins

_NUM_ALT = "|".join(AUG_SIXTH + ["N"] + UPPER + LOWER)

CHORD_RE = re.compile(
    r"^(?P<alt>[b#♭♯])?"
    r"(?P<num>" + _NUM_ALT + r")"
    # Longest alias first: "maj"/"MM" must win over "M", "dim" over "d".
    r"(?P<qual>dim|aug|maj|MM|hd|o|\+|ø|⌀|0|M)?"
    r"(?P<fig>" + "|".join(FIGURES) + r")?"
    r"(?:/(?P<salt>[b#♭♯])?(?P<snum>" + "|".join(UPPER + LOWER) + r"))?$"
)

_ALT_MAP = {"b": FLAT, "#": SHARP, FLAT: FLAT, SHARP: SHARP}
_QUAL_MAP = {
    "o": "o", "dim": "o",
    "+": "+", "aug": "+",
    "ø": HALF_DIM, "⌀": HALF_DIM, "0": HALF_DIM, "hd": HALF_DIM,
    "M": "M", "maj": "M", "MM": "M",
}

# Degree and diameter/empty-set signs people actually type for diminished and
# half-diminished qualities.
_SYMBOLS = str.maketrans({"°": "o", "º": "o", "∅": HALF_DIM, "Ø": HALF_DIM})
# " / " around an applied-chord slash, and figured-bass written as 6/5 or 4/3.
_SLASH_SPACING_RE = re.compile(r"\s*/\s*")
_STACKED_FIGURES_RE = re.compile(r"(?<=\d)/(?=\d)")
_AUG_SIXTH_RE = re.compile(r"^(fr|gr|it)(6?)$", re.IGNORECASE)
# Fragments that are clearly the tail of the previous chord rather than a chord.
_FRAGMENT_RE = re.compile(r"^(?:[0-9]+|[o+ø⌀]|/.*)$")


def _normalise(text: str) -> str:
    """Fold the common ways of writing the same chord into one spelling."""
    text = text.translate(_SYMBOLS)
    text = _SLASH_SPACING_RE.sub("/", text)  # "V7 / V" -> "V7/V"
    return _STACKED_FIGURES_RE.sub("", text)  # "V6/5" -> "V65"
# The site rejects ⌀ unless one of these figures follows.
HALF_DIM_FIGURES = {"7", "65", "43", "42"}

# Splits on any dash variant or whitespace.
_SPLIT_RE = re.compile(r"[\s,]*[-‐‑‒–—―]+[\s,]*|[\s,]+")


class ProgressionError(ValueError):
    """Input that the site's chord-entry grammar would reject."""


@dataclass
class Progression:
    clicks: list[tuple[str, str]] = field(default_factory=list)
    """(css_class, button value) pairs, in click order. `space` means the Next-RN key."""
    expected: str = ""
    """Exactly what the #progression textarea should read once all clicks land."""
    chords: list[str] = field(default_factory=list)
    """Per-chord display labels, e.g. ['I', 'vii°6', 'V7']."""


def _parse_chord(raw: str) -> tuple[list[tuple[str, str]], str]:
    """Return (clicks, textarea-text) for one chord."""
    # Fr/Gr/It are names rather than roman numerals, so their case is not
    # meaningful the way I/i is — accept any.
    aug_sixth = _AUG_SIXTH_RE.match(raw)
    if aug_sixth:
        raw = aug_sixth[1].capitalize() + aug_sixth[2]

    m = CHORD_RE.match(raw)
    if not m:
        raise ProgressionError(
            f"Could not parse chord {raw!r}. Expected something like "
            f"'I', 'V7', 'ii65', 'viio6', 'V42/IV', 'Fr6', 'bII6'."
        )
    alt, num = m["alt"], m["num"]
    qual = _QUAL_MAP[m["qual"]] if m["qual"] else None
    fig, salt, snum = m["fig"], m["salt"], m["snum"]

    if num in AUG_SIXTH:
        # The site pushes figures "6" itself and refuses any further entry.
        if qual:
            raise ProgressionError(f"{num} takes no quality marker (got {raw!r}).")
        if fig and fig != "6":
            raise ProgressionError(f"{num} is always a '6' chord; {raw!r} is not valid.")
        fig = None  # never click it — the site adds it
    else:
        if qual in ("o", HALF_DIM) and num not in LOWER:
            raise ProgressionError(
                f"'{qual}' requires a lowercase roman numeral; got {raw!r}. "
                f"Try '{num.lower()}{qual}{fig or ''}'."
            )
        if qual in ("+", "M") and num not in UPPER:
            kind = "Augmented triads" if qual == "+" else "MM7 chords"
            raise ProgressionError(
                f"{kind} require an uppercase roman numeral; got {raw!r}. "
                f"Try '{num.upper()}{qual}{fig or ''}'."
            )
        if qual == HALF_DIM and fig not in HALF_DIM_FIGURES:
            raise ProgressionError(
                f"A half-diminished chord requires figures 7, 65, 43 or 42; got {raw!r}."
            )

    clicks: list[tuple[str, str]] = []
    if alt:
        clicks.append(("root-alteration", _ALT_MAP[alt]))
    clicks.append(("numeral", num))
    if qual:
        clicks.append(("quality-marker", qual))
    if fig:
        clicks.append(("figures", fig))
    if snum:
        clicks.append(("separator", "/"))
        if salt:
            clicks.append(("root-alteration", _ALT_MAP[salt]))
        clicks.append(("numeral", snum))

    text = "".join(v for _, v in clicks)
    if num in AUG_SIXTH:
        text += "6"  # the auto-appended figure shows up in the textarea
    return clicks, text


def parse(progression: str) -> Progression:
    """Parse a roman-numeral progression into the site's click sequence.

    Accepts dash- or space-separated chords, e.g. ``"I - ii65 - V7 - I"`` or
    ``"i ii042 V65 i"``. Raises :class:`ProgressionError` on anything the site
    would reject.
    """
    parts = [p for p in _SPLIT_RE.split(_normalise(progression).strip()) if p]
    if not parts:
        raise ProgressionError("Empty progression.")

    out = Progression()
    for i, raw in enumerate(parts):
        if i:
            out.clicks.append(("space", NEXT_RN))
            out.expected += NEXT_RN
        if _FRAGMENT_RE.match(raw):
            # e.g. "V 7" or "vii o 6" — a chord is one unbroken token.
            previous = parts[i - 1] if i else "the numeral"
            raise ProgressionError(
                f"{raw!r} is not a chord on its own. Write each chord as a single "
                f"token with no spaces inside it — did you mean {previous + raw!r}?"
            )
        clicks, text = _parse_chord(raw)
        out.clicks.extend(clicks)
        out.expected += text
        out.chords.append(text)
    return out


# The site's #key <select> values. Case carries the mode: "C" major, "c" minor.
MAJOR_KEYS = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B", "Cb"]
MINOR_KEYS = ["c", "c#", "d", "d#", "eb", "e", "f", "f#", "g", "g#", "ab", "a", "a#", "bb", "b"]


def parse_key(key: str) -> str:
    """Normalize a key name to the exact value the site's #key <select> expects.

    Case decides mode ("Eb" major vs "eb" minor); a trailing word like "major",
    "minor", "maj" or "min" overrides it.
    """
    text = key.strip()
    mode = None
    m = re.match(r"^([A-Ga-g][#b♯♭]?)\s*[-\s]*(major|minor|maj|min|M|m)?$", text)
    if not m:
        raise ProgressionError(f"Unrecognised key {key!r}. Try 'Eb', 'f# minor', 'C'.")
    tonic, word = m[1], m[2]
    if word:
        mode = "minor" if word.lower().startswith("min") or word == "m" else "major"

    tonic = tonic.replace("♯", "#").replace("♭", "b")
    letter, accidental = tonic[0], tonic[1:]
    if mode is None:
        mode = "major" if letter.isupper() else "minor"

    candidate = (letter.upper() if mode == "major" else letter.lower()) + accidental
    valid = MAJOR_KEYS if mode == "major" else MINOR_KEYS
    if candidate not in valid:
        raise ProgressionError(
            f"{key!r} is not one of the site's supported {mode} keys: {', '.join(valid)}"
        )
    return candidate
