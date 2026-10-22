"""BleLink: connection lifecycle and policy.

One class per rule (the class docstring); each test is an example of it.
"""
from __future__ import annotations

import asyncio

from types import SimpleNamespace

from bleak_retry_connector import BleakNotFoundError

from custom_components.scent_assistant.const import (
    AROMAWAVE_CHAR_UUID,
    AROMAWAVE_SERVICE_UUID,
    SM_GW_NOTIFY_UUID,
    SM_GW_WRITE_UUID,
    DeviceType,
    LinkPolicy,
)
from custom_components.scent_assistant.link import (
    LINGER_SECONDS,
    REFRESH_SECONDS,
    RELEASE_SECONDS,
    SEND_ATTEMPTS,
    SEND_RETRY_SECONDS,
)
from custom_components.scent_assistant.protocol_ble import ScentMarketingGwProtocol
from tests.captures import aroma_wave_order, gw_dps, gw_messages
from tests.fakes import FakeBluetooth, settle

GW = DeviceType.SCENT_MARKETING_GW
AW = DeviceType.AROMA_WAVE
POWER_ON = ScentMarketingGwProtocol().build_power(True)


def aw_orders(client) -> list[str]:
    return [aroma_wave_order(w)[1][4:8] for w in client.written(AROMAWAVE_CHAR_UUID)]


def gw_frames(client) -> list[bytes]:
    return [m.frame for m in gw_messages(client.written(SM_GW_WRITE_UUID))]


