"""BLE link to one diffuser.

Owns everything between "send this frame" and the radio: connecting through
Home Assistant's Bluetooth stack, the per-connection handshake, chunked
writes, notifications, polling, availability, and the connection policy:

- persistent: hold one connection and reconnect with backoff when it
  drops. The hold can be released so the vendor app can connect; HA takes
  it back after RELEASE_SECONDS, and connects briefly for any command sent
  in between.
- on_demand: connect for a command and stay connected LINGER_SECONDS for
  follow-ups, then disconnect. Every REFRESH_SECONDS the link connects to
  read the device's state.

The link is available while it is connected or while HA's Bluetooth has a
connectable advertisement from the device.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakClient, BleakError
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakNotFoundError,
    close_stale_connections_by_address,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import DeviceType, LinkPolicy
from .protocol_ble import BleProtocol, get_protocol, protocol_from_services

_LOGGER = logging.getLogger(__name__)

BACKOFF_INITIAL = 5     # reconnect backoff for persistent links (seconds)
BACKOFF_MAX = 60
LINGER_SECONDS = 30     # stay connected after a transient command
RELEASE_SECONDS = 600   # how long a released persistent link stays released
REFRESH_SECONDS = 300   # on-demand state read interval

_RECENT_NOTIFICATIONS = 20
_RECENT_COMMANDS = 10


class BleLink:
    """Connection to one diffuser, held according to a LinkPolicy."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        protocol: BleProtocol,
        *,
        policy: LinkPolicy,
        on_frame: Callable[[bytes], None],
        on_change: Callable[[], None],
        password: str | None = None,
        pid: int | None = None,
    ) -> None:
        self._hass = hass
        self._address = address
        self._name = name or address
        self._protocol = protocol
        self._policy = policy
        self._on_frame = on_frame
        self._on_change = on_change
        self._password = password
        self._pid = pid

        self._client: BleakClient | None = None
        self._connected = False
        self._present = False
        self._held = policy is LinkPolicy.PERSISTENT
        self._stopping = False
        self._lock = asyncio.Lock()
        self._dropped = asyncio.Event()
        self._resumed = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._linger: asyncio.Task | None = None
        self._release: asyncio.Task | None = None
        self._unsubscribe: list[Callable[[], None]] = []
        # GATT reconciliation runs once per link lifetime.
        self._gatt_checked = False

        self.recent_notifications: list[str] = []
        self.recent_commands: list[str] = []

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    @property
    def protocol(self) -> BleProtocol:
        """May change once, at first connect, if the GATT layout disagrees
        with the configured device type."""
        return self._protocol

    @property
    def policy(self) -> LinkPolicy:
        return self._policy

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    @property
    def available(self) -> bool:
        return self.connected or self._present

    @property
    def held(self) -> bool | None:
        """Whether a persistent link is holding the connection (None for
        on-demand links, which never hold it)."""
        return self._held if self._policy is LinkPolicy.PERSISTENT else None

    async def start(self) -> None:
        self._stopping = False
        self._present = bluetooth.async_address_present(
            self._hass, self._address, connectable=True
        )
        self._unsubscribe = [
            bluetooth.async_register_callback(
                self._hass, self._on_advertisement,
                {"address": self._address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            ),
            bluetooth.async_track_unavailable(
                self._hass, self._on_unavailable, self._address, connectable=True
            ),
        ]
        if self._policy is LinkPolicy.PERSISTENT:
            self._resumed.set()
            self._spawn(self._supervise())
            if self._protocol.poll_interval_seconds:
                self._spawn(self._poll())
        else:
            self._spawn(self._refresh())

    async def stop(self) -> None:
        self._stopping = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        self._dropped.set()
        self._resumed.set()
        for task in {*self._tasks, self._linger, self._release}:
            if task and not task.done():
                task.cancel()
        await self._disconnect()

    async def send(self, frame: bytes) -> bool:
        """Write one command. Connects for it when the policy allows: an
        on-demand link, or a persistent link whose hold is released. A held
        persistent link that is reconnecting fails fast instead."""
        if not frame or self._stopping:
            return False
        if not self.connected:
            if self._held:
                return False
            if not await self._connect():
                return False
        ok = await self._write(frame)
        if not self._held:
            self._linger_then_disconnect()
        return ok

    async def set_held(self, held: bool) -> None:
        """Hold or release a persistent link. Releasing disconnects so the
        vendor app can connect; the hold comes back after RELEASE_SECONDS."""
        if self._policy is not LinkPolicy.PERSISTENT or held == self._held:
            return
        self._held = held
        self._cancel(self._release)
        if held:
            self._resumed.set()
        else:
            self._resumed.clear()
            self._release = self._spawn(self._release_for(RELEASE_SECONDS))
            await self._disconnect()
        self._on_change()

    # ------------------------------------------------------------------
    # Policy tasks
    # ------------------------------------------------------------------

    async def _supervise(self) -> None:
        backoff = BACKOFF_INITIAL
        while not self._stopping:
            if not self._held:
                await self._resumed.wait()
                continue
            self._dropped.clear()
            if await self._connect():
                backoff = BACKOFF_INITIAL
                await self._dropped.wait()
                continue
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)

    async def _poll(self) -> None:
        """Periodic status poll for persistent links whose device doesn't
        push state on its own."""
        interval = self._protocol.poll_interval_seconds or 0
        while not self._stopping and interval > 0:
            await asyncio.sleep(interval)
            frame = self._protocol.build_poll()
            if self.connected and frame:
                await self._write(frame)

    async def _refresh(self) -> None:
        """On-demand links: connect now and every REFRESH_SECONDS to read
        state (the handshake and poll make the device report it)."""
        while not self._stopping:
            if self.connected or await self._connect():
                if frame := self._protocol.build_poll():
                    await self._write(frame)
                self._linger_then_disconnect()
            await asyncio.sleep(REFRESH_SECONDS)

    async def _release_for(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.set_held(True)

    def _linger_then_disconnect(self) -> None:
        self._cancel(self._linger)
        self._linger = self._spawn(self._disconnect_after(LINGER_SECONDS))

    async def _disconnect_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if not self._held:
            await self._disconnect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def _connect(self) -> bool:
        if self.connected:
            return True
        async with self._lock:
            if self.connected:
                return True
            ble_device = bluetooth.async_ble_device_from_address(
                self._hass, self._address, connectable=True
            )
            if ble_device is None:
                _LOGGER.debug("BLE device %s (%s) is not currently discoverable to HA",
                              self._name, self._address)
                return False
            try:
                # Some adapters hang on to a half-open connection from a
                # previous attempt; bleak-retry-connector cleans them up.
                await close_stale_connections_by_address(self._address)
                self._client = await establish_connection(
                    BleakClient, ble_device, self._name,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=3,
                )
            except BleakNotFoundError as err:
                _LOGGER.warning("BLE device %s not found: %s", self._name, err)
                return False
            except BLEAK_RETRY_EXCEPTIONS as err:
                _LOGGER.warning("BLE connect failed for %s: %s", self._name, err)
                return False
            self._connected = True
            self._reconcile_protocol_from_gatt()
            try:
                await self._client.start_notify(
                    self._protocol.notify_char_uuid, self._on_notification
                )
            except Exception as err:  # noqa: BLE001 - some firmwares reject CCCD writes
                _LOGGER.debug("Could not subscribe to notifications: %s", err)
            for frame in self._protocol.build_handshake(self._password):
                await self._write(frame)
            _LOGGER.info("BLE connected: %s", self._name)
        self._on_change()
        return True

    async def _disconnect(self) -> None:
        self._cancel(self._linger)
        client, self._client = self._client, None
        was_connected = self._connected
        self._connected = False
        self._dropped.set()
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception as err:  # noqa: BLE001 - best effort on the way out
                _LOGGER.debug("BLE disconnect from %s failed: %s", self._name, err)
        if was_connected and not self._stopping:
            self._on_change()

    def _on_disconnect(self, client: BleakClient) -> None:
        """bleak's disconnected callback, for drops and our own disconnects."""
        if client is not self._client:
            return
        self._connected = False
        self._dropped.set()
        self._cancel(self._linger)
        if not self._stopping:
            self._on_change()

    async def _write(self, frame: bytes) -> bool:
        if not self.connected:
            return False
        self._remember(self.recent_commands, frame, _RECENT_COMMANDS)
        for chunk in self._protocol.wire_chunks(frame):
            try:
                await self._client.write_gatt_char(
                    self._protocol.write_char_uuid, chunk, response=True
                )
            except (BleakError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("BLE write failed: %s", err)
                return False
        return True

    def _on_notification(self, sender: int, data: bytearray) -> None:
        raw = bytes(data)
        self._remember(self.recent_notifications, raw, _RECENT_NOTIFICATIONS)
        self._on_frame(raw)

    def _reconcile_protocol_from_gatt(self) -> None:
        """Switch protocol if the GATT layout disagrees with the configured
        type: the config flow stores AROMA_LINK as a fallback when it saw no
        manufacturer data."""
        if self._gatt_checked or self._client is None:
            return
        self._gatt_checked = True
        services = getattr(self._client, "services", None)
        if not services:
            return
        # One GATT dump per link lifetime, to diagnose unknown protocols
        # without another PacketLogger capture.
        for service in services:
            _LOGGER.info("GATT service %s", service.uuid)
            for char in service.characteristics:
                _LOGGER.info("  char %s handle=0x%04X props=%s",
                             char.uuid, char.handle, ",".join(char.properties))
        detected = protocol_from_services(services)
        current = self._protocol.device_type
        if detected is None or detected == current:
            return
        # Don't downgrade the XOR-encrypted GW variant to plain GW.
        if (detected == DeviceType.SCENT_MARKETING_GW
                and current == DeviceType.SCENT_MARKETING_GW_XOR):
            return
        _LOGGER.warning("GATT layout for %s indicates %s, switching from %s",
                        self._name, detected.value, current.value)
        self._protocol = get_protocol(
            detected, mac=self._address.replace(":", ""), pid=self._pid
        )

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    def _on_advertisement(self, service_info, change) -> None:
        if not self._present:
            self._present = True
            self._on_change()

    def _on_unavailable(self, service_info) -> None:
        if self._present:
            self._present = False
            self._on_change()

    # ------------------------------------------------------------------

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @staticmethod
    def _cancel(task: asyncio.Task | None) -> None:
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    @staticmethod
    def _remember(buffer: list[str], data: bytes, limit: int) -> None:
        buffer.append(data.hex())
        del buffer[:-limit]
