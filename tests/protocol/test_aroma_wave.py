"""Aroma Wave Ultra Pro (dev505) protocol, checked against app captures."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from custom_components.scent_assistant.protocol_ble import AromaWaveProtocol
from tests.captures import aroma_wave_order, capture_names, load_capture, rechunk

AW = "aroma_wave"
STATUS_PUSH = re.compile(rb'"ALL":"FE EF 05 0([45]) .*EF FF"')


@pytest.fixture
def proto() -> AromaWaveProtocol:
    return AromaWaveProtocol()


def app_writes(name: str) -> list[bytes]:
    return load_capture(AW, name).writes


def all_app_writes() -> set[bytes]:
    return {w for name in capture_names(AW) for w in app_writes(name)}


def replay(proto: AromaWaveProtocol, notifications: list[bytes]) -> dict:
    """Feed notifications in order; return the merged state updates."""
    state: dict = {}
    for data in notifications:
        updates = proto.parse_notification(data)
        modes = updates.pop("aromawave_mode_updates", {})
        state.update(updates)
        for mode, fields in modes.items():
            state.setdefault("modes", {}).setdefault(mode, {}).update(fields)
    return state


def frame(hex_bytes: str) -> bytes:
    """A device state push: {"num":1,"duan":0,"ALL":"FE EF ... EF FF"}."""
    spaced = " ".join(hex_bytes[i:i + 2] for i in range(0, len(hex_bytes), 2)).upper()
    return b'{"num":1,"duan":0,"ALL":"FE EF ' + spaced.encode() + b' EF FF"}'


# ---------------------------------------------------------------------------
# Commands: byte-for-byte against the app
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("build", [
    lambda p: p.build_power(True),
    lambda p: p.build_power(False),
    lambda p: p.build_poll(),
    lambda p: p.build_connect_init()[0],
    lambda p: p.build_connect_init()[1],
    lambda p: p.build_connect_init()[2],
    lambda p: p.build_mode_weekday(1, 0x7F),
    lambda p: p.build_mode_schedule(1, 8, 0, 23, 0, 10, 120, enabled=True),
    lambda p: p.build_mode_schedule(1, 8, 0, 23, 0, 10, 120, enabled=False),
], ids=["power_on", "power_off", "poll", "unlock", "read_schedules", "read_weekdays",
        "mode_1_weekdays", "mode_1_enabled", "mode_1_disabled"])
def test_command_matches_app(proto: AromaWaveProtocol, build) -> None:
    assert build(proto) in all_app_writes()


@pytest.mark.parametrize(("on", "value"), [(True, "00"), (False, "01")])
def test_power_polarity_is_inverted(proto: AromaWaveProtocol, on: bool, value: str) -> None:
    # Regression for 71a2d6a: 00 = on, 01 = off.
    assert aroma_wave_order(proto.build_power(on)) == (3, f"FEEF000E{value}{'00' * 17}EFFF")


@pytest.mark.parametrize(("name", "now"), [
    ("cold_open", datetime(2026, 6, 14, 21, 59, 39)),
    ("on_off_mode_1", datetime(2026, 5, 17, 22, 43, 43)),
])
def test_time_sync_matches_app(proto: AromaWaveProtocol, name: str, now: datetime) -> None:
    # Regression for 0b884b3: year (u16), month, day, ISO weekday, h, m, s.
    assert proto.build_time_sync(now) in app_writes(name)


def test_read_schedules_uses_type_4(proto: AromaWaveProtocol) -> None:
    assert aroma_wave_order(proto.build_connect_init()[1]) == (4, f"FEEF06{'00' * 19}EFFF")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("push", "power"), [
    ("0504" + "00" * 18, True),
    ("0505" + "00" * 18, False),
])
def test_status_push_power(proto: AromaWaveProtocol, push: str, power: bool) -> None:
    assert proto.parse_notification(frame(push)) == {
        "power": power, "phase": "idle" if power else "off",
    }


def test_status_pushes_in_capture(proto: AromaWaveProtocol) -> None:
    pushes = [
        (n, m.group(1)) for n in load_capture(AW, "on_off_mode_1").notifications
        if (m := STATUS_PUSH.search(n))
    ]
    assert {flag for _, flag in pushes} == {b"4", b"5"}
    for push, flag in pushes:
        assert proto.parse_notification(push)["power"] is (flag == b"4")


def test_poll_echo_does_not_change_power(proto: AromaWaveProtocol) -> None:
    # The echo of our 05 poll has a zero value byte; only the push counts.
    assert proto.parse_notification(proto.build_poll()) == {}


def test_power_echo_reads_back(proto: AromaWaveProtocol) -> None:
    assert proto.parse_notification(proto.build_power(False))["power"] is False


def test_stored_schedules_readout(proto: AromaWaveProtocol) -> None:
    # In this capture the app later writes mode 1 as 08:00-23:00, 10 s / 120 s,
    # so the readout at connect must already say the same.
    capture = load_capture(AW, "on_off_mode_1")
    mode_1 = replay(proto, capture.notifications[:6])["modes"][1]
    assert {k: mode_1[k] for k in ("start_hour", "start_minute", "end_hour", "end_minute",
                                   "work_seconds", "pause_seconds")} == {
        "start_hour": 8, "start_minute": 0, "end_hour": 23, "end_minute": 0,
        "work_seconds": 10, "pause_seconds": 120,
    }


def test_mode_1_enable_toggle_reads_back(proto: AromaWaveProtocol) -> None:
    capture = load_capture(AW, "on_off_mode_1")
    enabled = [
        mode["enabled"] for n in capture.notifications
        if (mode := proto.parse_notification(n).get("aromawave_mode_updates", {}).get(1))
        and "enabled" in mode
    ]
    # The readout at connect (3E: mode 1 enabled), then each schedule write
    # the app made is echoed; byte 14 is 00 = enabled.
    written = [aroma_wave_order(w)[1][32:34] == "00" for w in capture.writes
               if aroma_wave_order(w)[1].startswith("FEEF0018")]
    assert len(written) > 2
    assert enabled == [True, *written]


# dev505's 06 readouts on 18 Oct, read live from HA around toggling mode 1
# (09:00-23:59, 1 s / 30 s; mode 2 09:00-19:01, 31 s / 151 s).
LIVE_SCHEDULES = (
    '{{"num":2,"duan":0,"ALL":"FE EF 06 09 09 00 00 00 00 00 00 00 00 00 00 17 13 00 00 '
    '00 00 3B 01 00 00 00 00 01 00 1F 00 00"}}'
    '{{"num":2,"duan":1,"ALL":"00 00 00 00 00 00 00 1E 00 97 00 00 00 00 00 00 00 00 00 '
    '07 EA 0A 12 07 11 2E 09 00 {flags} 05 EF FF"}}'
)


class TestScheduleReadoutEnabled:
    """The 06 readout's flag byte has bit m set while mode m+1 is disabled."""

    @pytest.mark.parametrize(("flags", "mode_1"), [("3E", True), ("3F", False)])
    def test_live_toggle(self, proto: AromaWaveProtocol, flags: str, mode_1: bool) -> None:
        modes = replay(proto, [LIVE_SCHEDULES.format(flags=flags).encode()])["modes"]
        assert modes[1]["enabled"] is mode_1
        assert {m: modes[m]["enabled"] for m in range(2, 7)} == dict.fromkeys(range(2, 7), False)

    def test_live_schedule(self, proto: AromaWaveProtocol) -> None:
        mode_1 = replay(proto, [LIVE_SCHEDULES.format(flags="3F").encode()])["modes"][1]
        assert {k: mode_1[k] for k in ("start_hour", "start_minute", "end_hour", "end_minute",
                                       "work_seconds", "pause_seconds")} == {
            "start_hour": 9, "start_minute": 0, "end_hour": 23, "end_minute": 59,
            "work_seconds": 1, "pause_seconds": 30,
        }


