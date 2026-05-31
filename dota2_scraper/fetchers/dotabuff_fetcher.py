from __future__ import annotations

import asyncio
from contextlib import suppress

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from dota2_scraper.models import FetchJob, FetchedPage


class DotabuffFetcher:
    """Fetches Dotabuff pages via a stealth-capable headless browser."""

    def __init__(self, fingerprint_seed: int, delay_seconds: float = 2.5) -> None:
        self.fingerprint_seed = fingerprint_seed
        self.delay_seconds = delay_seconds
        self._browser = None

    async def __aenter__(self) -> "DotabuffFetcher":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.close()

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from cloakbrowser import launch_async
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Stealth browser dependency is required for this fetcher. "
                "Install with `pip install cloakbrowser` and run `patchright install-deps chromium`."
            ) from exc
        logger.info("Launching stealth browser with fixed fingerprint seed {}", self.fingerprint_seed)
        self._browser = await launch_async(args=[f"--fingerprint={self.fingerprint_seed}"])

    async def close(self) -> None:
        if self._browser is None:
            return
        with suppress(Exception):
            await self._browser.close()
        self._browser = None

    @retry(
        retry=retry_if_exception_type((TimeoutError, RuntimeError, OSError)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch(self, job: FetchJob) -> FetchedPage:
        if self._browser is None:
            await self.start()
        page = await self._browser.new_page()
        try:
            logger.debug("Dotabuff fetch {}", job.url)
            await page.goto(job.url, wait_until="domcontentloaded", timeout=45_000)
            await self._wait_for_render(page)
            html = await page.content()
            final_url = getattr(page, "url", job.url)
            return FetchedPage(job=job, html=html, final_url=final_url, status_code=None)
        finally:
            with suppress(Exception):
                await page.close()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)

    async def _wait_for_render(self, page) -> None:  # type: ignore[no-untyped-def]
        # Dotabuff stat sections often render after initial DOMContentLoaded and after scroll.
        await asyncio.sleep(2.0)
        for y in (600, 1400, 2600, 4200):
            with suppress(Exception):
                await page.evaluate("y => window.scrollTo({ top: y, behavior: 'smooth' })", y)
            with suppress(Exception):
                await page.mouse.wheel(0, 500)
            await asyncio.sleep(0.8)
        with suppress(Exception):
            await page.evaluate("() => window.scrollTo({ top: 0, behavior: 'smooth' })")
        await asyncio.sleep(1.0)
