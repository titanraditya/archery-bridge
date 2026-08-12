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

**Deteksi tembakan terjadi sedekat mungkin dengan sensor, bukan di konsumen.**
Agar tidak bergantung pada frame rate Unity. Di jalur HP, `phone.html` yang
mendeteksi tepi lepas. Di jalur BLE, pengukurannya terpaksa naik satu tingkat
lagi ke firmware: dua IR gate dilewati dalam ~1 ms, sementara notify cuma 60 Hz
(16 ms), jadi firmware yang mencatat Δt dan menaikkan `shot_seq`. `ble_reader.py`
tetap yang memegang counter `shot` yang dikirim ke Unity, jarak gate, dan
penyaringan lintasan yang bukan lesatan.

**Kekuatan tembakan diukur dari kecepatan, bukan dari posisi tarikan.**
Anak panah di rig ini paten — terpasang tetap, tidak dicabut-pasang — dan tidak
ada potensiometer. Penanda di batangnya melewati dua sensor IR berjarak tetap;
Δt-nya jadi m/s. Konsekuensi yang harus diingat: **tidak ada nilai apa pun
selama menarik.** `draw` di paket adalah kekuatan tembakan *terakhir*, ditahan
sampai tembakan berikutnya, bukan posisi tarikan sekarang. Bar tarikan di GUI
karena itu terlihat diam di jalur BLE — itu benar, bukan bug.

**Quaternion yang dikirim relatif terhadap titik kalibrasi** (`ref⁻¹ × q`).
Heading absolut HP/IMU tidak sejajar dengan arah kamera Unity. Tanpa kalibrasi,
crosshair akan menghadap arah acak setiap kali mulai.

**Titik kalibrasi bisa direset dari dua tempat, dikerjakan di satu tempat.**
Busur punya tombol Reset Posisi fisik supaya penembak tidak perlu melepas
pegangan atau minta tolong orang di depan PC. Tombolnya tidak mengubah arah
komunikasi: firmware cuma menyelipkan pencacah `btn_seq` di paket yang memang
sudah mengalir ke PC, dan `ble_reader._center()` yang mengerjakannya — sama
persis dengan yang dipanggil tombol GUI. Dikirim sebagai pencacah, bukan
keadaan tertekan/tidak, dengan alasan yang sama seperti `shot`.

**Firmware busur dibiarkan sebodoh mungkin.** Yang dikirim lewat BLE cuma
quaternion mentah + Δt antar IR gate + pencacah lesatan. Kalibrasi, preset
sumbu, jarak gate, konversi ke m/s, dan counter `shot` dikerjakan
`ble_reader.py`. Pengukuran Δt satu-satunya yang terpaksa di firmware karena
alasan waktu di atas — dan yang dikirim tetap angka mentah, jadi menggeser
posisi sensor cukup mengubah `GATE_DIST_M`. Alasannya: tombol Kalibrasi Tengah dan preset 0–3
tetap bekerja tanpa perlu mem-flash ulang busur setiap kali penyetelan berubah.

