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
| Kontrol | Kalibrasi Tengah dan preset sumbu 0–3, dikirim ke sumber dari PC. Di jalur BLE, tombol Reset Posisi di busur melakukan hal yang sama |
| Cube test | Kubus yang dirotasi langsung oleh `q`, plus reticle arah bidik |
| Paket | Bar `draw` (plus m/s di jalur BLE), counter `shot`, dan nilai `q` / yaw-pitch-roll |

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
4. Arahkan busur ke monitor, tekan **Kalibrasi Tengah** — atau tombol **Reset
   Posisi** di busur itu sendiri, hasilnya sama persis
5. Tarik dan lepas — anak panah tetap terpasang di wadahnya, yang diukur
   penandanya saat melewati dua sensor IR

Kalau koneksi putus, reader mencoba menyambung ulang ke device yang sama tiap 2
detik sampai ditekan **Stop**.

Kubus di GUI harus ikut berputar di kedua versi, dan counter tembakan naik saat
dilepas. Bar `draw` berbeda: di versi HP ia terisi selama menarik, di versi BLE
ia diam sampai panah lepas lalu melompat ke nilai tembakan terakhir — di sana
tidak ada sensor tarikan, kekuatannya dihitung dari kecepatan panah melewati dua
sensor IR, dan angka m/s-nya muncul di sebelah persentase.

### Tanpa GUI

```bash
python relay.py                          # versi HP, output ke terminal
python ble_reader.py                     # versi BLE, cari device MOTUS
python ble_reader.py AA:BB:CC:DD:EE:FF   # versi BLE, alamat dipaksa
python monitor.py                        # konsumen UDP, bisa jalan bersamaan
```

`monitor.py` tetap berguna untuk membuktikan paket benar-benar keluar ke UDP —
GUI mengintipnya di sisi sumber, bukan dengan ikut mengikat port 5005.

## Format paket — Python → Unity (UDP)

Ada dua kontrak JSON di proyek ini dan isinya memang berbeda, karena batasnya
berbeda. Jangan tertukar:

