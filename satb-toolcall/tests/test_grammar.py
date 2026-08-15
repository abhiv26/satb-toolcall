"""Grammar tests.

The ground truth is the site's own preset table (the minified `Ne` array in
js/partwriter.min.js), transcribed verbatim below. Each entry is the exact
internal token state the site builds when you click that example in, so a
round-trip through our parser must reproduce it.
"""

import pytest

from satb_toolcall.grammar import (
    AUG_SIXTH,
    ProgressionError,
    parse,
    parse_key,
)

# Verbatim from `Ne` in partwriter.min.js, with the site's own labels.
SITE_PRESETS = [
    ("V-I", [("numeral", "V"), ("space", "‒"), ("numeral", "I")]),
    ("V7-I", [("numeral", "V"), ("figures", "7"), ("space", "‒"), ("numeral", "I")]),
    ("I-IV-V-I", [
        ("numeral", "I"), ("space", "‒"), ("numeral", "IV"), ("space", "‒"),
        ("numeral", "V"), ("space", "‒"), ("numeral", "I")]),
    ("vi-ii-V-I", [
        ("numeral", "vi"), ("space", "‒"), ("numeral", "ii"), ("space", "‒"),
        ("numeral", "V"), ("space", "‒"), ("numeral", "I")]),
    ("I-ii65-V42-I6", [
        ("numeral", "I"), ("space", "‒"), ("numeral", "ii"), ("figures", "65"),
        ("space", "‒"), ("numeral", "V"), ("figures", "42"), ("space", "‒"),
        ("numeral", "I"), ("figures", "6")]),
    ("I-viio6-I6-IV-I64-V7-I", [
        ("numeral", "I"), ("space", "‒"), ("numeral", "vii"), ("quality-marker", "o"),
        ("figures", "6"), ("space", "‒"), ("numeral", "I"), ("figures", "6"),
        ("space", "‒"), ("numeral", "IV"), ("space", "‒"), ("numeral", "I"),
        ("figures", "64"), ("space", "‒"), ("numeral", "V"), ("figures", "7"),
        ("space", "‒"), ("numeral", "I")]),
    ("I-V42/IV-IV6-Fr6-I64-V-I", [
        ("numeral", "I"), ("space", "‒"), ("numeral", "V"), ("figures", "42"),
        ("separator", "/"), ("numeral", "IV"), ("space", "‒"), ("numeral", "IV"),
        ("figures", "6"), ("space", "‒"), ("numeral", "Fr"), ("figures", "6"),
        ("space", "‒"), ("numeral", "I"), ("figures", "64"), ("space", "‒"),
        ("numeral", "V"), ("space", "‒"), ("numeral", "I")]),
    ("i-ii042-V65-i", [
        ("numeral", "i"), ("space", "‒"), ("numeral", "ii"), ("quality-marker", "⌀"),
        ("figures", "42"), ("space", "‒"), ("numeral", "V"), ("figures", "65"),
        ("space", "‒"), ("numeral", "i")]),
]


def _expected_clicks(tokens):
    """Drop the figures '6' the site auto-appends after Fr/Gr/It — we must not click it."""
    clicks, skip = [], False
    for cls, val in tokens:
        if skip and (cls, val) == ("figures", "6"):
            skip = False
            continue
        skip = cls == "numeral" and val in AUG_SIXTH
        clicks.append((cls, val))
    return clicks


@pytest.mark.parametrize("text,tokens", SITE_PRESETS, ids=[p[0] for p in SITE_PRESETS])
def test_matches_site_preset(text, tokens):
    got = parse(text)
    assert got.clicks == _expected_clicks(tokens)
    # The textarea mirrors the full internal state, auto-added figures included.
    assert got.expected == "".join(v for _, v in tokens)


def test_separators_are_flexible():
    for text in ["I-V-I", "I - V - I", "I – V – I", "I‒V‒I", "I V I", "I, V, I"]:
        assert parse(text).expected == "I‒V‒I"


