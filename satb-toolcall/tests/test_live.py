"""Live tests against partwriter.com through a real browser.

Run with:  pytest -m live
Skipped by default because they need network access and a Chrome install.
"""

import re

import pytest

from satb_toolcall.driver import PartWriterError, PartWriterSession

pytestmark = pytest.mark.live

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_RE = re.compile(r"^([A-G])(b{1,2}|#{1,2})?(-?\d+)$")


def midi(spelling: str) -> int:
    """'Eb3' -> MIDI number. Scientific pitch notation, as the site emits."""
    match = NOTE_RE.match(spelling)
    assert match, f"unparseable note {spelling!r}"
    letter, accidental, octave = match.groups()
    shift = {"b": -1, "bb": -2, "#": 1, "##": 2}.get(accidental or "", 0)
    return (int(octave) + 1) * 12 + STEPS[letter] + shift


@pytest.fixture(scope="module")
async def session():
    s = PartWriterSession()
    await s.start()
    yield s
    await s.close()


async def test_v7_i_resolves_tendency_tones(session):
    """Every V7-I setting must resolve its tendency tones correctly.

    These are the site's own guarantees, so a failure here means our note
    extraction is misaligned with the notation, not that the site is wrong.
    V7 in C is G-B-D-F: the seventh (F) always falls to E, and the leading tone
    (B) rises to C — except in an inner voice, where it may instead drop to the
    fifth so the tonic triad comes out complete.
    """
    result = await session.solve("C", "V7-I", limit=10, want_images=False)
    assert result.total > 0
    assert result.solutions

    for solution in result.solutions:
        v7, tonic = solution.chords
        assert len(v7) == 4 and len(tonic) == 4
        for voice, (before, after) in enumerate(zip(v7, tonic)):
            where = f"setting {solution.index} voice {voice}: {before} -> {after}"
            if before.startswith("B"):
                if voice == 3:  # soprano: the leading tone must rise
                    assert after.startswith("C") and midi(after) - midi(before) == 1, where
                else:  # inner voice: rise to the tonic, or fall to the fifth
                    assert after.startswith("C") or after.startswith("G"), where
            if before.startswith("F"):
                assert after.startswith("E") and midi(before) - midi(after) == 1, where


async def test_no_parallel_perfects_by_default(session):
    """With consecutive perfects disallowed, no setting may contain any."""
    result = await session.solve("Eb", "I-ii65-V7-I", limit=6, want_images=False)
    assert result.solutions

    for solution in result.solutions:
        for chord_index in range(len(solution.chords) - 1):
            first, second = solution.chords[chord_index], solution.chords[chord_index + 1]
            for a in range(4):
                for b in range(a + 1, 4):
                    before = abs(midi(first[a]) - midi(first[b])) % 12
                    after = abs(midi(second[a]) - midi(second[b])) % 12
                    moved = midi(first[a]) != midi(second[a]) and midi(first[b]) != midi(second[b])
                    if before in (0, 7) and before == after and moved:
                        pytest.fail(
                            f"parallel {'octave/unison' if before == 0 else 'fifth'} in "
                            f"setting {solution.index} between voices {a},{b}: "
                            f"{first[a]}/{first[b]} -> {second[a]}/{second[b]}"
                        )


async def test_voices_are_ordered_and_in_range(session):
    """Voices come back bass -> soprano and sit in plausible SATB tessituras."""
    result = await session.solve("f", "i-ii042-V65-i", limit=5, want_images=False)
    assert result.solutions

    for solution in result.solutions:
        assert len(solution.chords) == 4
        for chord in solution.chords:
            pitches = [midi(n) for n in chord]
            assert pitches == sorted(pitches), f"not bass->soprano: {chord}"
            assert midi("E2") <= pitches[0] <= midi("E4"), f"bass out of range: {chord}"
            assert midi("C4") <= pitches[3] <= midi("A5"), f"soprano out of range: {chord}"


async def test_images_are_pngs(session):
    result = await session.solve("C", "I-IV-V-I", limit=3)
    assert len(result.solutions) == 3
    for solution in result.solutions:
        assert solution.png and solution.png.startswith(b"\x89PNG"), solution.index
        assert len(solution.png) > 5_000


async def test_french_augmented_sixth_spelling(session):
    """Fr6 in C must be spelled Ab-C-D-F#, and needs no explicit '6' click."""
    result = await session.solve("C", "I-Fr6-V-I", limit=3, want_images=False)
    assert result.chords[1] == "Fr6"
    for solution in result.solutions:
        letters = {re.match(r"[A-G][b#]*", n).group(0) for n in solution.chords[1]}
        assert letters == {"Ab", "C", "D", "F#"}, solution.chords[1]


async def test_total_exceeds_returned_and_is_reported(session):
    result = await session.solve("C", "I-V42/IV-IV6-Fr6-I64-V-I", limit=2, want_images=False)
    assert result.total > 2
    assert len(result.solutions) == 2
    assert all(s.chords for s in result.solutions)


async def test_distinct_solutions(session):
    """Pagination must not hand back the same realization twice."""
    result = await session.solve("C", "I-IV-V-I", limit=8, want_images=False)
    ids = [s.mei_id for s in result.solutions]
    assert len(ids) == len(set(ids)), ids
    voicings = [tuple(tuple(c) for c in s.chords) for s in result.solutions]
    assert len(voicings) == len(set(voicings)), "duplicate voicings returned"


async def test_rejected_progression_names_the_problem(session):
    with pytest.raises(Exception) as excinfo:
        await session.solve("C", "ii+")
    assert "uppercase" in str(excinfo.value)


async def test_session_recovers_from_a_dead_page(session):
    await session.solve("C", "V-I", limit=1, want_images=False)
    await session._page.close()  # simulate a crashed tab
    result = await session.solve("C", "V-I", limit=1, want_images=False)
    assert result.solutions, "session did not relaunch after the page died"


async def test_unsupported_key_is_rejected(session):
    with pytest.raises((PartWriterError, ValueError)):
        await session.solve("H", "I-V-I")
