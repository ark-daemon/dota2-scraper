from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter


def _is_retryable_opendota_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


class AsyncRateLimiter:
    """Simple global rate limiter for evenly spaced requests."""

    def __init__(self, max_requests: int, period_seconds: float) -> None:
        self.min_interval = period_seconds / max_requests
        self._next_available = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            if now < self._next_available:
                await asyncio.sleep(self._next_available - now)
                now = asyncio.get_running_loop().time()
            self._next_available = now + self.min_interval


class OpenDotaFetcher:
    """OpenDota REST API client with rate limiting + retry handling."""

    def __init__(
        self,
        base_url: str = "https://api.opendota.com/api",
        user_agent: str = "Dota2EsportsResearchBot/0.1",
        timeout_seconds: float = 30.0,
        requests_per_minute: int = 40,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._rate_limiter = AsyncRateLimiter(max_requests=requests_per_minute, period_seconds=60.0)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenDotaFetcher":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.close()

    async def start(self) -> None:
        if self._client is not None:
            return
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
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
        retry=retry_if_exception(_is_retryable_opendota_error),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    async def _request_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            await self.start()
        assert self._client is not None

        await self._rate_limiter.wait()
        logger.debug("OpenDota request path={} params={}", path, params)
        response = await self._client.get(path, params=params)
        if response.status_code >= 400:
            logger.warning(
                "OpenDota non-2xx status={} path={} params={} body={}",
                response.status_code,
                path,
                params,
                response.text[:500],
            )
        response.raise_for_status()
        return response.json()

    async def get_pro_matches(self, less_than_match_id: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if less_than_match_id is not None:
            params["less_than_match_id"] = less_than_match_id
        payload = await self._request_json("/proMatches", params=params or None)
        return payload if isinstance(payload, list) else []

    async def get_match_details(self, match_id: int) -> dict[str, Any]:
        payload = await self._request_json(f"/matches/{match_id}")
        return payload if isinstance(payload, dict) else {}

    async def get_pro_players(self) -> list[dict[str, Any]]:
        payload = await self._request_json("/proPlayers")
        return payload if isinstance(payload, list) else []

    async def get_leagues(self) -> list[dict[str, Any]]:
        payload = await self._request_json("/leagues")
        return payload if isinstance(payload, list) else []

    async def get_heroes(self) -> list[dict[str, Any]]:
        payload = await self._request_json("/heroes")
        return payload if isinstance(payload, list) else []

    async def get_team(self, team_id: int) -> dict[str, Any]:
        payload = await self._request_json(f"/teams/{team_id}")
        return payload if isinstance(payload, dict) else {}
