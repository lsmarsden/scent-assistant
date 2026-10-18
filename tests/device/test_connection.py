"""Persistent-connection lifecycle of ScentDiffuserDevice."""
from __future__ import annotations

from types import SimpleNamespace

from bleak_retry_connector import BleakNotFoundError

from custom_components.scent_assistant.const import (
    AROMAWAVE_CHAR_UUID,
    AROMAWAVE_SERVICE_UUID,
    SM_GW_NOTIFY_UUID,
    SM_GW_WRITE_UUID,
    DeviceType,
)
from tests.captures import aroma_wave_order, gw_dps, gw_messages

from tests.fakes import FakeLink, settle


def aw_orders(client) -> list[str]:
    return [aroma_wave_order(w)[1][4:8] for w in client.written(AROMAWAVE_CHAR_UUID)]


def gw_frames(client) -> list[bytes]:
    return [m.frame for m in gw_messages(client.written(SM_GW_WRITE_UUID))]


async def test_aroma_wave_connect_sequence(connected, link: FakeLink) -> None:
    await connected(DeviceType.AROMA_WAVE)
    # Time sync must come first or the device ignores control writes
    # (81b0ea6); then the 02 unlock and the stored-schedule read (TYPE 4).
    assert aw_orders(link.client) == ["0007", "0200", "0600"]
    assert aroma_wave_order(link.client.written()[2])[0] == 4
    assert set(link.client.notify_callbacks) == {AROMAWAVE_CHAR_UUID}


async def test_gw_connect_sends_time_sync_then_password(connected, link: FakeLink) -> None:
    await connected(DeviceType.SCENT_MARKETING_GW, gw_password="8888")
    assert [set(gw_dps(f)) for f in gw_frames(link.client)] == [{10}, {13}]
    assert set(link.client.notify_callbacks) == {SM_GW_NOTIFY_UUID}


async def test_gw_without_password_sends_time_sync_only(connected, link: FakeLink) -> None:
    await connected(DeviceType.SCENT_MARKETING_GW)
    assert [set(gw_dps(f)) for f in gw_frames(link.client)] == [{10}]


async def test_reconnects_and_resyncs_after_drop(connected, link: FakeLink) -> None:
    await connected(DeviceType.AROMA_WAVE)
    first = link.client

    first.drop()
    await settle()

    assert len(link.clients) == 2
    assert link.client is not first
    assert aw_orders(link.client)[0] == "0007"


async def test_backs_off_while_device_is_not_visible(make_device, link: FakeLink) -> None:
    link.visible = False
    dev = make_device(DeviceType.SCENT_MARKETING_GW)
    await dev.async_setup()

    await link.advance(5 + 10 + 20 + 40 + 60 + 60)

    assert link.clients == []
    assert [s for s in link.sleeps if s > 1] == [5, 10, 20, 40, 60, 60, 60]


async def test_connects_once_device_reappears(make_device, link: FakeLink) -> None:
    link.connect_errors = [BleakNotFoundError("gone"), BleakNotFoundError("gone")]
    dev = make_device(DeviceType.SCENT_MARKETING_GW)
    await dev.async_setup()
    await settle()
    assert link.clients == []

    await link.advance(5 + 10)

    assert len(link.clients) == 1
    assert await dev.set_power(True) is True


async def test_backoff_resets_after_a_good_connection(connected, link: FakeLink) -> None:
    await connected(DeviceType.SCENT_MARKETING_GW)
    link.connect_errors = [BleakNotFoundError("gone")]
    link.client.drop()
    await link.advance(5)
    assert len(link.clients) == 2

    link.connect_errors = [BleakNotFoundError("gone")]
    link.client.drop()
    await link.advance(5)
    assert len(link.clients) == 3


async def test_aroma_wave_polls_every_60s(connected, link: FakeLink) -> None:
    await connected(DeviceType.AROMA_WAVE)
    connect_writes = len(link.client.writes)

    await link.advance(59)
    assert len(link.client.writes) == connect_writes
    await link.advance(1)
    assert aw_orders(link.client)[connect_writes:] == ["0500"]


async def test_gw_is_not_polled(connected, link: FakeLink) -> None:
    await connected(DeviceType.SCENT_MARKETING_GW)
    connect_writes = len(link.client.writes)
    await link.advance(600)
    assert len(link.client.writes) == connect_writes


async def test_gatt_layout_overrides_fallback_type(connected, link: FakeLink) -> None:
    # The config flow stores aroma_link when it saw no manufacturer data;
    # the GATT table at first connect corrects it.
    link.services = [SimpleNamespace(uuid=AROMAWAVE_SERVICE_UUID, characteristics=[])]
    dev = await connected(DeviceType.AROMA_LINK)
    assert dev.device_type == DeviceType.AROMA_WAVE
    assert await dev.set_power(True) is True
    assert link.client.written(AROMAWAVE_CHAR_UUID)


async def test_shutdown_disconnects_and_stops(connected, link: FakeLink) -> None:
    dev = await connected(DeviceType.AROMA_WAVE)
    client = link.client

    await dev.async_shutdown()
    await link.advance(600)

    assert client.is_connected is False
    assert len(link.clients) == 1
    assert await dev.set_power(True) is False
