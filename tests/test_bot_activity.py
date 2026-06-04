"""Unit tests for building the bot's presence activity from config strings."""

from __future__ import annotations

from dataclasses import dataclass

import discord

from bot import _activity_from_config


@dataclass
class _ActivityConfig:
    bot_activity_type: str
    bot_activity_text: str


def test_activity_maps_known_type():
    activity = _activity_from_config(
        _ActivityConfig("listening", "to /apply")
    )
    assert activity.type is discord.ActivityType.listening
    assert activity.name == "to /apply"


def test_activity_defaults_to_watching_for_unknown_type():
    activity = _activity_from_config(
        _ActivityConfig("bogus", "for new applicants")
    )
    assert activity.type is discord.ActivityType.watching
    assert activity.name == "for new applicants"