| Batas | Arah | Bagian |
|---|---|---|
| UDP `127.0.0.1:5005` | Python → Unity / `monitor.py` | yang di bawah ini |
| BLE notify | firmware busur → Python | [Format BLE](#format-ble--firmware-busur--python) |

Yang di bawah ini **keluaran** Python: sudah dikalibrasi, sudah dipetakan, siap
pakai. Yang di format BLE adalah **masukannya**: angka mentah dari sensor.
`draw` di sini, misalnya, adalah hasil olahan `dt_us` dari sana.

Kontrak antara Python dan konsumen. JSON satu baris per paket, ~60/detik:

```json
{ "q": [w, x, y, z], "draw": 0.42, "shot": 7, "speed": 26.8 }
```

| Field   | Tipe      | Arti |
|---------|-----------|------|
| `q`     | float[4]  | Orientasi **relatif terhadap titik kalibrasi**, urutan w,x,y,z, konvensi **tangan-kiri Unity** |
| `draw`  | float 0–1 | Kekuatan tembakan |
| `shot`  | int       | Counter tembakan, naik 1 tiap lepas |
| `speed` | float m/s | Kecepatan lesatan terukur. **Hanya jalur BLE** — konsumen wajib menganggapnya opsional |

Perhatikan bedanya per jalur. Di jalur HP, `draw` adalah lama tekan-tahan, jadi
naik mulus selama menarik. Di jalur BLE tidak ada sensor tarikan sama sekali:
kekuatannya diukur dari kecepatan anak panah melewati dua sensor IR, jadi
angkanya **baru ada sesudah lepas** dan ditahan sampai tembakan berikutnya.
Konsumen yang ingin benar di dua-duanya harus membaca `draw` saat `shot`
bertambah, bukan memakainya untuk menganimasikan tarikan.

`shot` sengaja berupa counter, bukan boolean. UDP tidak menjamin pengiriman —
kalau boolean `released: true` hilang satu paket, tembakan hilang. Dengan
counter, konsumen cukup membandingkan nilai terakhir yang sudah dikonsumsi,
jadi paket yang drop tidak menghilangkan event.

Arah sebaliknya (GUI → sumber) bukan bagian dari kontrak ini dan tidak pernah
masuk ke UDP. Isinya `{"cmd": "center"}` dan `{"cmd": "axis", "value": n}`,
disaring whitelist: di jalur HP diteruskan ke `phone.html`, di jalur BLE
diselesaikan `ble_reader.py` sendiri.

## Format BLE — firmware busur → Python

Ini **masukan** ke `ble_reader.py`, bukan yang keluar ke Unity. Isinya angka
mentah dari sensor; yang keluar ke UDP adalah hasil olahannya di
[Format paket](#format-paket--python--unity-udp).

Ringkasan. Detail lengkapnya — advertising, parameter koneksi, debounce IR,
pemasangan IR gate, kerangka ESP32, urutan pengujian — ada di
[FIRMWARE.md](FIRMWARE.md).

Notify lewat Nordic UART Service, karakteristik `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`.
Byte pertama menentukan formatnya — biner kalau bukan `{`, JSON kalau `{`:

**Biner, 13 byte little-endian `<4h H B B B`** — format normal (12 byte kalau
busurnya tanpa tombol Reset Posisi):

| Offset | Tipe | Isi |
|---|---|---|
| 0–7 | int16 ×4 | `qw,qx,qy,qz` × 32767 — orientasi **mentah**, belum dikalibrasi |
| 8–9 | uint16 | `meas` — Δt antar dua sensor IR dalam µs; 0 = belum ada tembakan |
| 10 | uint8 | `shot_seq` — naik 1 tiap panah lewat kedua sensor ke arah depan |
| 11 | uint8 | `seq`, wrap 0–255 — hanya untuk menghitung paket hilang |
| 12 | uint8 | `btn_seq` — **opsional**, naik 1 tiap tombol Reset Posisi ditekan |

**JSON satu baris** — pengganti format biner di atas, untuk firmware coba-coba.
Isinya sama, cuma cara membungkusnya yang beda. Dua baris di bawah bukan dua
format, melainkan satu format dengan key alternatif untuk hasil IR gate. Pilih
salah satu; sisa key-nya sengaja dibuat sama supaya jelas cuma dua key itu yang
berbeda:

```json
{"q":[1,0,0,0], "dt_us":1042, "shot_seq":7, "btn_seq":2}
{"q":[1,0,0,0], "speed":47.9, "shot":7,     "btn_seq":2}
```

Baris pertama firmware bodoh: kirim Δt mentah, Python yang menghitung m/s dan
counter tembakan. Baris kedua firmware yang sudah menghitung sendiri. Baris
pertama yang dianjurkan — jarak sensor jadi bisa disetel tanpa flash ulang.

`btn_seq` opsional di dua-duanya; buang kalau busurnya tanpa tombol.

Firmware sengaja dibiarkan sebodoh mungkin. Kalibrasi (`ref⁻¹ × q`), preset
sumbu, jarak antar sensor, konversi Δt → m/s, dan counter `shot` dikerjakan
`ble_reader.py`, jadi tombol di GUI dan penyetelan sensor tetap berfungsi tanpa
mem-flash ulang busur. Tombol Reset Posisi di busur pun begitu: firmware cuma
menaikkan `btn_seq`, kalibrasinya tetap dihitung di PC.

Yang tidak bisa dipindah ke Python cuma pengukuran waktunya sendiri: pada 50 m/s
dengan sensor berjarak 5 cm, Δt-nya ~1 ms — lewat begitu saja di antara dua
notify 60 Hz. Firmware mencatatnya lewat interrupt dan menahan hasilnya.

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
| `n lintasan IR diabaikan` | Δt di luar `SPEED_SANE` — biasanya `GATE_DIST_M` salah, atau firmware ikut menghitung saat busur ditarik ulang |
| `shot` naik saat menarik, bukan saat lepas | Pin sensor depan dan belakang tertukar di firmware |
| `shot` naik dua tiap lepas | Debounce/reset `tBack` di firmware bocor |
| m/s tidak masuk akal | `GATE_DIST_M` belum disesuaikan dengan jarak sensor sebenarnya |
| Tombol Reset Posisi tidak bereaksi | Firmware masih mengirim 12 byte — byte ke-13 `btn_seq` belum ada |
| `titik tengah direset dari tombol busur` muncul berkali-kali sekali tekan | Debounce tombol di firmware kurang |

Dua-duanya:

| Gejala | Penyebab |
|---|---|
| Nilai melompat / drift | Gangguan magnetik. Jauhkan dari logam, tekan Kalibrasi Tengah lagi |
| `monitor.py` diam | Sumber belum di-Start, atau konsumen lain sudah mengikat port 5005 |

## Berikutnya

- [ ] Cocokkan preset sumbu (tombol **0–3** di GUI) dengan orientasi Unity
- [ ] Sambungkan `UdpBowInput.cs` ke Unity — format paket tidak berubah
- [ ] Uji versi BLE dengan busur betulan: nama advertise dan laju notify
- [ ] Ukur jarak antar sensor IR → `GATE_DIST_M`, lalu catat m/s terpelan dan
      terkencang → `SPEED_RANGE` (dua-duanya di `ble_reader.py`)
