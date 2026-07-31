# Kontrak firmware busur — sisi BLE

Yang harus dikirim busur fisik supaya `app_ble.py` / `ble_reader.py` langsung
jalan tanpa mengubah kode Python. Semua di dokumen ini adalah pembacaan
`ble_reader.py`; kalau ada yang berubah di sana, dokumen ini ikut berubah.

Ringkasnya: **busur cuma mengirim satu paket 12 byte, ~60 kali per detik,
lewat satu karakteristik notify.** Tidak ada arah sebaliknya.

## 1. Advertising

| Hal | Nilai | Kenapa |
|---|---|---|
| Local name | diawali `MOTUS` — mis. `MOTUS-BOW` | `BleReader._scan()` mencocokkan awalan nama, bukan MAC. Kalau cuma satu yang cocok, langsung disambung otomatis |
| Service UUID di paket adv | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | Opsional — scan tidak memfilter service. Tetap dipasang supaya gampang dikenali di nRF Connect |
| Enkripsi / bonding | **jangan dipakai** | Karakteristik yang minta pairing bikin `bleak` gagal connect tanpa dialog OS |

Alamat perangkat tidak perlu tetap; pencocokan lewat nama. Kalau mau memaksa
satu alamat: `python ble_reader.py AA:BB:CC:DD:EE:FF`.

## 2. Karakteristik

Cuma satu yang wajib. Nordic UART Service dipilih karena hampir semua stack —
ESP32, nRF52, Arduino — sudah punya contohnya.

| Peran | UUID | Properti |
|---|---|---|
| Busur → PC (data) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | **Notify** + CCCD `0x2902` (biasanya ditambahkan otomatis oleh stack) |
| PC → busur | — | **tidak ada** |

Python tidak pernah menulis apa pun ke busur. Perintah **Kalibrasi Tengah** dan
preset sumbu **0–3** di GUI diselesaikan di `ble_reader.py`, bukan dikirim ke
firmware — itu sebabnya penyetelan bisa diubah tanpa flash ulang.

## 3. Payload — 12 byte, little-endian

Format `<4h H B B`, urutannya persis begini:

| Offset | Tipe | Field | Isi |
|---|---|---|---|
| 0–1 | int16 | `qw` | w × 32767 |
| 2–3 | int16 | `qx` | x × 32767 |
| 4–5 | int16 | `qy` | y × 32767 |
| 6–7 | int16 | `qz` | z × 32767 |
| 8–9 | uint16 | `draw` | 0..65535 → 0.0..1.0 |
| 10 | uint8 | `flags` | bit0 = IR terhalang (anak panah terpasang); bit1–7 isi 0 |
| 11 | uint8 | `seq` | naik 1 tiap paket, wrap di 256 |

Byte ke-13 dan seterusnya diabaikan parser, jadi menambah field di belakang
tidak merusak apa-apa.

### Quaternion

Urutan **w dulu**, bukan x. Ini jebakan yang paling sering kena: BNO085
mengeluarkan `i, j, k, real` (x, y, z, w) — harus dibalik sebelum dikirim.

Kirim **mentah**, jangan dikalibrasi di firmware. `ble_reader.py` yang
menghitung `ref⁻¹ × q` terhadap titik kalibrasi, lalu menerapkan preset sumbu.
Normalisasi tidak harus sempurna (Python menormalisasi ulang), tapi nilainya
jangan sampai lewat ±1 sebelum dikali 32767 karena int16 akan overflow diam-diam.

### draw

Ini **satu-satunya yang belum ditangani sisi Python.** `ble_reader.py` cuma
menjepit hasilnya ke 0..1; pemetaan nilai sensor mentah → 0..65535 tugas
firmware:

1. simpan nilai saat tali kendur (`draw_min`) dan saat tarikan penuh (`draw_max`)
2. kirim `(mentah - draw_min) / (draw_max - draw_min) * 65535`, dijepit di ujungnya

Kalau busur belum punya sensor tarikan, kirim `0` dulu — jalur datanya tetap
bisa diuji, cuma bar tarikan di GUI diam.

### flags bit0 dan penghitungan tembakan

`1` selama anak panah menempel / IR terhalang, `0` saat lepas.

`ble_reader.py` menaikkan counter `shot` pada transisi **1 → 0**. Konsekuensinya:

- sensor IR harus **terhalang selama menarik** dan bersih tepat saat lepas
- tiap tepi turun dihitung satu tembakan, jadi sinyal yang bouncing akan
  menghasilkan tembakan hantu — **beri debounce di firmware** (mis. abaikan
  perubahan yang lebih cepat dari ~30 ms)
- tidak perlu mengirim event "lepas"; keadaan sekarang saja sudah cukup

### seq

Cuma untuk statistik. Kalau lompat, `ble_reader.py` menghitungnya sebagai paket
hilang dan melaporkannya ke log GUI maksimal tiap 5 detik. Boleh mulai dari
angka berapa pun.

## 4. Alternatif: JSON satu baris

Untuk firmware coba-coba yang belum mau berurusan dengan struct. Parser memilih
format dari byte pertama — `{` berarti JSON.

```json
{"q":[1,0,0,0],"draw":0.42,"nocked":true}
{"q":[1,0,0,0],"draw":0.42,"shot":7}
```

