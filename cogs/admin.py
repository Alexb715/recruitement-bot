from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.interview import ApplyView, build_apply_embed
from content import build_faq_embed, build_requirements_embed

logger = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="post-apply-message",
        description="Post the persistent 'Apply Here' button in this channel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def post_apply_message(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Run this in a regular text channel or thread.", ephemeral=True
            )
            return

        await interaction.channel.send(embed=build_apply_embed(), view=ApplyView())
        await interaction.response.send_message(
            "Posted the Apply Here message.", ephemeral=True
        )

    @app_commands.command(
        name="post-faq",
        description="Post the FAQ message in this channel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def post_faq(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Run this in a regular text channel or thread.", ephemeral=True
            )
            return

        await interaction.channel.send(
            embed=build_faq_embed(self.bot.config),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.response.send_message(
            "Posted the FAQ message.", ephemeral=True
        )

    @app_commands.command(
        name="post-requirements",
        description="Post the Requirements message in this channel.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def post_requirements(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Run this in a regular text channel or thread.", ephemeral=True
            )
            return

        await interaction.channel.send(embed=build_requirements_embed())
        await interaction.response.send_message(
            "Posted the Requirements message.", ephemeral=True
        )
