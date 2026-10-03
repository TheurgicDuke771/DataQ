"""Generate the local stack's certificate authority and the certificates it signs.

Run by the `local-ca` one-shot of `docker-compose.ghcr.yml`, writing into named volumes:

    python -m backend.scripts.local_ca /certs /ca-key --service postgres:/tls/postgres:70

`/certs` gets the CA certificate, which is public and every client mounts. The `localhost`
certificate the UI serves goes to `--localhost-dir` (default: beside the CA). Each
`--service NAME:DIR:UID` gets a certificate for the hostname NAME in DIR, its key owned by
UID (the user that service runs as), so one service cannot read another's key.

Idempotent: an existing CA is kept (so a trusted CA stays trusted across restarts), and a
leaf is re-issued only when it is missing, close to expiry, for other names, or signed by a
different CA. The CA
private key goes to its own directory, so a service that mounts the certificates to serve TLS
is never handed the key that could mint more of them.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_NAME = "DataQ Local Dev CA"
CA_VALID = timedelta(days=3650)
# Under the 398 days browsers accept for a leaf, including under a locally trusted CA.
LEAF_VALID = timedelta(days=365)
LEAF_RENEW_WITHIN = timedelta(days=30)

_DNS_NAMES = ("localhost",)
_IP_ADDRESSES = ("127.0.0.1", "::1")


class Service(NamedTuple):
    """A server inside the stack: reached by `name`, running as `uid`."""

    name: str
    directory: Path
    uid: int


def _write(path: Path, data: bytes, mode: int) -> None:
    """Create with the final mode rather than chmod after, so a key is never briefly readable."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _new_ca(now: datetime) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(CA_NAME))
        .issuer_name(_name(CA_NAME))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + CA_VALID)
        # path_length=0: this CA signs leaves only, never another CA.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _new_leaf(
    ca: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    now: datetime,
    dns_names: tuple[str, ...],
    ip_addresses: tuple[str, ...] = (),
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_addresses]
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(dns_names[0]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + LEAF_VALID)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return cert, key


def _leaf_is_current(
    leaf_path: Path,
    key_path: Path,
    ca: x509.Certificate,
    now: datetime,
    dns_names: tuple[str, ...],
) -> bool:
    if not (leaf_path.is_file() and key_path.is_file()):
        return False
    try:
        leaf = x509.load_pem_x509_certificate(leaf_path.read_bytes())
        leaf.verify_directly_issued_by(ca)
        sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:
        return False
    if tuple(sans.get_values_for_type(x509.DNSName)) != dns_names:
        return False
    return leaf.not_valid_after_utc - now > LEAF_RENEW_WITHIN


def _ensure_leaf(
    cert_path: Path,
    key_path: Path,
    ca: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    now: datetime,
    dns_names: tuple[str, ...],
    ip_addresses: tuple[str, ...] = (),
) -> str:
    if _leaf_is_current(cert_path, key_path, ca, now, dns_names):
        return "kept"
    leaf, leaf_key = _new_leaf(ca, ca_key, now, dns_names, ip_addresses)
    _write(key_path, _key_pem(leaf_key), 0o600)
    _write(cert_path, leaf.public_bytes(serialization.Encoding.PEM), 0o644)
    return "issued"


def ensure(
    directory: Path,
    ca_key_dir: Path,
    *,
    localhost_dir: Path | None = None,
    services: tuple[Service, ...] = (),
    now: datetime | None = None,
) -> dict[str, str]:
    """Make `directory` hold the CA certificate and a current `localhost` leaf, with the CA
    private key in `ca_key_dir`, and give each of `services` its own certificate. Returns
    what was done, keyed `ca`, `leaf` and each service name.
    """
    now = now or datetime.now(UTC)
    directory.mkdir(parents=True, exist_ok=True)
    ca_key_dir.mkdir(parents=True, exist_ok=True)
    ca_path, ca_key_path = directory / "ca.pem", ca_key_dir / "ca.key"
    ui_dir = localhost_dir or directory
    ui_dir.mkdir(parents=True, exist_ok=True)
    leaf_path, leaf_key_path = ui_dir / "localhost.pem", ui_dir / "localhost.key"
    if ui_dir != directory:
        # An earlier layout kept the UI's pair beside the CA; a key must not linger where
        # every client now mounts.
        for stale in (directory / "localhost.pem", directory / "localhost.key"):
            stale.unlink(missing_ok=True)

    if ca_path.is_file() and ca_key_path.is_file():
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        loaded = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        if not isinstance(loaded, ec.EllipticCurvePrivateKey):
            raise SystemExit(f"{ca_key_path} is not the key this script generates; remove it")
        ca_key, ca_action = loaded, "kept"
    else:
        ca, ca_key = _new_ca(now)
        _write(ca_key_path, _key_pem(ca_key), 0o600)
        _write(ca_path, ca.public_bytes(serialization.Encoding.PEM), 0o644)
        ca_action = "created"

    result = {
        "ca": ca_action,
        "leaf": _ensure_leaf(leaf_path, leaf_key_path, ca, ca_key, now, _DNS_NAMES, _IP_ADDRESSES),
    }
    for service in services:
        service.directory.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = service.directory / "cert.pem", service.directory / "key.pem"
        result[service.name] = _ensure_leaf(cert_path, key_path, ca, ca_key, now, (service.name,))
        # Every run, not only on issue: the key must end up readable by that service alone.
        if os.geteuid() == 0:
            os.chown(key_path, service.uid, -1)
    return result


def _service(spec: str) -> Service:
    name, directory, uid = spec.split(":")
    return Service(name, Path(directory), int(uid))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m backend.scripts.local_ca")
    parser.add_argument("cert_dir", type=Path)
    parser.add_argument("ca_key_dir", type=Path)
    parser.add_argument("--localhost-dir", type=Path, default=None)
    parser.add_argument("--service", action="append", default=[], type=_service)
    args = parser.parse_args()
    done = ensure(
        args.cert_dir,
        args.ca_key_dir,
        localhost_dir=args.localhost_dir,
        services=tuple(args.service),
    )
    print(f"Local CA ({CA_NAME}): " + " ".join(f"{k}={v}" for k, v in done.items()))
