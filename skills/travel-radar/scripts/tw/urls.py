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
