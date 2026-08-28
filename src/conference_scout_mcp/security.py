from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def hash_api_key(raw_key: str, pepper: str | None = None) -> str:
    secret = pepper if pepper is not None else os.getenv("CONFERENCE_SCOUT_API_KEY_PEPPER", "")
    return hmac.new(secret.encode(), raw_key.encode(), hashlib.sha256).hexdigest()


def _is_forbidden_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not address.is_global


def validate_public_url(url: str, *, resolve_dns: bool = True) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("Only absolute public http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL port") from exc
    if port not in {None, 80, 443}:
        raise UnsafeUrlError("Only standard public web ports are allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeUrlError("Localhost URLs are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and _is_forbidden_ip(hostname):
        raise UnsafeUrlError("Private, loopback, link-local, and reserved IPs are not allowed")
    if resolve_dns:
        try:
            default_port = 443 if parsed.scheme == "https" else 80
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, port or default_port)}
        except socket.gaierror as exc:
            raise UnsafeUrlError("URL hostname could not be resolved") from exc
        if not addresses or any(_is_forbidden_ip(address) for address in addresses):
            raise UnsafeUrlError("URL hostname resolves to a non-public address")
    return parsed.geturl()
