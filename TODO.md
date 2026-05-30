# TODO — Recruitment Bot

Follow-up work deferred from the Accept/Reject feature rollout.

## Per-department orientation content

`content.py` ships placeholder text per department (`DEPARTMENT_ORIENTATION`).
Staff need to replace each block with real info:

- **OPP** — point-of-contact role, training/ride-along channel, sub-division list.
- **Fire/EMS** — point-of-contact role, onboarding channel, probationary shift schedule.
- **Civilian Ops** — point-of-contact role, civ-ops channel, character-approval flow.
- **Communications** — point-of-contact role, dispatch/comms channel, CAD training docs, TeamSpeak rooms.

If we want clickable channel/role mentions, mirror the FAQ pattern: add new
optional env vars in `config.py`, pass `config` into `build_acceptance_dm`,
and `.format(**mentions)` each block.

## Auto role grant on accept

Bot currently has no role management. To gate the server behind acceptance:

- Decide on the role transition (e.g. add "Member", remove "Applicant").
- Add `MEMBER_ROLE_ID` / `APPLICANT_ROLE_ID` env vars.
- Add `manage_roles` permission to the bot's role.
- In `handle_accept`, after recording the decision, call
  `member.add_roles(...)` / `member.remove_roles(...)`.

## Audit log

Optional: post a one-line audit message ("App #42 accepted by @alex") to a
dedicated channel so staff can see decisions at a glance without scrolling the
applications channel.

## Reject → kick?

Decide whether rejected applicants should be kicked from the server, kept with
limited access, or left alone. Currently rejection only DMs them and blocks
reapplication for 14 days; they keep whatever server access they had.

## Reapply embargo override

If staff ever want to let someone reapply early, there's no UI for it today.
Options: a `/clear-rejection <user>` slash command, or a button on the
rejected embed.