class TestWeekdayReadout:
    """The 09 readout leads with each mode's weekday mask."""

    def test_app_session(self, proto: AromaWaveProtocol) -> None:
        # Mode 2 had no days until the app set it up later in this session.
        modes = replay(proto, load_capture(AW, "session_0515").notifications)["modes"]
        assert modes[1]["weekday_mask"] == 0x7F

    def test_unused_modes_have_no_days(self, proto: AromaWaveProtocol) -> None:
        notifications = load_capture(AW, "cold_open").notifications
        weekday_frames = [n for n in notifications if b"FE EF 09" in n]
        assert weekday_frames
        modes = replay(proto, notifications)["modes"]
        assert [modes[m]["weekday_mask"] for m in range(1, 7)] == [0x7F, 0x7F, 0, 0, 0, 0]

    def test_read_at_connect_uses_type_4(self, proto: AromaWaveProtocol) -> None:
        assert aroma_wave_order(proto.build_connect_init()[2]) == (4, f"FEEF09{'00' * 19}EFFF")


def test_final_power_matches_last_command(proto: AromaWaveProtocol) -> None:
    capture = load_capture(AW, "on_off_mode_1")
    last_power_write = [w for w in capture.writes if b"FEEF000E" in w][-1]
    assert replay(proto, capture.notifications)["power"] is (b"FEEF000E00" in last_power_write)


@pytest.mark.parametrize("name", capture_names(AW))
def test_small_mtu_chunking_gives_same_state(name: str) -> None:
    # The device splits JSON across 20-byte notifications on a default MTU;
    # reassembly must not depend on where the splits fall.
    notifications = load_capture(AW, name).notifications
    whole = replay(AromaWaveProtocol(), notifications)
    assert whole
    assert replay(AromaWaveProtocol(), rechunk(notifications)) == whole


def test_new_fragment_series_discards_stale_tail(proto: AromaWaveProtocol) -> None:
    # duan 0 of a fresh frame resets a series whose tail never arrived.
    proto.parse_notification(b'{"num":2,"duan":0,"ALL":"FE EF 06 01 02"}')
    push = b'{"num":2,"duan":0,"ALL":"FE EF 05 05 ' + b"00 " * 17 + b'00"}'
    tail = b'{"num":2,"duan":1,"ALL":"EF FF"}'
    assert proto.parse_notification(push) == {}
    assert proto.parse_notification(tail)["power"] is False


def test_malformed_object_is_skipped(proto: AromaWaveProtocol) -> None:
    data = b'{"num":1,"duan":0,"ALL":}' + frame("0504" + "00" * 18)
    assert proto.parse_notification(data)["power"] is True
