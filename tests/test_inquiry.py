"""Unit tests for the pure inquiry-embed builder. The Discord interaction
paths (modal, channel posting, pings) are verified manually in a dev guild."""

from __future__ import annotations

from cogs.inquiry import (
    build_inquiry_embed,
    is_ticket_channel_meta,
    owner_id_from_topic,
    ticket_channel_name,
)


class _FakeUser:
    def __init__(self, user_id: int, username: str) -> None:
        self.id = user_id
        self.name = username

    def __str__(self) -> str:
        return self.name


def test_build_inquiry_embed_uses_message_as_description():
    user = _FakeUser(123456789, "someuser")
    embed = build_inquiry_embed(user, "How do I switch departments?")
    assert embed.title == "Help / General Inquiry"
    assert embed.description == "How do I switch departments?"


def test_build_inquiry_embed_includes_user_identity():
    user = _FakeUser(123456789, "someuser")
    embed = build_inquiry_embed(user, "hi there")
    assert embed.author.name == "someuser"
    from_field = next(f for f in embed.fields if f.name == "From")
    assert "123456789" in from_field.value
    assert "someuser" in from_field.value


def test_ticket_channel_name_slugifies_username():
    assert ticket_channel_name(_FakeUser(42, "SomeUser")) == "ticket-someuser"


def test_ticket_channel_name_replaces_symbols_and_spaces():
    assert ticket_channel_name(_FakeUser(42, "Cool Guy! 99")) == "ticket-cool-guy-99"


def test_ticket_channel_name_falls_back_to_id_when_no_usable_chars():
    assert ticket_channel_name(_FakeUser(42, "!!!")) == "ticket-42"


def test_ticket_channel_name_capped_at_discord_limit():
    name = ticket_channel_name(_FakeUser(42, "x" * 200))
    assert len(name) <= 100
    assert name.startswith("ticket-")


def test_owner_id_from_topic_extracts_id():
    assert owner_id_from_topic("GORP help ticket | owner:123456789") == 123456789


def test_owner_id_from_topic_none_when_absent():
    assert owner_id_from_topic("some unrelated topic") is None
    assert owner_id_from_topic(None) is None
    assert owner_id_from_topic("") is None


def test_is_ticket_channel_meta_true_for_owner_topic():
    # Identified by the owner stamp even with no category configured (e.g. an
    # orphaned ticket after the categories were unset).
    assert is_ticket_channel_meta("GORP help ticket | owner:42", None, set()) is True


def test_is_ticket_channel_meta_true_for_ticket_category():
    # No owner stamp, but the channel sits in a configured ticket category.
    assert is_ticket_channel_meta(None, 999, {999, 1000}) is True


def test_is_ticket_channel_meta_false_for_plain_channel():
    assert is_ticket_channel_meta("just a normal channel", 555, {999, 1000}) is False
    assert is_ticket_channel_meta(None, None, {999}) is False
    assert is_ticket_channel_meta(None, 555, set()) is False