| Key | Wajib | Arti |
|---|---|---|
| `q` | ya | `[w, x, y, z]`, mentah |
| `draw` | tidak (default 0) | 0.0–1.0, sudah dalam satuan akhir |
| `nocked` atau `ir` | salah satu | bool — Python yang menghitung tembakan, sama seperti flags bit0 |
| `shot` | — | int, kalau firmware mau menghitung sendiri. Menang atas `nocked` |

Baris seperti di atas ~45 byte, sedangkan MTU ATT default cuma menyisakan
**20 byte** payload notify. Jadi mode JSON butuh negosiasi MTU
(`NimBLEDevice::setMTU(128)` atau setara). Format biner 12 byte muat di MTU
default — itu salah satu alasan format biner jadi yang utama.

## 5. Parameter koneksi

| Hal | Target |
|---|---|
| Laju notify | ~60 Hz (tiap ~16 ms) |
| Connection interval | ≤ 15 ms; 7.5 ms kalau mau lega |
| Slave latency | 0 — ini data real-time, jangan ada |

Kalau interval-nya 30 ms ke atas, notify akan menumpuk dan pesan
`n paket BLE hilang` mulai muncul di log GUI.

## 6. Yang tidak perlu dikerjakan firmware

Sengaja dibiarkan bodoh, supaya busur tidak perlu di-flash ulang tiap
penyetelan berubah:

- kalibrasi tengah (`ref⁻¹ × q`)
- preset sumbu 0–3
- penghitungan counter `shot`
- format paket ke Unity
- reconnect setelah putus

Semuanya di `ble_reader.py`.

## 7. Kerangka ESP32 (NimBLE-Arduino)

```cpp
#include <NimBLEDevice.h>

#define NUS_SERVICE "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

struct __attribute__((packed)) Packet {
  int16_t  qw, qx, qy, qz;
  uint16_t draw;
  uint8_t  flags;
  uint8_t  seq;
};
static_assert(sizeof(Packet) == 12, "harus 12 byte");

NimBLECharacteristic* tx;

void setup() {
  NimBLEDevice::init("MOTUS-BOW");          // nama harus berawalan MOTUS
  auto* svc = NimBLEDevice::createServer()->createService(NUS_SERVICE);
  tx = svc->createCharacteristic(NUS_TX, NIMBLE_PROPERTY::NOTIFY);
  svc->start();

  auto* adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->start();
}

void loop() {
  static uint8_t seq = 0;
  float q[4];                                // w,x,y,z — mentah dari IMU
  readImuQuaternion(q);

  Packet p;                                  // ESP32 little-endian, jadi
  p.qw = (int16_t)(q[0] * 32767.0f);         // struct-nya bisa dikirim apa adanya
  p.qx = (int16_t)(q[1] * 32767.0f);
  p.qy = (int16_t)(q[2] * 32767.0f);
  p.qz = (int16_t)(q[3] * 32767.0f);
  p.draw  = (uint16_t)(constrain(readDraw01(), 0.0f, 1.0f) * 65535.0f);
  p.flags = irBlocked() ? 0x01 : 0x00;
  p.seq   = seq++;

  tx->setValue((uint8_t*)&p, sizeof(p));
  tx->notify();
  delay(16);                                 // ~60 Hz
}
```

API pemasangan nama advertising beda tipis antara NimBLE 1.x dan 2.x —
sesuaikan dengan versi library yang terpasang.

## 8. Urutan pengujian yang disarankan

Jangan menunggu IMU dan sensor tarikan selesai sekaligus. Naikkan satu per satu:

1. **Firmware dummy** — quaternion yang berputar pelan di sumbu Z, `draw` naik
   turun sinus, `flags` berdenyut tiap 3 detik. Jalankan `python app_ble.py`:
   kubus harus berputar mulus, bar terisi, counter naik tiap 3 detik.
2. **IMU asli, draw dan IR masih palsu** — cek kubus mengikuti busur, lalu tekan
   **Kalibrasi Tengah** dan pastikan kubus kembali ke posisi netral.
3. **Sensor IR asli** — tarik dan lepas, pastikan `shot` naik tepat satu kali
   per lepasan (di sinilah debounce ketahuan kurang).
4. **Sensor tarikan asli** — setel `draw_min` / `draw_max`, pastikan bar
   mencapai 100% saat tarikan penuh dan 0% saat kendur.
5. **Uji laju** — angka Hz di kanan atas GUI harus stabil di ~60. Kalau jauh di
   bawah, perbaiki connection interval dulu sebelum menyalahkan kode Python.

Checklist sebelum bilang "firmware selesai":

- [ ] nama advertise berawalan `MOTUS`
- [ ] notify di `6E400003-…`, tanpa enkripsi
- [ ] 12 byte, urutan w,x,y,z lalu draw, flags, seq
- [ ] quaternion mentah (tidak dikalibrasi di firmware), w di depan
- [ ] `draw` sudah dipetakan ke rentang penuh 0..65535
- [ ] IR terhalang saat menarik, bersih saat lepas, sudah di-debounce
- [ ] `seq` naik tiap paket
- [ ] ~60 Hz dengan connection interval ≤ 15 ms
