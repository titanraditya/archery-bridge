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
       meas         uint16  hasil IR gate: dt antar sensor dalam mikrodetik
                            (atau m/s x100, lihat speed_unit). 0 = belum ada tembakan
       shot_seq     uint8   dinaikkan firmware tiap anak panah selesai melewati
                            KEDUA sensor ke arah depan. wrap 0-255
       seq          uint8   nomor urut paket, wrap 0-255 — hanya untuk deteksi drop
       btn_seq      uint8   OPSIONAL, byte ke-13. Dinaikkan tiap tombol Reset
                            Posisi di busur ditekan. 12 byte saja tetap sah

2. JSON satu baris — untuk firmware coba-coba yang belum tega pakai struct.
   Satu format, dua pilihan key untuk hasil IR gate; key lainnya sama:

       {"q":[w,x,y,z], "dt_us":1042, "shot_seq":7, "btn_seq":2}
       {"q":[w,x,y,z], "speed":47.9, "shot":7,     "btn_seq":2}

   Baris kedua untuk firmware yang menghitung m/s dan counter sendiri.
   btn_seq opsional di dua-duanya.

Anak panah di simulator ini PATEN — dipasang tetap di wadahnya, tidak dilepas
manual. Jadi tidak ada keadaan "terpasang / tidak"; yang diukur adalah penanda
di batang panah yang melewati dua sensor IR berjarak tetap. Selisih waktunya
memberi kecepatan lesatan, dan kecepatan itulah pengganti sensor tarikan.

Deteksi tepinya HARUS di firmware, bukan di sini: pada ~50 m/s dengan jarak
sensor 5 cm, Δt-nya cuma ~1 ms — mustahil terlihat oleh notify 60 Hz. Firmware
cuma perlu mengunci Δt-nya lalu menaikkan shot_seq; sisanya tetap di Python:

    * kalibrasi     q yang dikirim = ref^-1 x q_mentah. Titik nolnya bisa
                    disetel dari tombol GUI maupun tombol fisik di busur —
                    firmware cuma melapor "ditekan", bukan mengubah apa-apa
    * preset sumbu  AXIS_PRESETS, sama urutannya dengan phone.html
    * jarak gate    Δt -> m/s, jadi menggeser sensor tidak perlu flash ulang
    * penyaringan   Δt di luar akal (mis. penanda lewat pelan saat menarik
                    ulang) tidak dihitung sebagai tembakan
    * normalisasi   m/s -> draw 0..1, supaya kontrak paket ke Unity tidak berubah

Jadi tombol Kalibrasi Tengah dan preset 0-3 di GUI tetap bekerja tanpa perlu
mem-flash ulang firmware, dan paket yang keluar tetap sebentuk dengan jalur HP.
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

# Byte ke-13, opsional: pencacah tombol Reset Posisi di busur. Naik tiap ditekan,
# bukan keadaan tertekan/tidak — alasannya sama dengan shot_seq, tekanan tombol
# tidak boleh hilang gara-gara satu notify yang drop. Firmware yang cuma
# mengirim 12 byte tetap jalan, cuma tombolnya yang tidak ada.
BTN_OFFSET = PACKET.size

# Jarak antar dua sensor IR, meter. Satu-satunya angka yang harus diukur ulang
# kalau rig-nya digeser — sengaja di sini, bukan di firmware.
GATE_DIST_M = 0.05

# m/s yang masih masuk akal untuk lesatan panah. Di luar ini dianggap penanda
# lewat karena sebab lain (busur ditarik ulang, tangan menghalangi sensor).
SPEED_SANE = (2.0, 200.0)

# m/s yang dipetakan ke draw 0.0..1.0. Setel setelah tahu rentang busur asli.
SPEED_RANGE = (10.0, 50.0)

