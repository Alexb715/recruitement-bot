from dataclasses import dataclass, field
from typing import Literal

QuestionKind = Literal[
    "text",
    "yes_no",
    "date",
    "choice",
    "multi_choice",
]


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    kind: QuestionKind = "text"
    choices: tuple[str, ...] = ()
    label: str = ""

    def display_label(self) -> str:
        return self.label or self.key.replace("_", " ").title()


DUAL_CLAN_POLICY = (
    "**Dual Clan Policy** — While an active member of Greater Ontario Gaming you are "
    "ALLOWED to be a part of another community. If you wish to dual clan, it is "
    "expected you will be active during our patrol hours starting at 7:00 PM EST. "
    "Being on other servers during our patrol hours will result in warnings, kicks, "
    "or a ban — unless the daily patrol has been canceled for that day."
)

PATROL_TIMES_EXPLAINER = (
    "**Patrol times** — Are you able to attend a minimum of 2 patrols a week for "
    "2 hours a patrol? Please be aware that failure to meet minimum patrol without "
    "notice for 2 weeks can result in removal."
)

AGREEMENT_STATEMENT_1 = (
    "I understand the Rules and Regulations put forth by Greater Ontario Gaming, "
    "and agree to follow and abide by these rules to the best of my ability."
)
AGREEMENT_STATEMENT_2 = (
    "The Information you have given during this interview is up to date, complete "
    "and honest to the best of your knowledge and belief."
)


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="rules_agreement",
        label="Read and agree to GOG rules and expectations",
        prompt="Have you read and agree to the GOG rules and expectations? (yes/no)",
        kind="yes_no",
    ),
    Question(
        key="knows_code",
        label="Knows the code from the rules",
        prompt="What is the code found in the rules? Do you know it? (yes/no)",
        kind="yes_no",
    ),
    Question(
        key="community_name",
        label="First initial + last name in community",
        prompt=(
            "What is the first initial and last name you wish to go by in the "
            "community? (e.g. `J. Smith`)"
        ),
        kind="text",
    ),
    Question(
        key="date_of_birth",
        label="Date of Birth",
        prompt="What is your Date of Birth? Please use the format `YYYY-MM-DD`.",
        kind="date",
    ),
    Question(
        key="department",
        label="Department(s) interested in",
        prompt=(
            "What Department(s) are you interested in joining? Pick one or more "
            "by number, comma-separated (e.g. `1,3`)."
        ),
        kind="multi_choice",
        choices=("OPP", "Fire/EMS", "Civilian Ops", "Communications"),
    ),
    Question(
        key="dual_clan_active",
        label="Active in other FiveM communities",
        prompt=(
            f"{DUAL_CLAN_POLICY}\n\n"
            "Are you actively a member of any other FiveM affiliated communities? (yes/no)"
        ),
        kind="yes_no",
    ),
    Question(
        key="dual_clan_agreement",
        label="Agrees to Dual Clan Policy",
        prompt="Do you understand and agree to our Dual Clan Policy? (yes/no)",
        kind="yes_no",
    ),
    Question(
        key="respect_definition",
        label="Definition of respect",
        prompt="Define \"respect\" in your own words as best you can.",
        kind="text",
    ),
    Question(
        key="rp_strengths",
        label="FiveM RP strengths",
        prompt="When it comes to FiveM roleplay, what are your greatest strengths?",
        kind="text",
    ),
    Question(
        key="rp_weaknesses",
        label="FiveM RP weaknesses",
        prompt="When it comes to FiveM roleplay, what are your greatest weaknesses?",
        kind="text",
    ),
    Question(
        key="chain_of_command_explanation",
        label="How chain of command operates",
        prompt="Explain how a chain of command operates.",
        kind="text",
    ),
    Question(
        key="follow_chain_of_command",
        label="Willing to follow Chain of Command",
        prompt="Are you willing to follow the Chain of Command? (yes/no)",
        kind="yes_no",
    ),
    Question(
        key="follow_orders_regardless_of_age",
        label="Follow orders regardless of ranking member's age/experience",
        prompt=(
            "Are you willing to follow orders even if the ranking members are "
            "younger than yourself or you may perceive them as less experienced? (yes/no)"
        ),
        kind="yes_no",
    ),
    Question(
        key="patrols_per_week",
        label="Patrols committed per week",
        prompt=(
            f"{PATROL_TIMES_EXPLAINER}\n\n"
            "How many patrols do you think you can commit to per week on average?"
        ),
        kind="text",
    ),
    Question(
        key="failrp_response",
        label="Response to FailRP",
        prompt="In your own words, what would you do in the event of FailRP?",
        kind="text",
    ),
    Question(
        key="new_life_rule",
        label="New Life Rule explanation",
        prompt="Describe the New Life Rule in your own words as best you can.",
        kind="text",
    ),
    Question(
        key="goals_in_gorp",
        label="Goals in GORP",
        prompt=(
            "What goals do you plan to achieve in Greater Ontario Gaming? "
            "(This includes Sub-division, Media, and Ranking.)"
        ),
        kind="text",
    ),
    Question(
        key="found_gorp_via",
        label="How they found GORP",
        prompt="How did you find GORP? Pick one by number.",
        kind="choice",
        choices=(
            "Instagram",
            "Facebook",
            "FiveM Forum",
            "Reddit",
            "YouTube",
            "TikTok",
            "Other",
        ),
    ),
    Question(
        key="agreement_rules",
        label="Agreement to Rules and Regulations",
        prompt=f"{AGREEMENT_STATEMENT_1}\n\nDo you agree with this statement? (yes/no)",
        kind="yes_no",
    ),
    Question(
        key="agreement_honesty",
        label="Agreement that info is honest",
        prompt=f"{AGREEMENT_STATEMENT_2}\n\nDo you agree with this statement? (yes/no)",
        kind="yes_no",
    ),
)


def by_key(key: str) -> Question:
    for q in QUESTIONS:
        if q.key == key:
            return q
    raise KeyError(key)
