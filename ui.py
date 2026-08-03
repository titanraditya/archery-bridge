"""
Motus Bridge — kerangka GUI yang dipakai kedua versi.

    python app.py       versi HP  — sumber: Relay (HTTPS + WebSocket)
    python app_ble.py   versi BLE — sumber: BleReader (bleak)

Isi file ini adalah semua yang tidak peduli datanya datang dari mana: cube
test, bar draw, counter shot, readout, log, tombol Start/Stop, dan panel
Kontrol (Kalibrasi Tengah + preset sumbu 0–3).

Yang khusus per jalur dibungkus dua kelas yang disediakan pemanggil:

    Source        adapter — bikin thread & panel, plus persiapan sebelum start
    SourcePanel   widget kartu "SUMBER" — QR/URL untuk HP, daftar device untuk BLE

Kontrak status yang dipahami MainWindow (sudah dinormalkan oleh SourceThread
masing-masing versi, jadi tidak sama dengan nama status internal Relay):

    ready        target=<teks>, detail=<teks>   sumber siap, belum ada data
    connected    remote=<teks|None>
    disconnected
    stopped
    info         text=<teks>, level="info"|"warn"

Status apa pun juga diteruskan ke panel lewat on_status(), supaya versi BLE
bisa menambah kind sendiri (mis. "devices") tanpa menyentuh file ini.
"""

import math
import sys
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

BG = "#0d0f12"
PANEL = "#12151a"
LINE = "#232830"
FG = "#e8eaed"
DIM = "#6b7280"
ACCENT = "#4ade80"
WARN = "#f59e0b"
AXIS_COLORS = ("#f87171", "#4ade80", "#60a5fa")

STALE_S = 1.5  # tanpa paket selama ini, tampilan dianggap tidak hidup


# -------------------------------------------------------------------- math


def rotate(q, v):
    """Rotasi vektor oleh quaternion: v' = q v q*."""
    w, x, y, z = q
    tx = 2 * (y * v[2] - z * v[1])
    ty = 2 * (z * v[0] - x * v[2])
    tz = 2 * (x * v[1] - y * v[0])
    return (
        v[0] + w * tx + (y * tz - z * ty),
        v[1] + w * ty + (z * tx - x * tz),
        v[2] + w * tz + (x * ty - y * tx),
    )


