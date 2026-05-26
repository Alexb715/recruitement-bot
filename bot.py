from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from cogs.admin import AdminCog
from cogs.interview import ApplyView, InterviewCog, build_apply_embed
from config import load_config
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
        await self.add_cog(
            InterviewCog(
                self,
                results_channel_id=self.config.results_channel_id,
                recruiter_role_id=self.config.recruiter_role_id,
                db_path=self.config.db_path,
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

        self.loop.create_task(self._auto_post_apply_button())

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id)

    async def _auto_post_apply_button(self) -> None:
        """If APPLY_CHANNEL_ID is configured, ensure exactly one Apply Here
        message exists in that channel. Reuses the previously-posted message
        across restarts; reposts only if it has been deleted."""
        await self.wait_until_ready()
        if self._auto_post_done:
            return
        self._auto_post_done = True

        channel_id = self.config.apply_channel_id
        if not channel_id:
            return

        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as exc:
            logger.warning(
                "APPLY_CHANNEL_ID=%s could not be loaded (%s); skipping auto-post.",
                channel_id,
                exc,
            )
            return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "APPLY_CHANNEL_ID=%s is not a text channel/thread; skipping auto-post.",
                channel_id,
            )
            return

        state_key = f"apply_message:{channel_id}"
        existing_id = get_state(self.config.db_path, state_key)
        if existing_id:
            try:
                existing_msg = await channel.fetch_message(int(existing_id))
            except (discord.NotFound, discord.Forbidden, ValueError):
                logger.info(
                    "Previous Apply message %s in #%s is gone - reposting.",
                    existing_id,
                    channel.name,
                )
                delete_state(self.config.db_path, state_key)
            else:
                logger.info(
                    "Apply button already present in #%s as message %s - reusing.",
                    channel.name,
                    existing_msg.id,
                )
                return

        try:
            posted = await channel.send(embed=build_apply_embed(), view=ApplyView())
        except discord.Forbidden:
            logger.warning(
                "No permission to send Apply message in #%s.", channel.name
            )
            return

        set_state(self.config.db_path, state_key, str(posted.id))
        logger.info(
            "Posted Apply button in #%s as message %s.", channel.name, posted.id
        )


def main() -> None:
    config = load_config()
    bot = RecruiterBot(config=config)
    asyncio.run(bot.start(config.token))


if __name__ == "__main__":
    main()
