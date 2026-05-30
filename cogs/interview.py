from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from content import (
    REAPPLY_EMBARGO_DAYS,
    build_acceptance_dm,
    build_rejection_dm,
)
from db import (
    get_application,
    latest_rejection_for_user,
    save_application,
    set_application_decision,
)
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


# --- Decision buttons (Accept / Reject on staff-channel application embeds) ---
#
# These use DynamicItem so a single class handles every application_id baked
# into the custom_id. On startup the bot registers the two classes via
# `add_dynamic_items`, which lets the buttons survive bot restarts without
# tracking each message individually.

ACCEPT_CUSTOM_ID_TEMPLATE = r"gorp:accept_app:(?P<app_id>\d+)"
REJECT_CUSTOM_ID_TEMPLATE = r"gorp:reject_app:(?P<app_id>\d+)"


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
        embed = self._build_embed(record)
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
        await channel.send(**kwargs)

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
        departments = app.get("answers", {}).get("department", []) or []
        if isinstance(departments, str):
            departments = [departments]
        dm_body = build_acceptance_dm(list(departments))
        dm_status = await self._dm_applicant(applicant_id, dm_body)

        await self._finalize_decision_embed(
            interaction.message,
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
            status="rejected",
            color=discord.Color.red(),
            prefix=prefix,
            dm_status=dm_status,
        )

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
        status: str,
        color: discord.Color,
        prefix: str,
        dm_status: str,
    ) -> None:
        """Edit the original staff message: recolor the embed, prepend the
        decision banner to the description, append DM status to the footer, and
        strip the buttons so the decision can't be reclicked."""
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
            await message.edit(embed=new, view=None)
        except Exception:
            logger.exception("Failed to update decision embed")

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
