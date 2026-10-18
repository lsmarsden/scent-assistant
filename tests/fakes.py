"""A fake Bluetooth stack for driving BleLink and ScentDiffuserDevice
without a radio.

link.py reaches Bluetooth through bleak-retry-connector (establish_connection,
close_stale_connections_by_address) and HA's bluetooth helpers (BLEDevice
lookup, presence, advertisement and unavailable callbacks). FakeBluetooth
replaces them, plus asyncio.sleep in link.py and device.py with a virtual
clock, and hands out FakeBleakClient instances. Tests then drive the link
and the device manager through their public interfaces only.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from bleak import BleakError

from custom_components.scent_assistant import device as device_module
from custom_components.scent_assistant import link as link_module

GW_ADDRESS = "AA:BB:CC:DD:EE:01"
AW_ADDRESS = "AA:BB:CC:DD:EE:02"

COMMAND_WAIT = 1.0

_real_sleep = asyncio.sleep


class FakeBleakClient:
    def __init__(self, services: list | None = None) -> None:
        self.is_connected = True
        self.services = services or []
        self.writes: list[tuple[str, bytes]] = []
        self.notify_callbacks: dict[str, Callable] = {}
        self.fail_writes = False
        self.disconnected_callback: Callable | None = None

    async def start_notify(self, uuid: str, callback: Callable) -> None:
        self.notify_callbacks[uuid] = callback

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = True) -> None:
        if self.fail_writes:
            raise BleakError("write failed")
        self.writes.append((uuid, bytes(data)))

    async def disconnect(self) -> None:
        # Like bleak, our own disconnect also fires the callback.
        if self.is_connected:
            self.drop()

    def drop(self) -> None:
        """The peripheral went away."""
        self.is_connected = False
        assert self.disconnected_callback is not None
        self.disconnected_callback(self)

    def notify(self, data: bytes) -> None:
        for callback in self.notify_callbacks.values():
            callback(0, bytearray(data))

    def written(self, uuid: str | None = None) -> list[bytes]:
        return [data for u, data in self.writes if uuid is None or u == uuid]


class FakeBluetooth:
    """Controls what the link sees of the Bluetooth stack."""

    def __init__(self) -> None:
        self.visible = True  # advertising, so HA can see and connect to it
        self._advertisement_callbacks: list[Callable] = []
        self._unavailable_callbacks: list[Callable] = []
        self.connect_errors: list[Exception] = []
        self.services: list = []
        self.clients: list[FakeBleakClient] = []
        self.sleeps: list[float] = []
        self.now = 0.0
        self._timers: list[tuple[float, int, asyncio.Future]] = []
        self._order = itertools.count()

    @property
    def client(self) -> FakeBleakClient:
        return self.clients[-1]

    @property
    def connected_clients(self) -> list[FakeBleakClient]:
        return [c for c in self.clients if c.is_connected]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_stale_connections(address: str) -> None:
            return None

        bt = link_module.bluetooth
        monkeypatch.setattr(link_module, "establish_connection", self.establish_connection)
        monkeypatch.setattr(link_module, "close_stale_connections_by_address",
                            no_stale_connections)
        monkeypatch.setattr(bt, "async_ble_device_from_address", self.lookup)
        monkeypatch.setattr(bt, "async_address_present", self.present)
        monkeypatch.setattr(bt, "async_register_callback", self.register_callback)
        monkeypatch.setattr(bt, "async_track_unavailable", self.track_unavailable)
        # Only link.py's and device.py's view of asyncio gets the virtual clock.
        clock = _AsyncioWithClock(self.sleep)
        monkeypatch.setattr(link_module, "asyncio", clock)
        monkeypatch.setattr(device_module, "asyncio", clock)

    def lookup(self, hass, address: str, connectable: bool = True):
        return SimpleNamespace(address=address) if self.visible else None

    def present(self, hass, address: str, connectable: bool = True) -> bool:
        return self.visible

    def register_callback(self, hass, callback, matcher, mode):
        self._advertisement_callbacks.append(callback)
        return lambda: self._advertisement_callbacks.remove(callback)

    def track_unavailable(self, hass, callback, address, connectable=True):
        self._unavailable_callbacks.append(callback)
        return lambda: self._unavailable_callbacks.remove(callback)

    def appear(self) -> None:
        """The device starts advertising."""
        self.visible = True
        for callback in list(self._advertisement_callbacks):
            callback(SimpleNamespace(), "advertisement")

    def vanish(self) -> None:
        """HA's Bluetooth stops hearing the device."""
        self.visible = False
        for callback in list(self._unavailable_callbacks):
            callback(SimpleNamespace())

    async def establish_connection(self, client_class, ble_device, name, *,
                                   disconnected_callback=None, **kwargs):
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        client = FakeBleakClient(self.services)
        client.disconnected_callback = disconnected_callback
        self.clients.append(client)
        return client

    async def sleep(self, delay: float, *args) -> None:
        """Virtual clock. Short waits (the 1 s pause after a command) return
        at once; longer ones (backoff, poll interval) wait for advance()."""
        self.sleeps.append(delay)
        if delay <= COMMAND_WAIT:
            await _real_sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._timers, (self.now + delay, next(self._order), future))
        await future

    async def advance(self, seconds: float) -> None:
        """Move the clock forward, waking sleepers in deadline order."""
        target = self.now + seconds
        await settle()
        while self._timers and self._timers[0][0] <= target:
            deadline, _, future = heapq.heappop(self._timers)
            self.now = deadline
            if not future.done():
                future.set_result(None)
            await settle()
        self.now = target


async def settle(rounds: int = 50) -> None:
    """Let the supervisor and poll tasks run until they block."""
    for _ in range(rounds):
        await _real_sleep(0)


class _AsyncioWithClock:
    """The asyncio module, except for sleep."""

    def __init__(self, sleep: Callable) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str):
        return getattr(asyncio, name)
