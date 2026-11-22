"""URL validation + SSRF protection."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class InvalidURLError(ValueError):
    pass


class BlockedURLError(ValueError):
    pass


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise InvalidURLError("URL is empty.")
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https"):
        raise InvalidURLError("Only http and https URLs are supported.")
    host = p.hostname
    if not host or ("." not in host and host != "localhost" and ":" not in host):
        raise InvalidURLError("URL must contain a valid hostname.")
    if p.username or p.password:
        raise InvalidURLError("URLs with credentials are not allowed.")
    return raw


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


async def assert_public_url(url: str) -> None:
    """Resolve the host and reject if ANY resolved address is non-public."""
    host = urlparse(url).hostname or ""
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise InvalidURLError(f"Could not resolve host '{host}'.")
        ips = list({i[4][0] for i in infos})
    if not ips or not all(_is_public(ip) for ip in ips):
        raise BlockedURLError("URL resolves to a private or internal address.")
