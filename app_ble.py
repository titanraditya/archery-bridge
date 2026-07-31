"""
Motus Bridge — GUI versi BLE.

    python app_ble.py

Sumber datanya `BleReader`: busur fisik dengan IMU + sensor IR, disambung lewat
Bluetooth Low Energy. Paket yang keluar ke UDP:5005 identik dengan versi HP,
jadi monitor.py dan sisi Unity tidak berubah sama sekali.

Versi HP-nya ada di app.py. Beda keduanya cuma dua kelas di file ini —
SourcePanel (daftar device hasil scan, bukan QR) dan Source (BleReader, bukan
Relay + sertifikat). Sisa GUI-nya sama, dari ui.py.

Butuh `pip install bleak`, plus Bluetooth yang menyala. Di Linux BlueZ harus
jalan; di Windows tidak perlu pairing manual — device disambung langsung dari
daftar hasil scan.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton

import ui
from ble_reader import NAME_PREFIX, UDP_DEST, BleReader


class BlePanel(ui.SourcePanel):
    """Kartu SUMBER jalur BLE: hasil scan + tombol Sambung.

    Padanan QR + URL di versi HP. Yang di luar kelas ini tidak tahu bedanya.
    """

    def __init__(self) -> None:
        super().__init__()

        self.state = QLabel("belum dipindai")
        self.state.setObjectName("note")
        self.state.setWordWrap(True)

        self.list = QListWidget()
        self.list.setMinimumHeight(132)
        self.list.itemDoubleClicked.connect(lambda _: self._connect())
        self.list.itemSelectionChanged.connect(self._sync_buttons)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.scanBtn = QPushButton("Pindai")
        self.scanBtn.clicked.connect(lambda: self.command.emit({"cmd": "scan"}))
        self.connBtn = QPushButton("Sambung")
        self.connBtn.setObjectName("primary")
        self.connBtn.clicked.connect(self._connect)
        self.connBtn.setEnabled(False)
        row.addWidget(self.scanBtn)
        row.addWidget(self.connBtn)

        note = QLabel(f"Device yang namanya berawalan {NAME_PREFIX} disambung "
                      "otomatis kalau cuma ada satu.")
        note.setObjectName("note")
        note.setWordWrap(True)

        self.box.addWidget(self.state)
        self.box.addWidget(self.list)
        self.box.addLayout(row)
        self.box.addWidget(note)

    # ---- dipanggil MainWindow ------------------------------------------

    def show_target(self, fields: dict) -> None:
        self.state.setText("memindai device…")

    def clear(self) -> None:
        self.state.setText("berhenti")
        self.list.clear()
        self._sync_buttons()

    def on_status(self, kind: str, fields: dict) -> None:
        if kind == "devices":
            self._fill(fields.get("items", []))
        elif kind == "connected":
            self.state.setText(f"terhubung: {fields.get('remote', '')}")
        elif kind == "disconnected":
            self.state.setText("terputus — pilih device lalu Sambung")
        elif kind == "info":
            self.state.setText(fields.get("text", ""))

    # ---- internal -------------------------------------------------------

    def _fill(self, items: list[dict]) -> None:
        self.list.clear()
        for it in items:
            rssi = f"{it['rssi']:>4} dBm" if it.get("rssi") is not None else "   ? dBm"
            row = QListWidgetItem(f"{it['name']}   {rssi}\n{it['address']}")
            row.setData(Qt.UserRole, it)
            self.list.addItem(row)
            if it["name"].upper().startswith(NAME_PREFIX) and not self.list.selectedItems():
                self.list.setCurrentItem(row)
        self.state.setText(f"{len(items)} device terlihat" if items else "tidak ada device")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.connBtn.setEnabled(bool(self.list.selectedItems()))

    def _connect(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        it = item.data(Qt.UserRole)
        self.state.setText(f"menyambung ke {it['name']}…")
        self.command.emit({"cmd": "connect", "address": it["address"], "name": it["name"]})


class BleThread(ui.SourceThread):
    """BleReader sudah memakai kosakata status yang sama dengan ui.py, jadi
    tidak ada yang perlu diterjemahkan di sini."""

    def __init__(self) -> None:
        super().__init__(
            lambda on_packet, on_status: BleReader(on_packet=on_packet, on_status=on_status),
            oserror_hint="kesalahan Bluetooth",
        )


class BleSource(ui.Source):
    window_title = "Motus Bridge — BLE"

    def panel(self) -> ui.SourcePanel:
        return BlePanel()

    def thread(self) -> ui.SourceThread:
        return BleThread()

    def prepare(self, log) -> bool:
        try:
            import bleak  # noqa: F401
        except ImportError:
            log("bleak belum terpasang — pip install bleak", ui.WARN)
            return False
        log(f"UDP keluar {UDP_DEST[0]}:{UDP_DEST[1]}")
        return True


def main() -> None:
    ui.run_gui(BleSource())


if __name__ == "__main__":
    sys.exit(main())
