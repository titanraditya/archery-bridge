"""
Membuat cert.pem + key.pem self-signed untuk HTTPS lokal.

API sensor browser (DeviceOrientationEvent) hanya aktif di secure context,
jadi relay wajib HTTPS meskipun cuma di jaringan lokal.

    python make_cert.py
"""

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HERE = Path(__file__).parent


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ip = lan_ip()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
    now = datetime.datetime.now(datetime.timezone.utc)

    # SAN wajib berisi IP, bukan cuma CN — browser modern mengabaikan CN.
    san = x509.SubjectAlternativeName(
        [
            x509.IPAddress(ipaddress.ip_address(ip)),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.DNSName("localhost"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )

    (HERE / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (HERE / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"cert.pem & key.pem dibuat untuk IP {ip}")
    print("Jika IP LAN berubah (pindah Wi-Fi), jalankan ulang skrip ini.")


if __name__ == "__main__":
    main()
