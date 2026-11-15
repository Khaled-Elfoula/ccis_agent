"""HTTP fetching for the scrapers: robots.txt, throttling, retries and a disk cache."""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "CCISSupportAgent/1.0 (+https://ccis.ksu.edu.sa; contact: khaled.elfoula@example.com)"
)
MIN_INTERVAL = 1.0
TIMEOUT = 30.0
ATTEMPTS = 3
RETRY_STATUSES = (429, 500, 502, 503, 504)
CACHE_DIR = Path(__file__).resolve().parent.parent / "db" / "cache"

_last_request: dict[str, float] = {}
_robots: dict[str, RobotFileParser | None] = {}


class ScrapeError(RuntimeError):
    """A page could not be fetched, or fetching it is not allowed."""


@dataclass
class Fetched:
    """A downloaded page; not_modified means the cached copy was reused."""

    url: str
    html: str
    not_modified: bool = False


def _throttle(host: str, interval: float = MIN_INTERVAL) -> None:
    """Wait until at least `interval` seconds have passed since the last call to this host."""
    wait = interval - (time.monotonic() - _last_request.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def _robots_for(client: httpx.Client, host: str) -> RobotFileParser | None:
    """Fetch and cache robots.txt for a host; None means the rules are unknown."""
    if host in _robots:
        return _robots[host]
    url = f"https://{host}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(url)
    try:
        response = client.get(url, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("robots.txt for %s unreachable: %s", host, exc)
        _robots[host] = None
        return None
    if response.status_code < 400:
        parser.parse(response.text.splitlines())
    elif 400 <= response.status_code < 500:
        parser.parse([])
    else:
        logger.warning("robots.txt for %s returned HTTP %s", host, response.status_code)
        _robots[host] = None
        return None
    _robots[host] = parser
    return parser


def fetch(
    source_id: str,
    url: str,
    force: bool = False,
    timeout: float = TIMEOUT,
    attempts: int = ATTEMPTS,
    min_interval: float = MIN_INTERVAL,
) -> Fetched:
    """Download a page with retries, keeping a copy of the HTML on disk."""
    host = urlparse(url).hostname or ""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{source_id}.html"
    meta_file = CACHE_DIR / f"{source_id}.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ar,en;q=0.8"}
    if cache_file.exists() and not force:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    last_error = ""
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        robots = _robots_for(client, host)
        if robots is None:
            raise ScrapeError(f"robots.txt for {host} unavailable; skipping {url}")
        if not robots.can_fetch(USER_AGENT, url):
            raise ScrapeError(f"robots.txt disallows {url}")
        for attempt in range(1, attempts + 1):
            _throttle(host, min_interval)
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if response.status_code == 304 and cache_file.exists():
                    return Fetched(url, cache_file.read_text(encoding="utf-8"), not_modified=True)
                if response.status_code == 200:
                    cache_file.write_text(response.text, encoding="utf-8")
                    meta_file.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "etag": response.headers.get("etag"),
                                "last_modified": response.headers.get("last-modified"),
                                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    return Fetched(url, response.text)
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUSES:
                    raise ScrapeError(f"{url}: {last_error}")
            logger.warning("%s attempt %d failed: %s", url, attempt, last_error)
            if attempt < attempts:
                time.sleep(2**attempt)
    raise ScrapeError(f"{url}: {last_error}")

