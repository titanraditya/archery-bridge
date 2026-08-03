# Kontrak firmware busur — sisi BLE

Yang harus dikirim busur fisik supaya `app_ble.py` / `ble_reader.py` langsung
jalan tanpa mengubah kode Python. Semua di dokumen ini adalah pembacaan
`ble_reader.py`; kalau ada yang berubah di sana, dokumen ini ikut berubah.

Ringkasnya: **busur cuma mengirim satu paket 13 byte, ~60 kali per detik,
lewat satu karakteristik notify.** Tidak ada arah sebaliknya. (12 byte kalau
busurnya tanpa tombol Reset Posisi — byte terakhir memang opsional.)

## 0. Sensornya apa saja

| Bagian | Mengukur / gunanya | Jadi apa di Unity |
|---|---|---|
| BNO085 | orientasi busur (quaternion) | arah bidik |
| 2 × IR gate | penanda di batang panah melewati dua titik berjarak tetap | waktu lepas + kekuatan tembakan |
| 1 × tombol | Reset Posisi — dipegang penembak | menyamakan arah bidik dengan tengah layar |

Anak panah **paten** — terpasang tetap di wadahnya, tidak dicabut-pasang.
Jadi tidak ada keadaan "anak panah terpasang / tidak", dan **tidak ada
potensiometer atau sensor tarikan apa pun.** Kekuatan tembakan disimpulkan dari
kecepatan penanda saat melewati kedua sensor IR — artinya nilainya baru ada
**sesudah** panah lepas, tidak ada angka real-time selama menarik.

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

Tombol **Reset Posisi** di busur juga tidak mengubah arah ini: firmware cuma
menyelipkan pencacah tekanan (`btn_seq`) di paket yang memang sudah mengalir ke
PC, dan kalibrasinya tetap dikerjakan `ble_reader.py`.

## 3. Payload — 12–13 byte, little-endian

Format `<4h H B B`, urutannya persis begini:

| Offset | Tipe | Field | Isi |
|---|---|---|---|
| 0–1 | int16 | `qw` | w × 32767 |
| 2–3 | int16 | `qx` | x × 32767 |
| 4–5 | int16 | `qy` | y × 32767 |
| 6–7 | int16 | `qz` | z × 32767 |
| 8–9 | uint16 | `meas` | Δt antar sensor IR dalam **mikrodetik**; 0 = belum ada tembakan |
| 10 | uint8 | `shot_seq` | naik 1 tiap panah selesai melewati **kedua** sensor ke arah depan |
| 11 | uint8 | `seq` | naik 1 tiap paket, wrap di 256 |
| 12 | uint8 | `btn_seq` | **opsional** — naik 1 tiap tombol Reset Posisi ditekan |

Jadi panjangnya 12 byte tanpa tombol, 13 byte dengan tombol; dua-duanya sah.
Byte ke-14 dan seterusnya diabaikan parser, jadi menambah field di belakang
tidak merusak apa-apa.

### Quaternion

Urutan **w dulu**, bukan x. Ini jebakan yang paling sering kena: BNO085
mengeluarkan `i, j, k, real` (x, y, z, w) — harus dibalik sebelum dikirim.

Kirim **mentah**, jangan dikalibrasi di firmware. `ble_reader.py` yang
menghitung `ref⁻¹ × q` terhadap titik kalibrasi, lalu menerapkan preset sumbu.
Normalisasi tidak harus sempurna (Python menormalisasi ulang), tapi nilainya
jangan sampai lewat ±1 sebelum dikali 32767 karena int16 akan overflow diam-diam.

### meas dan shot_seq — dua-duanya dari IR gate

Ini bagian yang **tidak bisa didelegasikan ke Python**, dan satu-satunya
pengecualian dari prinsip "firmware dibiarkan bodoh". Alasannya aritmetika:
pada 50 m/s dengan jarak sensor 5 cm, Δt-nya cuma **1 ms**, sedangkan notify
cuma 60 Hz (16 ms). Kalau firmware tidak mengunci angkanya, kejadiannya lewat
di antara dua paket dan hilang selamanya.

Yang harus dikerjakan firmware:

1. dua sensor IR dipasang di jalur anak panah, jaraknya tetap dan **diukur**
   (angkanya tidak dikirim — dipegang Python di `GATE_DIST_M`)