def test_aliases_normalise():
    assert parse("viio6").expected == "vii" + "o" + "6"
    assert parse("iiø65").expected == parse("ii065").expected == parse("ii⌀65").expected
    assert parse("bII6").expected == "♭II6"
    assert parse("#ivo7").expected == "♯ivo7"
    assert parse("Vaug").expected == "V+"


@pytest.mark.parametrize("written,canonical", [
    # Diminished and half-diminished as people actually type them.
    ("vii°6", "viio6"), ("viiº6", "viio6"), ("viidim6", "viio6"),
    ("ii∅65", "ii⌀65"), ("iiØ65", "ii⌀65"), ("iiø65", "ii⌀65"),
    ("ii065", "ii⌀65"), ("iihd65", "ii⌀65"),
    # Figured bass written stacked, with or without spaces around the slash.
    ("V6/5", "V65"), ("V4/3", "V43"), ("V4/2", "V42"), ("V6/4", "V64"),
    ("V7 / V", "V7/V"), ("vii°7/V", "viio7/V"), ("V4/2/IV", "V42/IV"),
    # Augmented sixths are names, not roman numerals, so case is free.
    ("fr6", "Fr6"), ("FR6", "Fr6"), ("iT", "It6"), ("gr", "Gr6"),
    # Major-major sevenths and augmented triads.
    ("Imaj7", "IM7"), ("IMM7", "IM7"), ("IIIaug", "III+"),
])
def test_accepts_common_notation_variants(written, canonical):
    assert parse(written).expected == parse(canonical).expected


@pytest.mark.parametrize("text", ["V 7", "vii o 6", "I - V 65 - I", "V7 - / V"])
def test_split_chord_names_the_join(text):
    """A chord broken across spaces should say how to fix it, not fail vaguely."""
    with pytest.raises(ProgressionError, match="not a chord on its own"):
        parse(text)


def test_case_of_roman_numerals_is_still_significant():
    """Widening the parser must not blur the major/minor distinction."""
    assert parse("V").expected == "V"
    assert parse("v").expected == "v"
    assert parse("VI").expected != parse("vi").expected


def test_applied_chords():
    assert parse("V7/V").clicks == [
        ("numeral", "V"), ("figures", "7"), ("separator", "/"), ("numeral", "V")]
    assert parse("viio7/ii").clicks[-2:] == [("separator", "/"), ("numeral", "ii")]


def test_augmented_sixths_do_not_click_the_auto_figure():
    for name in ("Fr", "Gr", "It"):
        p = parse(f"{name}6")
        assert p.clicks == [("numeral", name)]
        assert p.expected == f"{name}6"
        assert parse(name).clicks == p.clicks


@pytest.mark.parametrize("bad,reason", [
    ("Vo", "'o' requires a lowercase"),
    ("VII⌀7", "requires a lowercase"),
    ("ii+", "require an uppercase"),
    ("viM7", "require an uppercase"),
    ("ii⌀", "requires figures"),
    ("ii⌀6", "requires figures"),
    ("Fr65", "always a '6' chord"),
    ("Fro6", "takes no quality marker"),
    ("VIII", "Could not parse"),
    ("H7", "Could not parse"),
    ("", "Empty progression"),
])
def test_rejects_what_the_site_rejects(bad, reason):
    with pytest.raises(ProgressionError, match=reason):
        parse(bad)


@pytest.mark.parametrize("text,expected", [
    ("C", "C"), ("Eb", "Eb"), ("eb", "eb"), ("f#", "f#"),
    ("F# minor", "f#"), ("bb major", "Bb"), ("a", "a"), ("A", "A"),
    ("E♭ minor", "eb"), ("c minor", "c"), ("G maj", "G"),
])
def test_parse_key(text, expected):
    assert parse_key(text) == expected


@pytest.mark.parametrize("bad", ["H", "Fb", "g##", "Z minor"])
def test_parse_key_rejects(bad):
    with pytest.raises(ProgressionError):
        parse_key(bad)
