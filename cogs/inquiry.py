"""General Inquiries / Help button, implemented as a support ticket system.

A second button on the apply message (`ApplyView` in `cogs/interview.py`) opens a
modal where a member types their question. The bot then creates a dedicated ticket
channel under a Tickets category, visible only to that member and the FAQ support
role, seeds it with the question, pings staff, and attaches **Close** and
**Delete** controls. Closing moves the channel to a closed/archive category and
revokes the opener's access, so it becomes a staff-only record; deleting removes
the channel and its history outright. Close is available to the opener or staff;
delete is staff-only (behind a confirm step) and is also exposed as the
`/delete-ticket` command, so staff can remove tickets opened before the Delete
button existed.

Everything is gated behind `TICKET_CATEGORY_ID`; when it's unset the button degrades
gracefully. This module must not import from `cogs.interview` (the apply button
imports from here, and a back-import would create a cycle).
"""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

INQUIRY_TITLE = "Help / General Inquiry"
OPEN_INQUIRY_CUSTOM_ID = "gorp:open_inquiry"
CLOSE_TICKET_CUSTOM_ID = "gorp:close_ticket"
DELETE_TICKET_CUSTOM_ID = "gorp:delete_ticket"

# Owner is stamped into the ticket channel's topic so we can identify the opener
# (for the duplicate guard and the close permission check) without a database.
_OWNER_TOPIC_PREFIX = "GORP help ticket | owner:"
_OWNER_TOPIC_RE = re.compile(r"owner:(\d+)")


def build_inquiry_embed(user: discord.abc.User, message_text: str) -> discord.Embed:
    """Build the embed that opens a ticket. Pure (no Discord calls) so it can be
    unit-tested; mirrors the identity formatting used for application embeds in
    `InterviewCog._build_embed`."""
    embed = discord.Embed(
        title=INQUIRY_TITLE,
        description=message_text,
        color=discord.Color.blurple(),
    )
    embed.set_author(name=str(user))
    embed.add_field(
        name="From",
        value=f"<@{user.id}> (`{user}`, ID `{user.id}`)",
        inline=False,
    )
    return embed


def ticket_channel_name(user: discord.abc.User) -> str:
    """Discord-safe channel name for a user's ticket: 'ticket-<slug>' where slug
    is the lowercased username reduced to a-z/0-9/hyphens, falling back to the
    user id when nothing usable remains. Capped to Discord's 100-char limit."""
    raw = getattr(user, "name", None) or str(user)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not slug:
        slug = str(user.id)
    return f"ticket-{slug}"[:100]


def owner_id_from_topic(topic: str | None) -> int | None:
    """Extract the ticket opener's user id from a channel topic, or None."""
    if not topic:
        return None
    match = _OWNER_TOPIC_RE.search(topic)
    return int(match.group(1)) if match else None


def is_ticket_channel_meta(
    topic: str | None,
    category_id: int | None,
    ticket_category_ids: set[int],
) -> bool:
    """Whether a channel is a help ticket, judged from its metadata alone (pure,
    so it can be unit-tested). True when the channel still carries the owner stamp
    in its topic - open and closed tickets both keep it - or when it sits in one of
    the configured ticket categories (which also catches a ticket whose topic was
    cleared by hand). Used to stop delete from touching ordinary channels."""
    if owner_id_from_topic(topic) is not None:
        return True
    return category_id is not None and category_id in ticket_category_ids


