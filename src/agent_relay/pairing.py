"""Spoken-code LAN pairing. Nothing secret is ever written to a file for transfer.

Alice runs ``peer-relay pair`` and reads a short code aloud (``7-orbit-lantern``).
Bob runs ``peer-relay join`` and types it. The leading number is a public channel
that only selects Alice's mDNS advertisement on a busy network; the two words are
the password for a SPAKE2 exchange. A wrong guess burns one of three attempts and
reveals nothing that allows offline guessing. After key confirmation, both screens
show the same six-digit check number. Bob generates his own responder token and
sends it encrypted; Alice stores only its SHA-256 and pins nothing but her own
certificate, which Bob pins in turn.
"""

import hashlib
import hmac
import json
import re
import secrets
import socket
import struct
import threading
import time
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B

SERVICE = "_peerrelay._tcp.local."
VERSION = 1
MAX_FRAME = 65536
ATTEMPTS = 3
WINDOW = 300
HUMAN_WAIT = 300
CHANNELS = range(2, 100)
SALT = b"peerrelay-pair-v1"

WORDS = (
    "acorn adobe agent alarm album alpine amber anchor angle apple apron arrow aspen atlas "
    "attic autumn badge bagel bamboo banjo barley basil beacon beaver berry bicycle bison "
    "blanket blossom bonfire border breeze brick bridge bronze bucket buffalo bugle butter "
    "cabin cactus camel candle canyon captain carbon carpet castle cedar cello cherry "
    "chimney cider cinema circus citrus clover cobalt coconut comet compass copper coral "
    "cotton cougar crayon cricket crystal cupcake cushion dahlia dancer delta desert diamond "
    "dinner dolphin domino dragon dynamo eagle easel echo eclipse elbow ember emerald engine "
    "falcon fennel ferry fiddle finch flame flute forest fossil fountain galaxy garden "
    "garlic gazelle geyser ginger glacier goblet gopher granite guitar hammer harbor harvest "
    "hazel helmet hermit hickory honey horizon husky igloo indigo island ivory jacket jaguar "
    "jasmine jelly jigsaw jungle kayak kernel kettle kiwi koala ladder lagoon lantern laptop "
    "lemon lilac lobster locket lotus magnet mango maple marble meadow melon meteor mitten "
    "monsoon mosaic muffin nectar needle nickel noodle nutmeg oasis ocean olive onion opal "
    "orbit orchid otter oyster paddle panda papaya parrot peanut pebble pepper piano pickle "
    "pillow pilot planet plum pocket polar pony poppy potato prism pumpkin puzzle quartz "
    "quill rabbit radar radish raven ribbon river robin rocket saddle salmon sapphire satin "
    "scarf signal silver sketch sparrow spider spruce squash statue summit sunset swan "
    "tablet tango teapot thistle thunder tiger timber tomato topaz tulip tunnel turtle "
    "umbrella valley velvet violet violin walnut walrus wagon willow window winter wizard "
    "yogurt zebra zipper acrobat anvil beetle cobra daisy feather gravel iris lily marlin "
    "nugget pretzel sequoia tundra"
).split()
WORD_SET = frozenset(WORDS)
assert len(WORDS) == 256 and len(WORD_SET) == 256


class PairingFailed(Exception):
    pass


class WrongCode(PairingFailed):
    pass


def new_code(taken=()):
    free = [c for c in CHANNELS if c not in set(taken)] or list(CHANNELS)
    return f"{secrets.choice(free)}-{secrets.choice(WORDS)}-{secrets.choice(WORDS)}"


def parse_code(text):
    parts = [p for p in re.split(r"[\s-]+", text.strip().lower()) if p]
    if (
        len(parts) != 3
        or not parts[0].isdigit()
        or int(parts[0]) not in CHANNELS
        or parts[1] not in WORD_SET
        or parts[2] not in WORD_SET
    ):
        raise ValueError("A pairing code looks like 7-orbit-lantern")
    channel = int(parts[0])
    return channel, f"{channel}-{parts[1]}-{parts[2]}"


def display_name(value, limit=64):
    # Peer-supplied text reaches a terminal: refuse escapes and control characters.
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.isprintable():
        raise PairingFailed("Peer sent an invalid name")
    return value


def default_name():
    return socket.gethostname().removesuffix(".local")[:64] or "PeerRelay device"


