"""The single URL parser used for every source decision (verify, confirm, resolve, record).

One parser avoids host confusion between components. Rejected: userinfo (`a@b`), non-numeric
ports, encoded slashes and traversal segments. Paths are percent-decoded and normalized.
"""
import posixpath
from urllib.parse import unquote, urlsplit


class URLError(ValueError):
    pass


def parse(url):
    """(host, path): host lowercase without "www."-stripping, path normalized without trailing slash."""
    raw = (url or "").strip()
    if not raw:
        raise URLError("empty URL")
    if "://" not in raw:
        raw = "https://" + raw
    u = urlsplit(raw)
    if "@" in u.netloc:
        raise URLError(f"URL with user info is not accepted: {url!r}")
    try:
        u.port  # noqa: B018 - raises ValueError on a non-numeric port
    except ValueError:
        raise URLError(f"URL with an invalid port: {url!r}")
    host = (u.hostname or "").lower().rstrip(".")
    if not host or any(c in host for c in "\\%"):
        raise URLError(f"URL without a valid host: {url!r}")
    path_raw = u.path or ""
    if "%2f" in path_raw.lower() or "%5c" in path_raw.lower():
        raise URLError(f"URL path with encoded slashes is not accepted: {url!r}")
    path = unquote(path_raw).lower()
    if any(seg in ("..", ".") for seg in path.split("/")):
        raise URLError(f"URL path with traversal segments is not accepted: {url!r}")
    path = posixpath.normpath(path) if path else ""
    path = "" if path in (".", "/") else path.rstrip("/")
    return host, path


def try_parse(url):
    try:
        return parse(url)
    except URLError:
        return "", ""


def is_public_host(host):
    """True only if every address the host resolves to is public (not private, loopback,
    link-local, multicast, reserved or unspecified). Used before any outbound request to a
    URL that did not come from the user or from config."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or not ip.is_global):
            return False
    return True


def safe_outbound(url):
    """http(s) URL whose host resolves only to public addresses."""
    from urllib.parse import urlsplit
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return False
    host = try_parse(url)[0]
    return bool(host) and is_public_host(host)
