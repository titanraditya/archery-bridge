"""
Relay: HP (browser) --WSS--> relay.py --UDP:5005--> konsumen (monitor.py / Unity)

Dipakai dua cara:
  python app.py     GUI (cara biasa) — GUI meng-embed kelas Relay di thread sendiri
  python relay.py   headless, output ke terminal seperti sebelumnya

Kelas Relay sengaja tidak tahu apa-apa soal Qt: komunikasi keluar lewat callback
on_packet / on_status, supaya sisi jaringan bisa diuji tanpa GUI.
"""

import asyncio
import json
import socket
import ssl
from pathlib import Path
from typing import Callable

from aiohttp import web, WSMsgType

HERE = Path(__file__).parent
STATIC = HERE / "static"
CERT = HERE / "cert.pem"
KEY = HERE / "key.pem"

HTTPS_PORT = 8443
UDP_DEST = ("127.0.0.1", 5005)

# Perintah yang boleh dikirim GUI ke HP. Whitelist, bukan pass-through.
ALLOWED_CMDS = {"center", "axis"}


def lan_ip() -> str:
    """IP LAN mesin ini (tanpa benar-benar mengirim paket)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---------------------------------------------------------------- sertifikat


def cert_ip() -> str | None:
    """IP LAN yang tertanam di SAN sertifikat, atau None kalau tidak terbaca."""
    if not CERT.exists():
        return None
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for addr in san.value.get_values_for_type(x509.IPAddress):
            if not addr.is_loopback:
                return str(addr)
    except Exception:
        return None
    return None


def cert_status(ip: str) -> tuple[bool, str]:
    """(siap_pakai, alasan). Pindah Wi-Fi mengubah IP LAN; sertifikat lama bikin
    browser HP menolak dengan error nama yang tidak cocok."""
    if not CERT.exists() or not KEY.exists():
        return False, "sertifikat belum dibuat"
    embedded = cert_ip()
    if embedded != ip:
        return False, f"sertifikat untuk IP {embedded or '?'}, sekarang {ip}"
    return True, f"cocok untuk {ip}"


def make_cert() -> None:
    """Buat ulang cert.pem + key.pem untuk IP LAN saat ini."""
    import make_cert as mc

    mc.main()


# ------------------------------------------------------------------- server


class Relay:
    """Server HTTPS + WebSocket yang meneruskan paket HP ke UDP.

    Isi paket tidak pernah diubah — byte yang diterima dari HP itu juga yang
    dikirim ke UDP. Callback hanya melihat salinannya.
    """

    def __init__(
        self,
        on_packet: Callable[[dict], None] | None = None,
        on_status: Callable[..., None] | None = None,
        https_port: int = HTTPS_PORT,
        udp_dest: tuple[str, int] = UDP_DEST,
    ) -> None:
        self.https_port = https_port
        self.udp_dest = udp_dest
        self._on_packet = on_packet or (lambda _: None)
        self._on_status = on_status or (lambda *a, **k: None)

        self.packets = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._phones: set[web.WebSocketResponse] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None

    # ---- dipanggil dari thread GUI ------------------------------------

    def url(self) -> str:
        return f"https://{lan_ip()}:{self.https_port}"

    def stop(self) -> None:
        """Minta server berhenti. Aman dipanggil dari thread lain."""
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def send_command(self, cmd: dict) -> None:
        """Kirim perintah ke semua HP yang terhubung. Aman dari thread lain."""
        if cmd.get("cmd") not in ALLOWED_CMDS:
            return
        if not self._loop:
            return
        payload = json.dumps(cmd)
        asyncio.run_coroutine_threadsafe(self._to_phones(payload), self._loop)

    # ---- internal (thread jaringan) -----------------------------------

    async def _to_phones(self, payload: str) -> None:
        for ws in list(self._phones):
            try:
                await ws.send_str(payload)
            except (ConnectionResetError, RuntimeError):
                self._phones.discard(ws)

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        self._phones.add(ws)
        self._on_status("phone", connected=True, remote=request.remote)

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                # Validasi ringan: pastikan JSON valid sebelum diteruskan.
                # Paket rusak lebih baik dibuang di sini daripada bikin
                # JsonUtility.FromJson di Unity melempar exception tiap frame.
                try:
                    packet = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue

                self.sock.sendto(msg.data.encode("utf-8"), self.udp_dest)
                self.packets += 1
                self._on_packet(packet)
        finally:
            self._phones.discard(ws)
            self._on_status("phone", connected=False)

        return ws

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", lambda _: web.FileResponse(STATIC / "phone.html"))
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_static("/static", STATIC)
        return app

    async def serve(self) -> None:
        """Jalan sampai stop() dipanggil. Error bind dilempar ke pemanggil."""
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)

        runner = web.AppRunner(self._app())
        await runner.setup()
        try:
            site = web.TCPSite(runner, "0.0.0.0", self.https_port, ssl_context=ctx)
            await site.start()
            self._on_status("listening", url=self.url(), udp=self.udp_dest)
            await self._stop.wait()
        finally:
            await runner.cleanup()
            self._loop = None
            self._stop = None
            self._on_status("stopped")

    def run(self) -> None:
        """Blocking. Dipakai thread jaringan di GUI, dan mode headless."""
        asyncio.run(self.serve())


# ---------------------------------------------------------- mode headless


def main() -> None:
    ip = lan_ip()
    ok, reason = cert_status(ip)
    if not ok:
        print(f"[cert] {reason} — membuat ulang…")
        make_cert()

    def on_status(kind: str, **f) -> None:
        if kind == "phone":
            print(
                f"\n[+] HP terhubung dari {f.get('remote')}"
                if f.get("connected")
                else "\n[-] HP terputus"
            )

    seen = 0

    def on_packet(_: dict) -> None:
        nonlocal seen
        seen += 1
        if seen % 60 == 0:
            print(f"\r    {seen} paket diteruskan", end="", flush=True)

    relay = Relay(on_packet=on_packet, on_status=on_status)

    print("=" * 52)
    print("  MOTUS BRIDGE — relay HP ke UDP (headless)")
    print("=" * 52)
    print(f"  Buka di HP : {relay.url()}")
    print(f"  UDP keluar : {UDP_DEST[0]}:{UDP_DEST[1]}")
    print()
    print("  HP akan menampilkan peringatan sertifikat.")
    print("  Pilih 'Advanced' lalu 'Proceed' — ini normal untuk")
    print("  sertifikat self-signed di jaringan lokal.")
    print()
    print("  Untuk versi GUI:  python app.py")
    print("=" * 52)

    relay.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nberhenti.")
