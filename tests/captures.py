"""Load PacketLogger captures of the vendor apps (tests/fixtures/captures).

Each fixture is the GATT traffic between a phone app and a real diffuser,
produced by scripts/pklg_to_fixture.py. They are the ground truth the
protocol code is checked against: "tx" events are what the app wrote, "rx"
events are what the device notified back.

The GW helpers here decode frames independently of protocol_ble, so a test
compares the integration's output with the app's bytes rather than with the
integration's own reading of them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "captures"

GW_CHUNK = 20          # on-wire chunk: nonce, seq, up to 18 payload bytes
GW_INLINE_DPS = {1, 3}  # power, lock: [type][value], no length prefix


@dataclass(frozen=True)
class Capture:
    name: str
    family: str
    description: str
    events: tuple[tuple[str, bytes], ...]

    @property
    def writes(self) -> list[bytes]:
        return [data for direction, data in self.events if direction == "tx"]

    @property
    def notifications(self) -> list[bytes]:
        return [data for direction, data in self.events if direction == "rx"]


def load_capture(family: str, name: str) -> Capture:
    raw = json.loads((FIXTURES / family / f"{name}.json").read_text())
    return Capture(
        name=name,
        family=raw["family"],
        description=raw["description"],
        events=tuple((e["dir"], bytes.fromhex(e["hex"])) for e in raw["events"]),
    )


def capture_names(family: str) -> list[str]:
    return sorted(p.stem for p in (FIXTURES / family).glob("*.json"))


# ---------------------------------------------------------------------------
# Scent Marketing GW
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GwMessage:
    """One reassembled GW frame plus the on-wire chunks that carried it."""

    nonce: int
    frame: bytes
    chunks: tuple[bytes, ...]


def gw_messages(chunks: list[bytes]) -> list[GwMessage]:
    """Reassemble GW transport chunks, keyed by nonce, in completion order.

    A chunk shorter than the full 20 bytes ends its frame.
    """
    pending: dict[int, list[bytes]] = {}
    done: list[GwMessage] = []
    for chunk in chunks:
        parts = pending.setdefault(chunk[0], [])
        parts.append(chunk)
        if len(chunk) < GW_CHUNK:
            del pending[chunk[0]]
            done.append(GwMessage(
                nonce=chunk[0],
                frame=b"".join(c[2:] for c in parts),
                chunks=tuple(parts),
            ))
    return done


def gw_dps(frame: bytes) -> dict[int, bytes]:
    """Split a GW frame into {dp_id: encoded DP bytes} (id excluded)."""
    dps: dict[int, bytes] = {}
    i = 2  # skip the FF/00 header byte and the counter byte
    while i + 2 <= len(frame):
        dp_id = int.from_bytes(frame[i:i + 2], "big")
        i += 2
        if dp_id in GW_INLINE_DPS:
            dps[dp_id] = frame[i:i + 2]
            i += 2
            continue
        length = int.from_bytes(frame[i + 1:i + 3], "big")
        dps[dp_id] = frame[i:i + 3 + length]
        i += 3 + length
    return dps


def gw_app_writes(capture: Capture) -> list[bytes]:
    """Frames the app wrote, excluding the 1-byte init/ack packet."""
    return [m.frame for m in gw_messages(capture.writes) if len(m.frame) > 1]


def gw_device_frames(capture: Capture) -> list[bytes]:
    return [m.frame for m in gw_messages(capture.notifications)]


# ---------------------------------------------------------------------------
# Aroma Wave
# ---------------------------------------------------------------------------

def aroma_wave_order(write: bytes) -> tuple[int, str]:
    """(TYPE, inner FEEF...EFFF hex) of one app write."""
    obj = json.loads(write)
    return obj["TYPE"], obj["MSG"].removeprefix("order:")


def rechunk(notifications: list[bytes], size: int = 20) -> list[bytes]:
    """Split notifications into fixed-size pieces, as a 23-byte MTU would."""
    stream = b"".join(notifications)
    return [stream[i:i + size] for i in range(0, len(stream), size)]
