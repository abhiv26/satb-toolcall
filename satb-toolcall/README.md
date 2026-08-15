# satb-toolcall

An MCP tool that lets an LLM use [partwriter.com](https://partwriter.com/)'s automatic
SATB part-writer. Ask for a progression in plain language and get back engraved
notation for each realization plus the exact note spellings and octaves.

```
You:    Part-write I – ii65 – V7 – I in E♭ major
Claude: [calls part_write]

        I‒ii65‒V7‒I in Eb (major)
        6 total settings, showing 3 — grouped into 4 opening voicing(s).

        [engraved staff image]
        --- Setting 1 ---
        I     (Eb3 Bb3 Eb4 G4)
        ii65  (Ab2 C4  Eb4 F4)
        V7    (Bb2 Bb3 D4  Ab4)
        I     (Eb3 Bb3 Eb4 G4)
        ...
```

## Setup

Requires Python 3.10+ and Google Chrome.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium   # optional, see note below
```

Register it with Claude Code:

```bash
claude mcp add satb -- /absolute/path/to/.venv/bin/python -m satb_toolcall
```

Or, for Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "satb": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "satb_toolcall"]
    }
  }
}
```

> **Browser note.** The driver prefers your installed Google Chrome
> (`channel="chrome"`) and falls back to Playwright's bundled Chromium. On
> macOS 12 the `playwright install chromium` step fails — Playwright ships no
> Chromium build for `mac12-arm64` — but the tool works fine on system Chrome,
> so that step is optional.

## The `part_write` tool

| argument | default | meaning |
|---|---|---|
| `key` | — | `"C"`, `"Eb"`, `"f# minor"`. Case sets the mode unless you spell it out. |
| `progression` | — | Dash- or space-separated roman numerals. |
| `limit` | `6` | Realizations to return (1–20). The true total is always reported. |
| `include_images` | `true` | Engraved notation per realization. |
| `allow_consecutive_perfects` | `false` | Permit parallel fifths and octaves. |
| `double_soprano` | `false` | Prefer doubling the soprano. |

### Progression syntax

Inversions are figured-bass digits written straight after the numeral, and case
matters exactly as in harmonic analysis:

| | |
|---|---|
| triads and sevenths | `I`, `ii`, `V7`, `ii65`, `V42`, `I64` |
| diminished | `viio6`, `viio7` |
| half-diminished | `ii065`, `iiø65`, `ii⌀65` |
| augmented | `III+` |
| major-major seventh | `IM7` |
| applied | `V7/V`, `viio6/ii` |
| altered roots | `bII6`, `N6`, `#ivo7` |
| augmented sixths | `Fr6`, `Gr6`, `It6` |

Anything the site would reject is caught up front with a message that names the
fix, e.g. `Vo` → *"'o' requires a lowercase roman numeral; got 'Vo'. Try 'vo'."*

## How it works

partwriter.com has no API. The solver is a sealed client-side IIFE: nothing is
exported to `window`, there are no network calls to a backend, and the
progression is not encoded in the URL. Its internal token array is built up
*only* by button clicks, and the on-page textarea is a read-only mirror of it —
setting the textarea does nothing.

So the tool drives the real UI:

- **`grammar.py`** turns roman-numeral text into the exact button-click sequence,
  reproducing the site's own validation rules. Illegal clicks are silently
  dropped by the site, which would yield a wrong-but-plausible progression, so
  after entry the driver compares the textarea against the string the clicks
  should have produced and fails loudly on any mismatch.
- **`driver.py`** keeps one warm Chrome page alive (Verovio's WASM toolkit is slow
  to start, so it is loaded once and reused; calls are serialised and the page
  relaunches itself if it dies). It sets the site's "settings per page" to 1 so
  Verovio draws each realization on its own system — every crop then comes with
  its own brace, clefs and key signature.
- **`extract.py`** parses the site's MEI export. Each realization is one
  `<measure>`, and every note carries an `xml:id` of the form
  `m{measure}c{chord}v{voice}` that Verovio reuses as the SVG element id — so a
  measure id joins a cropped image to its exact pitches.

Note spellings are always returned, images only when rendering succeeds, so the
text output is a structural fallback rather than an error path.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest              # grammar only, no browser
.venv/bin/python -m pytest -m live      # drives the real site
```

The grammar tests round-trip against the eight example progressions hard-coded
in the site's own JavaScript, so they catch any drift in its chord-entry
grammar. The live tests check the extracted music itself — leading tones
resolve, sevenths fall, no parallel perfects appear, `Fr6` in C spells
A♭–C–D–F♯ — which would fail if images and note data ever came apart.
