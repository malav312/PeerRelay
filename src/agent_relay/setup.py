"""Create a fresh paired relay; never read provider login credentials."""

import ipaddress
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def initialize(root, directory, host, port):
    root = Path(root).resolve(strict=True)
    directory = Path(directory).absolute()
    if (
        not root.is_dir()
        or directory.resolve() != directory
        or root == directory
        or root in directory.parents
        or directory in root.parents
    ):
        raise ValueError("Pairing directory must be separate from the project and not symlinked")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or not 1 <= port <= 65535:
        raise ValueError("Invalid host or port")
    if directory.exists():
        raise ValueError("Use a new pairing directory")
    directory.mkdir(mode=0o700, parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PeerRelay private pair")])
    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert = certificate.public_bytes(serialization.Encoding.PEM).decode()
    write_private(directory / "gateway.crt", cert)
    write_private(
        directory / "gateway.key",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    for role in ("requester", "responder"):
        folder = directory / role
        folder.mkdir(mode=0o700)
        write_private(folder / (role + ".token"), secrets.token_urlsafe(48))
        write_private(folder / "gateway.crt", cert)
        write_private(
            folder / "client.json",
            json.dumps(
                {
                    "role": role,
                    "url": f"https://{host}:{port}",
                    "ca_file": "gateway.crt",
                    "token_file": role + ".token",
                },
                indent=2,
            ),
        )
    write_private(
        directory / "server.json",
        json.dumps(
            {
                "root": str(root),
                "state_dir": "state",
                "host": host,
                "port": port,
                "ca_file": "gateway.crt",
                "key_file": "gateway.key",
                "tokens": {
                    role: str(directory / role / (role + ".token"))
                    for role in ("requester", "responder")
                },
            },
            indent=2,
        ),
    )
    return {
        "server_config": str(directory / "server.json"),
        "requester_config": str(directory / "requester/client.json"),
        "responder_bundle": str(directory / "responder"),
    }
