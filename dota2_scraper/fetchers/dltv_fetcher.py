from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


@dataclass(slots=True)
class InternalApiInspection:
    search_api_usable: bool
    search_api_status: int | None = None
    search_api_content_type: str | None = None


class DltvFetcher:
    """DLTV fetcher that prefers internal JSON endpoints and falls back to a stealth browser for HTML rendering."""

    def __init__(
        self,
        base_url: str = "https://dltv.org",
        user_agent: str = "Dota2EsportsResearchBot/0.1",
        timeout_seconds: float = 30.0,
        delay_seconds: float = 0.5,
        fingerprint_seed: int = 42069,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self.fingerprint_seed = fingerprint_seed

        self._http: httpx.AsyncClient | None = None
        self._browser = None
        self._api_inspection: InternalApiInspection | None = None

    async def __aenter__(self) -> "DltvFetcher":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.close()

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=True,
                http2=True,
            )
        if self._api_inspection is None:
            self._api_inspection = await self.inspect_internal_api()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._browser is not None:
            with suppress(Exception):
                await self._browser.close()
            self._browser = None

    @property
    def api_inspection(self) -> InternalApiInspection | None:
        return self._api_inspection

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def inspect_internal_api(self) -> InternalApiInspection:
        if self._http is None:
            await self.start()
        assert self._http is not None

        response = await self._http.get("/api/v1/search_compact", params={"query": "spirit"})
        content_type = response.headers.get("content-type", "")
        search_api_usable = False

        try:
            payload = response.json()
            search_api_usable = isinstance(payload, (list, dict)) and bool(payload)
        except Exception:
            search_api_usable = False

        logger.info(
            "DLTV API inspection search_compact status={} content_type={} usable={}",
            response.status_code,
            content_type,
            search_api_usable,
        )
        return InternalApiInspection(
            search_api_usable=search_api_usable,
            search_api_status=response.status_code,
            search_api_content_type=content_type,
        )

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def fetch_config(self, country_id: int = 96) -> dict[str, Any]:
        if self._http is None:
            await self.start()
        assert self._http is not None

        url = f"/config/{country_id}.json"
        logger.debug("DLTV fetch JSON {}", url)
        response = await self._http.get(url)
        response.raise_for_status()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def fetch_html_page(self, path: str) -> str:
        """Fetch HTML page through a stealth browser when JSON endpoints are insufficient."""
        if self._browser is None:
            await self._start_browser()
        assert self._browser is not None

        page = await self._browser.new_page()
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            logger.debug("DLTV stealth-browser fetch {}", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(1.0)
            html = await page.content()
            return html
        finally:
            with suppress(Exception):
                await page.close()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)

    async def _start_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from cloakbrowser import launch_async
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Stealth browser dependency is required for DLTV HTML fallback. "
                "Install with `pip install cloakbrowser`."
            ) from exc
        logger.info("Launching stealth browser for DLTV with fingerprint seed {}", self.fingerprint_seed)
        self._browser = await launch_async(args=[f"--fingerprint={self.fingerprint_seed}"])
