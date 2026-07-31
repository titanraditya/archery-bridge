# Konteks proyek — Motus Bridge

Catatan arsitektur untuk sesi Claude Code. Baca ini sebelum mengubah kode.

## Tujuan

Lapisan input untuk simulator panahan fisik. Tujuan akhir: busur fisik dengan
IMU (arah bidik) + sensor IR (deteksi lepas anak panah) mengirim data ke Unity 3D.

Ada **dua versi yang jalan berdampingan**, keluarannya identik:

| Versi | Titik masuk | Sumber data |
|---|---|---|
| HP | `app.py` | Browser HP lewat HTTPS + WebSocket (`relay.py`) |
| BLE | `app_ble.py` | Busur fisik IMU + IR lewat Bluetooth LE (`ble_reader.py`) |

Versi HP tetap dipakai selama hardware belum siap — mekanik dan mapping sumbu
bisa divalidasi tanpa firmware. Versi BLE adalah tujuan akhirnya. Keduanya
mengirim paket dengan format yang sama persis, jadi `monitor.py` dan sisi Unity
tidak peduli yang mana yang jalan.

## Keputusan yang sudah diambil — jangan diubah tanpa alasan kuat

**Transport UDP loopback, bukan TCP/stdout/file.**
Latensi ~0.1 ms tanpa Nagle delay. Unity tidak perlu mengelola lifecycle proses
Python. Kehilangan 1–2 paket orientasi tidak masalah karena frame berikutnya
segera datang.

**`shot` adalah counter, bukan boolean.**
Konsekuensi langsung dari memilih UDP. Konsumen menyimpan `consumedShot` dan
membandingkan; paket yang drop tidak menghilangkan event tembak.

**Deteksi rising-edge terjadi di sisi Python/HP, bukan di konsumen.**
Dilakukan di dekat sumber data agar tidak bergantung pada frame rate Unity.
Di jalur BLE artinya `ble_reader.py` yang menghitung `shot` dari tepi turun
sinyal IR — bukan firmware, bukan Unity.

**Quaternion yang dikirim relatif terhadap titik kalibrasi** (`ref⁻¹ × q`).
Heading absolut HP/IMU tidak sejajar dengan arah kamera Unity. Tanpa kalibrasi,
crosshair akan menghadap arah acak setiap kali mulai.

**Firmware busur dibiarkan bodoh.** Yang dikirim lewat BLE cuma quaternion
mentah + keadaan IR + draw. Kalibrasi, preset sumbu, dan penghitungan `shot`
dikerjakan `ble_reader.py`. Alasannya: tombol Kalibrasi Tengah dan preset 0–3
tetap bekerja tanpa perlu mem-flash ulang busur setiap kali penyetelan berubah,
dan preset yang sudah dikonfirmasi di jalur HP berlaku apa adanya di jalur BLE
(daftar `AXIS_PRESETS` sengaja dikembar di `phone.html` dan `ble_reader.py` —
kalau salah satu diubah, ubah dua-duanya).

**HTTPS wajib.** `DeviceOrientationEvent` hanya aktif di secure context.
Sertifikat self-signed dengan SAN berisi IP — CN saja diabaikan browser modern.

**GUI terpisah total dari sumber data.**
`Relay` maupun `BleReader` tidak mengimpor Qt sama sekali; keluarannya cuma
callback `on_packet`/`on_status`. GUI menjalankannya di `QThread` dengan event
loop asyncio sendiri dan mengubah callback jadi signal Qt, jadi tidak ada widget
yang disentuh dari thread jaringan. Konsekuensinya keduanya tetap bisa dipakai
headless.

**Satu GUI dipakai dua versi (`ui.py`), bukan dua file yang disalin.**
Yang berbeda cuma dua kelas per versi — `Source` (adapter: bikin thread, plus
persiapan seperti sertifikat) dan `SourcePanel` (kartu SUMBER: QR+URL vs daftar
device). Cube, bar draw, counter shot, readout, log, dan tombol Kontrol tidak
tahu sumbernya apa. Menambah fitur tampilan berarti mengubah `ui.py` sekali,
dan otomatis berlaku di dua-duanya.

**Status sumber dinormalkan sebelum masuk GUI.**
`ui.py` cuma paham `ready` / `connected` / `disconnected` / `stopped` / `info`.
`Relay` masih memakai istilah lamanya (`listening`, `phone`) dan diterjemahkan
di `PhoneThread`; `BleReader` langsung memakai kosakata ini. Kind lain
diteruskan ke panel (jalur BLE menambah `devices`), jadi versi baru tidak perlu
menyentuh `ui.py`.

