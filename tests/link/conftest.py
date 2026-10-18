"""BleLink instances wired to the fake Bluetooth stack."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from custom_components.scent_assistant.const import DeviceType, LinkPolicy
from custom_components.scent_assistant.link import BleLink
from custom_components.scent_assistant.protocol_ble import get_protocol
from tests.fakes import GW_ADDRESS, FakeBluetooth, settle


@dataclass
class Observed:
    """What the link reported back to its owner."""

    frames: list[bytes] = field(default_factory=list)
    changes: int = 0


@pytest.fixture
async def make_link(bt: FakeBluetooth):
    links: list[BleLink] = []

    async def make(device_type: DeviceType, policy: LinkPolicy | None = None,
                   password: str | None = None, start: bool = True
                   ) -> tuple[BleLink, Observed]:
        observed = Observed()
        protocol = get_protocol(device_type)

        def on_change() -> None:
            observed.changes += 1

        link = BleLink(
            object(), GW_ADDRESS, "diffuser", protocol,
            policy=policy or protocol.default_link_policy,
            password=password, on_frame=observed.frames.append, on_change=on_change,
        )
        links.append(link)
        if start:
            await link.start()
            await settle()
        return link, observed

    yield make
    for link in links:
        await link.stop()
    await settle()
