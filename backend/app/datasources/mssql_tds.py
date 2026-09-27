"""The two `python-tds` fixes DataQ ships (ADR 0044, "Spike outcome").

1. **Hostname verification.** ``pytds.tls.validate_host`` calls ``X509.get_extension``, which
   current pyOpenSSL has removed, so with host validation on every connection fails before login.
   `validate_host` replaces it: DNS subject-alternative names read through ``cryptography``,
   a wildcard only as a whole left-most label, an IP host only against IP-address SANs, and no
   fallback to the subject CN.
2. **Named-instance routing.** A TDS route can name ``host\\instance`` (Fabric does). pytds hands
   that whole string to DNS; only the host part is resolved (and verified), while the full name
   stays the LOGIN7 server name.

`install` applies both, once per process, before DataQ opens its first TDS session. It refuses to
patch a pytds whose shape it does not recognise, so a driver bump fails loudly instead of quietly
running with the broken validator.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any

_lock = threading.Lock()
_installed = False


def host_part(name: str) -> str:
    """``host`` from a ``host\\instance`` server name (the name unchanged when it has none)."""
    return name.split("\\", 1)[0]


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def dns_name_matches(pattern: str, host: str) -> bool:
    """RFC 6125 matching of one DNS SAN against a hostname: exact (case-insensitive), or a ``*``
    that is the WHOLE left-most label and stands for exactly one non-empty label. A wildcard over
    a single remaining label (``*.com``) or a partial one (``db*.example.com``) never matches.
    """
    pattern = pattern.lower().rstrip(".")
    host = host.lower().rstrip(".")
    if not pattern or not host:
        return False
    if "*" not in pattern:
        return pattern == host
    if not pattern.startswith("*.") or "*" in pattern[2:]:
        return False
    suffix = pattern[2:]
    if "." not in suffix:
        return False
    label, _, rest = host.partition(".")
    return bool(label) and rest == suffix


def certificate_matches(cert: Any, host: str) -> bool:
    """Whether a ``cryptography`` certificate is valid for ``host`` (a bare host, no instance)."""
    from cryptography import x509

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False  # no SAN: the subject CN is deliberately not consulted
    address = _ip(host)
    if address is not None:
        return address in san.get_values_for_type(x509.IPAddress)
    return any(dns_name_matches(name, host) for name in san.get_values_for_type(x509.DNSName))


def validate_host(cert: Any, name: bytes | str) -> bool:
    """Drop-in for ``pytds.tls.validate_host(cert, name)``: ``cert`` is the peer's pyOpenSSL
    ``X509``, ``name`` the server name pytds connected to (possibly ``host\\instance``).
    """
    if cert is None:
        return False
    host = host_part(name.decode("ascii") if isinstance(name, bytes) else name)
    return certificate_matches(cert.to_cryptography(), host)


class _RoutingSocketModule:
    """What pytds sees as its ``socket`` module: the real one, except that ``create_connection``
    resolves only the host part of a ``host\\instance`` address.
    """

    def __getattr__(self, attr: str) -> Any:
        return getattr(socket, attr)

    @staticmethod
    def create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        host, port = address
        return socket.create_connection((host_part(host), port), *args, **kwargs)


def install() -> None:
    """Apply both fixes to the imported pytds. Idempotent and thread-safe."""
    global _installed
    if _installed:
        return
    with _lock:
        if _installed:
            return
        import pytds
        import pytds.tls

        if not callable(getattr(pytds.tls, "validate_host", None)) or not hasattr(
            getattr(pytds, "socket", None), "create_connection"
        ):
            raise RuntimeError(
                "python-tds no longer has the shape DataQ patches (tls.validate_host, "
                "socket.create_connection) — re-verify ADR 0044's fixes before bumping it"
            )
        pytds.tls.validate_host = validate_host
        pytds.socket = _RoutingSocketModule()
        _installed = True