**GUI mengintip paket di sisi sumber, bukan dengan mengikat UDP 5005.**
Kalau GUI ikut `bind()` port itu, `monitor.py` dan Unity tidak bisa jalan
bersamaan dengan GUI.

## Kontrak paket

Batas antar komponen. Mengubah ini berarti mengubah semua konsumen.

```json
{ "q": [w, x, y, z], "draw": 0.0-1.0, "shot": int }
```

UTF-8 JSON satu baris, UDP ke `127.0.0.1:5005`, sekitar 60 paket/detik.
Dua-duanya — jalur HP dan jalur BLE — mengirim ini apa adanya.

## Kontrak BLE (batas ke firmware)

Notify lewat Nordic UART Service; `6E400003-…` arah busur → PC. Dua format,
dipilih parser dari byte pertama:

- biner 12 byte `<4h H B B`: quaternion ×32767 (mentah, belum dikalibrasi),
  draw uint16, flags (bit0 = IR terhalang / anak panah terpasang), seq
- JSON `{"q":[…], "draw":…, "nocked":bool}` — untuk firmware coba-coba

`seq` hanya untuk menghitung paket hilang. Tidak ada arah PC → busur: perintah
`center`/`axis` diselesaikan di `ble_reader.py`.

Versi lengkapnya untuk orang yang menulis firmware ada di `FIRMWARE.md` —
kalau kontrak di `ble_reader.py` berubah, file itu ikut diperbarui.

## File

| File | Peran |
|---|---|
| `ui.py` | Kerangka GUI PySide6 yang dipakai kedua versi. Tidak tahu sumbernya apa |
| `app.py` | Titik masuk versi HP: panel QR/URL + sertifikat + `Relay` |
| `app_ble.py` | Titik masuk versi BLE: panel daftar device + `BleReader` |
| `relay.py` | Kelas `Relay`: server HTTPS + WebSocket, meneruskan ke UDP. Tidak mengubah isi paket. Juga bisa jalan headless |
| `ble_reader.py` | Kelas `BleReader`: notify BLE → paket kontrak → UDP. Kalibrasi, preset sumbu, dan rising-edge `shot` ada di sini. Juga bisa jalan headless |
| `static/phone.html` | Kontroler di HP: baca sensor, tarik-lepas, kalibrasi, preset sumbu |
| `monitor.py` | Konsumen UDP untuk pengujian, pengganti Unity di fase ini |
| `make_cert.py` | Sertifikat self-signed dengan IP LAN saat ini (khusus jalur HP) |
| `FIRMWARE.md` | Kontrak BLE versi lengkap untuk penulis firmware busur |

## Sisi Unity (belum di repo ini)

`IBowInput` mengabstraksi input dengan tiga anggota:
`GetAimRay()`, `GetReleaseTriggered()`, `GetDrawStrength()`.

Implementasi yang ada: `MouseBowInput`. Yang akan ditambah: `UdpBowInput`,
membaca paket di thread latar, main thread mengambil nilai terakhir di bawah
lock. `Arrow3D` dan `ArcheryGame3D` tidak perlu berubah.

Panah memakai pendekatan busur kinematik + raycast ke titik kena, bukan
Rigidbody penuh — dipilih demi presisi skor dan kemudahan penyetelan.

## Yang masih terbuka

Mapping sumbu quaternion sumber → Unity belum ditentukan. Tombol **Sumbu** di
`phone.html` memutar antar preset agar bisa dicoba tanpa recompile; tombol
**0–3** di GUI mengirim preset tertentu ke sumber (lewat WebSocket di jalur HP,
diterapkan lokal di jalur BLE), supaya preset bisa diganti tanpa melepas
pegangan pada HP/busur.

Cube-test-nya ada di `ui.py` (`CubeView`) — kubus yang dirotasi langsung oleh
`q`. Preset yang benar tetap harus dikonfirmasi ulang di Unity, karena
`CubeView` memakai konvensi tangan-kanan Y-atas dan bisa berbeda dari konvensi
kamera di scene.

Jalur BLE belum pernah diuji dengan hardware betulan — yang sudah dibuktikan
baru jalur datanya (parsing, kalibrasi, preset, rising-edge, reconnect) dengan
bleak palsu. Yang perlu dicek saat busur siap: nama advertise berawalan `MOTUS`,
connection interval cukup untuk ~60 Hz, dan skala draw dari sensor tarikan.

## Menambah sumber ketiga

Bikin `Source` + `SourcePanel` baru di file titik masuk sendiri, dan satu kelas
pembaca yang menyediakan `run()`, `stop()`, `send_command()`, callback
`on_packet`/`on_status`. `ui.py` tidak perlu disentuh.
