"""Scent Marketing GW (A326) protocol, checked against the Ascent and Scent
Home app captures."""
from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.scent_assistant.const import SM_GW_HEARTBEAT_HEX
from custom_components.scent_assistant.protocol_ble import (
    GwModeTasks,
    GwTask,
    ScentMarketingGwProtocol,
)
from tests.captures import (
    capture_names,
    gw_app_writes,
    gw_app_writes_after_state,
    gw_device_frames,
    gw_dps,
    gw_messages,
    load_capture,
)

GW = "gw"


@pytest.fixture
def proto() -> ScentMarketingGwProtocol:
    return ScentMarketingGwProtocol()


def app_write(name: str, index: int) -> bytes:
    return gw_app_writes(load_capture(GW, name))[index]


def device_frame(name: str, index: int) -> bytes:
    return gw_device_frames(load_capture(GW, name))[index]


def parse_all(proto: ScentMarketingGwProtocol, chunks: list[bytes]) -> dict:
    state: dict = {}
    for chunk in chunks:
        state.update(proto.parse_notification(chunk))
    return state


# ---------------------------------------------------------------------------
# Transport: 18-byte chunks with a (nonce, seq) header
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", capture_names(GW))
def test_chunking_matches_app(name: str) -> None:
    for message in gw_messages(load_capture(GW, name).writes):
        assert ScentMarketingGwProtocol.chunk_for_transport(
            message.frame, nonce=message.nonce
        ) == list(message.chunks)


def test_full_last_chunk_gets_terminator() -> None:
    chunks = ScentMarketingGwProtocol.chunk_for_transport(bytes(36), nonce=0x42)
    assert [len(c) for c in chunks] == [20, 20, 2]
    assert chunks[-1] == bytes([0x42, 3])


@pytest.mark.parametrize("name", capture_names(GW))
def test_reassembles_every_device_frame(name: str, proto: ScentMarketingGwProtocol) -> None:
    capture = load_capture(GW, name)
    frames = [
        frame for chunk in capture.notifications
        if (frame := proto.handle_transport_chunk(chunk)) is not None
    ]
    assert frames == gw_device_frames(capture)


def test_interleaved_responses_reassemble_separately(proto: ScentMarketingGwProtocol) -> None:
    # Regression for 03e5204: the device interleaves replies to back-to-back
    # writes, so buffers must be per nonce.
    multi = [m for m in gw_messages(load_capture(GW, "app_session").notifications)
             if len(m.chunks) > 2]
    first = multi[0]
    second = next(m for m in multi if m.nonce != first.nonce)
    interleaved = [c for pair in zip(first.chunks, second.chunks) for c in pair]
    longer = first if len(first.chunks) > len(second.chunks) else second
    interleaved += longer.chunks[min(len(first.chunks), len(second.chunks)):]

    frames = [f for c in interleaved if (f := proto.handle_transport_chunk(c)) is not None]

    assert sorted(frames) == sorted([first.frame, second.frame])


def test_heartbeat_pulse_is_dropped(proto: ScentMarketingGwProtocol) -> None:
    assert proto.parse_notification(bytes([0x07, 0x01]) + bytes.fromhex(SM_GW_HEARTBEAT_HEX)) == {}


# ---------------------------------------------------------------------------
# Commands: the integration's frames vs what the app sent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("on", "app_frame"), [
    (True, "ff0100010101"),
    (False, "ff0100010100"),
])
def test_power_matches_app(proto: ScentMarketingGwProtocol, on: bool, app_frame: str) -> None:
    assert app_frame in [f.hex() for f in gw_app_writes(load_capture(GW, "app_session"))]
    assert proto.build_power(on).hex() == app_frame


@pytest.mark.parametrize(("on", "app_frame"), [
    (True, "ff01000baf00020101"),
    (False, "ff01000baf00020100"),
])
def test_lamp_matches_app(proto: ScentMarketingGwProtocol, on: bool, app_frame: str) -> None:
    assert app_frame in [f.hex() for f in gw_app_writes(load_capture(GW, "app_session"))]
    assert proto.build_light(on).hex() == app_frame


def test_time_sync_matches_app(proto: ScentMarketingGwProtocol) -> None:
    # Scent Home app, Friday 15 May 2026 18:48:33 (weekday 5, Sunday = 0).
    app_frame = app_write("app_session", 1)
    assert proto.build_time_sync(datetime(2026, 5, 15, 18, 48, 33)) == app_frame


# ---------------------------------------------------------------------------
# DP-4 mode tasks: decode the device's value, change one field, write it back
# ---------------------------------------------------------------------------

def device_dp4_payloads() -> list[bytes]:
    return [
        dps[4][3:] for name in capture_names(GW)
        for frame in gw_device_frames(load_capture(GW, name))
        if 4 in (dps := gw_dps(frame))
    ]


def app_dp4(frame: bytes) -> GwModeTasks:
    return GwModeTasks.decode(gw_dps(frame)[4][3:])


def test_mode_tasks_round_trip() -> None:
    payloads = set(device_dp4_payloads())
    assert len(payloads) >= 5
    for payload in payloads:
        assert GwModeTasks.decode(payload).encode() == payload


def test_mode_tasks_decode() -> None:
    mode_tasks = GwModeTasks.decode(gw_dps(device_frame("grade_3", 1))[4][3:])
    assert (mode_tasks.run, mode_tasks.grade, mode_tasks.quick_fragrance) == (1, 3, 0x28)
    assert mode_tasks.tasks == (GwTask(enabled=True, weekday_mask=0x7F, start_hour=0,
                                       start_minute=0, end_hour=23, end_minute=59),)


