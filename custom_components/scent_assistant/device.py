"""Unified device manager for scent diffusers.

Turns HA-level commands into protocol frames, sends them over the BLE link
(link.py, which owns the connection and its policy) or the Aroma-Link
cloud, and folds the device's notifications into DiffuserState.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .const import (
    DeviceType,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
    LinkPolicy,
)
from .link import BleLink
from .protocol_ble import (
    BleProtocol,
    DiffuserState,
    AromaWaveMode,
    AromaWaveProtocol,
    GwModeTasks,
    GwTask,
    ScheduleSlot,
    ScheduleSetup,
    AromaLinkBleProtocol,
    ScentimentProtocol,
    ScentMarketingAkProtocol,
    ScentMarketingGwProtocol,
    get_protocol,
    detect_device_type,
)
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

# How long a command waits for the device's response notification.
COMMAND_SETTLE_SECONDS = 1.0


class ScentDiffuserDevice:
    """Manages a single scent diffuser via BLE and/or cloud."""

    def __init__(
        self,
        hass: HomeAssistant | None = None,
        ble_address: str | None = None,
        ble_name: str | None = None,
        device_type: DeviceType | None = None,
        cloud_client: AromaLinkCloudClient | None = None,
        cloud_device_id: str | None = None,
        sm_metadata: dict | None = None,
        gw_password: str | None = None,
        link_policy: LinkPolicy | None = None,
    ) -> None:
        # Detection metadata from the config flow — populated only for
        # Scent Marketing family devices.
        self._sm_metadata = sm_metadata or {}
        self._ble_address = ble_address
        self._ble_name = ble_name or ""

        # Device type
        if not device_type:
            # No advertisement object available at this point — fall back to
            # name-only detection (config flow performs the richer
            # advertisement-aware detection up front and persists the result).
            device_type = (detect_device_type(ble_name) if ble_name else None) or DeviceType.AROMA_LINK

        # Protocol handler. GW-XOR needs the MAC for its keystream; GW
        # devices with PID 98 use the Tuya-DP hex parser.
        mac = (ble_address or "").replace(":", "")
        pid = self._sm_metadata.get("pid")
        protocol = get_protocol(device_type, mac=mac, pid=pid)

        # BLE: the link owns the protocol from here, since the GATT layout
        # at first connect can replace it.
        self._link: BleLink | None = None
        self._cloud_protocol: BleProtocol = protocol
        if ble_address:
            self._link = BleLink(
                hass, ble_address, self._ble_name, protocol,
                policy=link_policy or protocol.default_link_policy,
                password=gw_password or None,
                pid=pid,
                on_frame=self._on_frame,
                on_change=self._notify_state_changed,
            )

        # Cloud
        self._cloud: AromaLinkCloudClient | None = cloud_client
        self._cloud_device_id = cloud_device_id

        # State
        self._state = DiffuserState()
        self._state_callbacks: list[callable] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._ble_name or f"Diffuser {self._ble_address or self._cloud_device_id}"

    @property
    def unique_id(self) -> str:
        return self._ble_address or self._cloud_device_id or "unknown"

    @property
    def _protocol(self) -> BleProtocol:
        return self._link.protocol if self._link else self._cloud_protocol

    @property
    def device_type(self) -> DeviceType:
        return self._protocol.device_type

    @property
    def link_policy(self) -> LinkPolicy | None:
        return self._link.policy if self._link else None

    @property
    def link_held(self) -> bool | None:
        """Whether a persistent BLE link holds the connection (None when the
        link is on-demand or there is no BLE link)."""
        return self._link.held if self._link else None

    @property
    def sm_metadata(self) -> dict:
        """Scent Marketing detection metadata (empty for other families)."""
        return self._sm_metadata

    @property
    def recent_notifications(self) -> list[str]:
        return list(self._link.recent_notifications) if self._link else []

    @property
    def recent_commands(self) -> list[str]:
        return list(self._link.recent_commands) if self._link else []

    @property
    def model_name(self) -> str:
        """Human-readable model name for HA's device-info "model" field.

        For Scent Marketing devices this surfaces the detected family
        directly on the device page, so a reporter can verify our
        detection at a glance without digging through logs.
        """
        mapping = {
            DeviceType.TUYA_BLE: "ShinePick / Tuya BLE",
            DeviceType.AROMA_LINK: "Aroma-Link",
            DeviceType.SCENTIMENT: "Scentiment Air 2",
            DeviceType.SCENT_MARKETING_AK: "Scent Marketing (AK)",
            DeviceType.SCENT_MARKETING_GW: "Scent Marketing (GW)",
            DeviceType.SCENT_MARKETING_GW_XOR: "Scent Marketing (GW, encrypted)",
        }
        base = mapping.get(self.device_type, self.device_type.value)
        # Append the PID when known — different OEMs share the same family
        # but have distinct PIDs, useful for triage.
        pid = self._sm_metadata.get("pid")
        if pid is not None:
            return f"{base} — PID {pid}"
        return base

    @property
    def device_info(self) -> dict:
        """Shared HA device_info block, consumed by every entity."""
        return {
            "identifiers": {("scent_assistant", self.unique_id)},
            "name": self.name,
            "manufacturer": "Scent Diffuser",
            "model": self.model_name,
        }

    @property
    def state(self) -> DiffuserState:
        return self._state

    @property
    def supports_fan(self) -> bool:
        return self._protocol.supports_fan()

    @property
    def supports_cloud(self) -> bool:
        return self._cloud is not None and self._cloud_device_id is not None

    @property
    def connection_mode(self) -> str:
        if self._ble_address:
            return "ble"
        if self.supports_cloud and self._cloud and self._cloud.authenticated:
            return "cloud"
        return "offline"

    @property
    def available(self) -> bool:
        if self._link:
            return self._link.available
        return self.connection_mode != "offline"

    def register_state_callback(self, callback: callable) -> callable:
        """Call `callback` on every state change; returns an unsubscribe."""
        self._state_callbacks.append(callback)
        return lambda: self._state_callbacks.remove(callback)

    def _notify_state_changed(self) -> None:
        for cb in list(self._state_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in state callback")

    # ------------------------------------------------------------------
    # BLE link
    # ------------------------------------------------------------------

    async def _ble_send(self, data: bytes) -> bool:
        return await self._link.send(data) if self._link else False

    async def _ble_execute(self, data: bytes) -> bool:
        """Send a command and wait briefly for the notification response."""
        success = await self._ble_send(data)
        if success:
            await asyncio.sleep(COMMAND_SETTLE_SECONDS)
        return success

    async def set_hold_connection(self, held: bool) -> None:
        """Hold or release a persistent link (see BleLink.set_held)."""
        if self._link:
            await self._link.set_held(held)

    def _on_frame(self, raw: bytes) -> None:
        """Fold one BLE notification into the device state."""
        if self._state.apply(self._protocol.parse_notification(raw)):
            self._notify_state_changed()

    # ------------------------------------------------------------------
    # Commands (BLE first, cloud fallback)
    # ------------------------------------------------------------------

    async def set_aromawave_mode(
        self,
        mode_idx: int,
        *,
        enabled: bool | None = None,
        start_hour: int | None = None,
        start_minute: int | None = None,
        end_hour: int | None = None,
        end_minute: int | None = None,
        work_seconds: int | None = None,
        pause_seconds: int | None = None,
    ) -> bool:
        """Patch one or more fields of an AromaWave mode and re-emit the
        FEEF 00 18 frame. The device requires all fields each write, so we
        merge with cached state (falling back to safe defaults if unknown)."""
        if not self._ble_address or not isinstance(self._protocol, AromaWaveProtocol):
            return False
        cached = self._state.aromawave_modes.get(mode_idx, AromaWaveMode())
        new_enabled = enabled if enabled is not None else (cached.enabled or False)
        new_sh = start_hour if start_hour is not None else (cached.start_hour or 9)
        new_sm = start_minute if start_minute is not None else (cached.start_minute or 0)
        new_eh = end_hour if end_hour is not None else (cached.end_hour or 22)
        new_em = end_minute if end_minute is not None else (cached.end_minute or 0)
        new_ws = work_seconds if work_seconds is not None else (cached.work_seconds or 10)
        new_ps = pause_seconds if pause_seconds is not None else (cached.pause_seconds or 120)
        new_mask = cached.weekday_mask if cached.weekday_mask else 0x7F
        # The app always sends the 00 14 weekday frame immediately before the
        # 00 18 schedule frame. The device stores the schedule params from
        # 00 18 alone, but the enable transition only commits when paired with
        # the preceding 00 14 - so send both, in that order.
        await self._ble_send(self._protocol.build_mode_weekday(mode_idx, new_mask))
        cmd = self._protocol.build_mode_schedule(
            mode_idx, new_sh, new_sm, new_eh, new_em, new_ws, new_ps, new_enabled
        )
        if await self._ble_execute(cmd):
            # Optimistic local update; device echo will confirm.
            mode = self._state.aromawave_modes.setdefault(mode_idx, AromaWaveMode())
            mode.enabled = new_enabled
            mode.start_hour = new_sh
            mode.start_minute = new_sm
            mode.end_hour = new_eh
            mode.end_minute = new_em
            mode.work_seconds = new_ws
            mode.pause_seconds = new_ps
            mode.weekday_mask = new_mask
            self._notify_state_changed()
            return True
        return False

    async def set_power(self, on: bool) -> bool:
        """Turn device on or off."""
        # Try BLE
        if self._ble_address:
            cmd = self._protocol.build_power(on)
            if await self._ble_execute(cmd):
                self._state.power = on
                self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
                return True

        # Cloud fallback
        if self.supports_cloud and self._cloud:
            success = await self._cloud.set_power(self._cloud_device_id, on)
            if success:
                self._state.power = on
                self._state.phase = "idle" if on else "off"
                self._notify_state_changed()
            return success

        return False

    async def set_fan(self, on: bool) -> bool:
        """Turn fan on or off (Aroma-Link + Scent Marketing AK)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, (AromaLinkBleProtocol, ScentMarketingAkProtocol)):
            cmd = proto.build_fan(on)
            if await self._ble_execute(cmd):
                self._state.fan = on
                self._notify_state_changed()
                return True
        return False

    async def set_lock(self, on: bool) -> bool:
        """Toggle child-lock (Scent Marketing AK + GW + GW-XOR)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, (ScentMarketingAkProtocol, ScentMarketingGwProtocol)):
            cmd = proto.build_lock(on)
            if await self._ble_execute(cmd):
                self._state.lock = on
                self._notify_state_changed()
                return True
        return False

    async def set_customize(self, work_seconds: int, pause_seconds: int) -> bool:
        """Set GW customise mode work/pause durations (DP-15)"""
        if not self._ble_address:
            return False
        if not isinstance(self._protocol, ScentMarketingGwProtocol):
            return False
        work = max(5, min(35, int(work_seconds)))
        pause = max(60, min(300, int(pause_seconds)))
        mode_tasks = self._gw_mode_tasks().with_grade(0)
        cmd = self._protocol.build_customize(work, pause, mode_tasks)
        if await self._ble_execute(cmd):
            self._state.work_seconds = work
            self._state.pause_seconds = pause
            self._state.grade = 0
            self._state.gw_mode_tasks = mode_tasks
            self._notify_state_changed()
            return True
        return False

    async def set_grade(self, grade: int) -> bool:
        """Set the GW spray grade (1-5), keeping the rest of DP-4 as the
        device last reported it."""
        if not self._ble_address:
            return False
        if not isinstance(self._protocol, ScentMarketingGwProtocol):
            return False
        grade = max(1, min(5, int(grade)))
        mode_tasks = self._gw_mode_tasks().with_grade(grade)
        if await self._ble_execute(self._protocol.build_mode_tasks(mode_tasks)):
            self._state.grade = grade
            self._state.gw_mode_tasks = mode_tasks
            self._notify_state_changed()
            return True
        return False

    def _gw_mode_tasks(self) -> GwModeTasks:
        """The device's last pushed DP-4, or the app's defaults before one
        has arrived (one all-day, all-week task)."""
        return self._state.gw_mode_tasks or GwModeTasks()

    async def set_lamp(self, on: bool) -> bool:
        """Toggle auxiliary lamp (Scent Marketing AK lamp-bit, GW DP-11 light)."""
        if not self._ble_address:
            return False
        proto = self._protocol
        if isinstance(proto, ScentMarketingAkProtocol):
            cmd = proto.build_lamp(on)
        elif isinstance(proto, ScentMarketingGwProtocol):
            cmd = proto.build_light(on)
        else:
            return False
        if await self._ble_execute(cmd):
            self._state.light_on = on
            self._notify_state_changed()
            return True
        return False

    async def set_level(self, level: int) -> bool:
        """Set Scentiment spray intensity (1-3)."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_level(level)
        if await self._ble_execute(cmd):
            self._state.level = level
            self._notify_state_changed()
            return True
        return False

    async def set_rgb_color(self, r: int, g: int, b: int) -> bool:
        """Set Scentiment RGB LED color."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_rgb_color(r, g, b)
        if await self._ble_execute(cmd):
            self._state.rgb_color = (r, g, b)
            self._notify_state_changed()
            return True
        return False

    async def set_rgb_led(self, on: bool) -> bool:
        """Turn Scentiment RGB LED on or off."""
        if not isinstance(self._protocol, ScentimentProtocol) or not self._ble_address:
            return False
        cmd = self._protocol.build_set_rgb_led(on)
        if await self._ble_execute(cmd):
            self._state.rgb_on = on
            self._notify_state_changed()
            return True
        return False

    async def set_work_duration(self, seconds: int) -> bool:
        """Set the spray work duration and write to device."""
        self._state.work_seconds = seconds
        return await self._write_schedule_to_device()

    async def set_pause_duration(self, seconds: int) -> bool:
        """Set the pause duration and write to device."""
        self._state.pause_seconds = seconds
        return await self._write_schedule_to_device()

    async def set_schedule(
        self,
        weekday_mask: int,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        work_seconds: int,
        pause_seconds: int,
        enabled: bool = True,
    ) -> bool:
        """Set a full schedule on the device."""
        self._state.work_seconds = work_seconds
        self._state.pause_seconds = pause_seconds
        self._state.start_hour = start_hour
        self._state.start_minute = start_minute
        self._state.end_hour = end_hour
        self._state.end_minute = end_minute

        return await self._write_schedule_to_device(weekday_mask=weekday_mask, enabled=enabled)

    async def _write_schedule_to_device(self, weekday_mask: int = 0x7F, enabled: bool = True) -> bool:
        """Write the current schedule state to the device."""
        work = self._state.work_seconds or DEFAULT_WORK_DURATION
        pause = self._state.pause_seconds or DEFAULT_PAUSE_DURATION
        s_h = self._state.start_hour
        s_m = self._state.start_minute
        e_h = self._state.end_hour
        e_m = self._state.end_minute

        # Try BLE
        if self._ble_address:
            cmd = None
            if self.device_type == DeviceType.TUYA_BLE:
                setup = ScheduleSetup(
                    index=0, weekday_mask=weekday_mask, enabled=enabled,
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule([setup])
            elif isinstance(self._protocol, AromaLinkBleProtocol):
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule(weekday_mask, [slot])
            elif isinstance(self._protocol, ScentMarketingGwProtocol):
                # Grade and the other DP-4 fields stay as the device reported.
                mode_tasks = self._gw_mode_tasks().with_task(GwTask(
                    enabled=enabled, weekday_mask=weekday_mask,
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                ))
                cmd = self._protocol.build_mode_tasks(mode_tasks)
            elif isinstance(self._protocol, ScentMarketingAkProtocol):
                slot = ScheduleSlot(
                    start_hour=s_h, start_minute=s_m, end_hour=e_h, end_minute=e_m,
                    enabled=enabled, work_seconds=work, pause_seconds=pause,
                )
                cmd = self._protocol.build_schedule_v2(slot)

            if cmd and await self._ble_execute(cmd):
                self._notify_state_changed()
                return True

        # Cloud fallback
        if self.supports_cloud and self._cloud:
            day_indices = [i + 1 for i in range(7) if weekday_mask & (1 << i)]
            success = await self._cloud.set_schedule(
                self._cloud_device_id,
                work_seconds=work, pause_seconds=pause,
                weekdays=day_indices,
                start_time=f"{s_h:02d}:{s_m:02d}",
                end_time=f"{e_h:02d}:{e_m:02d}",
            )
            if success:
                self._notify_state_changed()
            return success

        return False

    async def refresh_state(self) -> None:
        """Refresh device state. BLE devices push notifications over the
        persistent connection, so this is a no-op for them."""
        if self._ble_address:
            return

        if self.supports_cloud and self._cloud:
            status = await self._cloud.get_status(self._cloud_device_id)
            if status:
                if "power" in status and status["power"] is not None:
                    self._state.power = status["power"]
                if "phase" in status:
                    self._state.phase = status["phase"]
                self._notify_state_changed()

    async def sync_time(self) -> bool:
        """Re-send the time-sync frame on the live connection."""
        if not self._link:
            return False
        time_sync = self._protocol.build_time_sync()
        if not time_sync:
            return True
        return await self._ble_send(time_sync)

    # ------------------------------------------------------------------
    # Startup / Shutdown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Start the BLE link (persistent links connect eagerly)."""
        if self._link:
            await self._link.start()

    async def async_shutdown(self) -> None:
        """Clean up resources."""
        if self._link:
            await self._link.stop()
        if self._cloud and hasattr(self._cloud, "close"):
            await self._cloud.close()
