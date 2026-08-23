"""A throwaway SMTP sink for the email-OTP E2E lane (ADR 0032, #736).

Does real STARTTLS with a self-signed cert (export AUTH_EMAIL_CA_BUNDLE=<printed
path> for the api) so the genuine mailer path is exercised, never mocked. Binds
loopback only, accepts any credentials, serves captured mail in the clear — the
point of the tool, and why it must never run anywhere but a test host (present in
the runtime image but never invoked by anything).

Usage::

    python -m backend.scripts.e2e_otp_smtp_sink --smtp-port 1025 --http-port 1080

HTTP API::

    GET    /code?email=<addr>   -> {"code": "123456"} | 404 when nothing captured
    GET    /messages            -> every captured message
    DELETE /messages            -> drop them all (per-spec isolation)
    GET    /healthz             -> {"status": "ok"} once both listeners are up
"""

from __future__ import annotations

import argparse
import datetime as dt
import email
import http.server
import ipaddress
import json
import re
import socketserver
import ssl
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

#: The shape of the code DataQ mails (`otp_service` mints 6 digits).
_CODE_RE = re.compile(r"\b(\d{6})\b")

_LOCALHOST = "127.0.0.1"


@dataclass
class Captured:
    recipients: list[str]
    subject: str
    body: str
    code: str | None


@dataclass
class Mailbox:
    """Every message the sink has accepted, newest last. Guarded by a lock —
    the SMTP side runs on connection threads and the HTTP side on its own.
    """

    messages: list[Captured] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, message: Captured) -> None:
        with self._lock:
            self.messages.append(message)

    def all(self) -> list[Captured]:
        with self._lock:
            return list(self.messages)

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()

    def latest_code_for(self, address: str) -> str | None:
        """The most recent code sent to `address`, or None."""
        wanted = address.strip().lower()
        with self._lock:
            for message in reversed(self.messages):
                if message.code and any(r.strip().lower() == wanted for r in message.recipients):
                    return message.code
        return None


