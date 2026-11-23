from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.inquiry import InquiryCog
from content import (
    REAPPLY_EMBARGO_DAYS,
    build_acceptance_dm,
    build_rejection_dm,
)
from db import (
    accepted_users_decided_before,
    clear_application_decision,
    get_application,
    latest_accepted_answers,
    latest_rejection_for_user,
    save_application,
    set_application_decision,
    set_results_message_id,
    record_activity,
)
from questions import QUESTIONS, Question
from session import InterviewSession, choice_prompt

logger = logging.getLogger(__name__)

IDLE_TIMEOUT = timedelta(minutes=1)
TIMEOUT_CHECK_SECONDS = 15

# How often the recruitment-server graduation scan runs. Frequent enough to
# action the day-based timeout promptly, infrequent enough to stay cheap.
GRADUATION_SCAN_HOURS = 6

SECONDS_PER_DAY = 24 * 60 * 60
# Discord caps invite max_age at 7 days; acceptance invites expire after the
# graduation window (clamped to this cap) so a stale invite can't be used once
# the applicant's join window has closed.
MAX_INVITE_AGE_SECONDS = 7 * SECONDS_PER_DAY

START_BUTTON_CUSTOM_ID = "gorp:start_interview"
START_KEYWORDS = {"apply", "start", "begin", "interview"}

WELCOME_DM_TEXT = (
    "**Welcome to Greater Ontario Gaming!**\n"
    "Thanks for joining the community. To become an active member, please "
    "complete our recruitment interview - it lives entirely here in DMs and "
    "takes about 10 minutes.\n\n"
    "Click **Start Interview** below when you're ready. You can also type "
    "`apply` at any time to begin, or `cancel` / `restart` mid-interview."
)

INTERVIEW_INTRO_TEXT = (
    "**Welcome to the GORP Interview.**\n"
    "I'll ask you a series of questions, one at a time. Answer in plain text.\n"
    "• Type `restart` at any point to start over.\n"
    "• Type `cancel` at any point to stop without submitting.\n"
    "• If you have questions for staff *before* starting, ask in the server "
    "first - once we begin, I'll only accept answers to the questions.\n\n"
    "Here we go!"
)

# Discord caps nicknames at 32 characters.
NICK_MAX_LEN = 32


def clean_nickname(community_name: str | None) -> str | None:
    """Normalize a `community_name` answer into a Discord nickname, or None when
    there's nothing usable. Callers MUST skip the rename on None - passing
    ``nick=None`` to ``member.edit`` clears the member's existing nickname
    instead of leaving it be."""
    if not community_name:
        return None
    cleaned = community_name.strip()
    if not cleaned:
        return None
    return cleaned[:NICK_MAX_LEN]


def main_server_role_ids(
    departments: Any,
    access_role_id: int | None,
    department_role_ids: Mapping[str, int],
) -> list[int]:
    """Role IDs to grant an accepted applicant in the main server: the access
    role (when configured) plus the role of every department they picked that
    has one configured. Tolerates a bare-string or missing `department` answer
    and drops duplicates while keeping order."""
    if isinstance(departments, str):
        departments = [departments]
    elif not isinstance(departments, (list, tuple)):
        departments = []
    role_ids: list[int] = []
    if access_role_id is not None:
        role_ids.append(access_role_id)
    for department in departments:
        role_id = department_role_ids.get(department)
        if role_id is not None and role_id not in role_ids:
            role_ids.append(role_id)
    return role_ids


class ApplyView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Interview",
        style=discord.ButtonStyle.success,
        custom_id=START_BUTTON_CUSTOM_ID,
    )
    async def start_interview_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now. Please try again later.",
                ephemeral=True,
            )
            return
        await cog.start_from_interaction(interaction)

    @discord.ui.button(
        label="Help / Inquiry",
        style=discord.ButtonStyle.secondary,
        custom_id="gorp:open_inquiry",
    )
    async def help_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InquiryCog")
        if not isinstance(cog, InquiryCog):
            await interaction.response.send_message(
                "Inquiries aren't available right now. Please contact staff directly.",
                ephemeral=True,
            )
            return
        await cog.open_inquiry(interaction)


def build_apply_embed() -> discord.Embed:
    return discord.Embed(
        title="Greater Ontario Gaming - Recruitment",
        description=(
            "Interested in joining GORP? Click **Start Interview** below and "
            "I'll DM you a short questionnaire. The whole thing takes about "
            "10 minutes and is done privately in your DMs.\n\n"
            "If your DMs are closed, you'll need to enable "
            "**Allow direct messages from server members** in this server's "
            "privacy settings first."
        ),
        color=discord.Color.blurple(),
    )


# --- Decision buttons (Accept / Reject on staff-channel application embeds) ---
#
# These use DynamicItem so a single class handles every application_id baked
# into the custom_id. On startup the bot registers the two classes via
# `add_dynamic_items`, which lets the buttons survive bot restarts without
# tracking each message individually.

ACCEPT_CUSTOM_ID_TEMPLATE = r"gorp:accept_app:(?P<app_id>\d+)"
REJECT_CUSTOM_ID_TEMPLATE = r"gorp:reject_app:(?P<app_id>\d+)"
REVERSE_CUSTOM_ID_TEMPLATE = r"gorp:reverse_app:(?P<app_id>\d+)"


class AcceptDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=ACCEPT_CUSTOM_ID_TEMPLATE,
):
    def __init__(self, app_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Accept",
                style=discord.ButtonStyle.success,
                custom_id=f"gorp:accept_app:{app_id}",
            )
        )
        self.app_id = app_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "AcceptDecisionButton":
        return cls(int(match["app_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now.", ephemeral=True
            )
            return
        await cog.handle_accept(interaction, self.app_id)


class RejectDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=REJECT_CUSTOM_ID_TEMPLATE,
):
    def __init__(self, app_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Reject",
                style=discord.ButtonStyle.danger,
                custom_id=f"gorp:reject_app:{app_id}",
            )
        )
        self.app_id = app_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "RejectDecisionButton":
        return cls(int(match["app_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now.", ephemeral=True
            )
            return
        await cog.handle_reject_click(interaction, self.app_id)


def build_decision_view(app_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AcceptDecisionButton(app_id))
    view.add_item(RejectDecisionButton(app_id))
    return view


class ReverseDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=REVERSE_CUSTOM_ID_TEMPLATE,
):
    """Persistent 'Reverse decision' button attached to a *decided* staff embed.
    Lets staff undo a mis-clicked accept/reject and reopen the application for a
    fresh decision. Like the accept/reject buttons it's a DynamicItem so one
    class serves every app_id and survives restarts once registered in bot.py."""

    def __init__(self, app_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Reverse decision",
                style=discord.ButtonStyle.secondary,
                custom_id=f"gorp:reverse_app:{app_id}",
            )
        )
        self.app_id = app_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "ReverseDecisionButton":
        return cls(int(match["app_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now.", ephemeral=True
            )
            return
        await cog.request_reverse(
            interaction, self.app_id, source_message=interaction.message
        )


def build_reverse_view(app_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReverseDecisionButton(app_id))
    return view


class ConfirmReverseView(discord.ui.View):
    """Ephemeral confirm/cancel shown after a reverse is requested, so a stray
    click can't wipe a real decision (the recorded decider/reason). Transient
    (not registered as persistent): it rides on an ephemeral message and only
    needs to outlast the staff member's decision. `source_message` is the staff
    embed for the button entry point, or None for the slash command (which
    relocates the message from the stored id inside handle_reverse)."""

    def __init__(
        self, app_id: int, source_message: discord.Message | None
    ) -> None:
        super().__init__(timeout=60)
        self.app_id = app_id
        self.source_message = source_message

    @discord.ui.button(
        label="Reverse decision",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now.", ephemeral=True
            )
            return
        await cog.handle_reverse(
            interaction, self.app_id, source_message=self.source_message
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="Reversal cancelled.", view=None
        )


class RejectReasonModal(discord.ui.Modal, title="Reject Application"):
    reason = discord.ui.TextInput(
        label="Reason (optional, shown to applicant)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, app_id: int, source_message: discord.Message | None) -> None:
        super().__init__()
        self.app_id = app_id
        # interaction.message is None on modal-submit interactions, so we stash
        # the originating staff-channel message here so the cog can edit it
        # after the reject is recorded.
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("InterviewCog")
        if not isinstance(cog, InterviewCog):
            await interaction.response.send_message(
                "The interview system isn't available right now.", ephemeral=True
            )
            return
        await cog.handle_reject_submit(
            interaction,
            self.app_id,
            (self.reason.value or "").strip() or None,
            source_message=self.source_message,
        )


class InterviewCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        results_channel_id: int,
        recruiter_role_id: int,
        db_path: Path,
        main_server_invite_channel_id: int | None = None,
        opp_server_invite_channel_id: int | None = None,
        main_server_access_role_id: int | None = None,
        main_server_department_role_ids: Mapping[str, int] | None = None,
        graduation_enabled: bool = True,
        graduation_days: int = 5,
    ) -> None:
        self.bot = bot
        self.results_channel_id = results_channel_id
        self.recruiter_role_id = recruiter_role_id
        self.db_path = db_path
        self.main_server_invite_channel_id = main_server_invite_channel_id
        self.opp_server_invite_channel_id = opp_server_invite_channel_id
        self.main_server_access_role_id = main_server_access_role_id
        self.main_server_department_role_ids = dict(
            main_server_department_role_ids or {}
        )
        self.graduation_enabled = graduation_enabled
        self.graduation_days = graduation_days
        self.sessions: dict[int, InterviewSession] = {}
        # Guild ids derived (once) from the configured channels: the recruitment
        # server hosts the interview/welcome DM; the community servers host the
        # rename-on-join. Cached only on a successful resolve so a transient miss
        # doesn't permanently disable the feature.
        self._recruitment_guild_id_cache: int | None = None
        self._community_guild_ids_cache: set[int] | None = None
        self._main_guild_id_cache: int | None = None
        self.expire_idle_sessions.start()
        if self.graduation_enabled:
            self.graduate_accepted_members.start()

    def cog_unload(self) -> None:
        self.expire_idle_sessions.cancel()
        if self.graduate_accepted_members.is_running():
            self.graduate_accepted_members.cancel()

    @tasks.loop(seconds=TIMEOUT_CHECK_SECONDS)
    async def expire_idle_sessions(self) -> None:
        now = datetime.now(timezone.utc)
        expired_ids = [
            uid
            for uid, sess in self.sessions.items()
            if now - sess.last_activity > IDLE_TIMEOUT
        ]
        for uid in expired_ids:
            self.sessions.pop(uid, None)
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(
                    "Your interview has timed out after 1 minute of inactivity. "
                    "Type `apply` to begin again."
                )
            except (discord.HTTPException, discord.NotFound):
                pass

    @expire_idle_sessions.before_loop
    async def _before_expire(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=GRADUATION_SCAN_HOURS)
    async def graduate_accepted_members(self) -> None:
        """Time-based graduation trigger: remove accepted members from the
        recruitment server once `graduation_days` have passed since their
        acceptance and they're still lingering here. The join-based trigger in
        `on_member_join` usually fires first; this is the backstop for those who
        never join a community server. Idempotent - members already gone are
        skipped."""
        guild = await self._recruitment_guild()
        if guild is None:
            logger.warning(
                "Recruitment guild not resolved; skipping graduation scan."
            )
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.graduation_days)
        for user_id in accepted_users_decided_before(self.db_path, cutoff):
            try:
                uid = int(user_id)
            except ValueError:
                continue
            member = guild.get_member(uid)
            if member is not None:
                await self._kick_from_recruitment(member)

    @graduate_accepted_members.before_loop
    async def _before_graduate(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        recruitment_guild_id = await self._recruitment_guild_id()
        if recruitment_guild_id is not None and member.guild.id == recruitment_guild_id:
            await self._send_welcome_dm(member)
            return

        community_guild_ids = await self._community_guild_ids()
        if member.guild.id in community_guild_ids:
            await self._welcome_accepted_member(member)

    async def _send_welcome_dm(self, member: discord.Member) -> None:
        """DM a new recruitment-server member the interview prompt. Only used for
        joins to the recruitment server - community-server joins are handled by
        `_welcome_accepted_member` instead."""
        try:
            await member.send(WELCOME_DM_TEXT, view=ApplyView())
        except discord.Forbidden:
            logger.warning(
                "Could not DM new member %s (%d): DMs closed or bot blocked.",
                member,
                member.id,
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to DM new member %s: %s", member, exc)

    async def _welcome_accepted_member(self, member: discord.Member) -> None:
        """Handle an accepted applicant joining a community server (main/OPP):
        set their nickname to their application `community_name`, grant their
        access + department roles if this is the main server, then - the
        join-based graduation trigger - remove them from the recruitment server.
        A non-accepted joiner has no accepted application, so all are no-ops."""
        answers = latest_accepted_answers(self.db_path, member.id)
        if answers is None:
            return
        community_name = answers.get("community_name")
        await self._apply_community_nickname(
            member, community_name if isinstance(community_name, str) else None
        )
        if member.guild.id == await self._main_guild_id():
            if member.pending:
                # Granting a role to a member still on the Membership Screening
                # (rules) gate lets them skip it, so hold off: on_member_update
                # grants the roles once they accept the rules.
                logger.info(
                    "%s joined the main server pending screening; roles deferred.",
                    member,
                )
            else:
                await self._grant_main_server_roles(member, answers)
        if self.graduation_enabled:
            await self._graduate_from_recruitment(member.id)

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """Finish the main-server role grant `_welcome_accepted_member` deferred
        for an accepted applicant who joined while pending Membership Screening:
        fires once they pass it (pending flips True -> False)."""
        if after.bot or not before.pending or after.pending:
            return
        if after.guild.id != await self._main_guild_id():
            return
        answers = latest_accepted_answers(self.db_path, after.id)
        if answers is not None:
            await self._grant_main_server_roles(after, answers)

    # --- guild resolution + membership actions ---------------------------

    async def _resolve_guild_id(self, channel_id: int | None) -> int | None:
        """Resolve the guild that owns `channel_id`, or None. Cache-first
        `get_channel` with a `fetch_channel` fallback (the pattern used by
        `_create_invite` and the bot's auto-post)."""
        if not channel_id:
            return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
                channel_id
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Could not resolve guild for channel %s (%s).", channel_id, exc
            )
            return None
        guild = getattr(channel, "guild", None)
        return guild.id if guild is not None else None

    async def _recruitment_guild_id(self) -> int | None:
        if self._recruitment_guild_id_cache is None:
            self._recruitment_guild_id_cache = await self._resolve_guild_id(
                self.results_channel_id
            )
        return self._recruitment_guild_id_cache

    async def _recruitment_guild(self) -> discord.Guild | None:
        guild_id = await self._recruitment_guild_id()
        return self.bot.get_guild(guild_id) if guild_id is not None else None

    async def _community_guild_ids(self) -> set[int]:
        if self._community_guild_ids_cache is not None:
            return self._community_guild_ids_cache
        ids: set[int] = set()
        for channel_id in (
            self.main_server_invite_channel_id,
            self.opp_server_invite_channel_id,
        ):
            guild_id = await self._resolve_guild_id(channel_id)
            if guild_id is not None:
                ids.add(guild_id)
        # Latch only a non-empty result so a transient resolve failure doesn't
        # permanently disable rename/graduation on join.
        if ids:
            self._community_guild_ids_cache = ids
        return ids

    async def _main_guild_id(self) -> int | None:
        if self._main_guild_id_cache is None:
            self._main_guild_id_cache = await self._resolve_guild_id(
                self.main_server_invite_channel_id
            )
        return self._main_guild_id_cache

    async def _grant_main_server_roles(
        self, member: discord.Member, answers: Mapping[str, Any]
    ) -> None:
        """Give an accepted applicant the main-server access role plus the role
        for each department they picked. Degrades gracefully: skips roles they
        already hold, roles missing from this server, and roles the bot can't
        assign (managed, or at/above its top role); logs (never raises) on a
        missing Manage Roles permission or a Forbidden."""
        role_ids = main_server_role_ids(
            answers.get("department"),
            self.main_server_access_role_id,
            self.main_server_department_role_ids,
        )
        if not role_ids:
            return
        guild = member.guild
        me = guild.me
        if me is not None and not me.guild_permissions.manage_roles:
            logger.warning(
                "Missing Manage Roles in %s; cannot grant roles to %s.", guild, member
            )
            return
        to_add: list[discord.Role] = []
        for role_id in role_ids:
            if member.get_role(role_id) is not None:
                continue
            role = guild.get_role(role_id)
            if role is None:
                logger.warning(
                    "Role %d not found in %s; not granting it to %s.",
                    role_id,
                    guild,
                    member,
                )
            elif not role.is_assignable():
                logger.warning(
                    "Can't assign role %s in %s (managed, or not below the bot's "
                    "top role); not granting it to %s.",
                    role,
                    guild,
                    member,
                )
            else:
                to_add.append(role)
        if not to_add:
            return
        try:
            await member.add_roles(
                *to_add, reason="Accepted GORP applicant - department roles"
            )
        except discord.Forbidden:
            logger.warning(
                "Forbidden granting roles to %s in %s (role hierarchy or permissions).",
                member,
                guild,
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to grant roles to %s in %s: %s", member, guild, exc)
        else:
            logger.info(
                "Granted %s to %s in %s.",
                ", ".join(role.name for role in to_add),
                member,
                guild,
            )

    async def _apply_community_nickname(
        self, member: discord.Member, community_name: str | None
    ) -> None:
        """Set `member`'s nickname to their community name, degrading gracefully:
        skips a blank name, the guild owner (Discord forbids renaming them), a
        missing Manage Nicknames permission, and a no-op rename; logs (never
        raises) on a hierarchy/permission Forbidden."""
        nick = clean_nickname(community_name)
        if nick is None:
            return
        guild = member.guild
        if member.id == guild.owner_id:
            logger.info(
                "Not renaming guild owner %s in %s (Discord forbids it).",
                member,
                guild,
            )
            return
        me = guild.me
        if me is not None and not me.guild_permissions.manage_nicknames:
            logger.warning(
                "Missing Manage Nicknames in %s; cannot set nick for %s.",
                guild,
                member,
            )
            return
        if member.nick == nick:
            return
        try:
            await member.edit(
                nick=nick, reason="Accepted GORP applicant community name"
            )
        except discord.Forbidden:
            logger.warning(
                "Forbidden setting nick for %s in %s (role hierarchy or permissions).",
                member,
                guild,
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to set nick for %s in %s: %s", member, guild, exc)
        else:
            logger.info("Set nickname %r for %s in %s.", nick, member, guild)

    async def _graduate_from_recruitment(self, user_id: int) -> None:
        """Join-based graduation trigger: if `user_id` is still in the recruitment
        server, remove them (they've reached a community server)."""
        guild = await self._recruitment_guild()
        if guild is None:
            return
        member = guild.get_member(user_id)
        if member is not None:
            await self._kick_from_recruitment(member)

    async def _kick_from_recruitment(self, member: discord.Member) -> None:
        """Kick an accepted member from the recruitment server (silent, no DM).
        Never touches staff or bots; logs (never raises) if the bot lacks Kick
        Members or is below the member in the role hierarchy."""
        if member.bot or member.guild_permissions.manage_guild:
            return
        try:
            await member.kick(
                reason="Accepted GORP applicant - graduated from recruitment server"
            )
        except discord.Forbidden:
            logger.warning(
                "No permission / hierarchy to graduate member %s (%d) from recruitment.",
                member,
                member.id,
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to graduate member %s: %s", member, exc)
        else:
            logger.info(
                "Graduated accepted member %s (%d) from the recruitment server.",
                member,
                member.id,
            )

    async def _rename_applicant_in_recruitment(
        self,
        interaction: discord.Interaction,
        applicant_id: int,
        community_name: str | None,
    ) -> None:
        """Set an accepted applicant's nickname in the recruitment server at the
        moment of acceptance. Resolves the applicant member from the interaction's
        guild (get -> fetch fallback); no-ops outside a guild or if they've left."""
        guild = interaction.guild
        if guild is None:
            return
        member = guild.get_member(applicant_id)
        if member is None:
            try:
                member = await guild.fetch_member(applicant_id)
            except (discord.NotFound, discord.HTTPException):
                member = None
        if member is not None:
            await self._apply_community_nickname(member, community_name)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        text = (message.content or "").strip()
        if not text:
            return
        await self._handle_dm(message.author, text)

    async def _handle_dm(self, user: discord.abc.User, text: str) -> None:
        record_activity(self.db_path, user.id, datetime.now(timezone.utc))
        session = self.sessions.get(user.id)
        lowered = text.lower()

        if session is None:
            if lowered in START_KEYWORDS:
                await self.start_for_user(user)
            else:
                await user.send(
                    "Hi! To begin your GORP recruitment interview, type `apply` "
                    "or click the **Start Interview** button."
                )
            return

        if lowered == "cancel":
            self.sessions.pop(user.id, None)
            await user.send(
                "Interview cancelled. Type `apply` if you'd like to start again."
            )
            return

        if lowered == "restart":
            session.restart()
            await user.send("Restarting from the beginning.")
            await self._ask_next(user, session)
            return

        current = session.current_question()
        if current is not None and current.key == "knows_code" and text.strip() != "416":
            await user.send(
                "That is not the correct code. Please re-read the rules carefully. "
                "Restarting the interview from the beginning."
            )
            session.restart()
            await self._ask_next(user, session)
            return

        error = session.submit_answer(text)
        if error:
            await user.send(error)
            question = session.current_question()
            if question is not None:
                await self._send_question(user, question)
            return

        if session.is_complete():
            await self._complete(user, session)
            return

        await self._ask_next(user, session)

    def _embargo_reapply_at(self, user_id: int) -> datetime | None:
        """Return the earliest UTC datetime at which `user_id` may reapply, or
        None if they're not currently under a rejection embargo."""
        rejection = latest_rejection_for_user(self.db_path, user_id)
        if not rejection:
            return None
        decided_at_raw = rejection.get("decided_at")
        if not decided_at_raw:
            return None
        try:
            decided_at = datetime.fromisoformat(decided_at_raw)
        except ValueError:
            return None
        if decided_at.tzinfo is None:
            decided_at = decided_at.replace(tzinfo=timezone.utc)
        reapply_at = decided_at + timedelta(days=REAPPLY_EMBARGO_DAYS)
        return reapply_at if reapply_at > datetime.now(timezone.utc) else None

    async def start_for_user(self, user: discord.abc.User) -> None:
        if user.id in self.sessions:
            await user.send(
                "You already have an interview in progress. Type `restart` to "
                "start over, or `cancel` to stop."
            )
            return
        reapply_at = self._embargo_reapply_at(user.id)
        if reapply_at is not None:
            await user.send(
                "Your previous application was not accepted. You may reapply on "
                f"<t:{int(reapply_at.timestamp())}:D> "
                f"(<t:{int(reapply_at.timestamp())}:R>)."
            )
            return
        session = InterviewSession(
            user_id=user.id,
            username=str(user),
            display_name=getattr(user, "display_name", None) or user.name,
        )
        self.sessions[user.id] = session
        await user.send(INTERVIEW_INTRO_TEXT)
        await self._ask_next(user, session)

    async def start_from_interaction(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if user.id in self.sessions:
            await interaction.response.send_message(
                "You already have an interview in progress in your DMs. Type "
                "`restart` there to start over, or `cancel` to stop.",
                ephemeral=True,
            )
            return
        reapply_at = self._embargo_reapply_at(user.id)
        if reapply_at is not None:
            await interaction.response.send_message(
                "Your previous application was not accepted. You may reapply on "
                f"<t:{int(reapply_at.timestamp())}:D> "
                f"(<t:{int(reapply_at.timestamp())}:R>).",
                ephemeral=True,
            )
            return
        try:
            dm = await user.create_dm()
            await dm.send("Opening your interview…")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't DM you. Please enable **Allow direct messages from "
                "server members** (Server → Privacy Settings) and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Check your DMs - I've started the interview.", ephemeral=True
        )
        await self.start_for_user(user)

    async def _ask_next(
        self, user: discord.abc.User, session: InterviewSession
    ) -> None:
        question = session.current_question()
        if question is None:
            await self._complete(user, session)
            return
        await self._send_question(user, question)

    async def _send_question(
        self, user: discord.abc.User, question: Question
    ) -> None:
        number = QUESTIONS.index(question) + 1
        body = f"**Question {number}/{len(QUESTIONS)}**\n{question.prompt}"
        if question.kind in ("choice", "multi_choice"):
            body = f"{body}\n\n{choice_prompt(question)}"
        await user.send(body)

    async def _complete(
        self, user: discord.abc.User, session: InterviewSession
    ) -> None:
        record = session.to_record()
        app_id: int | None = None
        try:
            app_id = save_application(
                self.db_path,
                user_id=record["user_id"],
                username=record["username"],
                started_at=record["started_at"],
                completed_at=record["completed_at"],
                answers=record["answers"],
            )
        except Exception:
            logger.exception("Failed to persist application for %s", user.id)

        try:
            await self._post_results_embed(record, app_id)
        except Exception:
            logger.exception("Failed to post results embed for %s", user.id)

        self.sessions.pop(user.id, None)
        await user.send(
            "**Interview complete.** Your application has been submitted to "
            "the GORP staff for review. You'll hear back soon - thank you."
        )

    async def _post_results_embed(
        self, record: dict, app_id: int | None
    ) -> None:
        channel = self.bot.get_channel(self.results_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.results_channel_id)
        embed = self._build_embed(record, app_id)
        content = (
            f"<@&{self.recruiter_role_id}> "
            f"New application from <@{record['user_id']}>"
        )
        kwargs: dict = {
            "content": content,
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(
                roles=True, users=False, everyone=False
            ),
        }
        if app_id is not None:
            kwargs["view"] = build_decision_view(app_id)
        message = await channel.send(**kwargs)
        # Remember which message carries the decision so /reverse-decision can
        # relocate and rebuild it later (the button rides on the message itself,
        # but the slash command has only the app id to go on). Best-effort: a
        # miss just means that one application can't be reversed via the command.
        if app_id is not None:
            try:
                set_results_message_id(
                    self.db_path, application_id=app_id, message_id=message.id
                )
            except Exception:
                logger.exception(
                    "Failed to store results message id for app %s", app_id
                )

    # --- Staff accept/reject handlers (invoked by DecisionView buttons) -----

    async def _check_recruiter(
        self, interaction: discord.Interaction
    ) -> bool:
        """Ephemerally reject the interaction and return False if the clicker
        doesn't hold the recruiter role. Bot-owner/admin members in the guild
        bypass the check (treated as recruiters)."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Decisions can only be made from inside the server.",
                ephemeral=True,
            )
            return False
        if member.guild_permissions.manage_guild:
            return True
        if any(role.id == self.recruiter_role_id for role in member.roles):
            return True
        await interaction.response.send_message(
            "You need the recruiter role to use these buttons.",
            ephemeral=True,
        )
        return False

    def _decision_already_made_message(self, app: dict) -> str:
        status = app.get("status", "decided")
        decided_by = app.get("decided_by")
        decided_at = app.get("decided_at") or ""
        who = f"<@{decided_by}>" if decided_by else "someone"
        when = f" on {decided_at[:10]}" if decided_at else ""
        return f"This application is already **{status}** by {who}{when}."

    async def handle_accept(
        self, interaction: discord.Interaction, app_id: int
    ) -> None:
        if not await self._check_recruiter(interaction):
            return
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.send_message(
                "I couldn't find that application in the database.",
                ephemeral=True,
            )
            return
        if app.get("status") != "submitted":
            await interaction.response.send_message(
                self._decision_already_made_message(app), ephemeral=True
            )
            return

        await interaction.response.defer()

        decided_at = datetime.now(timezone.utc)
        try:
            set_application_decision(
                self.db_path,
                application_id=app_id,
                status="accepted",
                decided_by=interaction.user.id,
                decided_at=decided_at,
            )
        except Exception:
            logger.exception("Failed to record accept for app %s", app_id)
            await interaction.followup.send(
                "Database error recording the decision. Try again.",
                ephemeral=True,
            )
            return

        applicant_id = int(app["user_id"])

        # Rename the applicant to their community name here in the recruitment
        # server (they're already a member); the community servers are handled on
        # join. interaction.user is the recruiter, so resolve the applicant.
        await self._rename_applicant_in_recruitment(
            interaction, applicant_id, app.get("answers", {}).get("community_name")
        )

        departments = app.get("answers", {}).get("department", []) or []
        if isinstance(departments, str):
            departments = [departments]
        main_invite_url = await self._create_invite(
            self.main_server_invite_channel_id, "main server"
        )
        opp_invite_url = None
        if "OPP" in departments:
            opp_invite_url = await self._create_invite(
                self.opp_server_invite_channel_id, "OPP server"
            )
        dm_body = build_acceptance_dm(
            list(departments),
            main_invite_url=main_invite_url,
            opp_invite_url=opp_invite_url,
            invite_expiry_days=self._invite_expiry_days(),
        )
        dm_status = await self._dm_applicant(applicant_id, dm_body)
        invite_bits: list[str] = []
        if self.main_server_invite_channel_id:
            invite_bits.append("main✓" if main_invite_url else "main✗")
        if "OPP" in departments and self.opp_server_invite_channel_id:
            invite_bits.append("OPP✓" if opp_invite_url else "OPP✗")
        if invite_bits:
            dm_status = f"{dm_status} · invites: {', '.join(invite_bits)}"

        await self._finalize_decision_embed(
            interaction.message,
            app_id=app_id,
            status="accepted",
            color=discord.Color.teal(),
            prefix=f"✅ Accepted by {interaction.user.mention}",
            dm_status=dm_status,
        )

    async def handle_reject_click(
        self, interaction: discord.Interaction, app_id: int
    ) -> None:
        if not await self._check_recruiter(interaction):
            return
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.send_message(
                "I couldn't find that application in the database.",
                ephemeral=True,
            )
            return
        if app.get("status") != "submitted":
            await interaction.response.send_message(
                self._decision_already_made_message(app), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            RejectReasonModal(app_id, interaction.message)
        )

    async def handle_reject_submit(
        self,
        interaction: discord.Interaction,
        app_id: int,
        reason: str | None,
        *,
        source_message: discord.Message | None,
    ) -> None:
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.send_message(
                "I couldn't find that application in the database.",
                ephemeral=True,
            )
            return
        if app.get("status") != "submitted":
            await interaction.response.send_message(
                self._decision_already_made_message(app), ephemeral=True
            )
            return

        await interaction.response.defer()

        decided_at = datetime.now(timezone.utc)
        try:
            set_application_decision(
                self.db_path,
                application_id=app_id,
                status="rejected",
                decided_by=interaction.user.id,
                decided_at=decided_at,
                reason=reason,
            )
        except Exception:
            logger.exception("Failed to record reject for app %s", app_id)
            await interaction.followup.send(
                "Database error recording the decision. Try again.",
                ephemeral=True,
            )
            return

        applicant_id = int(app["user_id"])
        reapply_at = decided_at + timedelta(days=REAPPLY_EMBARGO_DAYS)
        dm_body = build_rejection_dm(reapply_at, reason)
        dm_status = await self._dm_applicant(applicant_id, dm_body)

        prefix = f"❌ Rejected by {interaction.user.mention}"
        if reason:
            short = reason if len(reason) <= 200 else reason[:197] + "…"
            prefix = f"{prefix}\n**Reason:** {short}"
        await self._finalize_decision_embed(
            source_message,
            app_id=app_id,
            status="rejected",
            color=discord.Color.red(),
            prefix=prefix,
            dm_status=dm_status,
        )

    async def request_reverse(
        self,
        interaction: discord.Interaction,
        app_id: int,
        *,
        source_message: discord.Message | None,
    ) -> None:
        """Shared entry point for the Reverse button and /reverse-decision. Gate
        on recruiter + a currently-decided application, then pop an ephemeral
        confirmation instead of reversing on the first click - a reverse wipes
        the recorded decider and reason, so a stray click shouldn't do it."""
        if not await self._check_recruiter(interaction):
            return
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.send_message(
                "I couldn't find that application in the database.",
                ephemeral=True,
            )
            return
        if app.get("status") == "submitted":
            await interaction.response.send_message(
                f"There's nothing to reverse - application #{app_id} is still "
                "awaiting a decision.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"**Reverse the {app['status']} decision on application #{app_id}?** "
            f"This reopens <@{app['user_id']}>'s application for a fresh decision "
            "and clears the recorded decision and reason. The applicant isn't "
            "notified, and any DM or invite already sent can't be recalled.",
            view=ConfirmReverseView(app_id, source_message),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def handle_reverse(
        self,
        interaction: discord.Interaction,
        app_id: int,
        *,
        source_message: discord.Message | None,
    ) -> None:
        """Confirmed reverse: clear the decision and rebuild the staff message
        back to its pristine 'New Application' form with the accept/reject
        buttons restored, so it can be decided again. Re-checks the gate and the
        state in case they changed while the confirmation sat open. Sends the
        applicant nothing - reversal is a silent staff-side correction; the
        follow-up accept/reject will DM them as usual."""
        if not await self._check_recruiter(interaction):
            return
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.edit_message(
                content="I couldn't find that application in the database.",
                view=None,
            )
            return
        if app.get("status") == "submitted":
            await interaction.response.edit_message(
                content=(
                    f"Application #{app_id} is already awaiting a decision - "
                    "nothing to reverse."
                ),
                view=None,
            )
            return

        try:
            clear_application_decision(self.db_path, application_id=app_id)
        except Exception:
            logger.exception("Failed to clear decision for app %s", app_id)
            await interaction.response.edit_message(
                content="Database error reversing the decision. Try again.",
                view=None,
            )
            return

        message = source_message or await self._fetch_results_message(app)
        rebuilt = False
        if message is not None:
            try:
                await message.edit(
                    content=(
                        f"Reopened by {interaction.user.mention} · "
                        f"application from <@{app['user_id']}>"
                    ),
                    embed=self._build_pristine_embed_from_stored(app, app_id),
                    view=build_decision_view(app_id),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                rebuilt = True
            except discord.HTTPException:
                logger.exception(
                    "Reversed app %s but failed to rebuild its message", app_id
                )

        if rebuilt:
            note = (
                f"Decision reversed - application #{app_id} is open for a new "
                "decision."
            )
        else:
            # The original staff message is gone (deleted, or decided before
            # message ids were tracked), so post a fresh one to the results
            # channel rather than leaving the application with no buttons.
            reposted = await self._repost_decision_message(app, app_id)
            if reposted is not None:
                note = (
                    f"Decision reversed - application #{app_id}'s original "
                    "message was gone, so I posted a fresh one in the results "
                    f"channel: {reposted.jump_url}"
                )
            else:
                note = (
                    f"Decision reversed for application #{app_id}, but I couldn't "
                    "update or re-post its staff message. Check the results "
                    "channel is configured and I can post there."
                )
        await interaction.response.edit_message(content=note, view=None)

    async def _fetch_results_message(
        self, app: dict
    ) -> discord.Message | None:
        """Relocate the staff-channel message an application's decision was
        posted to, from the stored id. Returns None when no id was stored (e.g.
        the application was decided before this feature) or the message/channel
        is gone - the caller degrades to a 'couldn't update the message' note."""
        raw_id = app.get("results_message_id")
        if not raw_id:
            return None
        try:
            channel = self.bot.get_channel(
                self.results_channel_id
            ) or await self.bot.fetch_channel(self.results_channel_id)
            return await channel.fetch_message(int(raw_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        except (TypeError, ValueError):
            return None

    async def _repost_decision_message(
        self, app: dict, app_id: int
    ) -> discord.Message | None:
        """Post a fresh 'New Application' embed for a stored application to the
        results channel, with the Accept/Reject buttons, and record its new
        message id. Recovers an application whose original staff message is gone
        (deleted, or decided before message ids were tracked) so it can be
        decided again. Returns the new message, or None if the results channel
        can't be reached or posted to."""
        try:
            channel = self.bot.get_channel(
                self.results_channel_id
            ) or await self.bot.fetch_channel(self.results_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not load results channel to re-post app %s", app_id
            )
            return None
        if channel is None:
            return None
        try:
            message = await channel.send(
                content=(
                    f"<@&{self.recruiter_role_id}> Re-posted application "
                    f"#{app_id} from <@{app['user_id']}>, awaiting a decision"
                ),
                embed=self._build_pristine_embed_from_stored(app, app_id),
                view=build_decision_view(app_id),
                allowed_mentions=discord.AllowedMentions(
                    roles=True, users=False, everyone=False
                ),
            )
        except discord.HTTPException:
            logger.exception("Failed to re-post app %s", app_id)
            return None
        try:
            set_results_message_id(
                self.db_path, application_id=app_id, message_id=message.id
            )
        except Exception:
            logger.exception(
                "Re-posted app %s but failed to store its new message id", app_id
            )
        return message

    @app_commands.command(
        name="reverse-decision",
        description=(
            "Reopen a mis-decided application so it can be decided again "
            "(staff only)."
        ),
    )
    @app_commands.describe(
        app_id="The application number shown as 'App #…' in the results embed footer."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def reverse_decision_command(
        self, interaction: discord.Interaction, app_id: int
    ) -> None:
        """Slash-command entry point. Unlike the Reverse button it works on any
        decided application - including ones decided before that button existed -
        because it relocates the staff message from the stored id rather than
        riding on a component."""
        await self.request_reverse(interaction, app_id, source_message=None)

    @app_commands.command(
        name="repost-application",
        description=(
            "Re-post a submitted application's embed with Accept/Reject buttons "
            "(staff only)."
        ),
    )
    @app_commands.describe(
        app_id="The application number shown as 'App #…' in the results embed footer."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def repost_application_command(
        self, interaction: discord.Interaction, app_id: int
    ) -> None:
        """Post a fresh, decidable embed for an application whose staff message is
        gone - e.g. one reversed via /reverse-decision that couldn't be updated
        in place. Only acts on `submitted` applications; a still-decided one keeps
        its own message (reverse it first to redo it)."""
        if not await self._check_recruiter(interaction):
            return
        app = get_application(self.db_path, app_id)
        if app is None:
            await interaction.response.send_message(
                "I couldn't find that application in the database.",
                ephemeral=True,
            )
            return
        if app.get("status") != "submitted":
            await interaction.response.send_message(
                f"Application #{app_id} is currently {app['status']}, not awaiting "
                "a decision. Use /reverse-decision first if you want to redo it.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        message = await self._repost_decision_message(app, app_id)
        if message is None:
            await interaction.followup.send(
                "I couldn't post to the results channel. Check RESULTS_CHANNEL_ID "
                "and that I can send messages there.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Re-posted application #{app_id}: {message.jump_url}", ephemeral=True
        )

    def _invite_expiry_days(self) -> int:
        """Acceptance-invite lifetime in days: the graduation window, clamped to
        Discord's 7-day invite max_age cap. Shared by the invite creation and the
        acceptance DM so the promised expiry always matches the real one."""
        return min(self.graduation_days, MAX_INVITE_AGE_SECONDS // SECONDS_PER_DAY)

    async def _create_invite(
        self, channel_id: int | None, label: str
    ) -> str | None:
        """Create a single-use invite to `channel_id` (a channel in one of the
        community servers the bot has been added to) that expires after the
        graduation window, and return its URL. Expiring the invite makes it null
        and void if the applicant doesn't join within `graduation_days`, matching
        the recruitment-server graduation timeout. Degrades to None - logging a
        warning - when the channel isn't configured, can't be reached, isn't a
        text channel, or the bot lacks the Create Invite permission."""
        if not channel_id:
            return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
                channel_id
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Could not load %s invite channel %s (%s); skipping invite.",
                label,
                channel_id,
                exc,
            )
            return None
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "%s invite channel %s is not a text channel; skipping invite.",
                label,
                channel_id,
            )
            return None
        max_age = self._invite_expiry_days() * SECONDS_PER_DAY
        try:
            invite = await channel.create_invite(
                max_age=max_age,
                max_uses=1,
                unique=True,
                reason="Accepted GORP applicant",
            )
        except discord.Forbidden:
            logger.warning(
                "No permission to create an invite in the %s channel #%s.",
                label,
                channel.name,
            )
            return None
        except discord.HTTPException:
            logger.exception("Failed to create %s invite", label)
            return None
        return invite.url

    async def _dm_applicant(self, user_id: int, body: str) -> str:
        """DM the applicant the decision message. Returns a short status string
        suitable for appending to the staff embed footer."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(
                user_id
            )
        except discord.HTTPException:
            return "DM failed (could not fetch user)"
        try:
            await user.send(body)
        except discord.Forbidden:
            return "DM failed (user has DMs closed)"
        except discord.HTTPException:
            logger.exception("Failed to DM applicant %s", user_id)
            return "DM failed"
        return "DM sent"

    async def _finalize_decision_embed(
        self,
        message: discord.Message | None,
        *,
        app_id: int,
        status: str,
        color: discord.Color,
        prefix: str,
        dm_status: str,
    ) -> None:
        """Edit the original staff message: recolor the embed, prepend the
        decision banner to the description, append DM status to the footer, and
        swap the accept/reject buttons for a single 'Reverse decision' button so
        a mis-clicked decision can be undone (but not re-made) from the message."""
        try:
            if message is None:
                return
            embed = message.embeds[0] if message.embeds else discord.Embed()
            new = discord.Embed(
                title=embed.title,
                description=(
                    f"{prefix}\n\n{embed.description}"
                    if embed.description
                    else prefix
                ),
                color=color,
                timestamp=embed.timestamp,
            )
            if embed.author and embed.author.name:
                new.set_author(name=embed.author.name)
            for field in embed.fields:
                new.add_field(
                    name=field.name, value=field.value, inline=field.inline
                )
            footer_text = embed.footer.text if embed.footer else ""
            new.set_footer(
                text=f"{footer_text} • {status.title()} • {dm_status}".strip(
                    " •"
                )
            )
            await message.edit(embed=new, view=build_reverse_view(app_id))
        except Exception:
            logger.exception("Failed to update decision embed")

    def _build_pristine_embed_from_stored(
        self, app: dict, app_id: int
    ) -> discord.Embed:
        """Rebuild the original 'New Application' embed from a stored application
        row (as returned by get_application), for reopening a reversed decision.
        Bridges two gaps so _build_embed can be reused unchanged: the DB has no
        display_name column (it only ever lived on the in-memory session, so we
        fall back to the stored username - the reopened title reads 'New
        Application - <username>'), and started_at/completed_at come back as ISO
        strings, which _build_embed consumes as datetimes."""
        record = {
            "user_id": app["user_id"],
            "username": app["username"],
            "display_name": app["username"],
            "answers": app.get("answers", {}),
            "started_at": datetime.fromisoformat(app["started_at"]),
            "completed_at": datetime.fromisoformat(app["completed_at"]),
        }
        return self._build_embed(record, app_id)

    def _build_embed(
        self, record: dict, app_id: int | None = None
    ) -> discord.Embed:
        answers = record["answers"]
        embed = discord.Embed(
            title=f"New Application - {record['display_name']}",
            color=discord.Color.green(),
            timestamp=record["completed_at"],
        )
        embed.set_author(name=record["username"])
        embed.add_field(
            name="Discord User",
            value=(
                f"<@{record['user_id']}> "
                f"(`{record['username']}`, ID `{record['user_id']}`)"
            ),
            inline=False,
        )
        for question in QUESTIONS:
            raw = answers.get(question.key, "-")
            if isinstance(raw, list):
                value = ", ".join(raw) if raw else "-"
            else:
                value = str(raw) if raw not in (None, "") else "-"
            if len(value) > 1024:
                value = value[:1020] + "…"
            embed.add_field(
                name=question.display_label(),
                value=value,
                inline=False,
            )
        started = f"Started {record['started_at'].isoformat()}"
        embed.set_footer(
            text=f"App #{app_id} • {started}" if app_id is not None else started
        )
        return embed