def local_address():
    """The LAN address this machine would use for outbound traffic (sends no packets)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def check_number(sas):
    value = int.from_bytes(sas, "big") % 1_000_000
    return f"{value // 1000:03d} {value % 1000:03d}"


class Channel:
    """Length-prefixed frames; AES-GCM with per-direction keys after key confirmation."""

    def __init__(self, sock):
        self.sock = sock
        self.send_key = self.recv_key = None
        self.sent = self.received = 0

    def _exact(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise PairingFailed("The other device closed the connection")
            data.extend(chunk)
        return bytes(data)

    def send(self, data):
        if len(data) > MAX_FRAME:
            raise PairingFailed("Pairing message too large")
        self.sock.sendall(struct.pack(">I", len(data)) + data)

    def recv(self):
        (size,) = struct.unpack(">I", self._exact(4))
        if size > MAX_FRAME:
            raise PairingFailed("Pairing message too large")
        return self._exact(size)

    def secure(self, send_key, recv_key):
        self.send_key, self.recv_key = AESGCM(send_key), AESGCM(recv_key)

    def send_json(self, value):
        nonce = self.sent.to_bytes(12, "big")
        self.sent += 1
        self.send(self.send_key.encrypt(nonce, json.dumps(value).encode(), SALT))

    def recv_json(self):
        nonce = self.received.to_bytes(12, "big")
        self.received += 1
        try:
            value = json.loads(self.recv_key.decrypt(nonce, self.recv(), SALT))
        except Exception as error:
            raise PairingFailed("Pairing message failed authentication") from error
        if not isinstance(value, dict):
            raise PairingFailed("Malformed pairing message")
        return value


def derive(key):
    material = HKDF(hashes.SHA256(), 32 * 4 + 8, SALT, b"keys").derive(key)
    return {
        "confirm_host": material[0:32],
        "confirm_join": material[32:64],
        "host_to_join": material[64:96],
        "join_to_host": material[96:128],
        "check": check_number(material[128:136]),
    }


def _mac(key, transcript):
    return hmac.new(key, transcript, hashlib.sha256).digest()


def host_session(conn, code, offer, confirm, commit):
    """Alice's side of one attempt. Raises WrongCode once a SPAKE2 attempt was spent."""
    channel = Channel(conn)
    join_message = channel.recv()
    try:
        exchange = SPAKE2_A(code.encode(), idA=b"peerrelay-host", idB=b"peerrelay-join")
        host_message = exchange.start()
        channel.send(host_message)
        keys = derive(exchange.finish(join_message))
        transcript = host_message + join_message
        channel.send(_mac(keys["confirm_host"], transcript))
        if not hmac.compare_digest(channel.recv(), _mac(keys["confirm_join"], transcript)):
            raise WrongCode("Key confirmation failed")
    except WrongCode:
        raise
    except Exception as error:
        raise WrongCode("Pairing attempt failed before key confirmation") from error
    channel.secure(keys["host_to_join"], keys["join_to_host"])
    channel.send_json({"type": "offer", "v": VERSION, **offer})
    conn.settimeout(HUMAN_WAIT)
    reply = channel.recv_json()
    if reply.get("accept") is not True:
        raise PairingFailed("The other person declined this pairing")
    device = display_name(reply.get("device_name"))
    token = reply.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,200}", token):
        raise PairingFailed("Responder sent an invalid credential")
    peer = {
        "device": device,
        "address": conn.getpeername()[0],
        "check": keys["check"],
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    }
    if not confirm(peer):
        channel.send_json({"ok": False})
        raise PairingFailed("You declined this pairing")
    try:
        commit(peer)
    except BaseException:
        channel.send_json({"ok": False})
        raise
    channel.send_json({"ok": True})
    return peer


def host(listener, code, offer, confirm, commit, log=print, window=WINDOW):
    deadline = time.monotonic() + window
    attempts = 0
    while attempts < ATTEMPTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        listener.settimeout(remaining)
        try:
            conn, address = listener.accept()
        except (socket.timeout, TimeoutError):
            break
        with conn:
            conn.settimeout(30)
            try:
                return host_session(conn, code, offer, confirm, commit)
            except WrongCode:
                attempts += 1
                log(f"  ✗ {address[0]} tried a wrong code ({attempts}/{ATTEMPTS} attempts used)")
            except (OSError, socket.timeout) as error:
                log(f"  ! Connection from {address[0]} dropped: {type(error).__name__}")
    if attempts >= ATTEMPTS:
        raise PairingFailed("Too many wrong codes. Run `peer-relay pair` again for a new code.")
    raise PairingFailed("The pairing code expired. Run `peer-relay pair` again.")


