"""Prospective-member lifecycle: a recurring nudge ping plus inactivity
management (warn, then auto-kick).

Everything here is gated behind `enabled` (config `PROSPECT_MANAGEMENT_ENABLED`,
off by default) since auto-kick is destructive. Only members holding the
configured prospect role are ever eligible for a kick; staff, admins, and bots
are always skipped. A member counts as "active" if they send any message in the
server (tracked here) or interact with the interview in DMs (tracked in
`InterviewCog._handle_dm`); becoming active resets the clock.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from content import (
    build_prospect_kick_dm,
    build_prospect_ping,
    build_prospect_warning_dm,
)
from db import (
    delete_member_activity,
    get_member_activity,
    mark_warned,
    record_activity,
    seed_activity_if_absent,
)

logger = logging.getLogger(__name__)

# How often the inactivity scan runs - frequent enough to action warns/kicks
# promptly, infrequent enough to stay cheap.
INACTIVITY_SCAN_HOURS = 3

# Placeholder schedule for the prospect-ping loop declaration. The real,
# tz-aware times come from config and are applied via `change_interval` before
# the loop is started, so this default is never actually used.
_DEFAULT_PING_TIMES = [time(9), time(21)]


class ProspectsCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        db_path: Path,
        enabled: bool,
        prospect_role_id: int | None,
        ping_channel_id: int | None,
        ping_times: tuple[time, ...],
        warn_days: int,
        kick_days: int,
    ) -> None:
        self.bot = bot
        self.db_path = db_path
        self.enabled = enabled
        self.prospect_role_id = prospect_role_id
        self.ping_channel_id = ping_channel_id
        self.ping_times = ping_times
        self.warn_days = warn_days
        self.kick_days = kick_days
        self._backfilled = False

        if not self.enabled:
            logger.info(
                "Prospect management disabled; ping/inactivity loops not started."
            )
            return
        if self.prospect_role_id is None:
            logger.warning(
                "PROSPECT_MANAGEMENT_ENABLED is on but PROSPECT_ROLE_ID is unset; "
                "prospect loops will not start."
            )
            return

        if self.ping_channel_id:
            self.prospect_ping.change_interval(time=list(self.ping_times))
            self.prospect_ping.start()
        else:
            logger.info(
                "No PROSPECT_PING_CHANNEL_ID set; skipping the recurring prospect ping."
            )
        self.inactivity_scan.start()

    def cog_unload(self) -> None:
        if self.prospect_ping.is_running():
            self.prospect_ping.cancel()
        if self.inactivity_scan.is_running():
            self.inactivity_scan.cancel()

    # --- activity tracking ------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Record server activity, but only for current prospect-role holders -
        keeps the write volume tiny and the table scoped to who we care about."""
        if not self.enabled or self.prospect_role_id is None:
            return
        if message.author.bot or message.guild is None:
            return
        author = message.author
        if isinstance(author, discord.Member) and any(
            role.id == self.prospect_role_id for role in author.roles
        ):
            record_activity(
                self.db_path, author.id, datetime.now(timezone.utc)
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        delete_member_activity(self.db_path, member.id)

    # --- helpers ----------------------------------------------------------

    def _prospect_role(self) -> discord.Role | None:
        if self.prospect_role_id is None:
            return None
        for guild in self.bot.guilds:
            role = guild.get_role(self.prospect_role_id)
            if role is not None:
                return role
        return None

    def _backfill(self, role: discord.Role) -> None:
        """Seed a fresh activity window for current role holders we've never
        tracked, so existing prospects get a full grace period from the moment
        the feature is switched on rather than being kicked immediately."""
        now = datetime.now(timezone.utc)
        for member in role.members:
            if member.bot:
                continue
            seed_activity_if_absent(self.db_path, member.id, now)
        self._backfilled = True
        logger.info("Backfilled activity for %d current prospect(s).", len(role.members))

    # --- recurring ping ---------------------------------------------------

    @tasks.loop(time=_DEFAULT_PING_TIMES)
    async def prospect_ping(self) -> None:
        if self.prospect_role_id is None or self.ping_channel_id is None:
            return
        try:
            channel = self.bot.get_channel(self.ping_channel_id) or (
                await self.bot.fetch_channel(self.ping_channel_id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Prospect ping channel %s unavailable (%s).",
                self.ping_channel_id,
                exc,
            )
            return
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "Prospect ping channel %s is not a text channel.",
                self.ping_channel_id,
            )
            return
        try:
            await channel.send(
                build_prospect_ping(self.prospect_role_id),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.Forbidden:
            logger.warning(
                "No permission to send the prospect ping in #%s.", channel.name
            )
        except discord.HTTPException as exc:
            logger.warning("Failed to send prospect ping: %s", exc)

    @prospect_ping.before_loop
    async def _before_ping(self) -> None:
        await self.bot.wait_until_ready()

    # --- inactivity scan --------------------------------------------------

    @tasks.loop(hours=INACTIVITY_SCAN_HOURS)
    async def inactivity_scan(self) -> None:
        role = self._prospect_role()
        if role is None:
            logger.warning(
                "Prospect role %s not found in any guild; skipping inactivity scan.",
                self.prospect_role_id,
            )
            return
        if not self._backfilled:
            self._backfill(role)

        now = datetime.now(timezone.utc)
        warn_delta = timedelta(days=self.warn_days)
        kick_delta = timedelta(days=self.kick_days)

        for member in list(role.members):
            # Never touch staff, admins, or bots - kicks are role-gated and
            # additionally guarded here.
            if member.bot or member.guild_permissions.manage_guild:
                continue

            activity = get_member_activity(self.db_path, member.id)
            if activity is None:
                last_active = member.joined_at
                warned_at = None
            else:
                last_active = activity["last_activity"]
                warned_at = activity["warned_at"]
            if last_active is None:
                continue  # can't determine an age; play it safe

            inactive = now - last_active
            # Kick requires a prior warning, so even after downtime no one is
            # removed without first being warned.
            if inactive >= kick_delta and warned_at is not None:
                await self._kick_member(member)
            elif inactive >= warn_delta and warned_at is None:
                await self._warn_member(member, last_active, now)

    @inactivity_scan.before_loop
    async def _before_scan(self) -> None:
        await self.bot.wait_until_ready()

    async def _warn_member(
        self, member: discord.Member, last_active: datetime, now: datetime
    ) -> None:
        try:
            await member.send(build_prospect_warning_dm(self.kick_days))
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.info("Couldn't DM inactivity warning to %s (%s).", member, exc)
        # Mark warned even if the DM failed: otherwise we'd retry the warn DM
        # every scan forever and never progress to a kick.
        mark_warned(self.db_path, member.id, last_active, now)
        logger.info("Warned prospect %s (%d) for inactivity.", member, member.id)

    async def _kick_member(self, member: discord.Member) -> None:
        try:
            await member.send(build_prospect_kick_dm(self.kick_days))
        except (discord.Forbidden, discord.HTTPException):
            pass  # DMs closed - kick anyway
        try:
            await member.kick(
                reason=f"Inactive prospect for {self.kick_days}+ days (warned)"
            )
        except discord.Forbidden:
            logger.warning(
                "No permission / role hierarchy to kick prospect %s (%d).",
                member,
                member.id,
            )
            return
        except discord.HTTPException as exc:
            logger.warning("Failed to kick prospect %s: %s", member, exc)
            return
        delete_member_activity(self.db_path, member.id)
        logger.info("Kicked inactive prospect %s (%d).", member, member.id)