class TestHandshake:
    """Every connection starts with the protocol's handshake, before any command."""

    async def test_aroma_wave_syncs_time_then_unlocks_and_reads_schedules(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        # Time sync first or the device ignores control writes (81b0ea6).
        # Then schedules (06) and weekdays (09); on-demand, the refresh then
        # polls status (05).
        await make_link(AW)
        assert aw_orders(bt.client) == ["0007", "0200", "0600", "0900", "0500"]
        assert [aroma_wave_order(f)[0] for f in bt.client.written()[2:4]] == [4, 4]
        assert set(bt.client.notify_callbacks) == {AROMAWAVE_CHAR_UUID}

    async def test_gw_with_password_syncs_time_then_sends_it(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        await make_link(GW, password="8888")
        assert [set(gw_dps(f)) for f in gw_frames(bt.client)] == [{10}, {13}]
        assert set(bt.client.notify_callbacks) == {SM_GW_NOTIFY_UUID}

    async def test_gw_without_password_syncs_time_only(self, make_link, bt: FakeBluetooth) -> None:
        await make_link(GW)
        assert [set(gw_dps(f)) for f in gw_frames(bt.client)] == [{10}]

    async def test_reconnect_repeats_the_handshake(self, make_link, bt: FakeBluetooth) -> None:
        await make_link(GW, password="8888")
        bt.client.drop()
        await settle()
        assert len(bt.clients) == 2
        assert [set(gw_dps(f)) for f in gw_frames(bt.client)] == [{10}, {13}]


class TestPersistentLink:
    """A persistent link holds one connection and reconnects when it drops."""

    async def test_stays_connected(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(GW)
        await bt.advance(3600)
        assert link.connected
        assert len(bt.clients) == 1

    async def test_backs_off_while_device_is_not_visible(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        bt.visible = False
        await make_link(GW)
        await bt.advance(5 + 10 + 20 + 40 + 60 + 60)
        assert bt.clients == []
        assert [s for s in bt.sleeps if s > 1] == [5, 10, 20, 40, 60, 60, 60]

    async def test_connects_once_device_reappears(self, make_link, bt: FakeBluetooth) -> None:
        bt.connect_errors = [BleakNotFoundError("gone"), BleakNotFoundError("gone")]
        link, _ = await make_link(GW)
        assert not link.connected
        await bt.advance(5 + 10)
        assert link.connected
        assert await link.send(POWER_ON)

    async def test_backoff_resets_after_a_good_connection(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        await make_link(GW)
        for expected_clients in (2, 3):
            bt.connect_errors = [BleakNotFoundError("gone")]
            bt.client.drop()
            await bt.advance(5)
            assert len(bt.clients) == expected_clients

    async def test_command_while_reconnecting_fails_fast(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        # The supervisor owns reconnection: a command during its backoff
        # doesn't start a second connect, even though one would succeed.
        link, _ = await make_link(GW)
        bt.connect_errors = [BleakNotFoundError("gone")]
        bt.client.drop()
        await settle()
        assert await link.send(POWER_ON) is False
        assert len(bt.clients) == 1

    async def test_polls_when_the_protocol_needs_it(self, make_link, bt: FakeBluetooth) -> None:
        await make_link(AW, policy=LinkPolicy.PERSISTENT)
        after_connect = len(bt.client.writes)
        await bt.advance(59)
        assert len(bt.client.writes) == after_connect
        await bt.advance(1)
        assert aw_orders(bt.client)[after_connect:] == ["0500"]

    async def test_gw_is_not_polled(self, make_link, bt: FakeBluetooth) -> None:
        await make_link(GW)
        after_connect = len(bt.client.writes)
        await bt.advance(600)
        assert len(bt.client.writes) == after_connect


class TestReleasingTheHold:
    """Releasing a persistent link frees the device for the vendor app;
    HA connects briefly for commands and takes the hold back after 10 minutes."""

    async def test_release_disconnects_and_stays_off(self, make_link, bt: FakeBluetooth) -> None:
        link, observed = await make_link(GW)
        changes = observed.changes

        await link.set_held(False)
        await bt.advance(RELEASE_SECONDS - 1)

        assert (link.held, link.connected) == (False, False)
        assert len(bt.clients) == 1
        assert observed.changes > changes

    async def test_command_while_released_connects_briefly(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        link, _ = await make_link(GW, password="8888")
        await link.set_held(False)

        assert await link.send(POWER_ON)
        assert link.connected
        assert gw_frames(bt.client)[-1] == POWER_ON
        assert [set(gw_dps(f)) for f in gw_frames(bt.client)[:2]] == [{10}, {13}]

        await bt.advance(LINGER_SECONDS)
        assert not link.connected
        assert link.held is False

    async def test_hold_comes_back_after_10_minutes(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(GW)
        await link.set_held(False)
        await bt.advance(RELEASE_SECONDS)
        assert (link.held, link.connected) == (True, True)
        assert len(bt.connected_clients) == 1

    async def test_holding_again_reconnects_at_once(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(GW)
        await link.set_held(False)
        await link.set_held(True)
        await settle()
        assert link.connected
        await bt.advance(RELEASE_SECONDS * 2)
        assert len(bt.clients) == 2  # the release timer was cancelled

    async def test_on_demand_links_have_no_hold(self, make_link) -> None:
        link, _ = await make_link(AW)
        assert link.held is None
        await link.set_held(True)
        assert link.held is None


class TestOnDemandLink:
    """An on-demand link connects for a command, lingers 30 s for
    follow-ups, and reads state every 5 minutes."""

    async def test_start_reads_state_then_disconnects(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(AW)
        assert link.connected
        await bt.advance(LINGER_SECONDS)
        assert not link.connected

    async def test_follow_up_commands_share_one_connection(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        link, _ = await make_link(AW)
        await bt.advance(LINGER_SECONDS)
        power = link.protocol.build_power(True)

        assert await link.send(power)
        await bt.advance(LINGER_SECONDS - 1)
        assert await link.send(power)
        await bt.advance(LINGER_SECONDS - 1)

        assert link.connected
        assert len(bt.clients) == 2
        await bt.advance(1)
        assert not link.connected

    async def test_refreshes_every_5_minutes(self, make_link, bt: FakeBluetooth) -> None:
        await make_link(AW)
        await bt.advance(REFRESH_SECONDS * 2)
        assert len(bt.clients) == 3
        assert aw_orders(bt.client)[-1] == "0500"



class TestCommandRetry:
    """A command the link connects for is tried 3 times, 15 s apart, before
    it fails: at the edge of a proxy's range single attempts often fail."""

    async def test_succeeds_once_the_device_is_back(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(AW)
        await bt.advance(LINGER_SECONDS)
        bt.vanish()
        power = link.protocol.build_power(False)
        sent = asyncio.ensure_future(link.send(power))
        await settle()
        bt.appear()
        await bt.advance(SEND_RETRY_SECONDS)
        assert await sent is True
        assert bt.client.written()[-1] == power

    async def test_retries_a_failed_connect(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(AW)
        await bt.advance(LINGER_SECONDS)
        bt.connect_errors = [BleakNotFoundError("timeout"), BleakNotFoundError("timeout")]
        sent = asyncio.ensure_future(link.send(link.protocol.build_power(False)))
        await bt.advance(SEND_RETRY_SECONDS * 2)
        assert await sent is True

    async def test_a_failed_write_reconnects_and_tries_again(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        link, _ = await make_link(AW)
        bt.client.fail_writes = True  # half-dead connection
        power = link.protocol.build_power(False)
        sent = asyncio.ensure_future(link.send(power))
        await bt.advance(SEND_RETRY_SECONDS)
        assert await sent is True
        assert len(bt.clients) == 2
        assert bt.client.written()[-1] == power

    async def test_fails_after_3_attempts(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(AW)
        await bt.advance(LINGER_SECONDS)
        bt.vanish()
        sent = asyncio.ensure_future(link.send(link.protocol.build_power(False)))
        await bt.advance(SEND_RETRY_SECONDS * (SEND_ATTEMPTS - 1))
        assert await sent is False
        assert bt.sleeps.count(SEND_RETRY_SECONDS) == SEND_ATTEMPTS - 1

    async def test_held_link_still_fails_fast(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(GW)
        bt.client.fail_writes = True
        assert await link.send(POWER_ON) is False
        assert SEND_RETRY_SECONDS not in bt.sleeps


class TestAvailability:
    """A link is available while it is connected or the device is advertising."""

    async def test_on_demand_link_is_available_between_connections(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        link, _ = await make_link(AW)
        await bt.advance(LINGER_SECONDS)
        assert (link.connected, link.available) == (False, True)

    async def test_follows_advertisements(self, make_link, bt: FakeBluetooth) -> None:
        link, observed = await make_link(AW)
        await bt.advance(LINGER_SECONDS)

        changes = observed.changes
        bt.vanish()
        assert link.available is False
        bt.appear()
        assert link.available is True
        assert observed.changes == changes + 2

    async def test_connected_link_stays_available_without_advertisements(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        # Peripherals stop advertising while connected.
        link, _ = await make_link(GW)
        bt.vanish()
        assert link.available is True


class TestGattLayout:
    """The GATT table at first connect corrects a fallback device type."""

    async def test_aroma_link_fallback_becomes_aroma_wave(
        self, make_link, bt: FakeBluetooth
    ) -> None:
        bt.services = [SimpleNamespace(uuid=AROMAWAVE_SERVICE_UUID, characteristics=[])]
        link, _ = await make_link(DeviceType.AROMA_LINK)
        assert link.protocol.device_type == AW
        assert await link.send(link.protocol.build_power(True))
        assert bt.client.written(AROMAWAVE_CHAR_UUID)


class TestStop:
    """Stopping disconnects and ends all background work."""

    async def test_stop(self, make_link, bt: FakeBluetooth) -> None:
        link, _ = await make_link(GW)
        client = bt.client
        await link.stop()
        await bt.advance(3600)
        assert client.is_connected is False
        assert len(bt.clients) == 1
        assert await link.send(POWER_ON) is False


async def test_notifications_reach_the_owner(make_link, bt: FakeBluetooth) -> None:
    link, observed = await make_link(GW)
    bt.client.notify(b"\x01\x02\x03")
    assert observed.frames == [b"\x01\x02\x03"]
    assert link.recent_notifications == ["010203"]
