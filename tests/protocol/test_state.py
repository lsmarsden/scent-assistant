"""DiffuserState.apply: folding parsed notifications into the device state.

One class per rule (the class docstring); each test is an example of it.
"""
from __future__ import annotations

import dataclasses

import pytest

from custom_components.scent_assistant.protocol_ble import (
    AromaWaveMode,
    AromaWaveProtocol,
    DiffuserState,
    ScentMarketingGwProtocol,
)
from tests.captures import capture_names, load_capture

FIELDS = {f.name for f in dataclasses.fields(DiffuserState)}


class TestUpdatesTouchOnlyTheirFields:
    """A notification updates the fields it carries and leaves the rest."""

    def test_power_update_keeps_grade(self) -> None:
        state = DiffuserState(grade=3)
        assert state.apply({"power": True, "phase": "idle"}) is True
        assert (state.power, state.phase, state.grade) == (True, "idle", 3)

    def test_empty_update_changes_nothing(self) -> None:
        state = DiffuserState(power=True)
        assert state.apply({}) is False
        assert state == DiffuserState(power=True)

    def test_unknown_keys_are_ignored(self) -> None:
        state = DiffuserState()
        assert state.apply({"not_a_field": 1}) is False
        assert state == DiffuserState()


class TestParsersOnlyReportStateFields:
    """Every key a parser reports for our devices is a DiffuserState field,
    so nothing they decode is dropped."""

    @pytest.mark.parametrize(("family", "make"), [
        ("gw", ScentMarketingGwProtocol),
        ("aroma_wave", AromaWaveProtocol),
    ])
    def test_captures(self, family: str, make) -> None:
        seen: set[str] = set()
        for name in capture_names(family):
            proto = make()
            for data in load_capture(family, name).notifications:
                seen |= proto.parse_notification(data).keys()
        assert seen
        assert seen - {"aromawave_mode_updates"} <= FIELDS


class TestScheduleTimes:
    """A schedule time without minutes resets them: start to :00, end to :59."""

    def test_start_hour_alone_resets_minute(self) -> None:
        state = DiffuserState(start_hour=8, start_minute=30)
        state.apply({"start_hour": 9})
        assert (state.start_hour, state.start_minute) == (9, 0)

    def test_end_hour_alone_resets_minute(self) -> None:
        state = DiffuserState(end_hour=22, end_minute=15)
        state.apply({"end_hour": 21})
        assert (state.end_hour, state.end_minute) == (21, 59)

    def test_minutes_given_are_kept(self) -> None:
        state = DiffuserState()
        state.apply({"start_hour": 9, "start_minute": 45, "end_hour": 21, "end_minute": 10})
        assert (state.start_hour, state.start_minute, state.end_hour, state.end_minute) == (
            9, 45, 21, 10)


class TestAromaWaveModes:
    """Mode updates merge into that mode only."""

    def test_merge(self) -> None:
        state = DiffuserState(aromawave_modes={1: AromaWaveMode(enabled=True, start_hour=8)})
        assert state.apply({"aromawave_mode_updates": {
            1: {"start_hour": 9}, 2: {"work_seconds": 10},
        }}) is True
        assert state.aromawave_modes == {
            1: AromaWaveMode(enabled=True, start_hour=9),
            2: AromaWaveMode(work_seconds=10),
        }
