"""A fake BLE link for driving ScentDiffuserDevice without a radio.

device.py reaches Bluetooth through three calls: HA's BLEDevice lookup,
close_stale_connections_by_address and establish_connection. FakeLink.install
replaces all three (plus device.py's asyncio.sleep, with a virtual clock) and
hands out FakeBleakClient instances, so tests exercise the device manager
through its public methods only.
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
        self.is_connected = False

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


class FakeLink:
    """Controls what the device manager sees of the Bluetooth stack."""

    def __init__(self) -> None:
        self.visible = True
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

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_stale_connections(address: str) -> None:
            return None

        monkeypatch.setattr(device_module, "establish_connection", self.establish_connection)
        monkeypatch.setattr(device_module, "close_stale_connections_by_address",
                            no_stale_connections)
        monkeypatch.setattr(device_module.bluetooth, "async_ble_device_from_address",
                            self.lookup)
        # Only device.py's view of asyncio gets the virtual clock.
        monkeypatch.setattr(device_module, "asyncio", _AsyncioWithClock(self.sleep))

    def lookup(self, hass, address: str, connectable: bool = True):
        return SimpleNamespace(address=address) if self.visible else None

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
