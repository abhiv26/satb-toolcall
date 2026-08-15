"""Warm Playwright session that drives partwriter.com.

The site is a sealed client-side IIFE: no API, no exports, no URL state. The only
way in is to click its buttons, so this module keeps one browser page alive and
replays a click sequence into it per request. Verovio's WASM toolkit takes a
while to come up, so the page is loaded once and reused; calls are serialised.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from .extract import Solution, notes_by_measure
from .grammar import Progression, parse, parse_key

URL = "https://partwriter.com/"
VIEWPORT = {"width": 1600, "height": 1200}
CROP_PADDING = 14
RENDER_TIMEOUT = 120_000

# Option checkboxes the site reads (via .checked) at solve time.
OPTION_IDS = {
    "allow_consecutive_perfects": "allow-consecutive-perfects",
    "double_soprano": "double-soprano",
    "allow_doubled_third": "allow-root-pos-M-m-doubled-3rd",
    "allow_doubled_fifth": "allow-root-pos-M-m-doubled-5th",
}


class PartWriterError(RuntimeError):
    """The site refused the request, or its UI did not behave as expected."""


@dataclass
class SolveResult:
    key: str
    progression: str
    chords: list[str]
    total: int
    openings: int
    solutions: list[Solution] = field(default_factory=list)


class PartWriterSession:
    """A single long-lived browser page pointed at partwriter.com."""

    def __init__(self, *, headless: bool = True, url: str = URL) -> None:
        self._headless = headless
        self._url = url
        self._pw = None
        self._browser = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # Playwright ships no Chromium build for mac12-arm64, but the system
        # Chrome works fine; fall back to bundled Chromium elsewhere.
        try:
            self._browser = await self._pw.chromium.launch(channel="chrome", headless=self._headless)
        except PlaywrightError:
            self._browser = await self._pw.chromium.launch(headless=self._headless)

        self._page = await self._browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page = self._page
        await page.goto(self._url, wait_until="networkidle", timeout=90_000)
        await page.wait_for_selector("#RN-keys", timeout=60_000)
        await page.evaluate("document.getElementById('RN-keyboard').classList.remove('hide-me')")

        # "Show multiple solutions" mode. It is the default, but it drives which
        # code path the site takes, so set it explicitly and fire its listener.
        await page.evaluate("""() => {
            const r = document.getElementById('multiple');
            if (!r.checked) { r.checked = true; r.dispatchEvent(new Event('change', {bubbles: true})); }
        }""")

        # Warm-up solve. vrvToolkit lives inside the site's closure and cannot be
        # read from the outside, so a completed render is the only readiness probe.
        await self._solve_unlocked("C", parse("V-I"), limit=1, options={}, want_images=False)

    async def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass
        self._browser = self._pw = self._page = None

    async def _ensure_live(self) -> None:
        if self._page is None or self._page.is_closed():
            await self.close()
            await self.start()

    # ------------------------------------------------------------------- public

    async def solve(
        self,
        key: str,
        progression: str,
        *,
        limit: int = 6,
        want_images: bool = True,
        options: dict[str, bool] | None = None,
    ) -> SolveResult:
        parsed = parse(progression)
        site_key = parse_key(key)
        async with self._lock:
            await self._ensure_live()
            try:
                return await self._solve_unlocked(
                    site_key, parsed, limit=limit, options=options or {}, want_images=want_images
                )
            except PlaywrightError as exc:
                # A dead/wedged page should not poison every later call.
                await self.close()
                raise PartWriterError(f"Browser session failed: {exc}") from exc

    # ------------------------------------------------------------------ internals

    async def _solve_unlocked(
        self,
        site_key: str,
        parsed: Progression,
        *,
        limit: int,
        options: dict[str, bool],
        want_images: bool,
    ) -> SolveResult:
        page = self._page
        assert page is not None

        # After a solve the site collapses the entry panel to make room for the
        # results, taking #key and the whole keypad with it. Re-show it first.
        await page.evaluate("document.getElementById('RN-keyboard').classList.remove('hide-me')")

        await page.select_option("#key", site_key)
        # One realization per page: Verovio then draws each solution on its own
        # system, complete with brace, clefs and key signature, so every crop is
        # a legible standalone score. These live in a closed drawer and are read
        # straight off the element at solve time, so set them via JS.
        await page.evaluate("document.getElementById('settings-count').value = '1'")
        for name, value in options.items():
            if name in OPTION_IDS:
                await page.evaluate(
                    "([id, v]) => { document.getElementById(id).checked = v; }",
                    [OPTION_IDS[name], bool(value)],
                )

        await self._enter(parsed)

        await page.click("#go")
        await self._wait_for_render()

        total = self._parse_int(await page.inner_text("#start-info"), r"(\d+) total settings")
        openings = await page.eval_on_selector("#starting-voicing-select", "e => e.options.length")

        result = SolveResult(
            key=site_key,
            progression=parsed.expected,
            chords=parsed.chords,
            total=total,
            openings=max(openings, 1),
        )
        if limit <= 0:
            return result

        notes = await self._export_all_notes()
        result.solutions = await self._collect(limit, notes, want_images)
        return result

    async def _enter(self, parsed: Progression) -> None:
        """Replay the click sequence, then verify against the textarea mirror."""
        page = self._page
        await page.click("#clear-all")
        for css_class, value in parsed.clicks:
            selector = "#space" if css_class == "space" else f'#RN-keys button.{css_class}[value="{value}"]'
            await page.click(selector)

        got = await page.input_value("#progression")
        if got.strip() != parsed.expected:
            alert = (await page.inner_text("#alert-message")).strip()
            raise PartWriterError(
                f"partwriter.com rejected part of the progression: it read "
                f"{got.strip()!r} where {parsed.expected!r} was expected."
                + (f" The site said: {alert}" if alert else "")
            )

    async def _wait_for_render(self) -> None:
        page = self._page
        await page.wait_for_function(
            "() => getComputedStyle(document.getElementById('spinner-container')).display === 'none'",
            timeout=RENDER_TIMEOUT,
        )
        await page.wait_for_selector("#realization-output-svg svg .measure", timeout=RENDER_TIMEOUT)
        alert = (await page.inner_text("#alert-message")).strip()
        if alert:
            raise PartWriterError(f"partwriter.com reported: {alert}")

    async def _current_measure_id(self) -> str:
        return await self._page.eval_on_selector(
            "#realization-output-svg .measure", "e => e.id"
        )

    async def _navigate(self, button_id: str, previous_id: str) -> None:
        """Click a pagination button and wait for the SVG to actually swap.

        Pagination re-renders without touching the spinner, so waiting on the
        rendered measure id is the only reliable signal. Every realization has a
        distinct xml:id, so a changed id means a completed new render. The click
        is dispatched in-page because the site hides these controls whenever the
        current group has only one entry.
        """
        await self._page.eval_on_selector(f"#{button_id}", "e => e.click()")
        await self._page.wait_for_function(
            """prev => {
                 const m = document.querySelector('#realization-output-svg .measure');
                 return m && m.id && m.id !== prev;
               }""",
            arg=previous_id,
            timeout=RENDER_TIMEOUT,
        )

    async def _collect(
        self, limit: int, notes: dict[str, list[list[str]]], want_images: bool
    ) -> list[Solution]:
        """Walk openings and pages, capturing one solution per render."""
        page = self._page
        collected: list[Solution] = []
        seen: set[str] = set()

        for opening in range(1, (await self._opening_count()) + 1):
            pages = self._parse_int(await page.inner_text("#subset-info"), r"of (\d+)\)")
            if pages < 1:
                break  # nothing rendered; don't paginate into a timeout
            for page_index in range(pages):
                measure_id = await self._current_measure_id()
                if measure_id not in seen:
                    seen.add(measure_id)
                    collected.append(
                        Solution(
                            index=len(collected) + 1,
                            mei_id=measure_id,
                            opening=opening,
                            chords=notes.get(measure_id, []),
                            png=await self._crop() if want_images else None,
                        )
                    )
                if len(collected) >= limit:
                    return collected
                if page_index < pages - 1:
                    await self._navigate("next-subset", measure_id)
            if opening < (await self._opening_count()):
                await self._navigate("next-start", await self._current_measure_id())
        return collected

    async def _opening_count(self) -> int:
        return max(await self._page.eval_on_selector("#starting-voicing-select", "e => e.options.length"), 1)

    async def _crop(self) -> bytes | None:
        """Screenshot just the rendered system."""
        page = self._page
        box = await page.evaluate(
            """() => {
                 const root = document.querySelector('#realization-output-svg');
                 const svg = root.querySelector('svg');
                 // Verovio hides the SVG while it is being swapped in.
                 if (svg && /hidden/.test(svg.getAttribute('style') || '')) {
                     svg.setAttribute('style', 'visibility: visible;');
                 }
                 const sys = root.querySelector('.system') || root.querySelector('.measure');
                 if (!sys) return null;
                 const b = sys.getBoundingClientRect();
                 return {x: b.x, y: b.y, width: b.width, height: b.height};
               }"""
        )
        if not box or box["width"] < 1 or box["height"] < 1:
            return None
        clip = {
            "x": max(box["x"] - CROP_PADDING, 0),
            "y": max(box["y"] - CROP_PADDING, 0),
            "width": min(box["width"] + 2 * CROP_PADDING, VIEWPORT["width"] - max(box["x"] - CROP_PADDING, 0)),
            "height": min(box["height"] + 2 * CROP_PADDING, VIEWPORT["height"] - max(box["y"] - CROP_PADDING, 0)),
        }
        try:
            return await page.screenshot(clip=clip, type="png")
        except PlaywrightError:
            return None

    async def _export_all_notes(self) -> dict[str, list[list[str]]]:
        """Download the MEI for *every* solution and parse out the pitches.

        Keyed by xml:id, which is stable across renders and matches the SVG
        element ids, so images and note spellings can be joined exactly.
        """
        page = self._page
        # The #file-tab handler is what enables the output-subset options.
        await page.click("#file-tab")
        try:
            await page.evaluate("""() => {
                const subset = document.getElementById('output-subset');
                for (const o of subset.options) o.disabled = false;
                subset.value = 'all';
                document.getElementById('output-format').value = 'create-mei-file';
                document.getElementById('create-file').click();
            }""")
            href = None
            for _ in range(40):
                href = await page.get_attribute("#download-link", "href")
                if href and href.startswith("blob:"):
                    break
                await page.wait_for_timeout(50)
            if not href:
                return {}
            mei = await page.evaluate("async h => (await fetch(h)).text()", href)
        except PlaywrightError:
            return {}
        finally:
            # Keep the modal from covering the notation we are about to crop.
            await page.evaluate("document.getElementById('file-drawer').classList.add('hide-me')")
        return notes_by_measure(mei)

    @staticmethod
    def _parse_int(text: str, pattern: str) -> int:
        match = re.search(pattern, text or "")
        return int(match.group(1)) if match else 0
