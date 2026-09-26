# Server Setup Bot

## What it does
- `/setup_server` — creates the full category/channel layout: Server Info, Donations, Chats, Support, a locked **Female Verification** category (verifier chat + waiting/verify voice channels), a locked Staff category (now including `#server-logs`), a **📊 Server Stats** voice-only category, and a separate category per ticket type plus a locked `🎫 Ticket Logs` category.
- **`/fix-channels`** — a one-time cleanup command for your **existing** server. It only renames things — it never creates or deletes anything, so it's safe to run on a live server without colliding with what's already there:
  - Fixes 5 category names (typos + inconsistent "Dream"/"Dreams RP" branding) via `CATEGORY_RENAMES`.
  - Renames `#staff` → `#staff-general` and `#tickets` → `#ticket-hub` via `CHANNEL_RENAMES` (so it lines up with what the ticket panel code looks for).
  - Adds an emoji to any channel that doesn't already start with one — most of your channels already have one, so it only touches the handful that don't (e.g. `staff-rules`, `in-city-rules`). It never touches channels that already have an emoji, including ones using a leading `.` for sort order.
  - All three of these dicts (`CATEGORY_RENAMES`, `CHANNEL_RENAMES`, `EMOJI_KEYWORDS`) are plain Python dicts near the top of `bot.py` — edit them and re-run the command any time you want to rename more things.
  - Runs in the background and posts a summary (with every rename it made) once it's done, so it won't time out on a server with 100+ channels.
- **Rules-agree gate** — with `GATE_ENABLED=true` (default), everything except `⭐ Server Info` is hidden from new members until they click **I Agree** on the embed posted in `#rules` (post it manually anytime with `/rules-gate`). Clicking it grants the `Member` role, which is what unlocks the rest of the server.
- **Self-assign roles** — `/self-roles` posts a dropdown of opt-in ping roles (Crew, Event, Giveaway, Update pings — edit the `SELF_ROLES` dict in `bot.py` to change them). Members pick what they want; picking an already-selected one removes it.
- **Live FiveM server status** — if you set `FIVEM_SERVER_IP` (and optionally `CFX_JOIN_CODE`), the bot posts/updates a live embed in `#connect-code` showing online/offline and player count, refreshed every `STATUS_UPDATE_MINUTES` (default 5). Leave `FIVEM_SERVER_IP` blank and this feature does nothing.
- **Live member-count voice channel** — the first voice channel in `📊 Server Stats` renames itself to `Members: <count>` every 10 minutes (Discord rate-limits channel renames, so it can't go faster than that).
- **Auto-moderation** — deletes messages containing anything in `AUTOMOD_BANNED_WORDS` (comma-separated, empty by default) or Discord invite links (unless posted in a channel listed in `AUTOMOD_EXEMPT_CHANNELS`), and times out anyone sending more than `AUTOMOD_SPAM_LIMIT` messages within `AUTOMOD_SPAM_WINDOW` seconds. Staff are always exempt.
- **Server logs** — every join, leave, message edit, and message delete gets logged to `#server-logs` automatically.
- `/ticket-panel` — post (or re-post) the ticket panel in any channel, on demand. It's a **dropdown**, not a single button, with 5 ticket types: Ban Ticket, Female Verify Ticket, City Report Ticket, Reimburse Ticket, and Higher Ups. Picking one opens a private channel **inside that type's own category**, and its transcript goes to that type's own log channel (`ban-ticket-logs`, `female-verify-logs`, etc.) — add or rename types by editing the `TICKET_TYPES` dict near the top of `bot.py`.
- Ticket system — each ticket has **Claim** (marks who's handling it) and **Close Ticket** (saves a full transcript as a `.txt` file to that type's log channel, then deletes the channel after 5s).
- `/role-all` — give any role to every current member in one command. Runs in the background and posts a summary when it's done, so it won't time out on a big server.
- `/apply` — pops up a modal (age, timezone, RP experience, why they want in). Submissions post to `#applications` with Approve/Deny buttons; Approve grants the `Whitelisted` role and DMs the applicant either way.
- Auto-reactions & auto-threads — any message posted in `#suggestions` automatically gets 👍/👎; any message in `#bug-reports` automatically spins up a thread on it. No command needed for either.
- `/post_image` — post any image, with an optional caption, to any channel.
- `/giveaway start` / `/giveaway end` — button-entry giveaways that auto-pick winners when time's up (checked every 30s in the background, so it works even if nobody's watching).
- `/announce` — staff-only embed announcement to any channel.
- `/suggest` / `/report` — restricted to your `Whitelisted` role; alternative to just posting directly.
- Moderation — `/warn`, `/warnings`, `/kick`, `/ban`, `/unban`, `/timeout`. Warnings are saved to `data/warnings.json`.
- **Anti-nuke** — runs automatically in the background, no command needed. If the same person deletes several channels, deletes several roles, bans several members, or creates a webhook in a short burst (default: 3 actions in 10 seconds — tune with `ANTI_NUKE_MAX_ACTIONS` / `ANTI_NUKE_WINDOW_SECONDS`), the bot times them out for `ANTI_NUKE_TIMEOUT_MINUTES` (default 20) and posts an alert to `#mod-logs`. The server owner and anyone listed in `OWNER_IDS` are always exempt.

Note on Female Verification: the bot only builds the channel scaffolding (a staff-only text channel plus voice rooms for the verification call) — the actual verification is a manual staff process done by voice, same as it would be without a bot. The bot doesn't request, store, or process any photos/selfies as part of this.

