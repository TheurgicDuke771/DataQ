"""The python-tds fixes DataQ ships (ADR 0044): SAN hostname validation + named-instance routing."""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import threading
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from OpenSSL import crypto

from backend.app.datasources import mssql_tds

_KEY = ec.generate_private_key(ec.SECP256R1())


def _cert(
    *,
    cn: str = "unrelated.example",
    dns: tuple[str, ...] = (),
    ips: tuple[str, ...] = (),
) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(_KEY.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
    )
    general: list[x509.GeneralName] = [x509.DNSName(d) for d in dns]
    general += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    if general:
        builder = builder.add_extension(x509.SubjectAlternativeName(general), critical=False)
    return builder.sign(_KEY, hashes.SHA256())


def _openssl(cert: x509.Certificate) -> Any:
    """The pyOpenSSL `X509` pytds actually hands the validator."""
    return crypto.X509.from_cryptography(cert)


AZURE = _cert(dns=("*.database.windows.net", "*.control.database.windows.net"))


@pytest.mark.parametrize(
    ("pattern", "host", "expected"),
    [
        ("db.example.com", "db.example.com", True),
        ("DB.Example.com", "db.example.COM", True),
        ("db.example.com.", "db.example.com", True),
        ("db.example.com", "other.example.com", False),
        ("*.example.com", "db.example.com", True),
        # A wildcard is exactly ONE label: never the bare parent, never two labels deep.
        ("*.example.com", "example.com", False),
        ("*.example.com", "a.b.example.com", False),
        ("*.example.com", ".example.com", False),
        # Never a wildcard over a public suffix, a partial label, or a non-left-most label.
        ("*.com", "example.com", False),
        ("db*.example.com", "db1.example.com", False),
        ("a.*.example.com", "a.b.example.com", False),
        ("*.*.example.com", "a.b.example.com", False),
        ("", "db.example.com", False),
        ("db.example.com", "", False),
    ],
)
def test_dns_name_matching(pattern: str, host: str, expected: bool) -> None:
    assert mssql_tds.dns_name_matches(pattern, host) is expected


def test_exact_and_wildcard_san_accept_the_real_host() -> None:
    assert mssql_tds.validate_host(_openssl(AZURE), b"srv.database.windows.net")
    exact = _cert(dns=("sql.corp.example",))
    assert mssql_tds.validate_host(_openssl(exact), "sql.corp.example")


def test_wildcard_depth_is_refused() -> None:
    assert not mssql_tds.validate_host(_openssl(AZURE), b"a.srv.database.windows.net")


def test_an_ip_is_refused_against_dns_sans() -> None:
    """Connecting by IP to a server whose certificate names hosts — the live negative case."""
    assert not mssql_tds.validate_host(_openssl(AZURE), b"20.42.73.1")


def test_an_ip_matches_only_an_ip_san() -> None:
    cert = _cert(dns=("*.0.0.1",), ips=("10.0.0.1", "fd00::1"))
    assert mssql_tds.validate_host(_openssl(cert), b"10.0.0.1")
    assert mssql_tds.validate_host(_openssl(cert), b"fd00::1")
    assert not mssql_tds.validate_host(_openssl(cert), b"10.0.0.2")


def test_no_san_is_refused_even_when_the_cn_matches() -> None:
    """No CN fallback: the CN names the host exactly and is still not trusted."""
    cert = _cert(cn="sql.corp.example")
    assert not mssql_tds.validate_host(_openssl(cert), b"sql.corp.example")


def test_no_certificate_is_refused() -> None:
    assert not mssql_tds.validate_host(None, b"sql.corp.example")


def test_named_instance_is_verified_on_its_host_part_only() -> None:
    routed = _cert(dns=("*.pbidedicated.windows.net",))
    name = b"pbipeastus5-eastus.pbidedicated.windows.net\\ABCD-dw"
    assert mssql_tds.validate_host(_openssl(routed), name)
    # The instance never widens what the host part is checked against.
    assert not mssql_tds.validate_host(
        _openssl(routed), b"evil.example\\x.pbidedicated.windows.net"
    )


def test_host_part() -> None:
    assert mssql_tds.host_part("h.example\\inst") == "h.example"
    assert mssql_tds.host_part("h.example") == "h.example"


def test_routing_socket_resolves_only_the_host_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    def _record(address: Any, *args: Any, **kwargs: Any) -> str:
        seen.append((address, args, kwargs))
        return "sock"

    monkeypatch.setattr(socket, "create_connection", _record)
    module = mssql_tds._RoutingSocketModule()
    result: Any = module.create_connection(("r.example\\inst-dw", 1433), 5)
    assert result == "sock"
    assert seen == [(("r.example", 1433), (5,), {})]
    # Everything else is the real socket module.
    assert module.SOL_TCP == socket.SOL_TCP
    assert module.timeout is socket.timeout


def test_install_patches_pytds_once() -> None:
    import pytds
    import pytds.tls

    mssql_tds.install()
    mssql_tds.install()
    assert pytds.tls.validate_host is mssql_tds.validate_host
    assert isinstance(pytds.socket, mssql_tds._RoutingSocketModule)


def test_install_refuses_an_unrecognised_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pytds.tls

    monkeypatch.setattr(mssql_tds, "_installed", threading.Event())
    monkeypatch.delattr(pytds.tls, "validate_host")
    with pytest.raises(RuntimeError, match="re-verify ADR 0044"):
        mssql_tds.install()
