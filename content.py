"""Static text and embed builders for the bot's permanent informational
messages (the FAQ and Requirements channels).

This is the single source of truth for that prose. Channel and role references
in the FAQ are injected at call time from the loaded Config, so the embeds must
be built by these functions rather than as module-level constants (the values
aren't known until `.env` is loaded).
"""

from __future__ import annotations

import logging
from datetime import datetime

import discord

logger = logging.getLogger(__name__)

EMBED_DESCRIPTION_LIMIT = 4096

REAPPLY_EMBARGO_DAYS = 14


def _channel_mention(channel_id: int | None, fallback: str) -> str:
    """Render a clickable channel mention, or a plain-text fallback when the id
    is unset so the FAQ never shows a broken `<#None>`."""
    return f"<#{channel_id}>" if channel_id else fallback


def _role_mention(role_id: int | None, fallback: str) -> str:
    return f"<@&{role_id}>" if role_id else fallback


# Each entry is (question, answer). Answers may contain named placeholders that
# are filled from the config at call time. Order matches the published FAQ.
FAQ_ENTRIES: tuple[tuple[str, str], ...] = (
    (
        "How do I apply?",
        "Getting started is easy! Head to {interview_request} to begin. You can "
        "submit a written application or request a voice interview. Once you "
        "submit, our recruitment team is automatically notified and someone will "
        "reach out to you.",
    ),
    (
        "What departments are open?",
        "All of our primary departments are open. They don't close!",
    ),
    (
        "Who do I contact after applying?",
        "Nobody! A recruitment team member has already been pinged with your "
        "response. One of the team members will reach out to you shortly.",
    ),
    (
        "What if I need assistance?",
        "You can reach out to our {support_role} and someone will assist you!",
    ),
    (
        "Can I check out the server before I apply?",
        "We're sorry, but the only way to access our server is to complete your "
        "application.",
    ),
    (
        "How old do I have to be?",
        "Our minimum age requirement is 16 years old.",
    ),
    (
        "Do I have to be from Canada?",
        "No! Our server is open to anyone who can read and write in English.",
    ),
    (
        "What is the dual clan policy?",
        "Our dual clan policy can be found in our rule book. It can be found in "
        "the {rules} section of our Discord, Article I, Section I.",
    ),
    (
        "How do promotions work?",
        "Promotions are given to members who do well in their departments. Staff "
        "members are also always watching for members who go above and beyond, "
        "both in the server and in our Discord!",
    ),
    (
        "What subdivision can I join?",
        "The Ontario Provincial Police have multiple sub-divisions! If you're "
        "interested, you can check out {opp_recruitment} for more information.",
    ),
    (
        "Can I do a written exam?",
        "As of June 2026, written interviews are available for applicants. Please "
        "note that you may be summoned by a recruiter for a voice chat regarding "
        "your written application, so please ensure you have a working microphone. "
        "Recruiters will still be conducting verbal interviews, so be sure to look "
        "out for interview sessions!",
    ),
    (
        "What if I'm unhappy and want to change my current department? How do I switch?",
        "We have department transfer forms available for all of our departments! "
        "If you're no longer interested in a certain department, fill one out and "
        "a staff member will reach out to you.",
    ),
    (
        "Do I need to have a PC?",
        "Yes, GORP is a FiveM based server which is strictly for PC players. "
        "However, our communications department doesn't have to be in game! As "
        "long as they have Sonoran Radio and access to our CAD system, they're good "
        "to go.",
    ),
    (
        "Is the server public?",
        "No, Greater Ontario Roleplay is a whitelisted server.",
    ),
    (
        "How long do written applications take to be processed?",
        "While processing time may vary, we aim to process applications within "
        "48 hours of submission.",
    ),
    (
        "Do you have RCMP? Can I RP as RCMP?",
        "GORP is an Ontario based community, and while the RCMP does have "
        "detachments in Ontario, these are used for large federal investigations "
        "and other duties that do not involve day to day general patrols or "
        "responding to calls for service. Ontario has its own provincial police "
        "service, the Ontario Provincial Police, which provides policing services "
        "to towns and municipalities that don't have their own municipal police "
        "service, as well as assisting municipal police services as needed. As in "
        "real life, we don't offer RCMP as a functional department in GORP, as "
        "that would be impractical.",
    ),
)


