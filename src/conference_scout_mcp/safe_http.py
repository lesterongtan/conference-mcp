from __future__ import annotations

from urllib.parse import urljoin

import httpx

from .config import Settings
from .security import UnsafeUrlError, validate_public_url


class ResponseTooLargeError(ValueError):
    pass


def fetch_public_text(
    url: str,
    *,
    timeout: httpx.Timeout | float = 20.0,
    max_redirects: int = 4,
    max_bytes: int | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Fetch bounded public text while validating every redirect hop."""
    limit = max_bytes or Settings().max_response_bytes
    current = validate_public_url(url)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(max_redirects + 1):
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current = validate_public_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(
                    allowed in content_type
                    for allowed in (
                        "text/",
                        "application/json",
                        "application/ld+json",
                        "application/xhtml",
                    )
                ):
                    raise ValueError("Unsupported response content type")
                declared = response.headers.get("content-length")
                if declared and int(declared) > limit:
                    raise ResponseTooLargeError("Response exceeds configured size limit")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise ResponseTooLargeError("Response exceeds configured size limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), current, content_type
    raise UnsafeUrlError("Too many redirects")
