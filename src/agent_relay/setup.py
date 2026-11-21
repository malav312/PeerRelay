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


def write_replace(path, value):
    """Atomically replace a private file (used when re-pairing changes server.json)."""
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    write_private(temporary, value)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def check_directories(root, directory):
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
    return root, directory


def check_endpoint(host, port):
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or not 1 <= port <= 65535:
        raise ValueError("Invalid host or port")


def certificate(host):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PeerRelay private pair")])
    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    now = datetime.now(timezone.utc)
    cert = (
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
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def client_config(role, url, token_file, pin=False):
    value = {"role": role, "url": url, "ca_file": "gateway.crt", "token_file": token_file}
    if pin:
        # The pinned self-signed certificate is the only trust anchor, so the host
        # name check adds nothing and would break pairing whenever DHCP moves Alice.
        value["pin"] = True
    return json.dumps(value, indent=2)


def initialize(root, directory, host, port, grant="ro"):
    """Legacy pairing: creates both tokens and a responder/ bundle to transfer."""
    if grant not in ("ro", "rw", "exec"):
        raise ValueError("Invalid project grant")
    root, directory = check_directories(root, directory)
    check_endpoint(host, port)
    if directory.exists():
        raise ValueError("Use a new pairing directory")
    directory.mkdir(mode=0o700, parents=True)
    cert, key = certificate(host)
    write_private(directory / "gateway.crt", cert)
    write_private(directory / "gateway.key", key)
    for role in ("requester", "responder"):
        folder = directory / role
        folder.mkdir(mode=0o700)
        write_private(folder / (role + ".token"), secrets.token_urlsafe(48))
        write_private(folder / "gateway.crt", cert)
        write_private(
            folder / "client.json",
            client_config(role, f"https://{host}:{port}", role + ".token"),
        )
    write_private(
        directory / "server.json",
        json.dumps(
            {
                "root": str(root),
                "grant": grant,
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


def initialize_host(root, directory, host, port, grant="ro"):
    """Spoken-code pairing: Alice's side only. The responder is added by `pair`."""
    if grant not in ("ro", "rw", "exec"):
        raise ValueError("Invalid project grant")
    root, directory = check_directories(root, directory)
    check_endpoint(host, port)
    if directory.exists():
        raise ValueError("Use a new pairing directory")
    directory.mkdir(mode=0o700, parents=True)
    cert, key = certificate(host)
    write_private(directory / "gateway.crt", cert)
    write_private(directory / "gateway.key", key)
    folder = directory / "requester"
    folder.mkdir(mode=0o700)
    write_private(folder / "requester.token", secrets.token_urlsafe(48))
    write_private(folder / "gateway.crt", cert)
    write_private(
        folder / "client.json",
        client_config("requester", f"https://{host}:{port}", "requester.token", pin=True),
    )
    write_private(
        directory / "server.json",
        json.dumps(
            {
                "root": str(root),
                "grant": grant,
                "state_dir": "state",
                "host": host,
                "port": port,
                "ca_file": "gateway.crt",
                "key_file": "gateway.key",
                "tokens": {"requester": str(folder / "requester.token")},
            },
            indent=2,
        ),
    )
    return directory / "server.json"
