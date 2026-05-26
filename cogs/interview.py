from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from db import save_application
from questions import QUESTIONS, Question
from session import InterviewSession, choice_prompt

logger = logging.getLogger(__name__)

IDLE_TIMEOUT = timedelta(minutes=1)
TIMEOUT_CHECK_SECONDS = 15

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


class InterviewCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        results_channel_id: int,
        recruiter_role_id: int,
        db_path: Path,
    ) -> None:
        self.bot = bot
        self.results_channel_id = results_channel_id
        self.recruiter_role_id = recruiter_role_id
        self.db_path = db_path
        self.sessions: dict[int, InterviewSession] = {}
        self.expire_idle_sessions.start()

    def cog_unload(self) -> None:
        self.expire_idle_sessions.cancel()

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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
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

    async def start_for_user(self, user: discord.abc.User) -> None:
        if user.id in self.sessions:
            await user.send(
                "You already have an interview in progress. Type `restart` to "
                "start over, or `cancel` to stop."
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
        try:
            save_application(
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
            await self._post_results_embed(record)
        except Exception:
            logger.exception("Failed to post results embed for %s", user.id)

        self.sessions.pop(user.id, None)
        await user.send(
            "**Interview complete.** Your application has been submitted to "
            "the GORP staff for review. You'll hear back soon - thank you."
        )

    async def _post_results_embed(self, record: dict) -> None:
        channel = self.bot.get_channel(self.results_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.results_channel_id)
        embed = self._build_embed(record)
        content = (
            f"<@&{self.recruiter_role_id}> "
            f"New application from <@{record['user_id']}>"
        )
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                roles=True, users=False, everyone=False
            ),
        )

    def _build_embed(self, record: dict) -> discord.Embed:
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
        embed.set_footer(text=f"Started {record['started_at'].isoformat()}")
        return embed