def make_self_signed_cert(directory: Path, hostname: str = "localhost") -> tuple[Path, Path]:
    """Emit a throwaway cert+key for `hostname`. Returns (cert_path, key_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            # smtplib passes server_hostname, so the SAN — not the CN — is what Python's hostname
            # check actually consults.
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(hostname),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "sink-cert.pem"
    key_path = directory / "sink-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


class SmtpHandler(socketserver.StreamRequestHandler):
    """One SMTP conversation: EHLO → STARTTLS → AUTH → MAIL/RCPT/DATA → QUIT."""

    mailbox: Mailbox
    tls_context: ssl.SSLContext

    def _send(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode())
        self.wfile.flush()

    def _ehlo(self, secure: bool) -> None:
        self._send("250-localhost")
        # AUTH is advertised only after STARTTLS, matching a real submission
        # server — and smtplib refuses plaintext AUTH by default anyway.
        if secure:
            self._send("250-STARTTLS")
            self._send("250 AUTH PLAIN")
        else:
            self._send("250 STARTTLS")

    def handle(self) -> None:
        self._send("220 localhost DataQ E2E sink")
        secure = False
        recipients: list[str] = []

        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            upper = line.upper()

            if upper.startswith(("EHLO", "HELO")):
                self._ehlo(secure)
            elif upper == "STARTTLS":
                self._send("220 Ready to start TLS")
                self.connection = self.tls_context.wrap_socket(self.connection, server_side=True)
                # Rebind the buffered files onto the upgraded socket, or every
                # later read comes off the stale plaintext stream.
                self.rfile = self.connection.makefile("rb", -1)
                self.wfile = self.connection.makefile("wb", 0)
                secure = True
            elif upper.startswith("AUTH"):
                # Any credentials are accepted; the sink is not an authenticator.
                self._send("235 2.7.0 Authentication successful")
            elif upper.startswith("MAIL FROM"):
                # The envelope sender is accepted and discarded — specs assert on
                # the recipient and the code, never on who it came from.
                self._send("250 2.1.0 Ok")
            elif upper.startswith("RCPT TO"):
                recipients.append(_strip_angles(line.partition(":")[2].strip()))
                self._send("250 2.1.5 Ok")
            elif upper == "DATA":
                self._send("354 End data with <CR><LF>.<CR><LF>")
                self._capture(recipients, self._read_data())
                self._send("250 2.0.0 Ok: queued")
                recipients = []
            elif upper == "RSET":
                recipients = []
                self._send("250 2.0.0 Ok")
            elif upper == "QUIT":
                self._send("221 2.0.0 Bye")
                return
            elif upper == "NOOP":
                self._send("250 2.0.0 Ok")
            else:
                self._send("502 5.5.2 Command not implemented")

    def _read_data(self) -> str:
        lines: list[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace")
            if line.rstrip("\r\n") == ".":
                break
            # Undo SMTP dot-stuffing.
            lines.append(line[1:] if line.startswith("..") else line)
        return "".join(lines)

    def _capture(self, recipients: list[str], data: str) -> None:
        parsed: Message = email.message_from_string(data)
        body = _plain_text_of(parsed)
        match = _CODE_RE.search(body)
        self.mailbox.add(
            Captured(
                recipients=list(recipients),
                subject=str(parsed.get("Subject", "")),
                body=body,
                code=match.group(1) if match else None,
            )
        )


def _strip_angles(value: str) -> str:
    return value[1:-1] if value.startswith("<") and value.endswith(">") else value


def _plain_text_of(message: Message) -> str:
    if not message.is_multipart():
        payload = message.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(message.get_content_charset() or "utf-8", "replace")
        return str(message.get_payload())
    parts = [_plain_text_of(part) for part in message.get_payload() if isinstance(part, Message)]
    return "\n".join(parts)


class ThreadedTcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_http_handler(mailbox: Mailbox) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # The Playwright spec fetches this from a page origin on another port.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/healthz":
                self._json(200, {"status": "ok"})
            elif parsed.path == "/messages":
                self._json(
                    200,
                    [
                        {"to": m.recipients, "subject": m.subject, "code": m.code, "body": m.body}
                        for m in mailbox.all()
                    ],
                )
            elif parsed.path == "/code":
                address = urllib.parse.parse_qs(parsed.query).get("email", [""])[0]
                code = mailbox.latest_code_for(address)
                if code is None:
                    # 404, not `{"code": null}` — a spec that polls must be able to tell "not
                    # delivered yet" from "delivered, but unparseable".
                    self._json(404, {"error": "no code captured for that address"})
                else:
                    self._json(200, {"code": code})
            else:
                self._json(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            if urllib.parse.urlparse(self.path).path == "/messages":
                mailbox.clear()
                self._json(200, {"status": "cleared"})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *_args: object) -> None:
            """Silence the per-request access log — it would bury the one line
            the caller actually needs (the certificate path).
            """

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smtp-port", type=int, default=1025)
    parser.add_argument("--http-port", type=int, default=1080)
    parser.add_argument(
        "--cert-dir",
        default=None,
        help="Where to write the throwaway cert+key (default: a fresh temp dir).",
    )
    args = parser.parse_args(argv)

    cert_dir = Path(args.cert_dir) if args.cert_dir else Path(tempfile.mkdtemp(prefix="dataq-otp-"))
    cert_path, key_path = make_self_signed_cert(cert_dir)

    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

    mailbox = Mailbox()
    handler = type(
        "BoundSmtpHandler", (SmtpHandler,), {"mailbox": mailbox, "tls_context": tls_context}
    )
    smtp_server = ThreadedTcpServer((_LOCALHOST, args.smtp_port), handler)
    http_server = http.server.ThreadingHTTPServer(
        (_LOCALHOST, args.http_port), make_http_handler(mailbox)
    )

    threading.Thread(target=smtp_server.serve_forever, daemon=True).start()

    # Machine-readable, because the caller has to export AUTH_EMAIL_CA_BUNDLE from it BEFORE
    # starting the API — `Settings` validates the path exists at boot (#1146).
    print(f"DATAQ_OTP_SINK_CERT={cert_path}", flush=True)
    print(f"DATAQ_OTP_SINK_KEY={key_path}", flush=True)
    print(
        f"sink listening: smtp={_LOCALHOST}:{args.smtp_port} http={_LOCALHOST}:{args.http_port}",
        flush=True,
    )

    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        smtp_server.shutdown()
        http_server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
