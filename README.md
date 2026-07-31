# Motus Bridge

Jembatan sensor untuk simulator panahan. **Dua versi, keluarannya identik** —
pilih sesuai yang tersedia di meja:

```
[HP browser]  --WSS--> [app.py     / relay.py     ] ─┐
                                                     ├─ UDP:5005 ─> [monitor.py]
[busur IMU+IR] --BLE--> [app_ble.py / ble_reader.py] ─┘             [Unity UdpBowInput]
```

Versi HP dipakai untuk memvalidasi mekanik dan mapping sumbu tanpa perlu
firmware. Versi BLE adalah tujuan akhirnya. Karena paketnya sama persis,
`monitor.py` dan sisi Unity tidak perlu tahu yang mana yang jalan.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

`bleak` cuma dibutuhkan versi BLE, `aiohttp`/`cryptography`/`qrcode` cuma versi
HP — semuanya ikut terpasang, tinggal abaikan yang tidak dipakai.

Sertifikat tidak perlu dibuat manual: `app.py` membuatnya sendiri saat pertama
jalan, dan membuat ulang otomatis kalau IP LAN berubah (pindah Wi-Fi).

## Jendelanya sama untuk dua-duanya

```bash
python app.py       # versi HP
python app_ble.py   # versi BLE
```

| Bagian | Isi |
|---|---|
| Sumber | Cara menyambung — QR + URL (HP) atau daftar device hasil scan (BLE) |
| Kontrol | Kalibrasi Tengah dan preset sumbu 0–3, dikirim ke sumber dari PC |
| Cube test | Kubus yang dirotasi langsung oleh `q`, plus reticle arah bidik |
| Paket | Bar `draw`, counter `shot`, dan nilai `q` / yaw-pitch-roll |

Tombol **Start/Stop** di kanan atas menyalakan sumbernya; laju paket dan status
koneksi ada di sebelahnya. Log di bawah mencatat koneksi, sertifikat, dan hasil
scan.

### Versi HP — `python app.py`

Di HP (harus satu Wi-Fi dengan PC):

1. Pindai QR atau buka URL-nya, mis. `https://192.168.1.10:8443`
2. Peringatan sertifikat muncul → **Advanced** → **Proceed**
3. Ketuk **Mulai**, izinkan akses sensor gerak
4. Arahkan HP ke monitor, tekan **Kalibrasi Tengah** (di HP atau dari GUI)
5. Tekan-tahan area tengah untuk menarik, lepas untuk menembak

### Versi BLE — `python app_ble.py`

1. Nyalakan busur, pastikan Bluetooth PC aktif
2. GUI langsung memindai 5 detik; device yang namanya berawalan `MOTUS`
   disambung otomatis kalau cuma ada satu
3. Kalau tidak, pilih dari daftar lalu **Sambung** (**Pindai** untuk mengulang)
4. Arahkan busur ke monitor, tekan **Kalibrasi Tengah**
5. Tarik dan lepas anak panah

Kalau koneksi putus, reader mencoba menyambung ulang ke device yang sama tiap 2
detik sampai ditekan **Stop**.

Kedua versi: kubus di GUI harus ikut berputar, bar tarikan terisi saat ditarik,
dan counter tembakan naik saat dilepas.

### Tanpa GUI

```bash
python relay.py                          # versi HP, output ke terminal
python ble_reader.py                     # versi BLE, cari device MOTUS
python ble_reader.py AA:BB:CC:DD:EE:FF   # versi BLE, alamat dipaksa
python monitor.py                        # konsumen UDP, bisa jalan bersamaan
```

`monitor.py` tetap berguna untuk membuktikan paket benar-benar keluar ke UDP —
GUI mengintipnya di sisi sumber, bukan dengan ikut mengikat port 5005.

## Format paket

Kontrak antara Python dan konsumen. JSON satu baris per paket, ~60/detik:

```json
{ "q": [w, x, y, z], "draw": 0.42, "shot": 7 }
```

| Field  | Tipe      | Arti |
|--------|-----------|------|
| `q`    | float[4]  | Orientasi **relatif terhadap titik kalibrasi**, urutan w,x,y,z |
| `draw` | float 0–1 | Kekuatan tarikan |
| `shot` | int       | Counter tembakan, naik 1 tiap lepas |

