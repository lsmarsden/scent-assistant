"""Commands and notifications through ScentDiffuserDevice."""
from __future__ import annotations

import pytest

from custom_components.scent_assistant.const import (
    AROMAWAVE_CHAR_UUID,
    SM_GW_WRITE_UUID,
    DeviceType,
)
from custom_components.scent_assistant.protocol_ble import ScentMarketingGwProtocol
from tests.captures import (
    aroma_wave_order,
    gw_app_writes_after_state,
    gw_dps,
    gw_messages,
    load_capture,
)

from tests.fakes import FakeBluetooth


def last_gw_frame(bt: FakeBluetooth) -> bytes:
    return gw_messages(bt.client.written(SM_GW_WRITE_UUID))[-1].frame


def watch(dev) -> list[int]:
    calls: list[int] = []
    dev.register_state_callback(lambda: calls.append(1))
    return calls


# ---------------------------------------------------------------------------
# GW (A326)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("on", [True, False])
async def test_gw_power(connected, bt: FakeBluetooth, on: bool) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    calls = watch(dev)

    assert await dev.set_power(on) is True

    assert last_gw_frame(bt) == ScentMarketingGwProtocol().build_power(on)
    assert (dev.state.power, dev.state.phase) == (on, "idle" if on else "off")
    assert calls


async def test_command_fails_while_disconnected(make_device, bt: FakeBluetooth) -> None:
    bt.visible = False
    dev = make_device(DeviceType.SCENT_MARKETING_GW)
    await dev.async_setup()
    calls = watch(dev)

    assert await dev.set_power(True) is False
    assert dev.state.power is None
    assert calls == []


async def test_command_fails_on_write_error(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    bt.client.fail_writes = True
    assert await dev.set_power(True) is False
    assert dev.state.power is None


@pytest.mark.parametrize(("requested", "sent"), [(3, 3), (0, 1), (9, 5)])
async def test_gw_grade(connected, bt: FakeBluetooth, requested: int, sent: int) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    assert await dev.set_grade(requested) is True
    dp4 = gw_dps(last_gw_frame(bt))[4]
    assert dp4[-5] == sent
    assert dev.state.grade == sent


@pytest.mark.parametrize(("requested", "sent"), [((10, 120), (10, 120)), ((1, 999), (5, 300))])
async def test_gw_customise(connected, bt: FakeBluetooth, requested, sent) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    assert await dev.set_customize(*requested) is True
    dp15 = gw_dps(last_gw_frame(bt))[15]
    assert (int.from_bytes(dp15[3:5]), int.from_bytes(dp15[5:7])) == sent
    assert (dev.state.work_seconds, dev.state.pause_seconds, dev.state.grade) == (*sent, 0)


async def test_gw_grade_after_device_push_matches_app(connected, bt: FakeBluetooth) -> None:
    # Replay the Scent Home session's device pushes up to the app's
    # "grade 2" write, then make the same change through the device manager.
    capture = load_capture("gw", "app_session")
    app_frame, _ = gw_app_writes_after_state(capture, 4)[0]
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    app_chunks: list[bytes] = []
    for direction, data in capture.events:
        if direction == "rx":
            bt.client.notify(data)
            continue
        app_chunks.append(data)
        if app_frame in [m.frame for m in gw_messages(app_chunks)]:
            break  # the app sent its grade write here
    assert dev.state.gw_mode_tasks.quick_fragrance == 0x2C

    assert await dev.set_grade(2) is True

    assert gw_dps(last_gw_frame(bt))[4] == gw_dps(app_frame)[4]


async def test_gw_lamp(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    assert await dev.set_lamp(True) is True
    assert last_gw_frame(bt).hex() == "ff01000baf00020101"
    assert dev.state.light_on is True


async def test_gw_state_push_updates_state(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    calls = watch(dev)

    for chunk in load_capture("gw", "grade_3").notifications:
        bt.client.notify(chunk)

    s = dev.state
    assert (s.power, s.grade, s.device_name, s.oil_total_ml, s.oil_remaining_ml) == (
        True, 3, "A326", 120, 20,
    )
    assert s.firmware_version == "00.12.00 013.00.01"
    # Diagnostics keep the raw frames.
    assert dev.recent_notifications[-1] == load_capture("gw", "grade_3").notifications[-1].hex()
    assert len(calls) == 2  # one per reassembled frame


async def test_aroma_wave_only_commands_rejected_on_gw(connected) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    assert await dev.set_aromawave_mode(1, enabled=True) is False


# ---------------------------------------------------------------------------
# Aroma Wave (dev505)
# ---------------------------------------------------------------------------

async def test_aroma_wave_mode_write_reproduces_app(connected, bt: FakeBluetooth) -> None:
    # After the stored-schedule readout, enabling mode 1 must send exactly
    # what the app sent: weekday frame, then the schedule frame (36898d6).
    capture = load_capture("aroma_wave", "on_off_mode_1")
    dev = await connected(DeviceType.AROMA_WAVE)
    for n in capture.notifications[:6]:
        bt.client.notify(n)
    before = len(bt.client.writes)

    assert await dev.set_aromawave_mode(1, enabled=True) is True

    sent = bt.client.written(AROMAWAVE_CHAR_UUID)[before:]
    app = [w for w in capture.writes if b"FEEF0014" in w or b"FEEF0018" in w]
    enable_pair = next(app[i:i + 2] for i in range(len(app) - 1)
                       if b"050A00" in app[i + 1])
    assert sent == enable_pair
    assert dev.state.aromawave_modes[1].enabled is True


async def test_aroma_wave_mode_defaults_without_readout(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.AROMA_WAVE)
    before = len(bt.client.writes)

    assert await dev.set_aromawave_mode(1, work_seconds=15) is True

    weekday, schedule = (aroma_wave_order(w)[1] for w in bt.client.written()[before:])
    assert weekday.startswith("FEEF001400017F")
    # 09:00-22:00, 15 s work, 120 s pause, disabled (01).
    assert schedule.startswith("FEEF0018000109001600000F0078050A01")


async def test_aroma_wave_power_push(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.AROMA_WAVE)
    calls = watch(dev)
    off_push = next(n for n in load_capture("aroma_wave", "on_off_mode_1").notifications
                    if b'"ALL":"FE EF 05 05' in n)

    bt.client.notify(off_push)

    assert (dev.state.power, dev.state.phase) == (False, "off")
    assert calls


async def test_sync_time_resends(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.AROMA_WAVE)
    before = len(bt.client.writes)
    assert await dev.sync_time() is True
    assert aroma_wave_order(bt.client.written()[before])[1].startswith("FEEF0007")


async def test_state_callbacks_unsubscribe(connected, bt: FakeBluetooth) -> None:
    dev = await connected(DeviceType.SCENT_MARKETING_GW)
    calls: list[int] = []
    unsubscribe = dev.register_state_callback(lambda: calls.append(1))

    unsubscribe()
    assert await dev.set_lamp(True)

    assert calls == []
