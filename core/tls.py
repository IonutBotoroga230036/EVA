"""
HTTPS for other devices on your network (v0.2.5).

Why: browsers only allow the microphone on secure pages (HTTPS or localhost). Your phone opening
http://192.168.x.x:8001 gets no mic at all. So in network mode E.V.A. also serves HTTPS (port 8443)
with a certificate from her own small certificate authority, kept in data/tls/ (git-ignored).

    data/tls/eva-ca.crt     the CA certificate: install it on your phone once (public, safe to share)
    data/tls/eva-ca.key     the CA key: never leaves this PC
    data/tls/server.crt/key the certificate for this PC's LAN address, renewed automatically

Safety: the CA carries X.509 name constraints. It can only ever vouch for private network addresses
(10/8, 172.16/12, 192.168/16, 127/8) and localhost / *.local names. Even if its key leaked, it could
not impersonate google.com or your bank on the phone that trusts it.

The server certificate is renewed when it has under 30 days left or when your LAN address changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import socket
from pathlib import Path

from loguru import logger

DIR = Path("data/tls")
CA_CRT, CA_KEY = "eva-ca.crt", "eva-ca.key"
SRV_CRT, SRV_KEY = "server.crt", "server.key"
PRIVATE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
SERVER_DAYS = 397                     # browsers dislike longer-lived server certificates
RENEW_DAYS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write_key(path: Path, key) -> None:
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _write_crt(path: Path, crt) -> None:
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(crt.public_bytes(serialization.Encoding.PEM))


def _load(folder: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    crt = x509.load_pem_x509_certificate((folder / CA_CRT).read_bytes())
    key = serialization.load_pem_private_key((folder / CA_KEY).read_bytes(), password=None)
    return crt, key


def make_ca(folder: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "E.V.A. local CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "E.V.A. (this PC only)")])
    permitted = [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
    permitted += [x509.DNSName("localhost"), x509.DNSName("local")]
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(_now() - dt.timedelta(minutes=5)).not_valid_after(_now() + dt.timedelta(days=3650))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                        content_commitment=False, key_encipherment=False, data_encipherment=False,
                                        key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
           .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
           .sign(key, hashes.SHA256()))
    folder.mkdir(parents=True, exist_ok=True)
    _write_key(folder / CA_KEY, key)
    _write_crt(folder / CA_CRT, crt)
    logger.info(f"TLS: created E.V.A.'s local CA in {folder}")
    return crt, key


def names_for(ips: list[str]) -> tuple[list[str], list[str]]:
    """(IPs, DNS names) for the server certificate. Bare hostnames are left out: the CA may only name *.local."""
    host = socket.gethostname().split(".")[0].lower()
    ip_list = sorted({"127.0.0.1", *[i for i in ips if i]})
    return ip_list, ["localhost", f"{host}.local"]


def _needs_new(crt, ips: list[str], dns: list[str]) -> bool:
    from cryptography import x509
    try:
        san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return True
    have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    have_dns = set(san.get_values_for_type(x509.DNSName))
    expiring = crt.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_DAYS)
    return expiring or not set(ips) <= have_ips or not set(dns) <= have_dns


def ensure(ips: list[str], folder: Path | None = None) -> tuple[str, str]:
    """(server.crt path, server.key path), creating or renewing as needed."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    folder = folder or DIR
    try:
        ca_crt, ca_key = _load(folder)
    except (OSError, ValueError):
        ca_crt, ca_key = make_ca(folder)
    ip_list, dns = names_for(ips)
    crt_path, key_path = folder / SRV_CRT, folder / SRV_KEY
    try:
        current = x509.load_pem_x509_certificate(crt_path.read_bytes())
        if not _needs_new(current, ip_list, dns) and key_path.exists():
            return str(crt_path), str(key_path)
    except (OSError, ValueError):
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.IPAddress(ipaddress.ip_address(i)) for i in ip_list] + [x509.DNSName(d) for d in dns]
    crt = (x509.CertificateBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip_list[-1])]))
           .issuer_name(ca_crt.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(_now() - dt.timedelta(minutes=5)).not_valid_after(_now() + dt.timedelta(days=SERVER_DAYS))
           .add_extension(x509.SubjectAlternativeName(san), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    _write_key(key_path, key)
    _write_crt(crt_path, crt)
    logger.info(f"TLS: new certificate for {', '.join(ip_list + dns)}")
    return str(crt_path), str(key_path)


def ca_path(folder: Path | None = None) -> Path:
    return (folder or DIR) / CA_CRT


def ca_fingerprint(folder: Path | None = None) -> str:
    """SHA-256 of the CA certificate, to compare with what the phone shows."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    crt = x509.load_pem_x509_certificate(ca_path(folder).read_bytes())
    digest = hashlib.sha256(crt.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, 16, 2)) + "..."
