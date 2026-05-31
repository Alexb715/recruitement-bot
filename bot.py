from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from cogs.admin import AdminCog
from cogs.interview import (
    AcceptDecisionButton,
    ApplyView,
    InterviewCog,
    RejectDecisionButton,
    build_apply_embed,
)
from cogs.prospects import ProspectsCog
from config import load_config
from content import build_faq_embed, build_requirements_embed
from db import delete_state, get_state, init_db, set_state


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("recruiter")


def _make_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return intents


class RecruiterBot(commands.Bot):
    def __init__(self, *, config) -> None:
        super().__init__(command_prefix="!", intents=_make_intents())
        self.config = config
        self._auto_post_done = False

    async def setup_hook(self) -> None:
        init_db(self.config.db_path)
        self.add_view(ApplyView())
        self.add_dynamic_items(AcceptDecisionButton, RejectDecisionButton)
        await self.add_cog(
            InterviewCog(
                self,
                results_channel_id=self.config.results_channel_id,
                recruiter_role_id=self.config.recruiter_role_id,
                db_path=self.config.db_path,
                main_server_invite_channel_id=self.config.main_server_invite_channel_id,
                opp_server_invite_channel_id=self.config.opp_server_invite_channel_id,
            )
        )
        await self.add_cog(
            ProspectsCog(
                self,
                db_path=self.config.db_path,
                enabled=self.config.prospect_management_enabled,
                prospect_role_id=self.config.prospect_role_id,
                ping_channel_id=self.config.prospect_ping_channel_id,
                ping_interval_hours=self.config.prospect_ping_interval_hours,
                warn_days=self.config.inactivity_warn_days,
                kick_days=self.config.inactivity_kick_days,
            )
        )
        await self.add_cog(AdminCog(self))

        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(
                "Synced %d slash command(s) to dev guild %s",
                len(synced),
                self.config.dev_guild_id,
            )
        else:
            synced = await self.tree.sync()
            logger.info(
                "Synced %d slash command(s) globally (may take up to 1 hour)",
                len(synced),
            )

        self.loop.create_task(self._auto_post_all())

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id)

    async def _auto_post_all(self) -> None:
        """Ensure each configured permanent message (Apply button, FAQ,
        Requirements) exists exactly once in its channel. Reuses the
        previously-posted message across restarts and reposts only if it has
        been deleted. Runs once per process."""
        await self.wait_until_ready()
        if self._auto_post_done:
            return
        self._auto_post_done = True

        await self._auto_post_persistent(
            channel_id=self.config.apply_channel_id,
            state_prefix="apply_message",
            embed_factory=build_apply_embed,
            view=ApplyView(),
            label="Apply button",
        )
        await self._auto_post_persistent(
            channel_id=self.config.faq_channel_id,
            state_prefix="faq_message",
            embed_factory=lambda: build_faq_embed(self.config),
            allowed_mentions=discord.AllowedMentions.none(),
            label="FAQ",
        )
        await self._auto_post_persistent(
            channel_id=self.config.requirements_channel_id,
            state_prefix="requirements_message",
            embed_factory=build_requirements_embed,
            label="Requirements",
        )

    async def _auto_post_persistent(
        self,
        *,
        channel_id: int | None,
        state_prefix: str,
        embed_factory,
        view: discord.ui.View | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        label: str,
    ) -> None:
        """Post a single persistent embed to `channel_id` and remember its id
        under `state_prefix:{channel_id}`, reusing it across restarts. Skips
        silently when `channel_id` is not configured."""
        if not channel_id:
            return

        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as exc:
            logger.warning(
                "%s channel %s could not be loaded (%s); skipping auto-post.",
                label,
                channel_id,
                exc,
            )
            return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "%s channel %s is not a text channel/thread; skipping auto-post.",
                label,
                channel_id,
            )
            return

        state_key = f"{state_prefix}:{channel_id}"
        existing_id = get_state(self.config.db_path, state_key)
        if existing_id:
            try:
                await channel.fetch_message(int(existing_id))
            except (discord.NotFound, discord.Forbidden, ValueError):
                logger.info(
                    "Previous %s message %s in #%s is gone - reposting.",
                    label,
                    existing_id,
                    channel.name,
                )
                delete_state(self.config.db_path, state_key)
            else:
                logger.info(
                    "%s already present in #%s as message %s - reusing.",
                    label,
                    channel.name,
                    existing_id,
                )
                return

        kwargs: dict = {"embed": embed_factory()}
        if view is not None:
            kwargs["view"] = view
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions

        try:
            posted = await channel.send(**kwargs)
        except discord.Forbidden:
            logger.warning("No permission to send %s in #%s.", label, channel.name)
            return

        set_state(self.config.db_path, state_key, str(posted.id))
        logger.info("Posted %s in #%s as message %s.", label, channel.name, posted.id)


def main() -> None:
    config = load_config()
    bot = RecruiterBot(config=config)
    asyncio.run(bot.start(config.token))


if __name__ == "__main__":
    main()
