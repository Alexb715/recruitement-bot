# GORP Recruiter Bot

A Discord bot that runs the **Greater Ontario Gaming** recruitment interview entirely inside Discord DMs. It replaces the old Google Form: applicants are walked through the questionnaire one question at a time, answers are validated, and the completed application is delivered to a staff channel and stored in a local SQLite database for later review.

---

## Features

### Auto-join DM (the bot reaches out automatically)

The moment a new member joins your Discord server, the bot **automatically sends them a private welcome DM**. The DM contains:

- A short greeting from Greater Ontario Gaming.
- A brief explanation of the recruitment process and how long it takes.
- A green **Start Interview** button that the new member can click whenever they're ready.
- A reminder that they can type `apply` to begin or `cancel` / `restart` once the interview is underway.

The new member does not need to find the bot, run any command, or know any keywords - the bot finds them. If the user has DMs from server members disabled, the bot logs a warning and silently skips them; nothing breaks, and the user can still kick off the interview by clicking the channel button (below) or DMing the bot with `apply`.

This is implemented by the `on_member_join` listener in `cogs/interview.py`. To disable auto-join (e.g. if you'd rather rely only on the channel button), comment out or remove that listener.

### "Apply Here" persistent button

There are two ways to get this button into a channel:

1. **Auto-post on startup (recommended).** Set `APPLY_CHANNEL_ID` in `.env` to the target channel's ID. The bot will automatically post the embed + button in that channel the first time it starts, remember the message ID in SQLite, and **reuse the same message across restarts** - no duplicates. If you (or staff) delete the message, the bot reposts a fresh one the next time it starts. Move it by changing `APPLY_CHANNEL_ID` and restarting.
2. **Manually via slash command.** Leave `APPLY_CHANNEL_ID` blank and run **`/post-apply-message`** in any channel (Manage Server permission required). The bot posts the embed + button right there.

The button itself is *persistent* either way: clicks keep working across bot restarts thanks to the registered `ApplyView` (`bot.py` calls `bot.add_view(ApplyView())` at startup).

### DM interview flow

Once started, the bot asks the applicant **20 questions, one at a time**, in DMs. The order matches the original Google Form (the Discord User ID / Discord info questions are skipped because the bot autofills them), with Rod's two additions (`What are your greatest strengths?` / `What are your greatest weaknesses?` in FiveM RP) inserted immediately after the **"Define respect"** question.

The bot validates each answer based on its type:

- **Yes/No** answers accept `yes`, `y`, `yeah`, `no`, `n`, `nope`, etc.
- **Date of Birth** must be in `YYYY-MM-DD` format (a few other common formats are also accepted).
- **Department** and **How did you find GORP?** are presented as numbered lists - applicants reply with the option number(s).
- **Free-text** questions accept anything non-empty.

If an answer fails validation, the bot explains what went wrong and re-asks the same question.

Mid-interview controls (case-insensitive):

| Command | Effect |
|---------|--------|
| `restart` | Discards prior answers and starts the interview from question 1. |
| `cancel` | Aborts the interview. Nothing is saved. The applicant can type `apply` later to begin again. |

If the applicant goes idle for **30 minutes**, the session expires automatically and the bot sends them a notice.

### Auto-fill identity

The original Google Form asked the recruiter to copy and paste the applicant's Discord User ID and Discord tag. Because the bot is *running in Discord*, it already knows these - so it fills them in automatically and never asks. They still appear in the final application embed and in the saved record.

### Results delivery

When the applicant submits their final answer the bot:

1. Writes a row to `data/applications.db` (SQLite, see schema in `db.py`).
2. Posts a green **New Application** embed to the channel configured by `RESULTS_CHANNEL_ID`, with one field per question.
3. Pings the role configured by `RECRUITER_ROLE_ID` so staff are notified.
4. DMs the applicant a confirmation that their interview is complete.

---

## Setup

### 1. Create the Discord application

1. Go to <https://discord.com/developers/applications> and create a new application.
2. Under **Bot**, add a bot and copy the **Token** - this is your `DISCORD_TOKEN`.
3. **Enable the following Privileged Gateway Intents on the Bot page** (this is the most common setup mistake; without these, auto-join and DM answers will not work):
   - `SERVER MEMBERS INTENT` - required for `on_member_join`.
   - `MESSAGE CONTENT INTENT` - required to read DM answers.
4. Under **OAuth2 → URL Generator**, select scopes `bot` and `applications.commands`. Bot permissions needed:
   - Read Messages / View Channels
   - Send Messages
   - Send Messages in Threads
   - Embed Links
   - Read Message History
   - Mention `@everyone`, `@here`, and All Roles (needed to ping the recruiter role)
5. Use the generated URL to invite the bot to your server.

### 2. Install and configure

```bash
git clone <this-repo>
cd Recruiter
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Open .env and fill in DISCORD_TOKEN, RESULTS_CHANNEL_ID, RECRUITER_ROLE_ID.
```

### 3. Run

```bash
python bot.py
```

You should see `Logged in as <bot> (id=...)` and `Synced N slash command(s)`. Slash commands can take up to an hour to appear globally; set `DEV_GUILD_ID` in `.env` to have them appear instantly in a single test server.

### 4. Post the Apply button (optional)

In the channel where you want the button to live, run:

```
/post-apply-message
```

That's it - the button persists across restarts.

---

## Configuration reference

All configuration lives in `.env` at the project root.

| Variable | Required | What it does | Example |
|----------|----------|--------------|---------|
| `DISCORD_TOKEN` | yes | Bot token from the Developer Portal. | `MTA...` |
| `RESULTS_CHANNEL_ID` | yes | Numeric ID of the channel that receives the completed application embeds. | `1234567890` |
| `RECRUITER_ROLE_ID` | yes | Numeric ID of the role pinged on every new application. | `1234567890` |
| `APPLY_CHANNEL_ID` | no | If set, the bot auto-posts the Apply Here button in this channel on startup and reuses the same message across restarts. Leave blank to disable auto-post (use `/post-apply-message` manually instead). | `1234567890` |
| `DEV_GUILD_ID` | no | If set, slash commands sync only to this guild (instant). Leave blank for global sync. | `1234567890` |
| `DB_PATH` | no | Relative or absolute path for the SQLite database. | `data/applications.db` (default) |

To find a channel/role ID: enable **Developer Mode** in Discord (Settings → Advanced), then right-click the channel/role → **Copy ID**.

---

## Admin commands

| Command | Permission | What it does |
|---------|------------|--------------|
| `/post-apply-message` | Manage Server | Posts the persistent Apply Here embed + button in the current channel. |

---

## Querying past applications

Each completed interview is stored as a single row in `data/applications.db`. To list recent applications:

```bash
sqlite3 data/applications.db \
  "SELECT id, user_id, username, completed_at, status FROM applications ORDER BY completed_at DESC LIMIT 20;"
```

To pull a full application including all answers:

```bash
sqlite3 data/applications.db \
  "SELECT answers_json FROM applications WHERE id = 1;" | python -m json.tool
```

---

## Troubleshooting

**Auto-join DMs aren't arriving.**
Check, in order: (1) `SERVER MEMBERS INTENT` is enabled in the Developer Portal; (2) the bot is actually in the server; (3) the joining user has *Allow direct messages from server members* enabled for that server. The bot logs a warning when it can't DM someone.

**Applicants click Start Interview but nothing happens.**
The bot needs `MESSAGE CONTENT INTENT` enabled to read their answers. Also confirm the bot can send DMs to the user (same privacy setting as above).

**The Apply button stopped working after I restarted the bot.**
The persistent view should be registered automatically at startup (`bot.add_view(ApplyView())` in `bot.py`). If you customized that line, make sure it's still being called before login.

**`/post-apply-message` doesn't appear in the slash menu.**
Global slash sync can take up to an hour. For instant iteration, set `DEV_GUILD_ID` in `.env` to your test server's ID.

**The completed embed isn't posted but the database row exists.**
Likely the configured `RESULTS_CHANNEL_ID` is wrong or the bot can't see that channel. Check the bot has Read/Send Messages there and that the ID is correct.

---

## Project layout

```
Recruiter/
├── bot.py              Entry point: intents, cog loading, slash sync.
├── config.py           Loads .env into a typed Config.
├── db.py               SQLite schema + save/list/get helpers.
├── questions.py        Single source of truth for the question list.
├── session.py          Per-applicant state machine + input validation.
├── cogs/
│   ├── interview.py    Auto-join DM, Apply button view, DM message routing, completion flow.
│   └── admin.py        /post-apply-message slash command.
├── requirements.txt
├── .env.example
└── data/
    └── applications.db (created on first run; gitignored)
```

To add, remove, or reorder questions, edit only `questions.py`.