def test_mode_tasks_rejects_wrong_length() -> None:
    payload = device_dp4_payloads()[0]
    assert GwModeTasks.decode(payload[:-1]) is None
    assert GwModeTasks.decode(payload + b"\x00") is None


def writes_after_state(with_customise: bool) -> list:
    """App grade/customise writes made after the device had reported DP-4."""
    return [
        pytest.param(frame, before, id=f"{name}-{i}")
        for name in capture_names(GW)
        for i, (frame, before) in enumerate(gw_app_writes_after_state(load_capture(GW, name), 4))
        if (15 in gw_dps(frame)) == with_customise and app_dp4(frame).run == 0x01
    ]


@pytest.mark.parametrize(("app_frame", "device_dp4"), writes_after_state(with_customise=False))
def test_grade_write_keeps_device_state(
    proto: ScentMarketingGwProtocol, app_frame: bytes, device_dp4: bytes
) -> None:
    # The device reported quick fragrance 0x2c here; a grade change keeps it.
    grade = app_dp4(app_frame).grade
    ours = proto.build_mode_tasks(GwModeTasks.decode(device_dp4).with_grade(grade))
    assert gw_dps(ours) == {4: gw_dps(app_frame)[4]}


@pytest.mark.parametrize(("app_frame", "device_dp4"), writes_after_state(with_customise=True))
def test_customise_write_keeps_device_state(
    proto: ScentMarketingGwProtocol, app_frame: bytes, device_dp4: bytes
) -> None:
    app = gw_dps(app_frame)
    work, pause = int.from_bytes(app[15][3:5]), int.from_bytes(app[15][5:7])
    ours = gw_dps(proto.build_customize(
        work, pause, GwModeTasks.decode(device_dp4).with_grade(0)))
    assert ours == {4: app[4], 15: app[15]}


@pytest.mark.parametrize(("name", "grade"), [("grade_3", 3), ("grade_1_then_off", 1)])
def test_grade_write_before_any_state_matches_app(
    proto: ScentMarketingGwProtocol, name: str, grade: int
) -> None:
    # Captured from app start: no device push yet, so defaults must match.
    ours = proto.build_mode_tasks(GwModeTasks().with_grade(grade))
    assert gw_dps(ours) == {4: gw_dps(app_write(name, 0))[4]}


def test_customise_before_any_state_matches_app(proto: ScentMarketingGwProtocol) -> None:
    app = gw_dps(app_write("customise_10s_120s", 0))
    ours = gw_dps(proto.build_customize(10, 120, GwModeTasks().with_grade(0)))
    assert ours == {4: app[4], 15: app[15]}


@pytest.mark.xfail(strict=True, reason="the Scent Home app sends marker 0x01 before the "
                                       "password; build_password sends 0xC0")
def test_password_matches_app(proto: ScentMarketingGwProtocol) -> None:
    assert proto.build_password("8888") == app_write("app_session", 0)


# ---------------------------------------------------------------------------
# Notifications: device frames -> state
# ---------------------------------------------------------------------------

def test_full_state_push_after_grade_3(proto: ScentMarketingGwProtocol) -> None:
    state = proto.parse_frame(device_frame("grade_3", 1))
    assert state["power"] is True
    assert state["phase"] == "idle"
    assert state["grade"] == 3
    assert state["device_name"] == "A326"
    assert state["firmware_version"] == "00.12.00 013.00.01"


def test_oil_reset_to_20ml(proto: ScentMarketingGwProtocol) -> None:
    state = proto.parse_frame(device_frame("reset_oil_20ml", 0))
    assert state == {"oil_total_ml": 120, "oil_remaining_ml": 20, "oil_remaining": 17}


@pytest.mark.parametrize(("name", "work", "pause"), [
    ("customise_10s_120s", 10, 120),
    ("customise_5s_60s", 5, 60),
])
def test_customise_state(proto: ScentMarketingGwProtocol, name: str, work: int, pause: int) -> None:
    state = proto.parse_frame(device_frame(name, 1))
    assert (state["grade"], state["work_seconds"], state["pause_seconds"]) == (0, work, pause)


def test_power_off_is_reported(proto: ScentMarketingGwProtocol) -> None:
    capture = load_capture(GW, "grade_1_then_off")
    assert parse_all(proto, capture.notifications)["power"] is False


@pytest.mark.parametrize(("echo", "expected"), [
    ("000100010101", {"power": True, "phase": "idle"}),
    ("000100010100", {"power": False, "phase": "off"}),
    ("0001000baf00020101", {"light_on": True}),
    ("0001000baf00020100", {"light_on": False}),
])
def test_command_echo(proto: ScentMarketingGwProtocol, echo: str, expected: dict) -> None:
    # The device acknowledges a write by echoing it with a 00 header.
    frame = bytes.fromhex(echo)
    assert frame in gw_device_frames(load_capture(GW, "app_session"))
    assert proto.parse_frame(frame) == expected


def test_lamp_state_follows_app_session(proto: ScentMarketingGwProtocol) -> None:
    seen = [s["light_on"] for f in gw_device_frames(load_capture(GW, "app_session"))
            if "light_on" in (s := proto.parse_frame(f))]
    # Initial push (off), lamp on, lamp off, then later pushes keep it off.
    assert seen[:3] == [False, True, False]
    assert not any(seen[3:])


@pytest.mark.xfail(strict=True, reason="the password reply is an inline DP (type 0x11, "
                                       "value 0xA1 = accepted); the parser only reads "
                                       "length-prefixed DP-13")
def test_password_accepted_reply(proto: ScentMarketingGwProtocol) -> None:
    reply = device_frame("app_session", 0)
    assert reply.hex() == "0001000d11a1"
    assert proto.parse_frame(reply) == {"password_required": False}
