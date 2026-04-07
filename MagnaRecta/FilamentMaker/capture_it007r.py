#!/usr/bin/env python3
"""Capture Mitutoyo IT-007R readings to CSV.

Usage:
  python3 capture_it007r.py --port /dev/tty.usbserial-XXXX --out logs/measurements.csv
"""

import argparse
import csv
import datetime as dt
import os
import re
import sys
import time

try:
    import serial
except Exception:
    print("pyserial is required: python3 -m pip install --user pyserial", file=sys.stderr)
    sys.exit(1)

DATA_RE = re.compile(r"^01A([+-])(\d+\.\d+)$")
ERR_RE = re.compile(r"^91([12])$")


def parse_payload(payload: str):
    payload = payload.strip()
    m = DATA_RE.match(payload)
    if m:
        sign, num = m.groups()
        value = float(num)
        if sign == "-":
            value = -value
        return ("ok", value, payload)

    e = ERR_RE.match(payload)
    if e:
        code = e.group(1)
        reason = "no_data" if code == "1" else "format_error"
        return ("error", reason, payload)

    return ("unknown", None, payload)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", required=True, help="Serial port, e.g. /dev/tty.usbserial-XXXX")
    p.add_argument("--out", default="logs/it007r_measurements.csv", help="CSV output path")
    p.add_argument("--interval", type=float, default=1.0, help="Request interval seconds (>=1.0 recommended)")
    p.add_argument("--request", default="1", help="Request character sent to IT-007R")
    p.add_argument("--count", type=int, default=0, help="Number of samples (0 = infinite)")
    args = p.parse_args()

    if args.interval < 1.0:
        print("warning: interval < 1.0 may cause errors on IT-007R", file=sys.stderr)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with serial.Serial(
        port=args.port,
        baudrate=2400,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1.2,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    ) as ser, open(args.out, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["timestamp", "status", "value_or_reason", "raw"])

        n = 0
        while True:
            ser.write(args.request.encode("ascii", errors="ignore"))
            raw = ser.readline()  # expected CR terminated
            now = dt.datetime.now().isoformat(timespec="seconds")

            if raw:
                text = raw.decode("ascii", errors="replace").strip("\r\n")
                status, val, payload = parse_payload(text)
                writer.writerow([now, status, val if val is not None else "", payload])
                f.flush()
                print(f"{now} {status}: {payload}")
            else:
                writer.writerow([now, "timeout", "", ""])
                f.flush()
                print(f"{now} timeout")

            n += 1
            if args.count > 0 and n >= args.count:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
