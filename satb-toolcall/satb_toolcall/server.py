"""MCP server exposing partwriter.com's SATB part-writer as a tool."""

from __future__ import annotations

import base64

from mcp.server import MCPServer
from mcp.types import ContentBlock, ImageContent, TextContent

from .driver import PartWriterSession
from .extract import format_solution
from .grammar import MAJOR_KEYS, MINOR_KEYS

MAX_LIMIT = 20

server = MCPServer(
    name="satb-partwriter",
    version="0.1.0",
    instructions=(
        "Generates four-part SATB realizations of a roman-numeral progression by "
        "driving partwriter.com. Use it whenever asked to part-write, harmonize, or "
        "voice-lead a roman-numeral progression, or to show possible SATB settings."
    ),
)

# One warm browser page, shared across calls and started on first use. The
# session serialises calls internally and relaunches itself if the page dies.
_session = PartWriterSession()

TOOL_DESCRIPTION = f"""Part-write a roman-numeral progression in four voices (SATB).

Returns an engraved image of each realization plus its exact note spellings, and
reports how many realizations exist in total.

progression: dash- or space-separated roman numerals. Figured-bass inversions are
  written as digits directly after the numeral: 6, 64, 7, 65, 43, 42.
    triads/sevenths ...... I, ii, V7, ii65, V42, I64
    quality markers ...... viio7 (diminished), ii065 (half-diminished, also ø/⌀),
                           III+ (augmented), IMM7 written as IM7
    applied chords ....... V7/V, viio6/ii
    altered roots ........ bII6 (Neapolitan, or use N6), #ivo7
    augmented sixths ..... Fr6, Gr6, It6
  Case matters, exactly as in harmonic analysis: uppercase for major/augmented
  triads, lowercase for minor/diminished.

key: e.g. "C", "Eb", "f# minor". Case sets the mode ("Eb" major vs "eb" minor)
  unless you spell out "major"/"minor".
  major: {', '.join(MAJOR_KEYS)}
  minor: {', '.join(MINOR_KEYS)}
"""


@server.tool(
    name="part_write",
    title="SATB part-writer",
    description=TOOL_DESCRIPTION,
    structured_output=False,
)
async def part_write(
    key: str,
    progression: str,
    limit: int = 6,
    include_images: bool = True,
    allow_consecutive_perfects: bool = False,
    double_soprano: bool = False,
) -> list[ContentBlock]:
    """Generate SATB realizations.

    Args:
        key: Key of the progression, e.g. "C", "Eb", "f# minor".
        progression: Roman numerals, e.g. "I - ii65 - V7 - I".
        limit: How many realizations to return (1-20). The total found is always
            reported, even when more exist than are returned.
        include_images: Return engraved notation for each realization.
        allow_consecutive_perfects: Permit parallel fifths and octaves.
        double_soprano: Prefer doubling the soprano.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    result = await _session.solve(
        key,
        progression,
        limit=limit,
        want_images=include_images,
        options={
            "allow_consecutive_perfects": allow_consecutive_perfects,
            "double_soprano": double_soprano,
        },
    )

    shown = len(result.solutions)
    header = (
        f"{result.progression} in {result.key} "
        f"({'major' if result.key[0].isupper() else 'minor'})\n"
        f"{result.total} total settings"
        + (f", showing {shown}" if shown < result.total else "")
        + f" — grouped into {result.openings} opening voicing(s).\n"
        f"Each setting lists chords top to bottom, voices bass → soprano."
    )

    blocks: list[ContentBlock] = [TextContent(type="text", text=header)]
    for solution in result.solutions:
        if solution.png:
            blocks.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(solution.png).decode(),
                    mimeType="image/png",
                )
            )
        blocks.append(
            TextContent(type="text", text=format_solution(solution, result.chords))
        )
    if not result.solutions:
        blocks.append(TextContent(type="text", text="No realizations were returned."))
    return blocks


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