# Lompatan shot_seq sebesar ini dianggap firmware yang menghitung ulang, bukan
# tembakan beruntun — BLE putus 8 tembakan lamanya pasti sudah terlihat sebagai
# disconnect duluan. Tanpa batas ini, satu byte korup jadi puluhan shot hantu.
SHOT_SEQ_MAX_GAP = 8

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


# Dulu daftar ini dikembar dengan AXIS_PRESETS di static/phone.html. Sejak
# jalur HP presetnya sudah dikonfirmasi benar dan IMU busur ternyata terpasang
# dengan orientasi lain, dua daftar ini sengaja dibiarkan berbeda: nomor preset
# di GUI tidak lagi berarti sama di kedua jalur. Jangan menyamakan salah satu
# ke yang lain "supaya rapi" — masing-masing mengoreksi pemasangan sensornya
# sendiri.
#
# IMU di busur terpasang dengan sumbu putar tegaknya di Z, bukan Y: membidik
# kanan-kiri terbaca sebagai roll di sekitar arah bidik, jadi kubus berputar
# tapi reticle (arah -Z lokal) diam di tempat. Preset 1-3 semuanya memindahkan
# sumbu-Z sumber ke sumbu-Y (atas) supaya kanan-kiri jadi yaw betulan; yang
# membedakan cuma sisa kebebasannya — arah putar yaw, dan sumbu sumber mana
# yang jadi arah moncong. Coba 1 dulu, lalu 2 kalau kanan-kirinya kebalik,
# lalu 3 kalau reticle-nya menghadap ke samping saat busur lurus ke depan.
#
# Semua permutasi di bawah determinannya +1 (rotasi murni, bukan pencerminan).
# Kalau nanti menambah preset, jaga syarat itu — permutasi bertanda salah
# membalik arah putar dan bikin kalibrasi tengah kelihatan "hampir benar".
AXIS_PRESETS = [
    lambda v: v,                                # 0: apa adanya
    lambda v: (v[0],  v[1],  v[3], -v[2]),      # 1: Z->Y (atas)
    lambda v: (v[0],  v[1], -v[3],  v[2]),      # 2: Z->Y, yaw kebalik
    lambda v: (v[0],  v[2],  v[3],  v[1]),      # 3: Z->Y, moncong pindah ke X
]


