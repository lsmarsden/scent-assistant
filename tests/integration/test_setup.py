"""Entry setup, entities, services and diagnostics, end to end over the
fake BLE link: HA service call -> entity -> device manager -> GATT write."""
from __future__ import annotations

import json

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scent_assistant.const import (
    AROMAWAVE_CHAR_UUID,
    DOMAIN,
    SM_GW_WRITE_UUID,
)
from custom_components.scent_assistant.diagnostics import async_get_config_entry_diagnostics
from tests.captures import aroma_wave_order, gw_dps, gw_messages, load_capture
from tests.fakes import GW_ADDRESS, FakeLink

# Unique-id suffixes of the live entities each production entry has today.
# Automations and dashboards reach these through entity IDs that HA keys on
# the unique ID, so any change here renames or orphans an entity.
# (Production also holds two orphaned GW numbers, `work_duration` and
# `pause_duration`, left over from before the GW-specific DP-15 entities.)
GW_ENTITIES = {
    ("switch", "power"), ("switch", "lock"), ("switch", "lamp"),
    ("sensor", "status"), ("sensor", "connection"), ("sensor", "battery"),
    ("sensor", "oil"), ("sensor", "sm_detection"),
    ("number", "gw_work_duration"), ("number", "gw_pause_duration"), ("number", "grade"),
    ("time", "start_time"), ("time", "end_time"),
    ("button", "time_sync"),
}
AW_ENTITIES = {
    ("switch", "power"), ("switch", "aromawave_mode_1_enabled"),
    ("sensor", "status"), ("sensor", "connection"),
    ("number", "aromawave_mode_1_work"), ("number", "aromawave_mode_1_pause"),
    ("time", "start_time"), ("time", "end_time"),
    ("time", "aromawave_mode_1_start"), ("time", "aromawave_mode_1_end"),
    ("button", "time_sync"),
}


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entities(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def suffixes(hass: HomeAssistant, entry: MockConfigEntry) -> set[tuple[str, str]]:
    prefix = f"{entry.data['ble_address']}_"
    return {(e.domain, e.unique_id.removeprefix(prefix)) for e in entities(hass, entry)}


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, suffix: str) -> str:
    unique_id = f"{entry.data['ble_address']}_{suffix}"
    return next(e.entity_id for e in entities(hass, entry) if e.unique_id == unique_id)


async def push(hass: HomeAssistant, link: FakeLink, notifications: list[bytes]) -> None:
    for data in notifications:
        link.client.notify(data)
    await hass.async_block_till_done()


def gw_sent(link: FakeLink) -> list[bytes]:
    return [m.frame for m in gw_messages(link.client.written(SM_GW_WRITE_UUID))]


@pytest.mark.parametrize(("entry_fixture", "expected"), [
    ("gw_entry", GW_ENTITIES),
    ("aw_entry", AW_ENTITIES),
])
async def test_entities_match_production(
    hass: HomeAssistant, link: FakeLink, request, entry_fixture: str, expected
) -> None:
    entry = request.getfixturevalue(entry_fixture)
    await setup(hass, entry)
    assert suffixes(hass, entry) == expected


async def test_connection_sensor_disabled_by_default(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    connection = er.async_get(hass).async_get(entity_id(hass, gw_entry, "connection"))
    assert connection.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_setup_connects_with_password(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    assert [set(gw_dps(f)) for f in gw_sent(link)] == [{10}, {13}]


async def test_gw_state_push_reaches_entities(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    grade = entity_id(hass, gw_entry, "grade")
    assert hass.states.get(grade).state == STATE_UNAVAILABLE  # nothing reported yet

    await push(hass, link, load_capture("gw", "grade_3").notifications)

    assert hass.states.get(grade).state == "3"
    assert hass.states.get(entity_id(hass, gw_entry, "power")).state == STATE_ON
    assert hass.states.get(entity_id(hass, gw_entry, "oil")).state == "17"


async def test_both_devices_load_and_unload(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry, aw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    await setup(hass, aw_entry)
    gw_client = link.clients[0]

    assert await hass.config_entries.async_unload(gw_entry.entry_id)
    await hass.async_block_till_done()

    assert gw_entry.state is ConfigEntryState.NOT_LOADED
    assert aw_entry.state is ConfigEntryState.LOADED
    assert gw_client.is_connected is False
    assert link.client.is_connected is True


@pytest.mark.parametrize(("service", "frame"), [
    ("turn_on", "ff0100010101"),
    ("turn_off", "ff0100010100"),
])
async def test_power_switch_writes_to_device(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry, service: str, frame: str
) -> None:
    await setup(hass, gw_entry)
    await hass.services.async_call(
        "switch", service, {"entity_id": entity_id(hass, gw_entry, "power")}, blocking=True
    )
    assert gw_sent(link)[-1].hex() == frame


async def test_grade_number_writes_to_device(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    await push(hass, link, load_capture("gw", "grade_1_then_off").notifications[:2])

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": entity_id(hass, gw_entry, "grade"), "value": 3}, blocking=True,
    )

    assert gw_dps(gw_sent(link)[-1])[4][-5] == 3
    assert hass.states.get(entity_id(hass, gw_entry, "grade")).state == "3"


async def test_mode_1_switch_writes_to_device(
    hass: HomeAssistant, link: FakeLink, aw_entry: MockConfigEntry
) -> None:
    await setup(hass, aw_entry)
    await push(hass, link, load_capture("aroma_wave", "on_off_mode_1").notifications[:6])
    mode_1 = entity_id(hass, aw_entry, "aromawave_mode_1_enabled")

    await hass.services.async_call("switch", "turn_on", {"entity_id": mode_1}, blocking=True)

    weekday, schedule = (aroma_wave_order(w)[1]
                         for w in link.client.written(AROMAWAVE_CHAR_UUID)[-2:])
    assert weekday.startswith("FEEF0014")
    assert schedule == "FEEF0018000108001700000A0078050A0000000000FFEFFF"
    assert hass.states.get(mode_1).state == STATE_ON


async def test_set_schedule_service(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    await hass.services.async_call(
        DOMAIN, "set_schedule",
        {"days": ["mon", "wed"], "start_time": "08:30", "end_time": "22:15"},
        blocking=True,
    )
    # DP-4 task: enabled + Mon|Wed, 08:30-22:15.
    task = gw_dps(gw_sent(link)[-1])[4][7:12]
    assert task.hex() == "85081e160f"


async def test_diagnostics_redact_password(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    diag = json.dumps(await async_get_config_entry_diagnostics(hass, gw_entry))
    assert "8888" not in diag


@pytest.mark.xfail(strict=True, reason="sm_metadata.raw_hex embeds the MAC from the "
                                       "advertisement and is not redacted")
async def test_diagnostics_redact_mac(
    hass: HomeAssistant, link: FakeLink, gw_entry: MockConfigEntry
) -> None:
    await setup(hass, gw_entry)
    diag = json.dumps(await async_get_config_entry_diagnostics(hass, gw_entry))
    assert GW_ADDRESS.replace(":", "") not in diag.upper().replace(":", "")
