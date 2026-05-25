from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from questions import QUESTIONS, Question

YES_TOKENS = {"y", "yes", "yeah", "yep", "yup", "ye", "true"}
NO_TOKENS = {"n", "no", "nope", "nah", "false"}

AUTO_FIELDS = ("discord_user_id", "discord_information")


class InterviewSession:
    def __init__(self, user_id: int, username: str, display_name: str) -> None:
        self.user_id = user_id
        self.username = username
        self.display_name = display_name
        self.started_at = datetime.now(timezone.utc)
        self.last_activity = self.started_at
        self.index = 0
        self.answers: dict[str, Any] = {}
        self._fill_identity()

    def _fill_identity(self) -> None:
        self.answers["discord_user_id"] = str(self.user_id)
        self.answers["discord_information"] = self.username

    def touch(self) -> None:
        self.last_activity = datetime.now(timezone.utc)

    def current_question(self) -> Question | None:
        if self.index >= len(QUESTIONS):
            return None
        return QUESTIONS[self.index]

    def is_complete(self) -> bool:
        return self.index >= len(QUESTIONS)

    def restart(self) -> None:
        self.index = 0
        self.answers = {}
        self.started_at = datetime.now(timezone.utc)
        self.touch()
        self._fill_identity()

    def submit_answer(self, raw: str) -> str | None:
        """Validate and store the answer. Returns an error message on rejection,
        or None on success (and advances the index)."""
        question = self.current_question()
        if question is None:
            return "The interview is already complete."

        self.touch()
        validated, error = _validate(question, raw)
        if error is not None:
            return error
        self.answers[question.key] = validated
        self.index += 1
        return None

    def to_record(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "started_at": self.started_at,
            "completed_at": datetime.now(timezone.utc),
            "answers": dict(self.answers),
        }


def _validate(question: Question, raw: str) -> tuple[Any, str | None]:
    text = raw.strip()
    if not text:
        return None, "Please type a response — empty messages aren't accepted."

    if question.kind == "text":
        return text, None

    if question.kind == "yes_no":
        token = text.lower()
        if token in YES_TOKENS:
            return "Yes", None
        if token in NO_TOKENS:
            return "No", None
        return None, "Please answer with `yes` or `no`."

    if question.kind == "date":
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                return parsed.isoformat(), None
            except ValueError:
                continue
        return None, "Please use the date format `YYYY-MM-DD` (e.g. `2001-06-15`)."

    if question.kind == "choice":
        return _parse_indices(question.choices, text, multi=False)

    if question.kind == "multi_choice":
        return _parse_indices(question.choices, text, multi=True)

    return None, f"Unsupported question type: {question.kind}"


def _parse_indices(
    choices: tuple[str, ...], raw: str, *, multi: bool
) -> tuple[Any, str | None]:
    if not choices:
        return None, "This question has no choices configured."

    tokens = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    if not tokens:
        return None, _choice_prompt(choices, multi)

    selected: list[str] = []
    for token in tokens:
        if not token.isdigit():
            return None, f"`{token}` isn't a valid number. {_choice_prompt(choices, multi)}"
        idx = int(token)
        if idx < 1 or idx > len(choices):
            return None, f"`{idx}` is out of range. {_choice_prompt(choices, multi)}"
        value = choices[idx - 1]
        if value not in selected:
            selected.append(value)

    if not multi and len(selected) != 1:
        return None, "Pick exactly one option by its number."

    return (selected if multi else selected[0]), None


def _choice_prompt(choices: tuple[str, ...], multi: bool) -> str:
    lines = [f"`{i + 1}.` {c}" for i, c in enumerate(choices)]
    listing = "\n".join(lines)
    hint = (
        "Reply with one or more numbers separated by commas (e.g. `1,3`)."
        if multi
        else "Reply with the number of your choice (e.g. `2`)."
    )
    return f"**Options:**\n{listing}\n\n{hint}"


def choice_prompt(question: Question) -> str:
    return _choice_prompt(question.choices, multi=question.kind == "multi_choice")