def to_unity_handed(v):
    """Tangan-kanan (sumber & CubeView) -> tangan-kiri (Unity).

    Bukan penukaran sumbu, jadi tidak bisa dijadikan preset: semua preset di
    atas determinannya +1, sedangkan ini pencerminan (determinan -1). Tanpa ini
    bidik ke kanan bikin panah melesat ke kiri, dan gejalanya sama di jalur HP
    maupun BLE karena penyebabnya konvensi Unity, bukan pemasangan sensor.

    Yang dicerminkan sumbu kiri-kanan (X), jadi pitch tetap dan yang terbalik
    yaw + roll — sesuai gejala yang diamati. Mencerminkan Z (rumus konversi
    tangan-kanan yang lebih sering beredar) ikut membalik pitch, itu bukan yang
    dibutuhkan di sini.

    Konsekuensi yang harus diingat: kubus di GUI ikut tercermin karena GUI
    mengintip paket yang sudah jadi. Kubus yang kiri-kanannya terasa terbalik
    dibanding gerakan busur itu benar — jangan "diperbaiki" dengan mengganti
    preset, karena yang dicocokkan sekarang adalah Unity.
    """
    return (v[0], v[1], -v[2], -v[3])


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
        gate_dist_m: float = GATE_DIST_M,
        speed_range: tuple[float, float] = SPEED_RANGE,
        speed_unit: str = "dt_us",
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

        # "dt_us"      : field meas = mikrodetik antar sensor (firmware bodoh)
        # "speed_x100" : field meas = m/s x 100 (firmware sudah menghitung sendiri)
        self.gate_dist_m = gate_dist_m
        self.speed_range = speed_range
        self.speed_unit = speed_unit

        self._on_packet = on_packet or (lambda _: None)
        self._on_status = on_status or (lambda *a, **k: None)

        self.packets = 0
        self.dropped = 0
        self.rejected = 0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # keadaan olahan — semuanya hanya disentuh dari thread jaringan
        self.ref = (1.0, 0.0, 0.0, 0.0)
        self.axis = 0
        self.shot = 0
        # Keduanya ditahan sampai tembakan berikutnya: dengan IR gate, kekuatan
        # tembakan baru diketahui SESUDAH panah lepas, jadi tidak ada nilai
        # real-time yang bisa ditampilkan selama menarik.
        self.speed = 0.0
        self.draw = 0.0
        self._raw_q = (1.0, 0.0, 0.0, 0.0)
        self._seq: int | None = None
        self._shot_seq: int | None = None
        self._btn_seq: int | None = None
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

    def _center(self) -> None:
        """Titik nol = orientasi busur saat ini. Sama seperti center() di
        phone.html, hanya pindah dari HP ke sini. Dipakai dua-duanya: tombol
        Kalibrasi Tengah di GUI dan tombol Reset Posisi di busur."""
        self.ref = self._raw_q

    def _apply(self, cmd: dict) -> None:
        name = cmd.get("cmd")
        if name == "center":
            self._center()
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
        # Semuanya pencacah milik firmware, dan firmware bisa saja baru boot
        # ulang — selaraskan lagi dari paket pertama, jangan sampai terbaca
        # sebagai tembakan atau tombol yang tidak pernah ditekan.
        self._seq = None
        self._shot_seq = None
        self._btn_seq = None

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

    def _speed(self, meas: int) -> float | None:
        """Angka mentah IR gate -> m/s. 0 berarti belum ada tembakan tercatat."""
        if meas <= 0:
            return None
        if self.speed_unit == "speed_x100":
            return meas / 100.0
        return self.gate_dist_m / (meas * 1e-6)

    def _parse(self, data: bytes):
        """(q_mentah, speed|None, shot_seq|None, shot_absolut|None, btn_seq|None),
        None kalau formatnya tidak dikenali."""
        if data[:1] == b"{":
            try:
                m = json.loads(data.decode("utf-8"))
                q = tuple(float(v) for v in m["q"])
                if len(q) != 4:
                    return None
                speed = m.get("speed")
                speed = float(speed) if speed else self._speed(int(m.get("dt_us", 0)))
            except (ValueError, TypeError, KeyError, UnicodeDecodeError):
                return None
            shot, shot_seq, btn = m.get("shot"), m.get("shot_seq"), m.get("btn_seq")
            return q, speed, \
                int(shot_seq) if shot_seq is not None else None, \
                int(shot) if shot is not None else None, \
                int(btn) if btn is not None else None

        if len(data) < PACKET.size:
            return None
        qw, qx, qy, qz, meas, shot_seq, seq = PACKET.unpack_from(data)
        # Byte tombol boleh tidak ada — firmware tanpa tombol Reset Posisi
        # mengirim 12 byte dan tetap sah.
        btn = data[BTN_OFFSET] if len(data) > BTN_OFFSET else None

        if self._seq is not None and seq != (self._seq + 1) % 256:
            self.dropped += 1
        self._seq = seq

        q = tuple(v / 32767.0 for v in (qw, qx, qy, qz))
        return q, self._speed(meas), shot_seq, None, btn

    def _register(self, speed: float | None, count: int) -> None:
        """Penanda di batang panah baru saja melewati kedua sensor IR."""
        lo, hi = SPEED_SANE
        if speed is None or not lo <= speed <= hi:
            # Penanda lewat, tapi terlalu pelan atau terlalu cepat untuk sebuah
            # lesatan — biasanya busur sedang ditarik ulang. Bukan tembakan.
            self.rejected += 1
            return
        self.shot += count
        self.speed = speed
        v0, v1 = self.speed_range
        self.draw = max(0.0, min(1.0, (speed - v0) / (v1 - v0))) if v1 > v0 else 0.0

    def _on_notify(self, _sender, data: bytearray) -> None:
        parsed = self._parse(bytes(data))
        if parsed is None:
            return
        q_raw, speed, shot_seq, shot_abs, btn_seq = parsed

        self._raw_q = qnorm(q_raw)

        # Tombol Reset Posisi di busur. Harus sesudah _raw_q diperbarui, karena
        # yang dijadikan titik nol adalah orientasi pada paket ini. Berapa pun
        # kenaikannya dihitung satu kali — menekan dua kali cepat hasilnya sama
        # saja, dan paket pertama cuma dipakai menyelaraskan.
        if btn_seq is not None and btn_seq != self._btn_seq:
            if self._btn_seq is not None:
                self._center()
                self._status("info", text="titik tengah direset dari tombol busur")
            self._btn_seq = btn_seq

        if shot_abs is not None:
            self.shot = shot_abs
            if speed is not None:
                self._register(speed, 0)
        elif shot_seq is not None and shot_seq != self._shot_seq:
            # Paket pertama cuma dipakai untuk menyelaraskan, bukan dihitung —
            # firmware boleh mulai dari angka berapa pun. Selisihnya diambil utuh
            # supaya tembakan tidak hilang kalau ada notify yang drop.
            if self._shot_seq is not None:
                gap = (shot_seq - self._shot_seq) % 256
                self._register(speed, gap if gap <= SHOT_SEQ_MAX_GAP else 1)
            self._shot_seq = shot_seq

        # Urutannya penting: preset dulu (mencocokkan pemasangan sensor), baru
        # pencerminan (mencocokkan konvensi Unity). Dibalik hasilnya lain.
        rel = to_unity_handed(AXIS_PRESETS[self.axis](qmul(qconj(self.ref), self._raw_q)))
        packet = {
            "q": [round(v, 5) for v in rel],
            "draw": round(self.draw, 4),
            "shot": self.shot,
            "speed": round(self.speed, 2),
        }
        self.sock.sendto(
            json.dumps(packet, separators=(",", ":")).encode("utf-8"), self.udp_dest
        )
        self.packets += 1
        self._on_packet(packet)

        self._report_drops()

    def _report_drops(self) -> None:
        """Paket BLE hilang itu wajar sesekali; yang perlu terlihat cuma kalau
        jumlahnya naik terus (jarak, interferensi, connection interval).
        Lintasan yang ditolak juga dilaporkan di sini: kalau angkanya ikut naik
        tiap menembak, berarti jarak gate atau SPEED_SANE yang salah setel."""
        if not self.dropped and not self.rejected:
            return
        now = time.monotonic()
        if now - self._drop_note < DROP_REPORT_S:
            return
        self._drop_note = now
        note = []
        if self.dropped:
            note.append(f"{self.dropped} paket BLE hilang")
        if self.rejected:
            note.append(f"{self.rejected} lintasan IR diabaikan (di luar SPEED_SANE)")
        self._status("info", text=", ".join(note), level="warn")

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

    def on_packet(p: dict) -> None:
        nonlocal seen
        seen += 1
        if seen % 60 == 0:
            print(
                f"\r    {seen} paket diteruskan   "
                f"shot {p['shot']}   {p['speed']:5.1f} m/s",
                end="",
                flush=True,
            )

    reader = BleReader(
        on_packet=on_packet, on_status=on_status, address=address, rescan_s=10.0
    )

    print("=" * 52)
    print("  MOTUS BRIDGE — reader BLE ke UDP (headless)")
    print("=" * 52)
    print(f"  Device     : {address or f'cari nama berawalan {NAME_PREFIX}'}")
    print(f"  UDP keluar : {UDP_DEST[0]}:{UDP_DEST[1]}")
    print(f"  IR gate    : jarak {GATE_DIST_M * 100:.1f} cm, "
          f"draw 0..1 dari {SPEED_RANGE[0]:.0f}–{SPEED_RANGE[1]:.0f} m/s")
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
