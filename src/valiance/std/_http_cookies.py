"""Small, opt-in, reactor-confined HTTP cookie jar (RFC 6265 subset).

No cross-client or process-global state. Domain cookies require a public-suffix
check; when no platform PSL provider exists they are rejected conservatively
rather than accepting a potential supercookie. Host-only cookies always work.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import ipaddress
import re
from time import time
from urllib.parse import urlsplit

_COOKIE_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MAX_COOKIES = 256
_MAX_COOKIE_BYTES = 4096
_MAX_OUTBOUND_BYTES = 8192


def _public_suffix_checker():
    """Load an optional public-suffix checker for cookie domain validation."""
    name = ctypes.util.find_library('psl')
    if name is None:
        return None
    try:
        lib = C.CDLL(name)
        lib.psl_builtin.restype = C.c_void_p
        lib.psl_builtin.argtypes = []
        lib.psl_is_public_suffix.restype = C.c_int
        lib.psl_is_public_suffix.argtypes = [C.c_void_p, C.c_char_p]
        ctx = lib.psl_builtin()
        if ctx is None:
            return None
        # Closure retains CDLL for the lifetime of the reactor's jar.
        return lambda domain: bool(lib.psl_is_public_suffix(ctx, domain.encode('ascii')))
    except (OSError, AttributeError, ValueError):
        return None


@dataclass(slots=True)
class _Cookie:
    name: str
    value: str
    domain: str
    host_only: bool
    path: str
    secure: bool
    expires: float | None
    sequence: int


def _path_match(current: str, cookie_path: str) -> bool:
    """Check whether a request path falls within a cookie's allowed path scope."""
    return current == cookie_path or (current.startswith(cookie_path) and
                                     (cookie_path.endswith('/') or current[len(cookie_path):].startswith('/')))


def _default_path(path: str) -> str:
    """Derive the default cookie path from the URL that set the cookie."""
    if not path.startswith('/') or path.count('/') < 2:
        return '/'
    return path.rsplit('/', 1)[0]


def _is_ip(host: str) -> bool:
    """Detect literal IP addresses, which cannot accept domain cookies."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class CookieJar:
    """A single owner's opt-in cookie store, never shared between clients."""

    def __init__(self) -> None:
        """Initialize an independent client-scoped cookie store and expiration policy."""
        self._items: dict[tuple[str, str, str], _Cookie] = {}
        self._sequence = 0
        self._is_public_suffix = _public_suffix_checker()

    def _prune(self, now: float) -> None:
        """Remove expired cookies before selecting or updating cookie state."""
        for key, cookie in tuple(self._items.items()):
            if cookie.expires is not None and cookie.expires <= now:
                del self._items[key]

    def receive(self, url: str, header: str) -> bool:
        """Store Set-Cookie if well-formed and scoped to response's host.

        Malformed/unsupported cookies are ignored, not allowed to modify
        the request or inject transport headers. Max-Age wins over Expires.
        """
        if len(header.encode('utf-8', 'replace')) > _MAX_COOKIE_BYTES:
            return False
        parts = header.split(';')
        if '=' not in parts[0]:
            return False
        name, value = (piece.strip() for piece in parts[0].split('=', 1))
        if not _COOKIE_NAME.fullmatch(name):
            return False
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        # Conservative cookie-octet subset: reject whitespace, separators,
        # quotes/backslashes and all control bytes instead of normalizing.
        if any(not (0x21 <= ord(c) <= 0x7e) or c in ';,"\\' for c in value):
            return False
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            return False
        host = host.lower()
        secure_origin = parsed.scheme == 'https'
        path = _default_path(parsed.path)
        domain, host_only, secure = host, True, False
        max_age = None
        expires = None
        for fragment in parts[1:]:
            key, _, raw = fragment.strip().partition('=')
            attribute = key.lower()
            raw = raw.strip()
            if attribute == 'secure':
                secure = True
            elif attribute == 'domain':
                attr_domain = raw.lstrip('.').lower()
                if not attr_domain or raw.endswith('.') or not attr_domain.isascii():
                    return False
                if _is_ip(host) or not (host == attr_domain or host.endswith('.' + attr_domain)):
                    return False
                if self._is_public_suffix is None or self._is_public_suffix(attr_domain):
                    return False
                domain, host_only = attr_domain, False
            elif attribute == 'path':
                if raw.startswith('/'):
                    path = raw
            elif attribute == 'max-age':
                if re.fullmatch(r'-?[0-9]{1,15}', raw):
                    max_age = int(raw)
            elif attribute == 'expires':
                try:
                    date = parsedate_to_datetime(raw)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    expires = date.timestamp()
                except (ValueError, TypeError, OverflowError, IndexError):
                    pass
        if secure and not secure_origin:
            return False
        if name.startswith('__Secure-') and (not secure or not secure_origin):
            return False
        if name.startswith('__Host-') and (not secure or not secure_origin or
                                             not host_only or path != '/'):
            return False
        now = time()
        self._prune(now)
        # Insecure origins cannot replace a secure cookie of the same name
        # with overlapping domain and path scope (secure-cookie overlay).
        if not secure_origin:
            for protected in self._items.values():
                if (protected.secure and protected.name == name and
                    (domain == protected.domain or domain.endswith('.' + protected.domain)
                     or protected.domain.endswith('.' + domain)) and
                    _path_match(path, protected.path)):
                    return False
        expiry = now + max_age if max_age is not None else expires
        key = (name, domain, path)
        if expiry is not None and expiry <= now:
            self._items.pop(key, None)
            return True
        self._sequence += 1
        self._items[key] = _Cookie(name, value, domain, host_only, path, secure, expiry,
                                   self._sequence)
        if len(self._items) > _MAX_COOKIES:
            oldest = min(self._items, key=lambda k: self._items[k].sequence)
            del self._items[oldest]
        return True

    def outbound(self, url: str) -> str | None:
        """Select request cookies using domain, path, expiry, and Secure restrictions."""
        parsed = urlsplit(url)
        if parsed.hostname is None:
            return None
        host = parsed.hostname.lower()
        path = parsed.path or '/'
        now = time()
        self._prune(now)
        cookies = [cookie for cookie in self._items.values()
                   if (host == cookie.domain if cookie.host_only else
                       host == cookie.domain or host.endswith('.' + cookie.domain))
                   and _path_match(path, cookie.path)
                   and (not cookie.secure or parsed.scheme == 'https')]
        cookies.sort(key=lambda c: (-len(c.path), c.sequence))
        wire = '; '.join(c.name + '=' + c.value for c in cookies)
        # Never construct an unbounded outgoing Cookie header. Emit only the
        # highest-priority cookies that fit the fixed outbound wire budget.
        if len(wire) > _MAX_OUTBOUND_BYTES:
            selected = []
            used = 0
            for cookie in cookies:
                fragment = cookie.name + '=' + cookie.value
                additional = len(fragment) + (2 if selected else 0)
                if used + additional <= _MAX_OUTBOUND_BYTES:
                    selected.append(fragment)
                    used += additional
            wire = '; '.join(selected)
        return wire or None

    def __len__(self) -> int:
        """Return the count of currently stored client cookies."""
        self._prune(time())
        return len(self._items)
