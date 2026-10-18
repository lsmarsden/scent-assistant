"""Device-type detection from advertisements and GATT layouts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.scent_assistant.const import (
    AROMAWAVE_MFR_ID,
    AROMAWAVE_SERVICE_UUID,
    SM_GW_SERVICE_UUID,
    SM_MFR_ID_GW,
    DeviceType,
)
from custom_components.scent_assistant.protocol_ble import (
    AromaWaveProtocol,
    ScentMarketingGwProtocol,
    detect_device_type,
    extract_scent_marketing_metadata,
    get_protocol,
    protocol_from_services,
)

# A326's advertisement, with a placeholder MAC (bytes 11-16).
GW_ADV_PAYLOAD = bytes.fromhex("A1000D000100010200000A" + "AABBCCDDEEFF" + "00EF")


def adv(manufacturer_data: dict[int, bytes]) -> SimpleNamespace:
    return SimpleNamespace(manufacturer_data=manufacturer_data)


def services(*uuids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(uuid=u, characteristics=[]) for u in uuids]


def test_a326_advertisement_is_plain_gw() -> None:
    assert detect_device_type("A326", adv({SM_MFR_ID_GW: GW_ADV_PAYLOAD})) == (
        DeviceType.SCENT_MARKETING_GW
    )


@pytest.mark.parametrize("lead", ["01", "02", "03", "B1", "B2"])
def test_wifi_and_cellular_gw_are_xor(lead: str) -> None:
    payload = bytes.fromhex(lead) + GW_ADV_PAYLOAD[1:]
    assert detect_device_type("", adv({SM_MFR_ID_GW: payload})) == (
        DeviceType.SCENT_MARKETING_GW_XOR
    )


def test_gw_metadata() -> None:
    meta = extract_scent_marketing_metadata(adv({SM_MFR_ID_GW: GW_ADV_PAYLOAD}))
    assert meta == {
        "mfr_id": SM_MFR_ID_GW,
        "raw_hex": GW_ADV_PAYLOAD.hex().upper(),
        "pid": 13,
        "wifi_flag": "ble",
        "heartbeat": False,
        "mac_from_adv": "AABBCCDDEEFF",
    }


def test_aroma_wave_by_manufacturer_id() -> None:
    # dev505's name says nothing; only the manufacturer ID identifies it.
    assert detect_device_type("dev505", adv({AROMAWAVE_MFR_ID: b"\x00"})) == DeviceType.AROMA_WAVE
    assert detect_device_type("dev505") is None


@pytest.mark.parametrize(("uuids", "expected"), [
    ((SM_GW_SERVICE_UUID, "00001800-0000-1000-8000-00805f9b34fb"), DeviceType.SCENT_MARKETING_GW),
    ((AROMAWAVE_SERVICE_UUID,), DeviceType.AROMA_WAVE),
    (("00001800-0000-1000-8000-00805f9b34fb",), None),
])
def test_protocol_from_gatt_services(uuids: tuple[str, ...], expected) -> None:
    assert protocol_from_services(services(*uuids)) == expected


def test_factory() -> None:
    assert isinstance(get_protocol(DeviceType.AROMA_WAVE), AromaWaveProtocol)
    gw = get_protocol(DeviceType.SCENT_MARKETING_GW, pid=13)
    assert type(gw) is ScentMarketingGwProtocol
    assert gw._tuya_dp_mode is False
    assert get_protocol(DeviceType.SCENT_MARKETING_GW, pid=98)._tuya_dp_mode is True
