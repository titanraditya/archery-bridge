"""
BleReader: busur fisik (IMU + IR) --BLE--> ble_reader.py --UDP:5005--> konsumen

Pengganti relay.py untuk jalur hardware. Antarmuka keluarnya dibuat sama persis
— run(), stop(), send_command(), callback on_packet/on_status — supaya GUI
tidak perlu tahu bedanya (lihat ui.py).

Dipakai dua cara:
    python app_ble.py     GUI
    python ble_reader.py  headless, output ke terminal


Kontrak firmware (sisi busur)
-----------------------------
Notify lewat Nordic UART Service (NUS), karena hampir semua stack — ESP32,
nRF52, Arduino — sudah punya contohnya. Dua format diterima; parser memilih
berdasarkan byte pertama.

1. Biner, 12 byte little-endian `<4h H B B` — format yang dipakai firmware asli:

       qw,qx,qy,qz  int16   quaternion mentah x 32767 (ABSOLUT, belum dikalibrasi)
       draw         uint16  0..65535 -> 0.0..1.0
       flags        uint8   bit0 = anak panah terpasang (IR terhalang)
       seq          uint8   nomor urut, wrap 0-255 — hanya untuk deteksi drop

2. JSON satu baris — untuk firmware coba-coba yang belum tega pakai struct:

       {"q":[w,x,y,z], "draw":0.42, "nocked":true}
       {"q":[w,x,y,z], "draw":0.42, "shot":7}     <- kalau firmware mau hitung sendiri

Firmware sengaja dibiarkan bodoh: cukup kirim orientasi mentah + keadaan IR.
Tiga hal ini dikerjakan di sini, di dekat sumber data, persis seperti yang
dilakukan phone.html di jalur HP:

    * kalibrasi     q yang dikirim = ref^-1 x q_mentah
    * preset sumbu  AXIS_PRESETS, sama urutannya dengan phone.html
    * rising-edge   shot++ saat IR kembali terang (anak panah meninggalkan busur)

Jadi tombol Kalibrasi Tengah dan preset 0-3 di GUI tetap bekerja tanpa perlu
mem-flash ulang firmware, dan paket yang keluar identik dengan jalur HP.
"""

import asyncio
import json
import socket
import struct
import time
from typing import Callable

UDP_DEST = ("127.0.0.1", 5005)

# Nordic UART Service. TX = arah device -> PC (notify).
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

NAME_PREFIX = "MOTUS"  # awalan nama device yang dianggap busur kita
SCAN_S = 5.0
RETRY_S = 2.0
DROP_REPORT_S = 5.0

PACKET = struct.Struct("<4hHBB")

# Perintah yang boleh masuk. Whitelist, bukan pass-through.
ALLOWED_CMDS = {"center", "axis", "scan", "connect", "disconnect"}


# ------------------------------------------------------------- quaternion


def qmul(a, b):
    return (
        a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
        a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
        a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
        a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    )


def qconj(a):
    return (a[0], -a[1], -a[2], -a[3])


def qnorm(a):
    n = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2] + a[3] * a[3]) ** 0.5
    if n < 1e-9:
        return (1.0, 0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n, a[3] / n)


# Urutan dan isinya harus sama dengan AXIS_PRESETS di static/phone.html —
# preset yang dikonfirmasi lewat cube test di jalur HP harus tetap berlaku
# setelah pindah ke hardware.
AXIS_PRESETS = [
    lambda v: v,                                # 0: apa adanya
    lambda v: (v[0],  v[1],  v[3], -v[2]),      # 1
    lambda v: (v[0], -v[2],  v[1],  v[3]),      # 2
    lambda v: (v[0],  v[3],  v[2], -v[1]),      # 3
]


# ------------------------------------------------------------------ reader