def to_euler(q):
    """Yaw/pitch/roll derajat — konvensi sama persis dengan monitor.py."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    d = 180 / math.pi
    return yaw * d, pitch * d, roll * d


# ------------------------------------------------------------------ sumber


class SourceThread(QThread):
    """Menjalankan sumber data di thread sendiri.

    Callback dari thread sumber diubah jadi signal Qt, yang otomatis di-queue
    ke thread GUI — jadi tidak ada widget yang disentuh dari thread jaringan.

    `factory(on_packet, on_status)` mengembalikan objek yang menyediakan
    run(), stop(), dan send_command(). Relay dan BleReader dua-duanya cocok.
    """

    packet = Signal(dict)
    status = Signal(str, dict)
    failed = Signal(str)

    def __init__(self, factory, oserror_hint: str = "kesalahan I/O") -> None:
        super().__init__()
        self._hint = oserror_hint
        self.source = factory(
            self.packet.emit,
            lambda kind, **f: self.status.emit(kind, f),
        )

    def run(self) -> None:
        try:
            self.source.run()
        except OSError as e:
            self.failed.emit(f"{self._hint}: {e}")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class Source:
    """Adapter: satu-satunya tempat yang tahu jalur datanya apa.

    Versi HP dan versi BLE masing-masing menurunkan kelas ini; sisa GUI tidak
    pernah menyebut Relay maupun BleReader.
    """

    window_title = "Motus Bridge"
    note = ""

    def panel(self) -> "SourcePanel":
        raise NotImplementedError

    def thread(self) -> SourceThread:
        raise NotImplementedError

    def prepare(self, log) -> bool:
        """Dipanggil sebelum thread dinyalakan. False = batalkan start.

        Jalur HP memakai ini untuk sertifikat; jalur BLE tidak butuh apa-apa.
        """
        return True

    def tick(self, log) -> None:
        """Dipanggil tiap 5 detik selama GUI hidup (mis. cek IP LAN berubah)."""


# ----------------------------------------------------------------- widgets


class SourcePanel(QFrame):
    """Kartu "SUMBER": cara menyambungkan sumber data.

    Jalur HP: QR + URL. Jalur BLE: daftar device hasil scan + tombol Sambung.
    Yang di luar kelas ini tidak peduli sumbernya apa — cukup show_target() /
    clear(), dan `command` untuk hal yang perlu dikirim balik ke sumber.
    """

    command = Signal(dict)

    def __init__(self, title: str = "SUMBER") -> None:
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(14, 12, 14, 14)
        self.box.setSpacing(9)
        label = QLabel(title.upper())
        label.setObjectName("cardTitle")
        self.box.addWidget(label)

    def show_target(self, fields: dict) -> None:
        """Sumber siap. `fields` berisi minimal target= dan detail=."""

    def clear(self) -> None:
        """Sumber berhenti."""

    def on_status(self, kind: str, fields: dict) -> None:
        """Status apa pun, termasuk kind khusus versi ini."""


class CubeView(QWidget):
    """Cube-test: kubus yang dirotasi langsung oleh q, plus reticle arah bidik.

    Alat untuk menemukan preset sumbu yang benar (lihat CLAUDE.md) — putar
    sumbernya, lihat kubus, pilih preset yang bikin gerakannya masuk akal.
    """

    TILT = math.pi / 9  # kamera dimiringkan; tanpa ini yaw murni sulit dibaca
    VERTS = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    EDGES = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.live = False
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _project(self, v, cx, cy, scale):
        cT, sT = math.cos(self.TILT), math.sin(self.TILT)
        y = v[1] * cT - v[2] * sT
        z = v[1] * sT + v[2] * cT
        p = 3.6 / (3.6 + z)  # perspektif ringan
        return cx + v[0] * scale * p, cy - y * scale * p

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0a0c0f"))

        cx, cy = w / 2, h / 2
        scale = min(w, h) * 0.21
        pts = [self._project(rotate(self.q, v), cx, cy, scale) for v in self.VERTS]

        p.setPen(QPen(QColor("#8b95a3"), 2))
        for a, b in self.EDGES:
            p.drawLine(int(pts[a][0]), int(pts[a][1]), int(pts[b][0]), int(pts[b][1]))

        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        for vec, color, label in (
            ((1.9, 0, 0), AXIS_COLORS[0], "X"),
            ((0, 1.9, 0), AXIS_COLORS[1], "Y"),
            ((0, 0, 1.9), AXIS_COLORS[2], "Z"),
        ):
            ex, ey = self._project(rotate(self.q, vec), cx, cy, scale)
            p.setPen(QPen(QColor(color), 3))
            p.drawLine(int(cx), int(cy), int(ex), int(ey))
            p.drawText(int(ex) + 6, int(ey) + 4, label)

        self._reticle(p, w)

        if not self.live:
            p.setPen(QColor(DIM))
            p.drawText(14, h - 14, "menunggu paket…")
        p.end()

    def _reticle(self, p: QPainter, w: int) -> None:
        """Ke mana moncong busur menghadap — arah -Z lokal, diproyeksikan datar."""
        fx, fy, _ = rotate(self.q, (0, 0, -1))
        rx, ry, r = w - 76, 76, 46

        p.setPen(QPen(QColor("#262d36"), 1))
        p.drawEllipse(int(rx - r), int(ry - r), int(r * 2), int(r * 2))
        p.drawLine(int(rx - r), int(ry), int(rx + r), int(ry))
        p.drawLine(int(rx), int(ry - r), int(rx), int(ry + r))

        px = rx + max(-1.0, min(1.0, fx)) * r
        py = ry - max(-1.0, min(1.0, fy)) * r
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(int(px - 5), int(py - 5), 10, 10)
        p.setBrush(Qt.NoBrush)


class DrawBar(QWidget):
    """Bar tarikan 0–1."""

    def __init__(self) -> None:
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(18)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1b1f26"))
        p.drawRoundedRect(self.rect(), 9, 9)
        w = int(self.width() * max(0.0, min(1.0, self.value)))
        if w > 4:
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(0, 0, w, self.height(), 9, 9)
        p.end()


def card(title: str, *items) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(14, 12, 14, 14)
    box.setSpacing(9)
    label = QLabel(title.upper())
    label.setObjectName("cardTitle")
    box.addWidget(label)
    for item in items:
        if isinstance(item, QWidget):
            box.addWidget(item)
        else:
            box.addLayout(item)
    return frame


# ------------------------------------------------------------------ window


class MainWindow(QMainWindow):
    def __init__(self, spec: Source) -> None:
        super().__init__()
        self.spec = spec
        self.setWindowTitle(spec.window_title)
        self.resize(940, 620)

        self.thread: SourceThread | None = None
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.draw = 0.0
        self.shot = 0
        # Hanya diisi sumber yang mengukur kecepatan lesatan (jalur BLE, IR gate).
        # None = sumbernya tidak punya, labelnya tetap seperti semula.
        self.speed: float | None = None
        self.axis_sent: int | None = None
        self.connected = False
        self.last_packet = 0.0
        self.flash_until = 0.0
        self.window_count = 0
        self.window_start = time.monotonic()

        self.mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self._build()
        self._style()

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh)
        self.tick.start(16)

        self.slow = QTimer(self)
        self.slow.timeout.connect(lambda: self.spec.tick(self.log))
        self.slow.start(5000)

        self.start_source()

    # ---- tata letak ---------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.setSpacing(12)
        outer.addLayout(self._header())

        left = QVBoxLayout()
        left.setSpacing(12)
        self.panel = self.spec.panel()
        self.panel.command.connect(self.send_command)
        left.addWidget(self.panel)
        left.addWidget(self._controls())
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.cube = CubeView()
        right.addWidget(card("Cube test — dirotasi langsung oleh q", self.cube), 1)
        right.addWidget(self._data())

        body = QHBoxLayout()
        body.setSpacing(12)
        leftWrap = QWidget()
        leftWrap.setFixedWidth(260)
        leftWrap.setLayout(left)
        body.addWidget(leftWrap)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        self.logView = QPlainTextEdit()
        self.logView.setReadOnly(True)
        self.logView.setFont(self.mono)
        self.logView.setFixedHeight(84)
        outer.addWidget(self.logView)

        self.setCentralWidget(root)

    def _header(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        title = QLabel("MOTUS BRIDGE")
        title.setObjectName("title")
        bar.addWidget(title)

        self.dot = QLabel()
        self.dot.setObjectName("dot")
        self.dot.setFixedSize(9, 9)
        bar.addWidget(self.dot)

        self.stateLabel = QLabel("berhenti")
        self.stateLabel.setObjectName("dim")
        bar.addWidget(self.stateLabel)
        bar.addStretch(1)

        self.hzLabel = QLabel("0.0 Hz")
        self.hzLabel.setObjectName("dim")
        bar.addWidget(self.hzLabel)

        self.runBtn = QPushButton("Stop")
        self.runBtn.setObjectName("primary")
        self.runBtn.setFixedWidth(88)
        self.runBtn.clicked.connect(self.toggle_source)
        bar.addWidget(self.runBtn)
        return bar

    def _controls(self) -> QWidget:
        self.centerBtn = QPushButton("Kalibrasi Tengah")
        self.centerBtn.setObjectName("primary")
        self.centerBtn.clicked.connect(self.send_center)

        grid = QGridLayout()
        grid.setSpacing(6)
        self.axisBtns = []
        for i in range(4):
            b = QPushButton(str(i))
            b.clicked.connect(lambda _=False, n=i: self.send_axis(n))
            grid.addWidget(b, 0, i)
            self.axisBtns.append(b)

        note = QLabel("Preset sumbu 0–3.")
        note.setObjectName("note")
        self._set_controls(False)
        return card("Kontrol", self.centerBtn, grid, note)

    def _data(self) -> QWidget:
        self.drawBar = DrawBar()

        row = QHBoxLayout()
        self.drawPct = QLabel("draw 0%")
        self.drawPct.setObjectName("dim")
        self.drawPct.setFont(self.mono)
        self.shotLabel = QLabel("shot 0")
        self.shotLabel.setFont(self.mono)
        row.addWidget(self.drawPct)
        row.addStretch(1)
        row.addWidget(self.shotLabel)

        self.readout = QLabel("—")
        self.readout.setFont(self.mono)
        self.readout.setObjectName("readout")
        return card("Paket", self.drawBar, row, self.readout)

    def _style(self) -> None:
        self.setStyleSheet(f"""
            QWidget#root, QMainWindow {{ background:{BG}; }}
            QLabel {{ background:transparent; color:{FG}; font-size:13px; }}
            QLabel#title {{ font-size:14px; font-weight:600; letter-spacing:2px; }}
            QLabel#dim, QLabel#note {{ color:{DIM}; font-size:12px; }}
            QLabel#cardTitle {{ color:{DIM}; font-size:10px; font-weight:600;
                                letter-spacing:1px; }}
            QLabel#dot {{ background:{DIM}; border-radius:4px; }}
            QLabel#qr {{ background:#ffffff; border-radius:8px; color:#888; padding:8px; }}
            QLabel#url {{ color:{ACCENT}; background:{BG}; border:1px solid {LINE};
                          border-radius:8px; padding:7px; font-size:12px; }}
            QLabel#readout {{ color:{DIM}; font-size:12px; }}
            QFrame#card {{ background:{PANEL}; border:1px solid {LINE};
                           border-radius:12px; }}
            QPushButton {{ background:#171b21; color:{FG}; border:1px solid {LINE};
                           border-radius:8px; padding:7px 10px; font-size:13px; }}
            QPushButton:hover:enabled {{ background:#1f242c; }}
            QPushButton:disabled {{ color:#454b54; }}
            QPushButton#primary {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QPushButton[sel="true"] {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QListWidget {{ background:#0a0c0f; color:{FG}; border:1px solid {LINE};
                           border-radius:8px; font-size:12px; padding:2px; }}
            QListWidget::item {{ padding:5px 6px; border-radius:5px; }}
            QListWidget::item:selected {{ background:#1f242c; color:{ACCENT}; }}
            QPlainTextEdit {{ background:#0a0c0f; color:{DIM}; border:1px solid {LINE};
                              border-radius:8px; font-size:12px; padding:6px; }}
        """)

    # ---- siklus hidup sumber -------------------------------------------

    def toggle_source(self) -> None:
        if self.thread and self.thread.isRunning():
            self.stop_source()
        else:
            self.start_source()

    def start_source(self) -> None:
        if not self.spec.prepare(self.log):
            return

        self.thread = self.spec.thread()
        self.thread.packet.connect(self.on_packet)
        self.thread.status.connect(self.on_status)
        self.thread.failed.connect(self.on_failed)
        self.thread.start()
        self.runBtn.setText("Stop")

    def stop_source(self) -> None:
        if not self.thread:
            return
        self.thread.source.stop()
        if not self.thread.wait(3000):
            self.thread.terminate()
        self.thread = None
        self.runBtn.setText("Start")
        self._set_connected(False)
        self.stateLabel.setText("berhenti")
        self.panel.clear()
        self.log("berhenti")

    def on_status(self, kind: str, fields: dict) -> None:
        if kind == "ready":
            self.panel.show_target(fields)
            self.stateLabel.setText("menunggu sumber")
            detail = fields.get("detail", "")
            self.log(f"jalan — {fields.get('target', '')}{f' → {detail}' if detail else ''}",
                     ACCENT)
        elif kind == "connected":
            self._set_connected(True, fields.get("remote"))
        elif kind in ("disconnected", "stopped"):
            self._set_connected(False)
        elif kind == "info":
            self.log(fields.get("text", ""), WARN if fields.get("level") == "warn" else DIM)

        # Kind khusus per versi (mis. "devices" di jalur BLE) ditangani panel.
        self.panel.on_status(kind, fields)

    def on_failed(self, message: str) -> None:
        self.log(message, WARN)
        self.thread = None
        self.runBtn.setText("Start")
        self.stateLabel.setText("gagal")

    def on_packet(self, p: dict) -> None:
        q = p.get("q")
        if isinstance(q, (list, tuple)) and len(q) == 4:
            try:
                self.q = tuple(float(v) for v in q)
            except (TypeError, ValueError):
                return
        try:
            self.draw = float(p.get("draw", 0.0))
            shot = int(p.get("shot", 0))
            speed = p.get("speed")
            self.speed = float(speed) if speed is not None else None
        except (TypeError, ValueError):
            return

        if shot > self.shot:
            self.flash_until = time.monotonic() + 0.3
        self.shot = shot

        self.window_count += 1
        self.last_packet = time.monotonic()

    # ---- kontrol -------------------------------------------------------

    def send_command(self, cmd: dict) -> None:
        """Perintah ke sumber. Sumber menyaringnya sendiri dengan whitelist."""
        if self.thread:
            self.thread.source.send_command(cmd)

    def send_center(self) -> None:
        if self.thread:
            self.send_command({"cmd": "center"})
            self.log("kalibrasi tengah dikirim")

    def send_axis(self, n: int) -> None:
        if not self.thread:
            return
        self.send_command({"cmd": "axis", "value": n})
        self.axis_sent = n
        for i, b in enumerate(self.axisBtns):
            b.setProperty("sel", i == n)
            b.style().unpolish(b)
            b.style().polish(b)
        self.log(f"preset sumbu {n} dikirim")

    # ---- refresh -------------------------------------------------------

    def _set_connected(self, connected: bool, remote: str | None = None) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        self.dot.setStyleSheet(
            f"background:{ACCENT if connected else DIM};border-radius:4px"
        )
        self.stateLabel.setText("terhubung" if connected else "menunggu sumber")
        self._set_controls(connected)
        if connected:
            self.log(f"terhubung{f' dari {remote}' if remote else ''}", ACCENT)
        else:
            self.log("terputus", WARN)

    def _set_controls(self, enabled: bool) -> None:
        self.centerBtn.setEnabled(enabled)
        for b in self.axisBtns:
            b.setEnabled(enabled)

    def _refresh(self) -> None:
        now = time.monotonic()

        if now - self.window_start >= 0.5:
            self.hzLabel.setText(f"{self.window_count / (now - self.window_start):.1f} Hz")
            self.window_count = 0
            self.window_start = now

        live = now - self.last_packet < STALE_S
        self.cube.q = self.q
        self.cube.live = live
        self.cube.update()

        self.drawBar.value = self.draw
        self.drawBar.update()
        pct = f"draw {round(self.draw * 100)}%"
        if self.speed is not None:
            # Sumbernya IR gate: angkanya kekuatan tembakan TERAKHIR, bukan
            # posisi tarikan sekarang — jadi sebut kecepatannya biar tidak
            # disangka bar-nya macet.
            pct += f"   {self.speed:.1f} m/s"
        self.drawPct.setText(pct)

        self.shotLabel.setText(f"shot {self.shot}")
        self.shotLabel.setStyleSheet(
            f"color:{WARN if now < self.flash_until else FG};background:transparent"
        )

        yaw, pitch, roll = to_euler(self.q)
        w, x, y, z = self.q
        self.readout.setText(
            f"q   {w:+.3f} {x:+.3f} {y:+.3f} {z:+.3f}\n"
            f"ypr {yaw:+7.1f} {pitch:+7.1f} {roll:+7.1f}\n"
            f"sumbu {'—' if self.axis_sent is None else self.axis_sent}"
        )

    def log(self, text: str, color: str = FG) -> None:
        self.logView.appendHtml(
            f'<span style="color:#3f4650">{time.strftime("%H:%M:%S")}</span> '
            f'<span style="color:{color}">{text}</span>'
        )

    def closeEvent(self, event) -> None:
        if self.thread:
            self.thread.source.stop()
            self.thread.wait(3000)
        event.accept()


def run_gui(spec: Source) -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(spec.window_title)
    win = MainWindow(spec)
    win.show()
    sys.exit(app.exec())
