"""The local stack's CA and `localhost` certificate (#2327)."""

from __future__ import annotations

import ipaddress
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature

from backend.scripts import local_ca


def _ensure(directory: Path, **kwargs: Any) -> dict[str, str]:
    return local_ca.ensure(directory / "certs", directory / "ca-key", **kwargs)


def _cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_the_leaf_is_for_localhost_and_issued_by_the_ca(tmp_path: Path) -> None:
    assert _ensure(tmp_path) == {"ca": "created", "leaf": "issued"}

    ca, leaf = _cert(tmp_path / "certs" / "ca.pem"), _cert(tmp_path / "certs" / "localhost.pem")
    leaf.verify_directly_issued_by(ca)
    sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.DNSName) == ["localhost"]
    assert set(sans.get_values_for_type(x509.IPAddress)) == {
        ipaddress.ip_address("127.0.0.1"),
        ipaddress.ip_address("::1"),
    }
    assert leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc < timedelta(days=398)


def test_the_ca_can_sign_leaves_but_not_other_cas(tmp_path: Path) -> None:
    _ensure(tmp_path)
    ca = _cert(tmp_path / "certs" / "ca.pem")
    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical
    assert constraints.value.ca is True
    assert constraints.value.path_length == 0
    assert ca.subject.rfc4514_string() == f"CN={local_ca.CA_NAME}"


def test_private_keys_are_owner_only(tmp_path: Path) -> None:
    _ensure(tmp_path)
    for key in (tmp_path / "ca-key" / "ca.key", tmp_path / "certs" / "localhost.key"):
        assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_the_ca_key_is_not_beside_the_certificates(tmp_path: Path) -> None:
    """Whoever mounts the certificate directory to serve TLS must not get the CA key."""
    _ensure(tmp_path)
    assert sorted(p.name for p in (tmp_path / "certs").iterdir()) == [
        "ca.pem",
        "localhost.key",
        "localhost.pem",
    ]


def test_a_second_run_keeps_the_ca_and_the_leaf(tmp_path: Path) -> None:
    _ensure(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert _ensure(tmp_path) == {"ca": "kept", "leaf": "kept"}
    assert {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_a_leaf_close_to_expiry_is_reissued_under_the_same_ca(tmp_path: Path) -> None:
    _ensure(tmp_path)
    ca_before = (tmp_path / "certs" / "ca.pem").read_bytes()
    later = datetime.now(UTC) + local_ca.LEAF_VALID - timedelta(days=5)

    assert _ensure(tmp_path, now=later) == {"ca": "kept", "leaf": "issued"}
    assert (tmp_path / "certs" / "ca.pem").read_bytes() == ca_before
    _cert(tmp_path / "certs" / "localhost.pem").verify_directly_issued_by(
        _cert(tmp_path / "certs" / "ca.pem")
    )


def test_a_leaf_from_another_ca_is_replaced(tmp_path: Path) -> None:
    other = tmp_path / "other"
    _ensure(other)
    _ensure(tmp_path)
    (tmp_path / "certs" / "localhost.pem").write_bytes(
        (other / "certs" / "localhost.pem").read_bytes()
    )
    with pytest.raises(InvalidSignature):
        _cert(tmp_path / "certs" / "localhost.pem").verify_directly_issued_by(
            _cert(tmp_path / "certs" / "ca.pem")
        )

    assert _ensure(tmp_path) == {"ca": "kept", "leaf": "issued"}
    _cert(tmp_path / "certs" / "localhost.pem").verify_directly_issued_by(
        _cert(tmp_path / "certs" / "ca.pem")
    )


def _services(tmp_path: Path) -> tuple[local_ca.Service, ...]:
    return (
        local_ca.Service("postgres", tmp_path / "tls" / "postgres", 70),
        local_ca.Service("redis", tmp_path / "tls" / "redis", 999),
    )


def test_each_service_gets_a_certificate_for_its_own_hostname(tmp_path: Path) -> None:
    done = _ensure(tmp_path, services=_services(tmp_path))
    assert done == {"ca": "created", "leaf": "issued", "postgres": "issued", "redis": "issued"}

    ca = _cert(tmp_path / "certs" / "ca.pem")
    for name in ("postgres", "redis"):
        leaf = _cert(tmp_path / "tls" / name / "cert.pem")
        leaf.verify_directly_issued_by(ca)
        sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        # Its own name only: a certificate valid for a sibling would let one service pose as it.
        assert sans.get_values_for_type(x509.DNSName) == [name]
        assert sans.get_values_for_type(x509.IPAddress) == []
        key = tmp_path / "tls" / name / "key.pem"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_service_certificates_are_kept_on_a_second_run(tmp_path: Path) -> None:
    _ensure(tmp_path, services=_services(tmp_path))
    done = _ensure(tmp_path, services=_services(tmp_path))
    assert done == {"ca": "kept", "leaf": "kept", "postgres": "kept", "redis": "kept"}


def test_a_certificate_for_another_name_is_reissued(tmp_path: Path) -> None:
    """A volume that once belonged to a differently named service must not keep its certificate."""
    _ensure(tmp_path, services=(local_ca.Service("redis", tmp_path / "tls" / "x", 999),))
    done = _ensure(tmp_path, services=(local_ca.Service("postgres", tmp_path / "tls" / "x", 70),))
    assert done["postgres"] == "issued"


def test_the_service_argument_is_name_directory_uid() -> None:
    assert local_ca._service("postgres:/tls/postgres:70") == local_ca.Service(
        "postgres", Path("/tls/postgres"), 70
    )


def test_the_ui_pair_can_live_apart_from_the_public_ca(tmp_path: Path) -> None:
    """Every client mounts the CA directory, so the UI's private key must not be in it."""
    _ensure(tmp_path)  # the earlier layout: the pair beside the CA
    assert (tmp_path / "certs" / "localhost.key").exists()

    done = _ensure(tmp_path, localhost_dir=tmp_path / "ui")

    assert done["leaf"] == "issued"
    assert sorted(p.name for p in (tmp_path / "certs").iterdir()) == ["ca.pem"]
    _cert(tmp_path / "ui" / "localhost.pem").verify_directly_issued_by(
        _cert(tmp_path / "certs" / "ca.pem")
    )
    assert stat.S_IMODE((tmp_path / "ui" / "localhost.key").stat().st_mode) == 0o600