2. keduanya dipasang di pin interrupt, catat `micros()` saat penanda memicu
3. urutan **belakang → depan** = lesatan. Urutan sebaliknya = busur sedang
   ditarik ulang; **jangan** dihitung, jangan menaikkan `shot_seq`
4. saat urutan lesatan lengkap: `meas = t_depan - t_belakang` (µs),
   lalu `shot_seq++`. Dua-duanya bertahan (latch) sampai tembakan berikutnya
5. sebelum tembakan pertama, kirim `meas = 0`

`ble_reader.py` yang menerjemahkan `meas` → m/s pakai jarak gate, membuang
lintasan yang di luar `SPEED_SANE` (2–200 m/s), menormalkan ke `draw` 0..1
lewat `SPEED_RANGE`, dan menaikkan counter `shot` sebanyak selisih `shot_seq`
— jadi tembakan tidak hilang walau ada notify yang drop.

Catatan angka: `meas` uint16 mentok di 65535 µs. Dengan gate 5 cm itu berarti
kecepatan terendah yang masih terukur ~0,8 m/s — jauh di bawah lesatan mana pun,
jadi aman. Kalau gate-nya jauh lebih lebar (>20 cm), pastikan Δt lesatan
terpelan masih di bawah 65 ms.

Debounce tetap perlu: penanda yang memantul di tepi berkas IR bisa memicu
interrupt dua kali. Abaikan pemicuan kedua pada sensor yang sama dalam ~1 ms —
jangan lebih lama dari itu, karena Δt lesatan sendiri sekitar segitu.

Kalau IR gate belum terpasang, kirim `meas = 0` dan `shot_seq` tetap — jalur
datanya tetap bisa diuji, cuma counter shot diam dan bar draw kosong.

### btn_seq — tombol Reset Posisi

Tombol fisik di busur yang menyamakan arah bidik dengan tengah layar, kembaran
tombol **Kalibrasi Tengah** di GUI. Gunanya supaya penembak bisa mengalibrasi
sendiri tanpa melepas pegangan dan tanpa orang kedua di depan PC.

Firmware **tidak menghitung apa-apa** saat tombol ditekan — cukup lapor bahwa
tombolnya ditekan, lalu `ble_reader.py` yang mengambil orientasi pada paket itu
sebagai titik nol yang baru:

1. tombol ke pin interrupt, debounce ~30 ms (di sini debounce boleh longgar,
   beda dengan IR gate — tekanan jari tidak ada yang secepat 1 ms)
2. tiap tepi tekan: `btn_seq++`. Jangan direset, biarkan wrap sendiri di 256
3. yang dikirim **pencacah, bukan keadaan tertekan/tidak** — alasannya sama
   dengan `shot_seq`: notify yang drop tidak boleh menghilangkan tekanan tombol

Berapa pun kenaikannya dihitung satu kali kalibrasi, jadi tombol yang ditekan
dua kali cepat tidak jadi masalah. Kalau busur belum punya tombol, kirim 12 byte
saja — tombol Kalibrasi Tengah di GUI tetap berfungsi.

### Alternatif: firmware yang menghitung m/s sendiri

Kalau jarak gate mau dipegang firmware, isi `meas` dengan **m/s × 100** dan
jalankan Python-nya dengan `BleReader(speed_unit="speed_x100")`. Parser tidak
bisa membedakan dua mode ini dari isinya, jadi harus disetel di Python. Mode
default `dt_us` lebih disukai: menggeser posisi sensor cukup mengubah satu
konstanta, tanpa flash ulang.

### seq

Cuma untuk statistik. Kalau lompat, `ble_reader.py` menghitungnya sebagai paket
hilang dan melaporkannya ke log GUI maksimal tiap 5 detik. Boleh mulai dari
angka berapa pun.

## 4. Alternatif: JSON satu baris

Untuk firmware coba-coba yang belum mau berurusan dengan struct. **Pengganti**
payload biner di bagian 3, bukan tambahan — isinya sama, bungkusnya beda. Parser
memilih format dari byte pertama: `{` berarti JSON.

Dua baris di bawah juga bukan dua format, melainkan satu format dengan key
alternatif untuk hasil IR gate. Pilih salah satu — sisanya sengaja dibuat
persis sama supaya kelihatan bahwa **cuma dua key itu** yang berbeda:

```json
{"q":[1,0,0,0],"dt_us":1042,"shot_seq":7,"btn_seq":2}
{"q":[1,0,0,0],"speed":47.9,"shot":7,"btn_seq":2}
```

Yang pertama untuk firmware bodoh (dianjurkan): kirim Δt mentah, Python yang
menghitung m/s dan counter. Yang kedua untuk firmware yang sudah menghitung
sendiri — jarak sensor jadi terkunci di firmware.

`q` wajib di dua-duanya. `btn_seq` opsional di dua-duanya — buang saja kalau
busurnya tanpa tombol Reset Posisi, dan sisanya tetap sah.

Jangan tertukar dengan JSON yang keluar dari `ble_reader.py` ke Unity
(`{"q":…,"draw":…,"shot":…,"speed":…}`, ada di README). Itu **hasil olahan** dari
yang di sini: sudah dikalibrasi, `dt_us` sudah jadi m/s dan `draw`, `shot_seq`
sudah jadi counter `shot`. Firmware tidak pernah mengirim bentuk itu.

| Key | Wajib | Arti |
|---|---|---|
| `q` | ya | `[w, x, y, z]`, mentah |
| `dt_us` | salah satu | int µs antar sensor, sama seperti `meas` |
| `speed` | salah satu | float m/s, kalau firmware sudah menghitung sendiri. Menang atas `dt_us` |
| `shot_seq` | ya kalau tanpa `shot` | int, naik tiap lesatan — Python yang menghitung counter |
| `shot` | — | int counter absolut, kalau firmware mau menghitung sendiri. Menang atas `shot_seq` |
| `btn_seq` | tidak | int, naik tiap tombol Reset Posisi ditekan |

Baris seperti di atas ~45 byte, sedangkan MTU ATT default cuma menyisakan
**20 byte** payload notify. Jadi mode JSON butuh negosiasi MTU
(`NimBLEDevice::setMTU(128)` atau setara). Format biner 13 byte muat di MTU
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
- jarak antar gate dan konversi Δt → m/s
- penyaringan lintasan yang bukan lesatan
- normalisasi m/s → `draw` 0..1 dan counter `shot` yang dikirim ke Unity
- format paket ke Unity
- reconnect setelah putus

Semuanya di `ble_reader.py`.

Satu-satunya yang tidak bisa dipindah ke Python adalah **pengukuran waktu antar
gate** (bagian `meas`/`shot_seq` di atas) — kejadiannya ~1 ms, terlalu cepat
untuk notify 60 Hz. Karena yang dikirim cuma Δt mentah dan pencacah, penyetelan
apa pun sesudahnya tetap tidak butuh flash ulang.

## 7. Kerangka ESP32 (NimBLE-Arduino)

```cpp
#include <NimBLEDevice.h>

#define NUS_SERVICE "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

struct __attribute__((packed)) Packet {
  int16_t  qw, qx, qy, qz;
  uint16_t meas;      // dt antar gate, mikrodetik
  uint8_t  shot_seq;
  uint8_t  seq;
  uint8_t  btn_seq;   // buang field ini kalau busur tanpa tombol -> 12 byte
};
static_assert(sizeof(Packet) == 13, "12 tanpa tombol, 13 dengan tombol");

#define PIN_GATE_BACK  16          // sensor yang dilewati lebih dulu saat lepas
#define PIN_GATE_FRONT 17
#define PIN_BTN         4          // tombol Reset Posisi, ke GND

NimBLECharacteristic* tx;

volatile uint32_t tBack = 0;       // micros() pemicuan terakhir gate belakang
volatile uint16_t gMeas = 0;       // dilatch sampai tembakan berikutnya
volatile uint8_t  gShotSeq = 0;
volatile uint32_t tBtn = 0;
volatile uint8_t  gBtnSeq = 0;

void IRAM_ATTR onBack()  { tBack = micros(); }

void IRAM_ATTR onBtn() {
  uint32_t t = millis();
  if (t - tBtn < 30) return;       // debounce jari, boleh longgar
  tBtn = t;
  gBtnSeq++;                       // cuma lapor "ditekan"; Python yang mengalibrasi
}

void IRAM_ATTR onFront() {
  uint32_t t = micros();
  if (!tBack) return;              // depan duluan = ditarik ulang, abaikan
  uint32_t dt = t - tBack;
  tBack = 0;                       // wajib: satu lesatan dihitung sekali saja
  if (dt < 50 || dt > 65000) return;   // pantulan sensor / gerakan pelan
  gMeas = (uint16_t)dt;
  gShotSeq++;
}

void setup() {
  NimBLEDevice::init("MOTUS-BOW");          // nama harus berawalan MOTUS
  auto* svc = NimBLEDevice::createServer()->createService(NUS_SERVICE);
  tx = svc->createCharacteristic(NUS_TX, NIMBLE_PROPERTY::NOTIFY);
  svc->start();

  pinMode(PIN_GATE_BACK, INPUT_PULLUP);
  pinMode(PIN_GATE_FRONT, INPUT_PULLUP);
  attachInterrupt(PIN_GATE_BACK,  onBack,  FALLING);   // FALLING = penanda menghalangi
  attachInterrupt(PIN_GATE_FRONT, onFront, FALLING);

  pinMode(PIN_BTN, INPUT_PULLUP);
  attachInterrupt(PIN_BTN, onBtn, FALLING);

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
  p.meas     = gMeas;                        // dilatch ISR, tidak dihitung di sini
  p.shot_seq = gShotSeq;
  p.seq      = seq++;
  p.btn_seq  = gBtnSeq;

  tx->setValue((uint8_t*)&p, sizeof(p));
  tx->notify();
  delay(16);                                 // ~60 Hz
}
```

