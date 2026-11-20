"""Transport-independent failures for outbound HTTP operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorKind(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_CONFIG = "invalid_config"
    DNS = "dns"
    CONNECT = "connect"
    TLS = "tls"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    TRANSFER = "transfer"
    RESOURCE_LIMIT = "resource_limit"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    UNSUPPORTED = "unsupported"


@dataclass(eq=False)
class HttpError(Exception):
    """A failure whose public identity does not depend on a transport's error codes."""

    kind: ErrorKind
    message: str
    stage: str = "request"

    def __str__(self) -> str:
        """Render an HTTP error with its structured category and safe diagnostic message."""
        return f"{self.kind.value} ({self.stage}): {self.message}"


class HttpValueError(ValueError):
    """A rejected value or configuration, before any network traffic."""