Daftar `AXIS_PRESETS` dulu dikembar di `phone.html` dan `ble_reader.py`.
Sekarang **tidak lagi**: IMU busur terpasang dengan sumbu tegak di Z, sedangkan
preset jalur HP sudah dikonfirmasi benar apa adanya, jadi masing-masing daftar
mengoreksi pemasangan sensornya sendiri dan nomor preset yang sama di GUI tidak
berarti hal yang sama di dua jalur.

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
{ "q": [w, x, y, z], "draw": 0.0-1.0, "shot": int, "speed": float }
```

UTF-8 JSON satu baris, UDP ke `127.0.0.1:5005`, sekitar 60 paket/detik.

`q` memakai **konvensi tangan-kiri Unity**, bukan tangan-kanan seperti sensornya.
Pencerminannya (`to_unity_handed()` / `toUnityHanded()`) dikerjakan di sisi
sumber, sesudah preset sumbu, di kedua jalur — tanpa itu bidik ke kanan bikin
panah melesat ke kiri. Ini beda dengan `AXIS_PRESETS` yang sengaja berbeda per
jalur: pencerminan ini sebabnya konvensi Unity, bukan pemasangan sensor, jadi
isinya wajib sama di `phone.html` dan `ble_reader.py`.

Tiga field pertama dikirim dua-duanya, jalur HP dan jalur BLE. `speed` (m/s,
hasil ukur IR gate) cuma ada di jalur BLE — konsumen wajib menganggapnya
opsional. `draw` di jalur BLE adalah `speed` yang dinormalkan lewat
`SPEED_RANGE`, jadi konsumen yang cuma paham `draw` tetap jalan tanpa diubah.

## Kontrak BLE (batas ke firmware)

Jangan tertukar dengan Kontrak paket di atas: yang ini **masuk** ke
`ble_reader.py` (mentah, dari sensor), yang di atas **keluar** darinya (sudah
dikalibrasi dan dipetakan). Firmware tidak pernah mengirim bentuk yang di atas.

Notify lewat Nordic UART Service; `6E400003-…` arah busur → PC. Dua format,
dipilih parser dari byte pertama:

- biner 12 byte inti `<4h H B B`: quaternion ×32767 (mentah, belum dikalibrasi),
  `meas` uint16 (Δt antar IR gate dalam µs, 0 = belum ada tembakan),
  `shot_seq` uint8 (naik tiap lesatan lengkap belakang→depan), `seq`
- byte ke-13 `btn_seq` opsional: pencacah tombol Reset Posisi di busur.
  Paket 12 byte tetap sah — busur tanpa tombol tidak perlu diubah
- JSON `{"q":[…], "dt_us":…, "shot_seq":…, "btn_seq":…}` — untuk firmware coba-coba

`seq` hanya untuk menghitung paket hilang. Tidak ada arah PC → busur: perintah
`center`/`axis` diselesaikan di `ble_reader.py`, dan tombol fisik di busur pun
cuma menumpang paket yang sudah mengalir ke PC.

Versi lengkapnya untuk orang yang menulis firmware ada di `FIRMWARE.md` —
kalau kontrak di `ble_reader.py` berubah, file itu ikut diperbarui.

## File

| File | Peran |
|---|---|
| `ui.py` | Kerangka GUI PySide6 yang dipakai kedua versi. Tidak tahu sumbernya apa |
| `app.py` | Titik masuk versi HP: panel QR/URL + sertifikat + `Relay` |
| `app_ble.py` | Titik masuk versi BLE: panel daftar device + `BleReader` |
| `relay.py` | Kelas `Relay`: server HTTPS + WebSocket, meneruskan ke UDP. Tidak mengubah isi paket. Juga bisa jalan headless |
| `ble_reader.py` | Kelas `BleReader`: notify BLE → paket kontrak → UDP. Kalibrasi, preset sumbu, jarak gate (`GATE_DIST_M`), konversi Δt→m/s, dan counter `shot` ada di sini. Juga bisa jalan headless |
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

`GetDrawStrength()` di jalur BLE bernilai benar hanya *pada saat* `shot`
bertambah — sebelum itu isinya kekuatan tembakan sebelumnya. Jadi baca
`draw`/`speed` bersamaan dengan mendeteksi kenaikan `shot`, jangan dipakai untuk
menganimasikan tarikan busur. Kalau nanti butuh balistik dengan satuan asli,
`speed` (m/s) sudah tersedia di paket dan tidak perlu skala tebak-tebakan.

Panah memakai pendekatan busur kinematik + raycast ke titik kena, bukan
Rigidbody penuh — dipilih demi presisi skor dan kemudahan penyetelan.

## Yang masih terbuka

Mapping sumbu quaternion sumber → Unity: jalur HP sudah dikonfirmasi, jalur BLE
belum. Di jalur BLE, membidik kanan-kiri terbaca sebagai roll di sekitar arah
bidik (kubus berputar, reticle diam) karena sumbu tegak IMU busur ada di Z —
preset 1–3 di `ble_reader.py` semuanya memindahkan Z sumber ke Y, bedanya cuma
arah putar yaw dan sumbu mana yang jadi arah moncong. Tombol **Sumbu** di
`phone.html` memutar antar preset agar bisa dicoba tanpa recompile; tombol
**0–3** di GUI mengirim preset tertentu ke sumber (lewat WebSocket di jalur HP,
diterapkan lokal di jalur BLE), supaya preset bisa diganti tanpa melepas
pegangan pada HP/busur.

Cube-test-nya ada di `ui.py` (`CubeView`) — kubus yang dirotasi langsung oleh
`q`. `CubeView` memakai konvensi tangan-kanan Y-atas, sementara `q` di paket
sekarang sudah tangan-kiri Unity. Akibatnya **kubus dan reticle di GUI kiri-
kanannya tercermin** dibanding gerakan HP/busur yang sebenarnya. Itu benar, bukan
bug: yang dicocokkan sekarang Unity, bukan kubus. Jangan mengganti preset sumbu
untuk "memperbaiki" kubus — preset dipilih dengan melihat sumbu mana yang
bergerak, bukan ke arah mana.

Jalur BLE belum pernah diuji dengan hardware betulan — yang sudah dibuktikan
baru jalur datanya (parsing, kalibrasi, preset, penghitungan shot, reconnect)
dengan bleak palsu. Yang perlu dicek saat busur siap: nama advertise berawalan
`MOTUS`, connection interval cukup untuk ~60 Hz, jarak sensor IR sebenarnya
(`GATE_DIST_M`), dan rentang m/s terpelan–terkencang untuk mengisi
`SPEED_RANGE`. Sampai keduanya diukur, angka `draw` dari jalur BLE cuma tebakan
kasar; `speed` sudah benar begitu `GATE_DIST_M` betul.

## Menambah sumber ketiga

Bikin `Source` + `SourcePanel` baru di file titik masuk sendiri, dan satu kelas
pembaca yang menyediakan `run()`, `stop()`, `send_command()`, callback
`on_packet`/`on_status`. `ui.py` tidak perlu disentuh.