class BleReader:
    """Membaca notify BLE, mengubahnya jadi paket kontrak, meneruskan ke UDP.

    Sama seperti Relay, kelas ini tidak tahu apa-apa soal Qt. Bedanya cuma
    satu: Relay meneruskan byte apa adanya karena HP sudah mengirim format
    final, sedangkan di sini paketnya dibentuk sendiri dari data mentah IMU.
    """

    def __init__(
        self,
        on_packet: Callable[[dict], None] | None = None,
        on_status: Callable[..., None] | None = None,
        udp_dest: tuple[str, int] = UDP_DEST,
        address: str | None = None,
        name_prefix: str = NAME_PREFIX,
        tx_uuid: str = NUS_TX,
        auto_connect: bool = True,
        rescan_s: float | None = None,
    ) -> None:
        self.udp_dest = udp_dest
        self.address = address
        self.name: str | None = None
        self.name_prefix = name_prefix.upper()
        self.tx_uuid = tx_uuid
        self.auto_connect = auto_connect
        # None = diam menunggu perintah setelah scan (GUI punya tombol Pindai).
        # Mode headless tidak punya tombol, jadi ia memindai ulang sendiri.
        self.rescan_s = rescan_s

        self._on_packet = on_packet or (lambda _: None)
        self._on_status = on_status or (lambda *a, **k: None)

        self.packets = 0
        self.dropped = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # keadaan olahan — semuanya hanya disentuh dari thread jaringan
        self.ref = (1.0, 0.0, 0.0, 0.0)
        self.axis = 0
        self.shot = 0
        self._raw_q = (1.0, 0.0, 0.0, 0.0)
        self._nocked = False
        self._seq: int | None = None
        self._drop_note = 0.0

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._changed: asyncio.Event | None = None  # ada perintah scan/connect
        self._dropped_conn: asyncio.Event | None = None

    # ---- dipanggil dari thread GUI ------------------------------------

    def stop(self) -> None:
        """Minta reader berhenti. Aman dipanggil dari thread lain."""
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def send_command(self, cmd: dict) -> None:
        """Perintah dari GUI. Aman dipanggil dari thread lain."""
        if cmd.get("cmd") not in ALLOWED_CMDS or not self._loop:
            return
        self._loop.call_soon_threadsafe(self._apply, dict(cmd))

    # ---- internal (thread jaringan) -----------------------------------

    def _status(self, kind: str, **fields) -> None:
        self._on_status(kind, **fields)

    def _apply(self, cmd: dict) -> None:
        name = cmd.get("cmd")
        if name == "center":
            # Titik nol = orientasi busur saat ini. Sama seperti center() di
            # phone.html, hanya pindah dari HP ke sini.
            self.ref = self._raw_q
        elif name == "axis":
            try:
                self.axis = int(cmd.get("value", 0)) % len(AXIS_PRESETS)
            except (TypeError, ValueError):
                pass
        elif name == "scan":
            self.address = None
            self.name = None
            self._wake()
        elif name == "connect":
            addr = cmd.get("address")
            if addr:
                self.address = addr
                self.name = cmd.get("name")
                self._wake()
        elif name == "disconnect":
            self.address = None
            self.name = None
            self._wake()

    def _wake(self) -> None:
        if self._changed:
            self._changed.set()

    async def _wait_any(self, *events: asyncio.Event, timeout: float | None = None) -> None:
        """Tunggu sampai salah satu event menyala, atau timeout habis."""
        tasks = [asyncio.create_task(e.wait()) for e in events]
        try:
            await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    # ---- pemindaian & koneksi -----------------------------------------

    async def _scan(self) -> None:
        from bleak import BleakScanner

        self._status("info", text=f"memindai {SCAN_S:.0f} detik…")
        try:
            found = await BleakScanner.discover(timeout=SCAN_S, return_adv=True)
            items = [
                {
                    "name": adv.local_name or dev.name or "(tanpa nama)",
                    "address": dev.address,
                    "rssi": adv.rssi,
                }
                for dev, adv in found.values()
            ]
        except Exception as e:
            # adapter mati, BlueZ tidak jalan, izin Bluetooth ditolak
            self._status("info", text=f"scan gagal: {e}", level="warn")
            return

        items.sort(key=lambda i: -(i["rssi"] if i["rssi"] is not None else 999))
        self._status("devices", items=items)

        match = [i for i in items if i["name"].upper().startswith(self.name_prefix)]
        if self.auto_connect and len(match) == 1:
            self.address, self.name = match[0]["address"], match[0]["name"]
            self._status("info", text=f"ketemu {self.name} — menyambung otomatis")
        elif not items:
            self._status("info", text="tidak ada device BLE terlihat", level="warn")

    async def _session(self) -> None:
        """Satu siklus koneksi: sambung, terima notify, sampai putus/berhenti."""
        from bleak import BleakClient

        label = self.name or self.address
        self._status("info", text=f"menyambung ke {label}…")
        self._dropped_conn.clear()
        self._seq = None

        try:
            async with BleakClient(
                self.address, disconnected_callback=lambda _: self._dropped_conn.set()
            ) as client:
                await client.start_notify(self.tx_uuid, self._on_notify)
                self._status("connected", remote=label)
                await self._wait_any(self._stop, self._changed, self._dropped_conn)
                if client.is_connected:
                    try:
                        await client.stop_notify(self.tx_uuid)
                    except Exception:
                        pass  # sudah putus duluan — tidak ada yang perlu dirapikan
        except Exception as e:
            self._status("info", text=f"koneksi gagal: {e}", level="warn")
        finally:
            self._status("disconnected")

    # ---- data ----------------------------------------------------------

    def _parse(self, data: bytes):
        """(q_mentah, draw, shot|None, nocked|None) atau None kalau tidak dikenali."""
        if data[:1] == b"{":
            try:
                m = json.loads(data.decode("utf-8"))
                q = tuple(float(v) for v in m["q"])
                if len(q) != 4:
                    return None
                draw = float(m.get("draw", 0.0))
            except (ValueError, TypeError, KeyError, UnicodeDecodeError):
                return None
            shot = m.get("shot")
            nocked = m.get("nocked", m.get("ir"))
            return q, draw, int(shot) if shot is not None else None, \
                bool(nocked) if nocked is not None else None

        if len(data) < PACKET.size:
            return None
        qw, qx, qy, qz, draw, flags, seq = PACKET.unpack_from(data)

        if self._seq is not None and seq != (self._seq + 1) % 256:
            self.dropped += 1
        self._seq = seq

        q = tuple(v / 32767.0 for v in (qw, qx, qy, qz))
        return q, draw / 65535.0, None, bool(flags & 1)

    def _on_notify(self, _sender, data: bytearray) -> None:
        parsed = self._parse(bytes(data))
        if parsed is None:
            return
        q_raw, draw, shot, nocked = parsed

        self._raw_q = qnorm(q_raw)

        if shot is not None:
            self.shot = shot
        elif nocked is not None:
            # IR kembali terang = anak panah meninggalkan busur. Deteksi tepinya
            # di sini, bukan di konsumen, supaya tidak bergantung frame rate Unity.
            if self._nocked and not nocked:
                self.shot += 1
            self._nocked = nocked

        rel = AXIS_PRESETS[self.axis](qmul(qconj(self.ref), self._raw_q))
        packet = {
            "q": [round(v, 5) for v in rel],
            "draw": round(max(0.0, min(1.0, draw)), 4),
            "shot": self.shot,
        }
        self.sock.sendto(
            json.dumps(packet, separators=(",", ":")).encode("utf-8"), self.udp_dest
        )
        self.packets += 1
        self._on_packet(packet)

        self._report_drops()

    def _report_drops(self) -> None:
        """Paket BLE hilang itu wajar sesekali; yang perlu terlihat cuma kalau
        jumlahnya naik terus (jarak, interferensi, connection interval)."""
        if not self.dropped:
            return
        now = time.monotonic()
        if now - self._drop_note < DROP_REPORT_S:
            return
        self._drop_note = now
        self._status("info", text=f"{self.dropped} paket BLE hilang", level="warn")

    # ---- siklus hidup ---------------------------------------------------

    async def serve(self) -> None:
        """Jalan sampai stop() dipanggil."""
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._changed = asyncio.Event()
        self._dropped_conn = asyncio.Event()

        self._status(
            "ready",
            target=self.address or "belum ada device",
            detail=f"UDP {self.udp_dest[0]}:{self.udp_dest[1]}",
        )
        try:
            while not self._stop.is_set():
                self._changed.clear()

                if self.address is None:
                    await self._scan()
                if self._stop.is_set():
                    break
                if self.address is None:
                    # tidak ada pilihan otomatis — tunggu tombol Sambung/Pindai
                    await self._wait_any(self._stop, self._changed, timeout=self.rescan_s)
                    continue

                await self._session()

                if not self._stop.is_set() and not self._changed.is_set():
                    # putus sendiri: coba lagi ke device yang sama
                    await self._wait_any(self._stop, self._changed, timeout=RETRY_S)
        finally:
            self._loop = None
            self._stop = None
            self._changed = None
            self._dropped_conn = None
            self._status("stopped")

    def run(self) -> None:
        """Blocking. Dipakai thread sumber di GUI, dan mode headless."""
        asyncio.run(self.serve())


