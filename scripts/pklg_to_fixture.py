"""Convert an Apple PacketLogger capture (.pklg) into a test fixture.

Keeps only the diffuser's GATT traffic: writes from the phone app and
notifications from the device, on the busiest write/notify handles. The
raw capture also holds the phone's address and unrelated Bluetooth traffic,
so commit the fixture, not the .pklg.

    uv run python scripts/pklg_to_fixture.py capture.pklg \
        tests/fixtures/captures/aroma_wave/on_off.json \
        --family aroma_wave --description "App: power off, then on"
"""
from __future__ import annotations

import argparse
import collections
import json
import struct
from pathlib import Path

ATT_CID = 0x0004
ATT_OPS = {0x12: "write_req", 0x52: "write_cmd", 0x1B: "notify", 0x1D: "indicate"}
PKLG_ACL_SENT = 0x02
PKLG_ACL_RECV = 0x03


def _records(buf: bytes):
    """Yield (timestamp, type, payload) records; handles both byte orders."""
    first = struct.unpack_from(">I", buf, 0)[0]
    fmt = ">" if 9 <= first < 70000 else "<"
    i = 0
    while i + 13 <= len(buf):
        (length,) = struct.unpack_from(fmt + "I", buf, i)
        secs, usecs = struct.unpack_from(fmt + "II", buf, i + 4)
        yield secs + usecs / 1e6, buf[i + 12], buf[i + 13:i + 4 + length]
        i += 4 + length


def att_events(buf: bytes) -> list[dict]:
    """Reassemble L2CAP and return every ATT write / notification."""
    partial: dict[tuple[int, str], bytearray] = {}
    events = []
    t0 = None
    for ts, typ, data in _records(buf):
        if typ not in (PKLG_ACL_SENT, PKLG_ACL_RECV) or len(data) < 4:
            continue
        t0 = ts if t0 is None else t0
        hdr, acl_len = struct.unpack_from("<HH", data, 0)
        conn, boundary = hdr & 0x0FFF, (hdr >> 12) & 0x3
        direction = "tx" if typ == PKLG_ACL_SENT else "rx"
        key = (conn, direction)
        fragment = data[4:4 + acl_len]
        if boundary in (0x0, 0x2):  # first fragment
            partial[key] = bytearray(fragment)
        elif key in partial:
            partial[key] += fragment
        else:
            continue
        l2cap = partial[key]
        if len(l2cap) < 4:
            continue
        l2_len, cid = struct.unpack_from("<HH", l2cap, 0)
        if len(l2cap) < 4 + l2_len:
            continue
        del partial[key]
        att = bytes(l2cap[4:4 + l2_len])
        if cid != ATT_CID or not att or att[0] not in ATT_OPS:
            continue
        events.append({
            "t": round(ts - t0, 3),
            "dir": direction,
            "op": ATT_OPS[att[0]],
            "handle": struct.unpack_from("<H", att, 1)[0],
            "hex": att[3:].hex(),
        })
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pklg", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--family", required=True)
    parser.add_argument("--description", default="")
    args = parser.parse_args()

    events = att_events(args.pklg.read_bytes())
    # Data handles = the busiest notify handle and the busiest write handle,
    # ignoring 2-byte CCCD subscription writes.
    notify = collections.Counter(e["handle"] for e in events if e["op"] == "notify")
    write = collections.Counter(
        e["handle"] for e in events
        if e["op"].startswith("write") and len(e["hex"]) != 4
    )
    notify_handle = notify.most_common(1)[0][0]
    write_handle = write.most_common(1)[0][0]
    kept = [
        {"t": e["t"], "dir": e["dir"], "hex": e["hex"]}
        for e in events
        if (e["op"] == "notify" and e["handle"] == notify_handle)
        or (e["op"].startswith("write") and e["handle"] == write_handle)
    ]
    t0 = kept[0]["t"] if kept else 0
    for e in kept:
        e["t"] = round(e["t"] - t0, 3)

    fixture = {
        "source": args.pklg.name,
        "family": args.family,
        "description": args.description,
        "events": kept,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"{args.out}: {len(kept)} events "
          f"(write handle {write_handle}, notify handle {notify_handle})")


if __name__ == "__main__":
    main()