`shot` sengaja berupa counter, bukan boolean. UDP tidak menjamin pengiriman —
kalau boolean `released: true` hilang satu paket, tembakan hilang. Dengan
counter, konsumen cukup membandingkan nilai terakhir yang sudah dikonsumsi,
jadi paket yang drop tidak menghilangkan event.

Arah sebaliknya (GUI → sumber) bukan bagian dari kontrak ini dan tidak pernah
masuk ke UDP. Isinya `{"cmd": "center"}` dan `{"cmd": "axis", "value": n}`,
disaring whitelist: di jalur HP diteruskan ke `phone.html`, di jalur BLE
diselesaikan `ble_reader.py` sendiri.

## Format BLE (yang harus dikirim firmware busur)

Ringkasan. Detail lengkapnya — advertising, parameter koneksi, debounce IR,
kalibrasi rentang `draw`, kerangka ESP32, urutan pengujian — ada di
[FIRMWARE.md](FIRMWARE.md).

Notify lewat Nordic UART Service, karakteristik `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`.
Byte pertama menentukan formatnya:

**Biner, 12 byte little-endian `<4h H B B`** — format normal:

| Offset | Tipe | Isi |
|---|---|---|
| 0–7 | int16 ×4 | `qw,qx,qy,qz` × 32767 — orientasi **mentah**, belum dikalibrasi |
| 8–9 | uint16 | `draw` 0..65535 → 0.0..1.0 |
| 10 | uint8 | flags, bit0 = anak panah terpasang (IR terhalang) |
| 11 | uint8 | `seq`, wrap 0–255 — hanya untuk menghitung paket hilang |

**JSON satu baris** — untuk firmware coba-coba:

```json
{"q":[1,0,0,0], "draw":0.42, "nocked":true}
{"q":[1,0,0,0], "draw":0.42, "shot":7}
```

Firmware sengaja dibiarkan bodoh. Kalibrasi (`ref⁻¹ × q`), preset sumbu, dan
penghitungan `shot` dari tepi turun sinyal IR dikerjakan `ble_reader.py`, jadi
tombol di GUI tetap berfungsi tanpa mem-flash ulang busur.

## Troubleshooting

Versi HP:

| Gejala | Penyebab |
|---|---|
| Log GUI: `gagal membuka port` | Port 8443 sudah dipakai instance lain |
| Halaman tidak terbuka di HP | Beda jaringan, atau firewall memblokir port 8443 |
| Tombol Mulai tidak merespons | Bukan HTTPS — API sensor mati di HTTP biasa |
| `sensor tidak mengirim data` | Izin ditolak, atau HP tidak punya magnetometer |
| Kubus diam | Reload halaman di HP; sebagian Android butuh reload setelah izin diberikan |
| HP menolak sertifikat | IP LAN berubah — tekan **Stop** lalu **Start**, sertifikat dibuat ulang otomatis |

Versi BLE:

| Gejala | Penyebab |
|---|---|
| `bleak belum terpasang` | `pip install bleak` |
| `scan gagal: No powered Bluetooth adapters` | Bluetooth mati, atau di Linux BlueZ tidak jalan |
| `tidak ada device BLE terlihat` | Busur belum menyala/advertise, atau sudah tersambung ke perangkat lain |
| Terhubung tapi 0 Hz | Firmware belum notify di UUID `6E400003-…` |
| `n paket BLE hilang` naik terus | Jarak/interferensi, atau connection interval terlalu longgar |
| Kubus diam padahal ada paket | Quaternion firmware belum dinormalisasi atau masih nol |

Dua-duanya:

| Gejala | Penyebab |
|---|---|
| Nilai melompat / drift | Gangguan magnetik. Jauhkan dari logam, tekan Kalibrasi Tengah lagi |
| `monitor.py` diam | Sumber belum di-Start, atau konsumen lain sudah mengikat port 5005 |

## Berikutnya

- [ ] Cocokkan preset sumbu (tombol **0–3** di GUI) dengan orientasi Unity
- [ ] Sambungkan `UdpBowInput.cs` ke Unity — format paket tidak berubah
- [ ] Uji versi BLE dengan busur betulan: nama advertise, laju notify, skala draw