REQUIREMENTS_ITEMS: tuple[str, ...] = (
    "Be 16 years of age or older.",
    "Have a working, legal copy of GTA5 for PC and FiveM installed. We are a "
    "PC-only community. (Dispatch has some minor exceptions.)",
    "Have a decent, working microphone.",
    "Have a mature, positive, and open attitude.",
    "Be accepting, open, and respectful of all members. NO EXCEPTIONS!",
    "PATIENCE. Our recruitment team is small and may take some time to get "
    "interviews set up, but we always try to host interview sessions at least "
    "ONCE a day!",
    "GORP requires you to be active for at least 2 full patrols a week "
    "(4 hours minimum).",
)


def build_faq_embed(config) -> discord.Embed:
    """Build the FAQ embed, injecting clickable channel/role mentions from the
    config. Role/channel mentions inside an embed description render as clickable
    links but do not produce notifications, so this is safe to post."""
    mentions = {
        "interview_request": _channel_mention(
            config.faq_interview_request_channel_id, "the interview request channel"
        ),
        "rules": _channel_mention(config.faq_rules_channel_id, "the rules channel"),
        "opp_recruitment": _channel_mention(
            config.faq_opp_recruitment_channel_id, "the OPP recruitment channel"
        ),
        "support_role": _role_mention(
            config.faq_support_role_id, "recruitment team"
        ),
    }

    blocks = [
        f"❓ **{question}**\n- *{answer.format(**mentions)}*"
        for question, answer in FAQ_ENTRIES
    ]
    description = "\n\n".join(blocks)

    if len(description) > EMBED_DESCRIPTION_LIMIT:
        logger.warning(
            "FAQ embed description is %d chars, over Discord's %d limit; it will "
            "need to be split across multiple embeds.",
            len(description),
            EMBED_DESCRIPTION_LIMIT,
        )

    return discord.Embed(
        title="Frequently Asked Questions",
        description=description,
        color=discord.Color.blurple(),
    )


ACCEPTANCE_DM_INTRO = (
    "**Welcome to Greater Ontario Roleplay!** 🎉\n"
    "Your application has been **accepted**. Here's a quick orientation so you "
    "know how the community works:\n\n"
    "**How we patrol**\n"
    "- We run **scheduled patrols** - keep an eye on the patrol-schedule channel "
    "for upcoming sessions.\n"
    "- You're expected to be active for at least **2 full patrols a week** "
    "(4 hours minimum).\n"
    "- Patrols are coordinated in Discord and over Sonoran Radio.\n\n"
    "**Important channels to check**\n"
    "- Announcements & rule updates\n"
    "- Patrol schedule\n"
    "- Your department's main channel (see below)\n"
    "- Support / help if you ever get stuck\n\n"
    "*(Real channel links will be shared with you in-server.)*"
)

# Per-department orientation. Keys must match the choice strings in
# questions.py's `department` question exactly. These are intentionally
# placeholder copies for the initial rollout - real point-of-contact roles,
# channel IDs, and training schedules to be filled in by staff (see TODO.md).
DEPARTMENT_ORIENTATION: dict[str, str] = {
    "OPP": (
        "**Next steps - OPP**\n"
        "- Your points of contact are **Sgt M. Allain** and **Sgt L. Rozsas** "
        "- reach out to them with any questions.\n"
        "- Recruit training & ride-along signup: "
        "https://discord.com/channels/951942783147606116/1070818727681081414\n"
        "- Opportunities within the OPP can be found here upon promotion to "
        "**Constable III**: "
        "https://discord.com/channels/951942783147606116/951942783722213390"
    ),
    "Fire/EMS": (
        "**Next steps - Fire/EMS**\n"
        "- Your point of contact is **J. Flood** - reach out with any "
        "questions.\n"
        "- Onboarding: "
        "https://discord.com/channels/592770866044207104/1083598448600809535 "
        "and "
        "https://discord.com/channels/592770866044207104/1197057854646005811"
    ),
    "Civilian Ops": (
        "**Next steps - Civilian Ops**\n"
        "- Your point of contact is **T. Smith** - reach out with any "
        "questions.\n"
        "- Civ Ops channel: "
        "https://discord.com/channels/592770866044207104/974154883164934204\n"
        "- Character approval: "
        "https://discord.com/channels/592770866044207104/974155126644301824"
    ),
    "Communications": (
        "**Next steps - Communications**\n"
        "- Your point of contact is <@1449855114394210484> - reach out with "
        "any questions.\n"
        "- A dedicated comms channel is coming soon.\n"
        "- We use **Sonoran Radio** (not TeamSpeak) for comms - setup details "
        "will be shared with you shortly."
    ),
}