# ---------------------------------------------------------- mode headless


def main() -> None:
    import sys

    address = sys.argv[1] if len(sys.argv) > 1 else None

    def on_status(kind: str, **f) -> None:
        if kind == "devices":
            print("\n  device terlihat:")
            for i in f["items"]:
                rssi = f"{i['rssi']:>4} dBm" if i["rssi"] is not None else "   ? dBm"
                print(f"    {rssi}  {i['address']}  {i['name']}")
        elif kind == "connected":
            print(f"\n[+] terhubung ke {f.get('remote')}")
        elif kind == "disconnected":
            print("\n[-] terputus")
        elif kind == "info":
            print(f"\n    {f.get('text', '')}")

    seen = 0

    def on_packet(_: dict) -> None:
        nonlocal seen
        seen += 1
        if seen % 60 == 0:
            print(f"\r    {seen} paket diteruskan", end="", flush=True)

    reader = BleReader(
        on_packet=on_packet, on_status=on_status, address=address, rescan_s=10.0
    )

    print("=" * 52)
    print("  MOTUS BRIDGE — reader BLE ke UDP (headless)")
    print("=" * 52)
    print(f"  Device     : {address or f'cari nama berawalan {NAME_PREFIX}'}")
    print(f"  UDP keluar : {UDP_DEST[0]}:{UDP_DEST[1]}")
    print()
    print("  Tanpa alamat, device yang namanya berawalan")
    print(f"  {NAME_PREFIX} disambung otomatis kalau cuma ada satu.")
    print("  Alamat bisa dipaksa:  python ble_reader.py AA:BB:CC:DD:EE:FF")
    print()
    print("  Untuk versi GUI:  python app_ble.py")
    print("=" * 52)

    reader.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nberhenti.")
