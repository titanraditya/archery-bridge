"""
Monitor: konsumen UDP:5005 yang menggantikan Unity selama fase prototipe.

Menampilkan quaternion mentah, konversi ke yaw/pitch/roll, bar draw,
counter tembakan, dan laju paket. Jalankan bersamaan dengan relay.py:

    python monitor.py
"""

import json
import math
import socket
import time

PORT = 5005
BAR_W = 30


def quat_to_euler(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    """Quaternion -> yaw/pitch/roll dalam derajat (untuk pembacaan manusia)."""
    # roll (sumbu X)
    sinr = 2 * (w * x + y * z)
    cosr = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)

    # pitch (sumbu Y) — dijepit agar tidak NaN di gimbal lock
    sinp = 2 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)

    # yaw (sumbu Z)
    siny = 2 * (w * z + x * y)
    cosy = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny, cosy)

    d = 180 / math.pi
    return yaw * d, pitch * d, roll * d


def bar(value: float, width: int = BAR_W) -> str:
    filled = int(max(0.0, min(1.0, value)) * width)
    return "█" * filled + "░" * (width - filled)


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", PORT))
    sock.settimeout(1.0)

    print(f"Mendengarkan UDP :{PORT} — Ctrl+C untuk berhenti\n")

    last_shot = 0
    shot_flash = 0.0
    count = 0
    t0 = time.time()
    hz = 0.0

    while True:
        try:
            data, _ = sock.recvfrom(1024)
        except socket.timeout:
            print("\r  (menunggu paket...)                              ", end="")
            continue
        except KeyboardInterrupt:
            break

        try:
            p = json.loads(data.decode("utf-8"))
            w, x, y, z = p["q"]
            draw = float(p.get("draw", 0.0))
            shot = int(p.get("shot", 0))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue

        count += 1
        now = time.time()
        if now - t0 >= 0.5:
            hz = count / (now - t0)
            count = 0
            t0 = now

        if shot != last_shot:
            last_shot = shot
            shot_flash = now

        yaw, pitch, roll = quat_to_euler(w, x, y, z)
        flash = "  *** LEPAS ***" if now - shot_flash < 0.35 else ""

        line = (
            f"\rq[{w:+.3f} {x:+.3f} {y:+.3f} {z:+.3f}]  "
            f"yaw{yaw:+7.1f} pit{pitch:+7.1f} rol{roll:+7.1f}  "
            f"draw {bar(draw)} {draw:4.2f}  "
            f"shot {shot:<4d} {hz:5.1f}Hz{flash}   "
        )
        print(line, end="", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nberhenti.")
