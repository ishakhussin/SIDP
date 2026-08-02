"""Explicit short speaker test for the ESP32 alarm controller."""

from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the SentryLab ESP32 alarm")
    parser.add_argument("--port", required=True, help="Windows COM port, for example COM5")
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    duration = min(10.0, max(0.5, args.seconds))

    import serial

    connection = serial.Serial(args.port, 115200, timeout=0.25, write_timeout=0.5)
    try:
        # Opening a serial port can reset some ESP32 boards.
        time.sleep(1.5)
        connection.reset_input_buffer()
        print(f"Starting a {duration:.1f}-second alarm test on {args.port}...")
        connection.write(b"ALARM_ON:MANUAL_TEST\n")
        connection.flush()
        time.sleep(duration)
    finally:
        try:
            connection.write(b"ALARM_OFF\n")
            connection.flush()
            time.sleep(0.1)
        finally:
            connection.close()
    print("Alarm test stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
