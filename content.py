"""Static text and embed builders for the bot's permanent informational
messages (the FAQ and Requirements channels).

This is the single source of truth for that prose. Channel and role references
in the FAQ are injected at call time from the loaded Config, so the embeds must
be built by these functions rather than as module-level constants (the values
aren't known until `.env` is loaded).
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)

EMBED_DESCRIPTION_LIMIT = 4096


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
        "long as they have TeamSpeak and access to our CAD system, they're good "
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


def build_requirements_embed() -> discord.Embed:
    """Build the Requirements embed. No dynamic mentions, so it takes no config
    (mirrors `build_apply_embed`)."""
    description = "\n".join(f"- {item}" for item in REQUIREMENTS_ITEMS)
    return discord.Embed(
        title="GORP Requirements",
        description=description,
        color=discord.Color.blurple(),
    )