REJECTION_DM_BODY = (
    "**Application update**\n"
    "Thanks for your interest in Greater Ontario Roleplay. After reviewing your "
    "application, we've decided not to move forward at this time.\n\n"
    "You're welcome to reapply on {reapply_date} "
    "(14 days from this decision). We hope to see another application from you "
    "then."
)


def build_acceptance_dm(
    departments: list[str],
    *,
    main_invite_url: str | None = None,
    opp_invite_url: str | None = None,
) -> str:
    """Build the acceptance DM body: fixed orientation intro, a block for each
    department the candidate selected, then a "join us" section listing whatever
    server invite links were created. Unknown department strings are skipped
    with a log warning (e.g. if the questions list changes but the orientation
    copy doesn't)."""
    parts: list[str] = [ACCEPTANCE_DM_INTRO]
    seen: set[str] = set()
    department_blocks = 0
    for dept in departments or []:
        if dept in seen:
            continue
        seen.add(dept)
        block = DEPARTMENT_ORIENTATION.get(dept)
        if block is None:
            logger.warning(
                "No DEPARTMENT_ORIENTATION entry for %r; skipping in DM.", dept
            )
            continue
        parts.append(block)
        department_blocks += 1
    if department_blocks == 0:
        parts.append(
            "*(No department-specific next steps available - a recruiter will "
            "reach out directly.)*"
        )

    invite_lines: list[str] = []
    if main_invite_url:
        invite_lines.append(f"- Main community server: {main_invite_url}")
    if opp_invite_url:
        invite_lines.append(f"- OPP division server: {opp_invite_url}")
    if invite_lines:
        parts.append(
            "**Join us in-server**\n"
            "Use the link(s) below to hop into the right Discord server(s):\n"
            + "\n".join(invite_lines)
        )
    return "\n\n".join(parts)


def build_rejection_dm(reapply_at: datetime, reason: str | None) -> str:
    """Build the rejection DM body. `reapply_at` is rendered as a Discord
    timestamp so it localizes to each viewer's timezone; optional `reason` is
    appended verbatim when staff provided one."""
    timestamp = f"<t:{int(reapply_at.timestamp())}:D>"
    body = REJECTION_DM_BODY.format(reapply_date=timestamp)
    if reason:
        body = f"{body}\n\n**Note from staff:** {reason}"
    return body


def build_prospect_ping(role_id: int) -> str:
    """The recurring nudge posted to prospects. Mentions the prospect role, so
    the caller must pass allowed_mentions that permit role pings."""
    return (
        f"<@&{role_id}> 👋 Still thinking it over? Your GORP application is only "
        "a few minutes away.\n"
        "DM me `apply` (or use the **Start Interview** button in the apply "
        "channel) to begin or finish your interview - we'd love to have you on "
        "patrol!"
    )


def build_prospect_warning_dm(kick_days: int) -> str:
    """Warning DM sent to an inactive prospect a couple days before removal."""
    return (
        "**Quick heads-up from Greater Ontario Roleplay**\n"
        "We noticed you joined but haven't been active yet. To keep our "
        f"recruitment server tidy, prospective members inactive for {kick_days} "
        "days are automatically removed.\n\n"
        "You'll be removed in about **2 days** unless you become active - just "
        "send a message in the server or DM me `apply` to start your interview. "
        "Hope to see you around!"
    )


def build_prospect_kick_dm(kick_days: int) -> str:
    """Notice DM sent right before an inactive prospect is removed."""
    return (
        "**Greater Ontario Roleplay**\n"
        f"You've been removed from our recruitment server after {kick_days} "
        "days without activity - no hard feelings, this just keeps things "
        "tidy.\n\n"
        "You're welcome to rejoin anytime and start your application whenever "
        "you're ready. Hope to see you again!"
    )


def build_requirements_embed() -> discord.Embed:
    """Build the Requirements embed. No dynamic mentions, so it takes no config
    (mirrors `build_apply_embed`)."""
    description = "\n".join(f"- {item}" for item in REQUIREMENTS_ITEMS)
    return discord.Embed(
        title="GORP Requirements",
        description=description,
        color=discord.Color.blurple(),
    )