def validate_offer(offer):
    if offer.get("type") != "offer" or offer.get("v") != VERSION:
        raise PairingFailed("Incompatible PeerRelay version on the other device")
    display_name(offer.get("host_name"))
    display_name(offer.get("project"), 128)
    display_name(offer.get("user"))
    if offer.get("grant") not in ("ro", "rw", "exec"):
        raise PairingFailed("Unknown access level offered")
    if offer.get("grant") == "exec" and offer.get("execution_backend") not in ("docker", "host"):
        raise PairingFailed("Unknown execution backend offered; update both clients")
    port = offer.get("relay_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise PairingFailed("Invalid relay port offered")
    pem = offer.get("certificate")
    if not isinstance(pem, str) or len(pem) > 16384:
        raise PairingFailed("Invalid relay certificate")
    try:
        x509.load_pem_x509_certificate(pem.encode())
    except ValueError as error:
        raise PairingFailed("Invalid relay certificate") from error
    return offer


def join(address, code, device_name, token, confirm, timeout=30):
    """Bob's side. Returns Alice's validated offer once she has stored the pairing."""
    try:
        sock = socket.create_connection(address, timeout=timeout)
    except OSError as error:
        raise PairingFailed(f"Could not reach {address[0]}:{address[1]}") from error
    with sock:
        channel = Channel(sock)
        exchange = SPAKE2_B(code.encode(), idA=b"peerrelay-host", idB=b"peerrelay-join")
        join_message = exchange.start()
        channel.send(join_message)
        host_message = channel.recv()
        try:
            keys = derive(exchange.finish(host_message))
        except Exception as error:
            raise WrongCode("Wrong code, or this is not the right device") from error
        transcript = host_message + join_message
        if not hmac.compare_digest(channel.recv(), _mac(keys["confirm_host"], transcript)):
            raise WrongCode("Wrong code, or this is not the right device")
        channel.send(_mac(keys["confirm_join"], transcript))
        channel.secure(keys["join_to_host"], keys["host_to_join"])
        offer = validate_offer(channel.recv_json())
        if not confirm(offer, keys["check"]):
            channel.send_json({"accept": False})
            raise PairingFailed("You declined this pairing")
        channel.send_json({"accept": True, "device_name": device_name, "token": token})
        sock.settimeout(HUMAN_WAIT)
        if channel.recv_json().get("ok") is not True:
            raise PairingFailed("The other person declined this pairing")
        return offer


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- mDNS discovery (advertised only while a pairing window is open) ---


class Advertisement:
    def __init__(self, address, port, channel, name):
        from zeroconf import ServiceInfo, Zeroconf

        self.zeroconf = Zeroconf()
        self.info = ServiceInfo(
            SERVICE,
            f"PeerRelay {secrets.token_hex(3)}.{SERVICE}",
            addresses=[socket.inet_aton(address)],
            port=port,
            properties={"v": str(VERSION), "ch": str(channel), "name": name},
        )
        try:
            # Instance names are random and collisions are renamed, so two people
            # pairing on the same network never block each other.
            self.zeroconf.register_service(self.info, allow_name_change=True)
        except BaseException:
            self.zeroconf.close()
            raise

    def close(self):
        try:
            self.zeroconf.unregister_service(self.info)
        finally:
            self.zeroconf.close()


def discover(seconds=4.0, channel=None):
    """Find open pairing windows. Results are untrusted hints; SPAKE2 authenticates."""
    from zeroconf import IPVersion, ServiceBrowser, Zeroconf

    found = {}
    lock = threading.Lock()

    class Listener:
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, timeout=1500)
            if info is None:
                return
            props = {
                k.decode(errors="replace"): (v or b"").decode(errors="replace")
                for k, v in info.properties.items()
            }
            try:
                entry = {
                    "name": display_name(props.get("name", "")),
                    "channel": int(props.get("ch", "")),
                    "address": info.parsed_addresses(IPVersion.V4Only)[0],
                    "port": info.port,
                }
            except (PairingFailed, ValueError, IndexError):
                return
            if props.get("v") == str(VERSION) and entry["channel"] in CHANNELS:
                with lock:
                    found[name] = entry

        update_service = add_service

        def remove_service(self, zc, kind, name):
            with lock:
                found.pop(name, None)

    zeroconf = Zeroconf()
    try:
        browser = ServiceBrowser(zeroconf, SERVICE, Listener())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with lock:
                if channel is not None and any(e["channel"] == channel for e in found.values()):
                    break
            time.sleep(0.1)
        browser.cancel()
    finally:
        zeroconf.close()
    with lock:
        entries = list(found.values())
    return [e for e in entries if channel is None or e["channel"] == channel]
