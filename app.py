"""
Motus Bridge — GUI versi HP.

    python app.py

Sumber datanya `Relay`: HP jadi controller lewat browser (HTTPS + WebSocket),
paketnya diteruskan ke UDP:5005. Ini versi fase prototipe, dipakai untuk
memvalidasi mekanik dan mapping sumbu sebelum hardware siap.

Versi keduanya ada di app_ble.py — GUI yang sama persis, sumbernya IMU/IR
lewat BLE. Semua yang tidak khusus jalur HP ada di ui.py; file ini cuma berisi
dua kelas yang membedakan: SourcePanel (QR + URL) dan Source (sertifikat +
Relay).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton

import ui
from relay import Relay, UDP_DEST, cert_status, lan_ip, make_cert


def qr_pixmap(text: str, target: int = 190) -> QPixmap | None:
    """QR digambar sendiri dari matriksnya — tidak butuh Pillow."""
    try:
        import qrcode
    except ImportError:
        return None

    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    cell = max(1, target // len(matrix))
    side = cell * len(matrix)

    pm = QPixmap(side, side)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("black"))
    for r, row in enumerate(matrix):
        for c, on in enumerate(row):
            if on:
                p.drawRect(c * cell, r * cell, cell, cell)
    p.end()
    return pm


class PhonePanel(ui.SourcePanel):
    """Kartu SUMBER jalur HP: QR + URL untuk dibuka di browser HP."""

    def __init__(self) -> None:
        super().__init__()

        self.qr = QLabel("—")
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setObjectName("qr")
        self.qr.setMinimumHeight(120)

        self.url = QLabel("—")
        self.url.setObjectName("url")
        self.url.setAlignment(Qt.AlignCenter)
        self.url.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.copyBtn = QPushButton("Salin URL")
        self.copyBtn.clicked.connect(self._copy)
        self.copyBtn.setEnabled(False)

        note = QLabel("Pindai dari HP di Wi-Fi yang sama. Peringatan sertifikat itu normal.")
        note.setObjectName("note")
        note.setWordWrap(True)

        for w in (self.qr, self.url, self.copyBtn, note):
            self.box.addWidget(w)

    def show_target(self, fields: dict) -> None:
        url = fields.get("target", "")
        self.url.setText(url)
        pm = qr_pixmap(url)
        if pm:
            self.qr.setPixmap(pm)
        else:
            self.qr.setText("pip install qrcode")
        self.copyBtn.setEnabled(True)

    def clear(self) -> None:
        self.url.setText("—")
        self.qr.setPixmap(QPixmap())
        self.qr.setText("—")
        self.copyBtn.setEnabled(False)

    def _copy(self) -> None:
        text = self.url.text()
        if text and text != "—":
            QGuiApplication.clipboard().setText(text)


class PhoneThread(ui.SourceThread):
    """Relay + penerjemah status internalnya ke kosakata yang dipakai ui.py."""

    def __init__(self) -> None:
        super().__init__(self._make, oserror_hint="gagal membuka port")

    def _make(self, on_packet, on_status):
        def status(kind: str, **f) -> None:
            if kind == "listening":
                udp = f.get("udp", UDP_DEST)
                on_status("ready", target=f.get("url", ""), detail=f"UDP {udp[0]}:{udp[1]}")
            elif kind == "phone":
                on_status(
                    "connected" if f.get("connected") else "disconnected",
                    remote=f.get("remote"),
                )
            else:
                on_status(kind, **f)

        return Relay(on_packet=on_packet, on_status=status)


class PhoneSource(ui.Source):
    window_title = "Motus Bridge — HP"

    def __init__(self) -> None:
        self.known_ip = lan_ip()

    def panel(self) -> ui.SourcePanel:
        return PhonePanel()

    def thread(self) -> ui.SourceThread:
        return PhoneThread()

    def prepare(self, log) -> bool:
        """Sertifikat khusus jalur HP; hilang sendiri di versi BLE."""
        ip = lan_ip()
        self.known_ip = ip
        ok, reason = cert_status(ip)
        if ok:
            return True
        log(f"sertifikat: {reason} — membuat ulang…", ui.WARN)
        try:
            make_cert()
        except Exception as e:
            log(f"gagal membuat sertifikat: {e}", ui.WARN)
            return False
        return True

    def tick(self, log) -> None:
        """IP LAN bisa berubah saat pindah Wi-Fi — sertifikat lama jadi tidak
        cocok dan HP akan menolak koneksi."""
        ip = lan_ip()
        if ip != self.known_ip:
            self.known_ip = ip
            log(f"IP LAN jadi {ip} — Stop lalu Start untuk perbarui sertifikat", ui.WARN)


def main() -> None:
    ui.run_gui(PhoneSource())


if __name__ == "__main__":
    main()