class InquiryModal(discord.ui.Modal, title=INQUIRY_TITLE):
    message = discord.ui.TextInput(
        label="What do you need help with?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Describe your question or what you need help with...",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("InquiryCog")
        if not isinstance(cog, InquiryCog):
            await interaction.response.send_message(
                "Inquiries aren't available right now. Please contact staff directly.",
                ephemeral=True,
            )
            return
        await cog.create_ticket(interaction, (self.message.value or "").strip())


class TicketControlsView(discord.ui.View):
    """Persistent controls on every ticket's opening message: Close archives the
    channel (opener or staff); Delete removes it outright (staff only, behind a
    confirm step). Tickets opened before Delete existed show only Close - use the
    `/delete-ticket` command on those, since the button only lands on new ones."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close ticket",
        emoji="🔒",
        style=discord.ButtonStyle.secondary,
        custom_id=CLOSE_TICKET_CUSTOM_ID,
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InquiryCog")
        if not isinstance(cog, InquiryCog):
            await interaction.response.send_message(
                "Ticket controls aren't available right now.", ephemeral=True
            )
            return
        await cog.close_ticket(interaction)

    @discord.ui.button(
        label="Delete ticket",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id=DELETE_TICKET_CUSTOM_ID,
    )
    async def delete_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InquiryCog")
        if not isinstance(cog, InquiryCog):
            await interaction.response.send_message(
                "Ticket controls aren't available right now.", ephemeral=True
            )
            return
        await cog.request_delete_ticket(interaction)


class ConfirmDeleteView(discord.ui.View):
    """Ephemeral confirm/cancel shown after Delete is clicked, so a stray click
    can't nuke a ticket. Transient (not registered as persistent): it rides on an
    ephemeral message and only needs to outlast the staff member's decision."""

    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.button(
        label="Delete permanently",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InquiryCog")
        if not isinstance(cog, InquiryCog):
            await interaction.response.send_message(
                "Ticket controls aren't available right now.", ephemeral=True
            )
            return
        await cog.delete_ticket(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="Deletion cancelled.", view=None
        )


class InquiryCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        open_category_id: int | None,
        closed_category_id: int | None,
        support_role_id: int | None,
    ) -> None:
        self.bot = bot
        self.open_category_id = open_category_id
        self.closed_category_id = closed_category_id
        self.support_role_id = support_role_id

    # --- open ------------------------------------------------------------

    async def open_inquiry(self, interaction: discord.Interaction) -> None:
        """Button entry point: pop the modal, or explain if tickets aren't set
        up (so nobody types a question that has nowhere to go)."""
        if self.open_category_id is None:
            await interaction.response.send_message(
                "Inquiries aren't set up right now. Please reach out to staff "
                "directly.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(InquiryModal())

    async def create_ticket(
        self, interaction: discord.Interaction, message_text: str
    ) -> None:
        if not message_text:
            await interaction.response.send_message(
                "Your message was empty. Please try again.", ephemeral=True
            )
            return
        if self.open_category_id is None:
            await interaction.response.send_message(
                "Inquiries aren't set up right now. Please reach out to staff "
                "directly.",
                ephemeral=True,
            )
            return

        # Defer ephemerally: creating a channel is several API round-trips and we
        # must respond to the modal submit within 3 seconds.
        await interaction.response.defer(ephemeral=True)

        category = await self._resolve_category(self.open_category_id)
        if category is None:
            await interaction.followup.send(
                "Sorry, ticket support isn't configured correctly. Please contact "
                "staff directly.",
                ephemeral=True,
            )
            return

        guild = category.guild
        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.HTTPException):
                member = None
        if member is None:
            await interaction.followup.send(
                "You need to be a member of the server to open a ticket.",
                ephemeral=True,
            )
            return

        existing = self._find_open_ticket(category, member.id)
        if existing is not None:
            await interaction.followup.send(
                f"You already have an open ticket: {existing.mention}",
                ephemeral=True,
            )
            return

        support_role = (
            guild.get_role(self.support_role_id) if self.support_role_id else None
        )
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if support_role is not None:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        try:
            channel = await guild.create_text_channel(
                name=ticket_channel_name(member),
                category=category,
                overwrites=overwrites,
                topic=f"{_OWNER_TOPIC_PREFIX}{member.id}",
                reason=f"Help ticket opened by {member}",
            )
        except discord.Forbidden:
            logger.warning(
                "Missing permission to create a ticket channel (need Manage "
                "Channels + Manage Roles)."
            )
            await interaction.followup.send(
                "Sorry, I couldn't open a ticket (missing permissions). Please "
                "contact staff directly.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.warning("Failed to create ticket channel: %s", exc)
            await interaction.followup.send(
                "Sorry, something went wrong opening your ticket. Please try "
                "again later.",
                ephemeral=True,
            )
            return

        if support_role is not None:
            content = f"{support_role.mention} {member.mention} opened a ticket."
            allowed = discord.AllowedMentions(
                roles=True, users=True, everyone=False
            )
        else:
            content = f"{member.mention} opened a ticket."
            allowed = discord.AllowedMentions(
                roles=False, users=True, everyone=False
            )
            logger.info(
                "FAQ_SUPPORT_ROLE_ID unset; ticket opened without a staff ping."
            )

        try:
            await channel.send(
                content=content,
                embed=build_inquiry_embed(member, message_text),
                view=TicketControlsView(),
                allowed_mentions=allowed,
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to post ticket opening message: %s", exc)

        await interaction.followup.send(
            f"Your ticket has been created: {channel.mention}", ephemeral=True
        )

    # --- close -----------------------------------------------------------

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This button only works inside a ticket channel.", ephemeral=True
            )
            return

        owner_id = owner_id_from_topic(channel.topic)
        member = interaction.user
        if not self._may_close(member, owner_id):
            await interaction.response.send_message(
                "Only staff or the ticket owner can close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = channel.guild

        edit_kwargs: dict = {}
        closed_category = await self._resolve_category(self.closed_category_id)
        if self.closed_category_id and closed_category is None:
            logger.warning(
                "Closed-ticket category %s unavailable; leaving channel in place.",
                self.closed_category_id,
            )
        if closed_category is not None:
            edit_kwargs["category"] = closed_category
        if not channel.name.startswith("closed-"):
            edit_kwargs["name"] = f"closed-{channel.name}"[:100]

        try:
            # Revoke the opener's access so a closed ticket is a staff-only record.
            if owner_id is not None:
                owner = guild.get_member(owner_id)
                if owner is not None:
                    await channel.set_permissions(
                        owner, overwrite=None, reason="Ticket closed"
                    )
            if edit_kwargs:
                await channel.edit(
                    reason=f"Ticket closed by {member}", **edit_kwargs
                )
        except discord.Forbidden:
            logger.warning("Missing permission to close ticket #%s.", channel.name)
            await interaction.followup.send(
                "I couldn't close this ticket (I need Manage Channels + Manage "
                "Roles).",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.warning("Failed to close ticket #%s: %s", channel.name, exc)
            await interaction.followup.send(
                "Something went wrong closing this ticket.", ephemeral=True
            )
            return

        try:
            await channel.send(f"🔒 Ticket closed by {member.mention}.")
        except discord.HTTPException:
            pass
        await interaction.followup.send("Ticket closed.", ephemeral=True)

    # --- delete ----------------------------------------------------------

    @app_commands.command(
        name="delete-ticket",
        description="Permanently delete this ticket channel and its history (staff only).",
    )
    @app_commands.default_permissions(manage_channels=True)
    async def delete_ticket_command(self, interaction: discord.Interaction) -> None:
        """Slash-command entry point. Unlike the Delete button it works on any
        ticket - including ones opened before that button existed - because it
        doesn't depend on a component being present on the channel's message."""
        await self.delete_ticket(interaction)

    async def request_delete_ticket(self, interaction: discord.Interaction) -> None:
        """Delete-button entry point: gate on staff + ticket channel, then pop an
        ephemeral confirmation instead of deleting on the first click."""
        if not self._is_ticket_channel(interaction.channel):
            await interaction.response.send_message(
                "This button only works inside a ticket channel.", ephemeral=True
            )
            return
        if not self._may_delete(interaction.user):
            await interaction.response.send_message(
                "Only staff can delete a ticket. Use **Close ticket** instead.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "**Permanently delete this ticket?** This removes the channel and its "
            "entire history and can't be undone.",
            view=ConfirmDeleteView(),
            ephemeral=True,
        )

    async def delete_ticket(self, interaction: discord.Interaction) -> None:
        """Actually delete the ticket channel. Shared by the slash command and the
        confirmation button, so it re-checks both gates itself. Acknowledges first,
        because the channel - and any message posted in it - vanishes with the
        delete."""
        channel = interaction.channel
        if not self._is_ticket_channel(channel):
            await interaction.response.send_message(
                "This only works inside a ticket channel.", ephemeral=True
            )
            return
        if not self._may_delete(interaction.user):
            await interaction.response.send_message(
                "Only staff can delete a ticket.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🗑️ Deleting this ticket…", ephemeral=True
        )
        try:
            await channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.Forbidden:
            logger.warning("Missing permission to delete ticket #%s.", channel.name)
            await interaction.followup.send(
                "I couldn't delete this ticket (I need Manage Channels).",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.warning("Failed to delete ticket #%s: %s", channel.name, exc)
            await interaction.followup.send(
                "Something went wrong deleting this ticket.", ephemeral=True
            )
            return
        logger.info(
            "Ticket #%s deleted by %s (%d).",
            channel.name,
            interaction.user,
            interaction.user.id,
        )

    # --- helpers ---------------------------------------------------------

    async def _resolve_category(
        self, category_id: int | None
    ) -> discord.CategoryChannel | None:
        if not category_id:
            return None
        channel = self.bot.get_channel(category_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(category_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.warning(
                    "Ticket category %s unavailable (%s).", category_id, exc
                )
                return None
        if not isinstance(channel, discord.CategoryChannel):
            logger.warning(
                "Configured ticket category %s is not a category.", category_id
            )
            return None
        return channel

    def _find_open_ticket(
        self, category: discord.CategoryChannel, owner_id: int
    ) -> discord.TextChannel | None:
        for channel in category.text_channels:
            if owner_id_from_topic(channel.topic) == owner_id:
                return channel
        return None

    def _may_close(self, member: discord.abc.User, owner_id: int | None) -> bool:
        if owner_id is not None and member.id == owner_id:
            return True
        if isinstance(member, discord.Member):
            perms = member.guild_permissions
            if perms.manage_guild or perms.manage_channels:
                return True
            if self.support_role_id and any(
                role.id == self.support_role_id for role in member.roles
            ):
                return True
        return False

    def _may_delete(self, member: discord.abc.User) -> bool:
        """Staff-only. Deleting destroys the channel and the staff-only record a
        close leaves behind, so - unlike `_may_close` - the ticket owner is not
        allowed; this is just the staff branch of that check."""
        if isinstance(member, discord.Member):
            perms = member.guild_permissions
            if perms.manage_guild or perms.manage_channels:
                return True
            if self.support_role_id and any(
                role.id == self.support_role_id for role in member.roles
            ):
                return True
        return False

    def _is_ticket_channel(self, channel: object) -> bool:
        """Guard so delete only ever touches real tickets, never an arbitrary
        channel someone happens to run `/delete-ticket` in."""
        if not isinstance(channel, discord.TextChannel):
            return False
        ticket_category_ids = {
            cid
            for cid in (self.open_category_id, self.closed_category_id)
            if cid
        }
        return is_ticket_channel_meta(
            channel.topic, channel.category_id, ticket_category_ids
        )
