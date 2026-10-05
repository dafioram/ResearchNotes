"""
Request guards for a no-login app on a private network (spec §14).

There are no accounts by design, so these checks are the only thing
between a web page open in some browser on the network and the notes.
Both are invisible in normal use:

- Host check (every request): the address used to reach the app must be
  an IP address, localhost, or a local network name -- or listed in
  ALLOWED_HOSTS. A public domain name in the Host header means a web page
  has pointed its own domain at this server (DNS rebinding), which would
  otherwise let that page read and change notes as if it were the app.

- Cross-site check (POST and other changing requests): the browser says
  which site a request came from (the Origin header, or Sec-Fetch-Site),
  and only the app's own pages may change anything. Requests with neither
  header (curl, scripts) are allowed: they aren't a browser acting on
  behalf of some other web page.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from flask import abort, current_app, request

# Names that can't be registered on the public internet, so no outside web
# page can point one at this server: reserved or private-use suffixes, plus
# common home-router defaults that are blocked from public use.
LOCAL_SUFFIXES = (".localhost", ".local", ".home.arpa", ".internal", ".lan", ".home", ".corp")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_PORTS = {"http": "80", "https": "443"}


def parse_allowed_hosts(raw: str | None) -> tuple[str, ...]:
    """ALLOWED_HOSTS from the environment: comma-separated names, where a
    leading dot allows every name under it (".example.com")."""
    return tuple(h.strip().lower().rstrip(".") for h in (raw or "").split(",") if h.strip())


def _host_name(host: str) -> str:
    """The name part of a Host header: 'notes.lan:5000' -> 'notes.lan',
    '[::1]:5000' -> '::1'."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def host_allowed(host: str, extra: tuple[str, ...] = ()) -> bool:
    name = _host_name(host)
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or "." not in name or name.endswith(LOCAL_SUFFIXES):
        return True
    return any(name == h or (h.startswith(".") and name.endswith(h)) for h in extra)


def _origin_key(scheme: str, netloc: str) -> str:
    """host[:port] with the scheme's default port dropped, for comparing an
    Origin header with the address the request was sent to."""
    netloc = netloc.lower()
    default = DEFAULT_PORTS.get(scheme)
    if default and netloc.endswith(":" + default):
        netloc = netloc[: -len(default) - 1]
    return netloc


def is_cross_site(req) -> bool:
    origin = req.headers.get("Origin")
    if origin is not None:
        if origin == "null":
            return True  # sandboxed frames, file:// pages and the like
        parts = urlsplit(origin)
        return _origin_key(parts.scheme, parts.netloc) != _origin_key(req.scheme, req.host)
    site = req.headers.get("Sec-Fetch-Site")
    return site is not None and site not in ("same-origin", "none")


def init_app(app) -> None:
    @app.before_request
    def guard_requests():
        if not host_allowed(request.host, current_app.config["ALLOWED_HOSTS"]):
            abort(400, description=(
                f"This app doesn't answer to the address “{_host_name(request.host)}”. "
                "Use its IP address or local network name, or add the name to "
                "ALLOWED_HOSTS in .env."
            ))
        if request.method in UNSAFE_METHODS and is_cross_site(request):
            abort(403, description="Changes can only be made from the app's own pages.")