The bot's Discord "Playing" status is set from `SERVER_NAME` in `.env` (defaults to "Dreams RP").

Edit the `SERVER_STRUCTURE` dictionary near the top of `bot.py` to add, rename, or remove channels/categories.

## 1. Create the bot
1. Go to https://discord.com/developers/applications → New Application.
2. Bot tab → Add Bot → copy the token (this goes in `.env`, never share it).
3. Under "Privileged Gateway Intents", enable **Server Members Intent** and **Message Content Intent**. Ban/unban events (used by anti-nuke) come from the standard Guild Bans intent, which is on by default.
4. OAuth2 → URL Generator → scopes: `bot`, `applications.commands`. Permissions needed for everything in this bot: **Administrator** is simplest and recommended for anti-nuke to reliably ban/strip roles from anyone; at minimum give Manage Channels, Manage Roles, Manage Guild, Kick Members, Ban Members, Moderate Members, Send Messages, Attach Files, Embed Links, View Audit Log.
5. Use the generated URL to invite the bot to your server.

## 2. Configure
```
cp .env.example .env
```
Fill in `DISCORD_BOT_TOKEN` and your `GUILD_ID` (right-click your server icon → Copy Server ID, with Developer Mode on).

## 3. Install & run locally (for testing)
```
pip install -r requirements.txt
python bot.py
```
Then in Discord run `/setup_server` (needs Administrator).

## Walking through it end-to-end
Once `/setup_server` has run and the bot is online:

1. **Tickets** — go to `#ticket-hub`, pick a type from the dropdown (e.g. Ban Ticket). In the new private channel (created inside the `🎫 Ban Ticket` category), click **Claim** as a staff member, then **Close Ticket** — the transcript lands in `#ban-ticket-logs` as a `.txt` attachment a few seconds later, and the channel deletes itself. Repeat with a different type from the dropdown and you'll see it land in a different category and log channel.
2. **Applications** — run `/apply`, fill out the modal, submit. It posts to `#applications` with Approve/Deny buttons. Click Approve from a staff account — the applicant gets the `Whitelisted` role and a DM.
3. **Suggestions** — just type a message in `#suggestions` (no command needed) — 👍/👎 appear automatically within a second.
4. **Bug reports** — post a message in `#bug-reports` — a thread spins up on it automatically.
5. **Giveaways** — `/giveaway start prize:"Test prize" minutes:1 winners:1` — watch it resolve on its own about a minute later (checked every 30s), picking from whoever clicked Enter.
6. **Role-all** — `/role-all role:@Member` — the bot replies immediately that it's started, then posts a summary in the same channel once every current member has the role.
7. **Rules gate** — from an alt/test account, join the server — you should only see `⭐ Server Info`. Click **I Agree** in `#rules` — everything else should appear immediately.
8. **Self-roles** — run `/self-roles` in any channel, pick a ping role from the dropdown, run it again and pick the same one to confirm it removes it.
9. **Auto-mod** — post a fake `discord.gg/` link in a normal channel — it should get deleted and you'll get a DM about it.
10. **Server logs** — edit or delete a message anywhere — check `#server-logs` for the log entry within a second or two.

**Important if `GATE_ENABLED=true`:** since the gate hides most channels behind the `Member` role, any of your **existing** members who joined before you enabled it won't have that role yet. Run `/role-all role:@Member` once right after `/setup_server` to grandfather everyone in, or they'll only see `⭐ Server Info` until they click Agree themselves.

**Using `/fix-channels` on an existing server:** run it once, read the summary it posts (it lists every rename it made), and double-check nothing looks off before moving on. It's non-destructive and safe to run more than once — anything already renamed just won't match the maps again, so re-running it is a no-op for those.

## 4. Keeping it running 24/7
Running `python bot.py` on your own laptop stops the bot the moment you close the terminal or your computer sleeps. To keep it online all the time, run it on something that stays powered on:

- **A cheap VPS** (e.g. a $4–6/mo droplet/instance): install Python, copy these files, run with `pm2` or a `systemd` service so it restarts automatically if it crashes.
- **Railway.app / Render.com**: connect this code as a repo, set `DISCORD_BOT_TOKEN` etc. as environment variables, deploy as a "worker"/background service.
- **A Raspberry Pi or spare machine** at home, left on.

Avoid free trial platforms that spin down on inactivity (they'll disconnect your bot repeatedly). discord.py itself automatically reconnects on network drops — the only thing that stops it is the host machine itself going offline.

## Notes
- The bot only builds channels that don't already exist by that name, so re-running `/setup_server` is safe.
- The Staff category is locked (`@everyone` denied view) and only visible to whatever role is named in `STAFF_ROLE_NAME` (default `Staff`) — the bot creates that role automatically if it doesn't exist yet.
- **Anti-nuke is reactive, not preventative** — it needs to see a few destructive actions happen (the threshold you set) before it acts, and it relies on the audit log, so the bot's role needs **View Audit Log** permission and must sit above any role you expect it to strip. It catches a compromised account or rogue staff member mid-rampage; it can't undo channels/roles already deleted (Discord has no "undelete"), so pair it with a Server Template/backup you keep up to date if you want fast recovery.
- Give trusted co-owners' IDs in `OWNER_IDS` — anyone not listed there (and not the server owner) is subject to anti-nuke, including regular staff with Manage Channels/Ban Members permissions.
