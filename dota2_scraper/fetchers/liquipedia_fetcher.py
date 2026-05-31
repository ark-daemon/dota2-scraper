from __future__ import annotations

import asyncio

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from dota2_scraper.models import FetchJob, FetchedPage


def is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


class LiquipediaFetcher:
    """Static HTML fetcher for Liquipedia using httpx."""

    def __init__(self, user_agent: str, timeout_seconds: float = 30.0, delay_seconds: float = 1.5) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "LiquipediaFetcher":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.close()

    async def start(self) -> None:
        if self._client is not None:
            return
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=True,
            http2=True,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception(is_retryable_http_error),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch(self, job: FetchJob) -> FetchedPage:
        if self._client is None:
            await self.start()
        assert self._client is not None
        logger.debug("Liquipedia fetch {}", job.url)
        response = await self._client.get(job.url)
        response.raise_for_status()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return FetchedPage(
            job=job,
            html=response.text,
            final_url=str(response.url),
            status_code=response.status_code,
        )