API pemasangan nama advertising beda tipis antara NimBLE 1.x dan 2.x —
sesuaikan dengan versi library yang terpasang.

## 8. Urutan pengujian yang disarankan

Jangan menunggu IMU dan IR gate selesai sekaligus. Naikkan satu per satu:

1. **Firmware dummy** — quaternion yang berputar pelan di sumbu Z, `shot_seq`
   naik tiap 3 detik dengan `meas` tetap (mis. 1200 µs). Jalankan
   `python app_ble.py`: kubus berputar mulus, counter naik tiap 3 detik, dan
   angka m/s di samping bar draw menunjukkan `GATE_DIST_M / 1200 µs`.
2. **IMU asli, IR masih palsu** — cek kubus mengikuti busur, lalu tekan
   **Kalibrasi Tengah** dan pastikan kubus kembali ke posisi netral.
   Uji tombol fisiknya di sini juga: arahkan busur ke mana saja, tekan tombol,
   kubus harus langsung netral dan log GUI menulis
   `titik tengah direset dari tombol busur`. Kalau baris itu muncul berkali-kali
   sekali tekan, debounce-nya kurang.
3. **IR gate asli, arah dulu** — tarik busur pelan-pelan tanpa melepas.
   `shot` **tidak boleh naik sama sekali**. Kalau naik, urutan pin belakang dan
   depan tertukar.
4. **IR gate asli, lesatan** — lepas beberapa kali. `shot` naik tepat satu per
   lepasan (kalau naik dua, debounce/`tBack = 0` bocor), dan angka m/s masuk
   akal. Bandingkan dengan rekaman kamera lambat kalau ragu.
5. **Setel rentang** — tembak pelan dan sekuat tenaga, catat m/s terendah dan
   tertinggi, lalu isikan ke `SPEED_RANGE` di `ble_reader.py` supaya bar draw
   memakai rentang penuh.
6. **Uji laju** — angka Hz di kanan atas GUI harus stabil di ~60. Kalau jauh di
   bawah, perbaiki connection interval dulu sebelum menyalahkan kode Python.

Checklist sebelum bilang "firmware selesai":

- [ ] nama advertise berawalan `MOTUS`
- [ ] notify di `6E400003-…`, tanpa enkripsi
- [ ] 12 byte (13 kalau ada tombol), urutan w,x,y,z lalu meas, shot_seq, seq, btn_seq
- [ ] quaternion mentah (tidak dikalibrasi di firmware), w di depan
- [ ] `meas` = Δt antar gate dalam µs, dilatch sampai tembakan berikutnya
- [ ] `shot_seq` cuma naik untuk urutan belakang→depan, sudah di-debounce
- [ ] jarak antar sensor IR sudah diukur dan dicatat di `GATE_DIST_M`
- [ ] `btn_seq` naik sekali per tekan (kalau tombolnya ada)
- [ ] `seq` naik tiap paket
- [ ] ~60 Hz dengan connection interval ≤ 15 ms
