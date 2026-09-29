"""
Custom Discord Server Bot
--------------------------
Features:
  1. /setup_server    - builds out categories & channels (info, donations, chats, support,
                          female verification, staff, server-stats, and a category per ticket type)
  2. /fix-channels    - one-time cleanup for an EXISTING server: renames categories/channels
                          per CATEGORY_RENAMES/CHANNEL_RENAMES and adds an emoji to any channel
                          that doesn't have one yet. Only renames — never creates or deletes.
  2. Ticket system     - /ticket-panel posts a dropdown with 5 ticket types (Ban, Female
                          Verify, City Report, Reimburse, Higher Ups); each type gets its own
                          category and its own transcript log channel. Tickets have Claim and
                          Close buttons; closing saves a full transcript before deleting.
  3. /apply            - modal application form; staff Approve/Deny in #applications grants the
                          Whitelisted role and DMs the applicant the result
  4. /rules-gate       - "I Agree" button in #rules that unlocks the rest of the server
                          (everything except Server Info is hidden until members click it)
  5. /self-roles       - dropdown of opt-in ping roles (crew, event, giveaway, update pings)
  6. Live FiveM server status embed in #connect-code (players online/offline), if
                          FIVEM_SERVER_IP is set — refreshes automatically
  7. Live member-count voice channel in "📊 Server Stats", refreshed every 10 minutes
  8. Auto-moderation   - deletes messages with banned words or invite links, times out spammers
  9. Server logs        - join/leave and message edit/delete logged to #server-logs
  10. Auto-reactions/threads - any message posted in #suggestions gets 👍/👎 automatically;
                          any message in #bug-reports automatically gets its own thread
  10b. /music         - custom music player: tracks from the music/ folder, uploaded audio files,
                          or direct audio/stream URLs (play, queue, skip, pause, loop, volume...)
  11. /post_image       - post an image (with optional caption) to any channel
  12. /giveaway start, /giveaway end - button-entry giveaways with auto winner pick
  13. /announce, /suggest, /report    - server utility commands (whitelist-gated)
  14. Moderation        - /warn, /warnings, /kick, /ban, /unban, /timeout, /role-all (mass-grant
                          a role to every current member, in the background)
  15. Anti-nuke         - detects mass channel/role deletion, mass bans, and rogue webhook
      creation, then times the offender out for 20 minutes and alerts staff — all
      automatically, without needing a command.
  16. Runs continuously with auto-reconnect (discord.py handles this internally);
      for true 24/7 uptime you need to host this on a server that stays on (see README).

Requires: discord.py >= 2.4, Python 3.10+
"""

import os
import io
import re
import json
import random
import asyncio
import datetime
import time
import urllib.parse
import socket
import ipaddress
from collections import defaultdict, deque
import discord
import aiohttp
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional: speeds up slash command sync during testing
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", "Staff")
WHITELIST_ROLE_NAME = os.getenv("WHITELIST_ROLE_NAME", "Whitelisted")
SERVER_NAME = os.getenv("SERVER_NAME", "Dreams RP")
AUTO_ROLE_NAME = os.getenv("AUTO_ROLE_NAME", "Member")

# Anti-nuke config
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
ANTI_NUKE_LOG_CHANNEL = os.getenv("ANTI_NUKE_LOG_CHANNEL", "mod-logs")
ANTI_NUKE_MAX_ACTIONS = int(os.getenv("ANTI_NUKE_MAX_ACTIONS", "3"))
ANTI_NUKE_WINDOW_SECONDS = int(os.getenv("ANTI_NUKE_WINDOW_SECONDS", "10"))
ANTI_NUKE_TIMEOUT_MINUTES = int(os.getenv("ANTI_NUKE_TIMEOUT_MINUTES", "20"))

# Rules-agree gate: if enabled, new members can only see "⭐ Server Info" until they
# click "I Agree" in #rules, which grants AUTO_ROLE_NAME and unlocks the rest of the server.
GATE_ENABLED = os.getenv("GATE_ENABLED", "true").lower() == "true"
RULES_CHANNEL_NAME = os.getenv("RULES_CHANNEL_NAME", "discord-rules")
APPLICATIONS_CHANNEL_NAME = os.getenv("APPLICATIONS_CHANNEL_NAME", "pending-apps")

# Live FiveM server status embed (posted/updated in STATUS_CHANNEL_NAME).
# Leave FIVEM_SERVER_IP blank to disable this feature entirely.
FIVEM_SERVER_IP = os.getenv("FIVEM_SERVER_IP", "")  # e.g. "203.0.113.10:30120"
CFX_JOIN_CODE = os.getenv("CFX_JOIN_CODE", "").strip()
if CFX_JOIN_CODE:
    # tolerate a full link being pasted in (https://cfx.re/join/abcd12, cfx.re/join/abcd12, etc.)
    CFX_JOIN_CODE = re.sub(r"^(https?://)?(www\.)?cfx\.re/join/", "", CFX_JOIN_CODE, flags=re.IGNORECASE).strip("/ ")
STATUS_CHANNEL_NAME = os.getenv("STATUS_CHANNEL_NAME", "connect-code")
STATUS_UPDATE_MINUTES = int(os.getenv("STATUS_UPDATE_MINUTES", "1"))

# Auto-mod
AUTOMOD_BANNED_WORDS = {w.strip().lower() for w in os.getenv("AUTOMOD_BANNED_WORDS", "").split(",") if w.strip()}
AUTOMOD_BLOCK_INVITES = os.getenv("AUTOMOD_BLOCK_INVITES", "true").lower() == "true"
AUTOMOD_EXEMPT_CHANNELS = {c.strip().lower() for c in os.getenv("AUTOMOD_EXEMPT_CHANNELS", "related-servers").split(",") if c.strip()}
AUTOMOD_SPAM_LIMIT = int(os.getenv("AUTOMOD_SPAM_LIMIT", "5"))  # messages
AUTOMOD_SPAM_WINDOW = int(os.getenv("AUTOMOD_SPAM_WINDOW", "6"))  # seconds
AUTOMOD_SPAM_TIMEOUT_MINUTES = int(os.getenv("AUTOMOD_SPAM_TIMEOUT_MINUTES", "5"))
_INVITE_REGEX = re.compile(r"(discord\.gg/|discord(?:app)?\.com/invite/)", re.IGNORECASE)

# Any-other-link soft-ban (separate from the invite-link rule above, and treated as part of
# the server's anti-nuke/anti-scam protection since unsolicited links are a common attack
# vector). Domains in LINK_ALLOWED_DOMAINS are never actioned.
AUTOMOD_BLOCK_ALL_LINKS = os.getenv("AUTOMOD_BLOCK_ALL_LINKS", "true").lower() == "true"
AUTOMOD_LINK_ACTION = os.getenv("AUTOMOD_LINK_ACTION", "softban")  # softban | ban | kick | timeout | delete
LINK_TIMEOUT_MINUTES = int(os.getenv("LINK_TIMEOUT_MINUTES", "60"))  # only used if AUTOMOD_LINK_ACTION=timeout
LINK_ALLOWED_DOMAINS = {
    d.strip().lower()
    for d in os.getenv(
        "LINK_ALLOWED_DOMAINS",
        "tenor.com,giphy.com,cfx.re,youtube.com,youtu.be,twitter.com,x.com,imgur.com",
    ).split(",")
    if d.strip()
}
_URL_REGEX = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Server logs (join/leave, message edit/delete)
SERVER_LOG_CHANNEL_NAME = os.getenv("SERVER_LOG_CHANNEL_NAME", "server-logs")

# Self-assign roles (ping opt-ins) — edit freely
SELF_ROLES = {
    "crew_ping": "🔫 Crew Ping",
    "event_ping": "🎉 Event Ping",
    "giveaway_ping": "🎁 Giveaway Ping",
    "update_ping": "📢 Update Ping",
}

DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)
WARNINGS_FILE = os.path.join(DATA_DIR, "warnings.json")

# Music player (custom tracks in the music/ folder, uploaded audio files, or direct audio/stream URLs)
MUSIC_DIR = os.getenv("MUSIC_DIR", "music")
MUSIC_DJ_ROLE_NAME = os.getenv("MUSIC_DJ_ROLE_NAME", "")  # blank = anyone in the voice channel can use it
MUSIC_DEFAULT_VOLUME = int(os.getenv("MUSIC_DEFAULT_VOLUME", "50"))  # 1-100
MUSIC_MAX_QUEUE = int(os.getenv("MUSIC_MAX_QUEUE", "50"))
_AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac")
os.makedirs(MUSIC_DIR, exist_ok=True)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.moderation = True  # ban/unban audit events

bot = commands.Bot(command_prefix="!", intents=intents)


# ----------------------------------------------------------------------
# SIMPLE JSON STORAGE HELPERS
# ----------------------------------------------------------------------
def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ----------------------------------------------------------------------
# CHANNEL CLEANUP (/fix-channels)
# Renames existing categories/channels — never creates or deletes anything.
# Edit these maps freely before running the command.
# ----------------------------------------------------------------------

# Exact category-name fixes (typos, inconsistent "Dream"/"Dreams RP" branding).
CATEGORY_RENAMES = {
    "The Dream Rp Annoucement": "The Dreams RP Announcements",
    "The Dream Baddies": "The Dreams RP Baddies",
    "The Dreams Rp Suggestions": "The Dreams RP Suggestions",
    "The Dream Rp Community": "The Dreams RP Community",
    "Dream Staff Process": "Dreams RP Staff Process",
}

# Exact channel-name fixes (case-insensitive match on the current name).
CHANNEL_RENAMES = {
    "staff": "staff-general",
    "tickets": "ticket-hub",
}

# Keyword -> emoji used only for channels that don't already start with an emoji.
# Checked in order, first match wins; falls back to a generic emoji at the end.
EMOJI_KEYWORDS = [
    # keep specific/compound keywords above their broader relatives so they match first
    ("female-verif", "💗"), ("female", "💗"), ("verif", "🪪"),
    ("ban-appeal", "⚖️"), ("ban", "🔨"), ("mute", "🔇"), ("punish", "😡"), ("mod-log", "📕"),
    ("ticket-log", "🗂️"), ("ticket", "🎫"),
    ("staff-chat", "🗯️"), ("staff-announce", "📯"), ("staff", "🛡️"),
    ("interview", "🗣️"), ("training", "✍️"),
    ("apply", "📝"), ("application", "📝"), ("pending", "⏳"), ("denied", "🙅"), ("approved", "✅"),
    ("rule", "📜"), ("law", "⚖️"),
    ("announce", "📢"), ("update", "🆕"), ("changelog", "🧾"), ("hotfix", "🔧"), ("patch", "🔧"),
    ("donat", "💰"), ("tebex", "💳"), ("prio", "⭐"), ("gang", "🔫"), ("gun", "🔫"), ("car", "🚗"),
    ("suggest", "💡"), ("feedback", "📮"), ("poll", "🗳️"), ("vote", "🗳️"),
    ("report", "🚨"), ("complaint", "🚨"), ("tos", "🚫"),
    ("giveaway", "🎁"), ("event", "🎉"), ("party", "🎊"), ("celebrat", "🎉"),
    ("welcome", "👋"), ("community", "🌐"), ("general", "💬"),
    ("connect", "🔌"), ("watermark", "⚡"), ("hiring", "🔔"), ("job", "🧰"),
    ("photo", "📷"), ("pic", "📸"), ("clip", "🎬"), ("snippet", "🎞️"), ("stream", "🎥"), ("media", "🎥"),
    ("baddie", "🌸"), ("gossip", "🗞️"),
    ("management", "👥"), ("responsib", "📋"), ("role", "🎭"), ("perm", "🔑"),
    ("bug", "🪲"), ("player-report", "🚨"),
    ("log", "📝"),
]
DEFAULT_TEXT_EMOJI = "💬"
DEFAULT_VOICE_EMOJI = "🔊"
EMOJI_SEPARATOR_TEXT = "."   # matches this server's existing "emoji.name" text-channel style
EMOJI_SEPARATOR_VOICE = " "  # matches this server's existing "emoji Name" voice-channel style


def _has_emoji_prefix(name: str) -> bool:
    return bool(name) and not name[0].isascii()


def _pick_emoji(channel) -> str:
    name_lower = channel.name.lower()
    for keyword, emoji in EMOJI_KEYWORDS:
        if keyword in name_lower:
            return emoji
    return DEFAULT_VOICE_EMOJI if isinstance(channel, discord.VoiceChannel) else DEFAULT_TEXT_EMOJI


@bot.tree.command(
    name="fix-channels",
    description="Rename categories/channels per the cleanup lists and add an emoji to channels missing one",
)
@app_commands.checks.has_permissions(administrator=True)
async def fix_channels(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel
    await interaction.response.send_message(
        "Starting cleanup — this only renames existing categories/channels, nothing is created or deleted. "
        "I'll post a summary here when it's done."
    )

    async def _run():
        renamed = []

        for category in guild.categories:
            new_name = CATEGORY_RENAMES.get(category.name)
            if new_name and category.name != new_name:
                try:
                    await category.edit(name=new_name)
                    renamed.append(f"📁 {category.name} → {new_name}")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(1)

        all_channels = list(guild.text_channels) + list(guild.voice_channels)
        for ch in all_channels:
            old_name = ch.name
            target = CHANNEL_RENAMES.get(old_name.lower(), old_name)

            if not _has_emoji_prefix(target):
                emoji = _pick_emoji(ch)
                sep = EMOJI_SEPARATOR_VOICE if isinstance(ch, discord.VoiceChannel) else EMOJI_SEPARATOR_TEXT
                target = f"{emoji}{sep}{target}"

            if target != old_name:
                try:
                    await ch.edit(name=target)
                    renamed.append(f"#{old_name} → {target}")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(1)  # stay well under Discord's rate limits

        summary = f"✅ /fix-channels finished — renamed {len(renamed)} categories/channels."
        if renamed:
            preview = "\n".join(renamed[:25])
            if len(renamed) > 25:
                preview += f"\n…and {len(renamed) - 25} more."
            summary += f"\n```{preview}```"
        await channel.send(summary)

    bot.loop.create_task(_run())


# ----------------------------------------------------------------------
# SERVER STRUCTURE
# Edit this dict to change what /setup_server builds.
# Each category maps to a list of (channel_name, channel_type) tuples.
# channel_type: "text" or "voice"
# ----------------------------------------------------------------------
SERVER_STRUCTURE = {
    # These reuse categories that already exist on The Dreams RP — get_or_create_category
    # finds them by exact name and only ADDS the channels below, it never touches or
    # recreates the category itself, and never touches channels already inside it.
    "Staff": [
        ("mod-logs", "text"),
        ("server-logs", "text"),
    ],
    "Support": [
        ("bug-reports", "text"),
        ("player-reports", "text"),
    ],
    # Genuinely new additions — nothing on the real server matches these names, so these
    # get created fresh the first time /setup_server runs.
    "📊 Server Stats": [
        ("Members: 0", "voice"),
    ],
    "🎫 Ticket Logs": [
        ("ticket-logs", "text"),
        ("ban-ticket-logs", "text"),
        ("female-verify-logs", "text"),
        ("city-report-logs", "text"),
        ("reimburse-logs", "text"),
        ("higher-ups-logs", "text"),
    ],
}

# Locked categories: hidden from @everyone, visible to STAFF_ROLE_NAME only.
# NOTE: this only applies when /setup_server CREATES the category — if it already exists
# (e.g. your real "Staff" or "Female Verification" categories), its existing permissions
# are left exactly as they are; nothing here retroactively changes them.
LOCKED_CATEGORIES = {"Female Verification", "🎫 Ticket Logs"}

# ----------------------------------------------------------------------
# TICKET TYPES
# category names below match your server's existing "Ban Ticket", "Female Verify Ticket",
# etc. categories exactly, so tickets open inside those rather than creating duplicates.
# key must be snake_case (no hyphens) — it's encoded into the ticket channel
# name so the bot can recover it after a restart. Add/remove types freely.
# ----------------------------------------------------------------------
TICKET_TYPES = {
    "ban": {"label": "Ban Ticket", "emoji": "🔨", "category": "Ban Ticket", "log_channel": "ban-ticket-logs"},
    "female_verify": {"label": "Female Verify Ticket", "emoji": "💗", "category": "Female Verify Ticket", "log_channel": "female-verify-logs"},
    "city_report": {"label": "City Report Ticket", "emoji": "🏙️", "category": "City Report Ticket", "log_channel": "city-report-logs"},
    "reimburse": {"label": "Reimburse Ticket", "emoji": "💸", "category": "Reimburse Ticket", "log_channel": "reimburse-logs"},
    "higher_ups": {"label": "Higher Ups", "emoji": "👑", "category": "Higher Ups", "log_channel": "higher-ups-logs"},
}


async def get_or_create_category(
    guild: discord.Guild, name: str, staff_role: discord.Role = None, auto_role: discord.Role = None
) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category is not None:
        return category
    overwrites = {}
    if name in LOCKED_CATEGORIES:
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    elif GATE_ENABLED and name != "⭐ Server Info":
        # Gate everything except Server Info behind the AUTO_ROLE_NAME role, granted by
        # clicking "I Agree" in #rules (see RulesGateView below).
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        if auto_role:
            overwrites[auto_role] = discord.PermissionOverwrite(view_channel=True)
    return await guild.create_category(name, overwrites=overwrites)


# ----------------------------------------------------------------------
# SETUP COMMAND
# ----------------------------------------------------------------------
@bot.tree.command(name="setup_server", description="Build all categories and channels for the server")
@app_commands.checks.has_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild

    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    if staff_role is None:
        staff_role = await guild.create_role(name=STAFF_ROLE_NAME, reason="Auto-created by setup_server")

    auto_role = discord.utils.get(guild.roles, name=AUTO_ROLE_NAME)
    if auto_role is None:
        auto_role = await guild.create_role(name=AUTO_ROLE_NAME, reason="Auto-created by setup_server")

    created = []
    for category_name, channels in SERVER_STRUCTURE.items():
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await get_or_create_category(guild, category_name, staff_role, auto_role)
            created.append(category_name)

        for chan_name, chan_type in channels:
            existing = discord.utils.get(category.channels, name=chan_name.lower().replace(" ", "-"))
            if existing:
                continue
            if chan_type == "voice":
                await guild.create_voice_channel(chan_name, category=category)
            else:
                await guild.create_text_channel(chan_name, category=category)
            created.append(f"{category_name} / {chan_name}")

    # Server Stats voice channel is view-only — nobody should be able to join it.
    stats_category = discord.utils.get(guild.categories, name="📊 Server Stats")
    if stats_category:
        for vc in stats_category.voice_channels:
            await vc.set_permissions(guild.default_role, connect=False, view_channel=True)

    await post_ticket_panel(guild)
    if GATE_ENABLED:
        await post_rules_gate(guild)
    if not update_member_count.is_running():
        update_member_count.start()
    if (CFX_JOIN_CODE or FIVEM_SERVER_IP) and not update_server_status.is_running():
        update_server_status.start()

    await interaction.followup.send(
        f"Done. Created {len(created)} new categories/channels, posted the ticket panel in #ticket-hub"
        + (", and the rules-agree gate in #rules." if GATE_ENABLED else "."),
        ephemeral=True,
    )


# ----------------------------------------------------------------------
# TICKET SYSTEM
# ----------------------------------------------------------------------
class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=cfg["label"], value=key, emoji=cfg["emoji"])
            for key, cfg in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Choose a ticket type...",
            options=options,
            custom_id="ticket_type_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await open_ticket(interaction, self.values[0])


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


async def open_ticket(interaction: discord.Interaction, ticket_key: str):
    cfg = TICKET_TYPES.get(ticket_key)
    if not cfg:
        await interaction.response.send_message("Unknown ticket type.", ephemeral=True)
        return

    guild = interaction.guild
    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)

    username_slug = interaction.user.name.lower().replace(" ", "-")
    channel_name = f"{ticket_key}-{username_slug}"
    existing = discord.utils.get(guild.text_channels, name=channel_name)
    if existing:
        await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return

    category = await get_or_create_category(guild, cfg["category"], staff_role)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    ticket_channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

    embed = discord.Embed(
        title=cfg["label"],
        description=f"{interaction.user.mention} thanks for opening a **{cfg['label']}**. Staff will be with you shortly.",
        color=discord.Color.blurple(),
    )
    await ticket_channel.send(embed=embed, view=TicketControlView())
    await interaction.response.send_message(f"Ticket created: {ticket_channel.mention}", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple, custom_id="ticket_claim_button")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"🔧 {interaction.user.mention} claimed this ticket.")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket and saving the transcript...")
        await close_ticket_with_transcript(interaction.channel, interaction.user)


async def close_ticket_with_transcript(channel: discord.TextChannel, closer: discord.abc.User):
    guild = channel.guild
    ticket_key = channel.name.split("-", 1)[0]
    cfg = TICKET_TYPES.get(ticket_key)
    log_channel_name = cfg["log_channel"] if cfg else "ticket-logs"
    log_channel = discord.utils.get(guild.text_channels, name=log_channel_name) or discord.utils.get(
        guild.text_channels, name="ticket-logs"
    )

    lines = []
    async for msg in channel.history(limit=500, oldest_first=True):
        stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        content = msg.content or (f"[attachment: {msg.attachments[0].filename}]" if msg.attachments else "[embed]")
        lines.append(f"[{stamp}] {msg.author}: {content}")
    transcript_text = "\n".join(lines) if lines else "No messages were sent in this ticket."

    if log_channel:
        transcript_file = discord.File(io.BytesIO(transcript_text.encode()), filename=f"{channel.name}-transcript.txt")
        embed = discord.Embed(title="Ticket closed", color=discord.Color.dark_grey())
        embed.add_field(name="Type", value=cfg["label"] if cfg else "Unknown", inline=True)
        embed.add_field(name="Channel", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Closed by", value=closer.mention, inline=True)
        await log_channel.send(embed=embed, file=transcript_file)

    await channel.send("This ticket will be deleted in 5 seconds.")
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except discord.NotFound:
        pass


async def post_ticket_panel(guild: discord.Guild, channel: discord.TextChannel = None):
    ticket_hub = channel or discord.utils.get(guild.text_channels, name="ticket-hub")
    if not ticket_hub:
        return
    # avoid duplicate panels
    async for msg in ticket_hub.history(limit=20):
        if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "Need Help?":
            return
    embed = discord.Embed(
        title="Need Help?",
        description="Pick a ticket type from the dropdown below to open a private channel with staff.",
        color=discord.Color.green(),
    )
    await ticket_hub.send(embed=embed, view=TicketPanelView())


@bot.tree.command(name="ticket-panel", description="Post the ticket panel in this channel (or #ticket-hub if omitted)")
@app_commands.describe(channel="Channel to post the panel in (defaults to this channel)")
@app_commands.checks.has_permissions(manage_guild=True)
async def ticket_panel_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    await post_ticket_panel(interaction.guild, channel or interaction.channel)
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


# ----------------------------------------------------------------------
# RULES-AGREE GATE
# ----------------------------------------------------------------------
class RulesGateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="I Agree", style=discord.ButtonStyle.green, custom_id="rules_agree_button")
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=AUTO_ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=AUTO_ROLE_NAME, reason="Auto-created base role")
        if role in interaction.user.roles:
            await interaction.response.send_message("You're already verified!", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Agreed to rules")
            await interaction.response.send_message("Welcome in! The rest of the server is unlocked for you now.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I couldn't give you the role — tell staff.", ephemeral=True)


async def post_rules_gate(guild: discord.Guild, channel: discord.TextChannel = None):
    rules_channel = channel or discord.utils.get(guild.text_channels, name=RULES_CHANNEL_NAME)
    if not rules_channel:
        return
    async for msg in rules_channel.history(limit=20):
        if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "Agree to the rules to continue":
            return
    embed = discord.Embed(
        title="Agree to the rules to continue",
        description=f"Read the rules above, then click **I Agree** to unlock the rest of **{SERVER_NAME}**.",
        color=discord.Color.green(),
    )
    await rules_channel.send(embed=embed, view=RulesGateView())


@bot.tree.command(name="rules-gate", description="Post the 'I Agree' verification gate in this channel")
@app_commands.describe(channel="Channel to post it in (defaults to this channel)")
@app_commands.checks.has_permissions(manage_guild=True)
async def rules_gate_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    await post_rules_gate(interaction.guild, channel or interaction.channel)
    await interaction.response.send_message("Rules gate posted.", ephemeral=True)


# ----------------------------------------------------------------------
# SELF-ASSIGN ROLES (ping opt-ins)
# ----------------------------------------------------------------------
class SelfRoleSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=key) for key, label in SELF_ROLES.items()]
        super().__init__(
            placeholder="Pick your pings...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id="self_role_select",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        selected = set(self.values)
        changes = []
        for key, label in SELF_ROLES.items():
            role = discord.utils.get(guild.roles, name=label)
            if role is None:
                role = await guild.create_role(name=label, reason="Self-assign role auto-created")
            has_it = role in member.roles
            wants_it = key in selected
            if wants_it and not has_it:
                await member.add_roles(role)
                changes.append(f"+{label}")
            elif not wants_it and has_it:
                await member.remove_roles(role)
                changes.append(f"-{label}")
        await interaction.response.send_message(
            "Updated: " + ", ".join(changes) if changes else "No changes.", ephemeral=True
        )


class SelfRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SelfRoleSelect())


@bot.tree.command(name="self-roles", description="Post the self-assign ping roles menu in this channel")
@app_commands.checks.has_permissions(manage_guild=True)
async def self_roles_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Pick your pings",
        description="Select the notifications you want. Select the same one again to remove it.",
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=SelfRoleView())
    await interaction.response.send_message("Posted.", ephemeral=True)


# ----------------------------------------------------------------------
# LIVE FIVEM SERVER STATUS (skipped entirely if FIVEM_SERVER_IP is blank)
# ----------------------------------------------------------------------
async def fetch_fivem_status():
    # Preferred: CFX_JOIN_CODE — uses CFX.re's public server-list API, no server IP needed
    # at all (only works if your server shows up on the FiveM server list / servers.fivem.net).
    if CFX_JOIN_CODE:
        url = f"https://servers-frontend.fivem.net/api/servers/single/{CFX_JOIN_CODE}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    raw_text = await resp.text()
                    if resp.status != 200:
                        print(f"FiveM status: CFX API returned HTTP {resp.status} for code '{CFX_JOIN_CODE}': {raw_text[:300]!r}")
                        return {"online": False}
                    try:
                        payload = json.loads(raw_text)
                    except json.JSONDecodeError:
                        print(f"FiveM status: CFX API returned non-JSON for code '{CFX_JOIN_CODE}': {raw_text[:300]!r}")
                        return {"online": False}
                    data = payload.get("Data") or {}
                    if not data:
                        print(f"FiveM status: CFX API had no 'Data' for code '{CFX_JOIN_CODE}' — check the code is correct and the server is public. Raw: {raw_text[:300]!r}")
                        return {"online": False}
                    return {
                        "online": True,
                        "players": data.get("clients", 0),
                        "max": data.get("sv_maxclients", "?"),
                        "hostname": data.get("hostname", SERVER_NAME),
                    }
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"FiveM status: request failed for code '{CFX_JOIN_CODE}': {e!r}")
            return {"online": False}

    # Fallback: direct IP:port, if you'd rather use that instead
    if not FIVEM_SERVER_IP:
        return None
    url = f"http://{FIVEM_SERVER_IP}/dynamic.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return {"online": False}
                data = await resp.json(content_type=None)
                return {
                    "online": True,
                    "players": data.get("clients", 0),
                    "max": data.get("sv_maxclients", "?"),
                    "hostname": data.get("hostname", SERVER_NAME),
                }
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return {"online": False}


@tasks.loop(minutes=STATUS_UPDATE_MINUTES)
async def update_server_status():
    status = await fetch_fivem_status()
    if status is None:
        return
    if status["online"]:
        embed = discord.Embed(title="🟢 Server Status: Online", color=discord.Color.green())
        embed.add_field(name="Players", value=f"{status['players']}/{status['max']}", inline=True)
    else:
        embed = discord.Embed(title="🔴 Server Status: Offline", color=discord.Color.red())
    if CFX_JOIN_CODE:
        embed.add_field(name="Connect", value=f"cfx.re/join/{CFX_JOIN_CODE}", inline=True)
    elif FIVEM_SERVER_IP:
        embed.add_field(name="Connect", value=f"connect {FIVEM_SERVER_IP}", inline=True)
    embed.set_footer(text=f"Updates every {STATUS_UPDATE_MINUTES} min")
    embed.timestamp = discord.utils.utcnow()

    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=STATUS_CHANNEL_NAME)
        if not channel:
            continue
        # delete the previous status message(s) rather than editing, then post a fresh one
        async for msg in channel.history(limit=20):
            if msg.author == guild.me and msg.embeds and msg.embeds[0].title and msg.embeds[0].title.startswith(("🟢", "🔴")):
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
        await channel.send(embed=embed)


# ----------------------------------------------------------------------
# LIVE MEMBER-COUNT VOICE CHANNEL
# ----------------------------------------------------------------------
@tasks.loop(minutes=10)
async def update_member_count():
    for guild in bot.guilds:
        category = discord.utils.get(guild.categories, name="📊 Server Stats")
        if not category or not category.voice_channels:
            continue
        channel = category.voice_channels[0]
        new_name = f"Members: {guild.member_count}"
        if channel.name != new_name:
            try:
                await channel.edit(name=new_name)
            except discord.HTTPException:
                pass


# ----------------------------------------------------------------------
# MUSIC
# Plays tracks from your music/ folder, uploaded audio files, or direct audio/stream URLs
# (mp3 links, internet radio streams). YouTube/Spotify/SoundCloud scraping is deliberately
# not included — it breaks those services' terms, which is why the big music bots got shut down.
# Needs FFmpeg + libopus on the host (the included Dockerfile installs both) and
# discord.py >= 2.7 with davey, since Discord now requires DAVE encryption for voice.
# ----------------------------------------------------------------------
class Track:
    def __init__(self, title: str, source: str, is_url: bool, requester: discord.abc.User):
        self.title = title
        self.source = source
        self.is_url = is_url
        self.requester = requester


class GuildMusic:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.volume = max(1, min(100, MUSIC_DEFAULT_VOLUME)) / 100
        self.loop = False


_music_state = {}  # guild_id -> GuildMusic


def get_music(guild_id: int) -> GuildMusic:
    return _music_state.setdefault(guild_id, GuildMusic())


def list_local_tracks():
    try:
        return sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(_AUDIO_EXTS))
    except FileNotFoundError:
        return []


async def _is_public_url(url: str) -> bool:
    """Only allow http(s) URLs that resolve to public addresses (blocks localhost/internal hosts)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def build_source(track: Track, volume: float):
    if track.is_url:
        before = "-protocol_whitelist http,https,tcp,tls,crypto -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    else:
        before = None
    audio = discord.FFmpegPCMAudio(track.source, before_options=before, options="-vn")
    return discord.PCMVolumeTransformer(audio, volume=volume)


async def play_next(guild: discord.Guild):
    state = get_music(guild.id)
    vc = guild.voice_client
    if vc is None or not vc.is_connected():
        state.current = None
        return

    if state.loop and state.current:
        track = state.current
    elif state.queue:
        track = state.queue.popleft()
    else:
        state.current = None
        return

    state.current = track
    try:
        source = build_source(track, state.volume)
    except Exception as e:
        print(f"Music: couldn't build source for {track.title}: {e!r}")
        state.loop = False
        state.current = None
        await play_next(guild)
        return

    def _after(error):
        if error:
            print(f"Music playback error: {error!r}")
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    vc.play(source, after=_after)


async def _music_guard(interaction: discord.Interaction) -> bool:
    """True if the user may control music right now; otherwise replies with why not."""
    member = interaction.user
    if MUSIC_DJ_ROLE_NAME and not (is_staff(member) or any(r.name == MUSIC_DJ_ROLE_NAME for r in member.roles)):
        await interaction.response.send_message(
            f"You need the **{MUSIC_DJ_ROLE_NAME}** role to use music commands.", ephemeral=True
        )
        return False
    if not member.voice or not member.voice.channel:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return False
    vc = interaction.guild.voice_client
    if vc and vc.channel != member.voice.channel:
        await interaction.response.send_message(
            f"I'm already in {vc.channel.mention} — join that channel to control me.", ephemeral=True
        )
        return False
    return True


async def _ensure_voice(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_connected():
        return vc
    try:
        return await interaction.user.voice.channel.connect(timeout=20, self_deaf=False)
    except Exception as e:
        print(f"Music: voice connect failed: {e!r}")
        return None


music_group = app_commands.Group(name="music", description="Music player", guild_only=True)


@music_group.command(name="play", description="Play a track from the library, an uploaded audio file, or an audio/stream URL")
@app_commands.describe(track="Library track name, or a direct audio/stream URL", file="Or upload an audio file")
async def music_play(interaction: discord.Interaction, track: str = None, file: discord.Attachment = None):
    if not await _music_guard(interaction):
        return
    if not track and not file:
        await interaction.response.send_message("Give me a library track, a URL, or upload an audio file.", ephemeral=True)
        return

    if file:
        if not file.filename.lower().endswith(_AUDIO_EXTS):
            await interaction.response.send_message(
                f"That doesn't look like an audio file. Supported: {', '.join(_AUDIO_EXTS)}", ephemeral=True
            )
            return
        new_track = Track(file.filename, file.url, True, interaction.user)
    elif track.lower().startswith(("http://", "https://")):
        if not await _is_public_url(track):
            await interaction.response.send_message("I can only play public http(s) audio links.", ephemeral=True)
            return
        parsed = urllib.parse.urlparse(track)
        title = urllib.parse.unquote(os.path.basename(parsed.path)) or parsed.netloc
        new_track = Track(title, track, True, interaction.user)
    else:
        wanted = track.lower()
        matches = [
            f for f in list_local_tracks()
            if f.lower() == wanted or os.path.splitext(f)[0].lower() == wanted
        ]
        if not matches:
            await interaction.response.send_message(
                "No track with that name in the library — try `/music library` to see what's there.", ephemeral=True
            )
            return
        new_track = Track(os.path.splitext(matches[0])[0], os.path.join(MUSIC_DIR, matches[0]), False, interaction.user)

    state = get_music(interaction.guild_id)
    if len(state.queue) >= MUSIC_MAX_QUEUE:
        await interaction.response.send_message("The queue is full.", ephemeral=True)
        return

    await interaction.response.defer()
    vc = await _ensure_voice(interaction)
    if vc is None:
        await interaction.followup.send(
            "I couldn't join the voice channel. Check I have Connect + Speak there, and see the bot logs if it keeps failing."
        )
        return

    state.queue.append(new_track)
    if not vc.is_playing() and not vc.is_paused():
        await play_next(interaction.guild)
        await interaction.followup.send(f"▶️ Now playing **{new_track.title}**")
    else:
        await interaction.followup.send(f"➕ Queued **{new_track.title}** (#{len(state.queue)})")


@music_play.autocomplete("track")
async def music_track_autocomplete(interaction: discord.Interaction, current: str):
    names = [n for n in list_local_tracks() if len(n) <= 100 and current.lower() in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]


@music_group.command(name="library", description="List the tracks in the music library")
async def music_library(interaction: discord.Interaction):
    names = list_local_tracks()
    if not names:
        await interaction.response.send_message(
            "The library is empty — add audio files to the bot's `music/` folder, or use `/music play` with a file or URL.",
            ephemeral=True,
        )
        return
    text = "\n".join(f"• {n}" for n in names)
    if len(text) > 1900:
        text = text[:1900].rsplit("\n", 1)[0] + "\n…and more"
    await interaction.response.send_message(f"**Music library ({len(names)})**\n{text}", ephemeral=True)


@music_group.command(name="queue", description="Show what's playing and what's up next")
async def music_queue(interaction: discord.Interaction):
    state = get_music(interaction.guild_id)
    if not state.current and not state.queue:
        await interaction.response.send_message("Nothing's playing.", ephemeral=True)
        return
    lines = []
    if state.current:
        lines.append(f"▶️ **{state.current.title}**" + (" 🔁" if state.loop else ""))
    for i, t in enumerate(list(state.queue)[:10], 1):
        lines.append(f"{i}. {t.title}")
    if len(state.queue) > 10:
        lines.append(f"…and {len(state.queue) - 10} more")
    await interaction.response.send_message("\n".join(lines))


@music_group.command(name="nowplaying", description="Show the current track")
async def music_nowplaying(interaction: discord.Interaction):
    state = get_music(interaction.guild_id)
    if not state.current:
        await interaction.response.send_message("Nothing's playing.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"🎵 **{state.current.title}** — requested by {state.current.requester.mention}"
    )


@music_group.command(name="skip", description="Skip the current track")
async def music_skip(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    vc = interaction.guild.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("Nothing to skip.", ephemeral=True)
        return
    state = get_music(interaction.guild_id)
    state.current = None  # so looping doesn't just replay it
    vc.stop()
    await interaction.response.send_message("⏭️ Skipped.")


@music_group.command(name="pause", description="Pause playback")
async def music_pause(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Paused.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@music_group.command(name="resume", description="Resume playback")
async def music_resume(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Resumed.")
    else:
        await interaction.response.send_message("Nothing is paused.", ephemeral=True)


@music_group.command(name="loop", description="Toggle looping the current track (handy for radio-style playback)")
async def music_loop(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    state = get_music(interaction.guild_id)
    state.loop = not state.loop
    await interaction.response.send_message("🔁 Loop on." if state.loop else "Loop off.")


@music_group.command(name="volume", description="Set the volume (1-100)")
@app_commands.describe(level="Volume from 1 to 100")
async def music_volume(interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]):
    if not await _music_guard(interaction):
        return
    state = get_music(interaction.guild_id)
    state.volume = level / 100
    vc = interaction.guild.voice_client
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = state.volume
    await interaction.response.send_message(f"🔊 Volume set to {level}%.")


@music_group.command(name="stop", description="Stop playback and clear the queue")
async def music_stop(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    state = get_music(interaction.guild_id)
    state.queue.clear()
    state.loop = False
    state.current = None
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
    await interaction.response.send_message("⏹️ Stopped and cleared the queue.")


@music_group.command(name="leave", description="Make the bot leave the voice channel")
async def music_leave(interaction: discord.Interaction):
    if not await _music_guard(interaction):
        return
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
        return
    _music_state.pop(interaction.guild_id, None)
    await vc.disconnect()
    await interaction.response.send_message("👋 Left the voice channel.")


bot.tree.add_command(music_group)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # The bot itself got disconnected (kicked, channel deleted, etc.) — drop its queue.
    if member.id == bot.user.id:
        if after.channel is None:
            _music_state.pop(member.guild.id, None)
        return
    # Leave when the last human walks out.
    vc = member.guild.voice_client
    if not vc or not vc.channel:
        return
    if before.channel == vc.channel and after.channel != vc.channel:
        if not [m for m in vc.channel.members if not m.bot]:
            _music_state.pop(member.guild.id, None)
            await vc.disconnect()


# ----------------------------------------------------------------------
# IMAGE POSTING
# ----------------------------------------------------------------------
@bot.tree.command(name="post_image", description="Post an image to a channel")
@app_commands.describe(channel="Channel to post in", image="Image file to post", caption="Optional caption")
@app_commands.checks.has_permissions(manage_messages=True)
async def post_image(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    image: discord.Attachment,
    caption: str = None,
):
    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("That attachment isn't an image.", ephemeral=True)
        return
    file = await image.to_file()
    await channel.send(content=caption, file=file)
    await interaction.response.send_message(f"Posted to {channel.mention}.", ephemeral=True)


# ----------------------------------------------------------------------
# GIVEAWAYS
# ----------------------------------------------------------------------
active_giveaways = {}  # message_id -> {channel_id, prize, end_time, winners, entries:set(), ended:bool}


class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int = None):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🎉 Enter", style=discord.ButtonStyle.blurple, custom_id="giveaway_enter_button")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = active_giveaways.get(interaction.message.id)
        if not data or data["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        if interaction.user.id in data["entries"]:
            data["entries"].discard(interaction.user.id)
            await interaction.response.send_message("You left the giveaway.", ephemeral=True)
        else:
            data["entries"].add(interaction.user.id)
            await interaction.response.send_message("You're entered! Good luck 🎉", ephemeral=True)


async def end_giveaway(message_id: int):
    data = active_giveaways.get(message_id)
    if not data or data["ended"]:
        return
    data["ended"] = True
    channel = bot.get_channel(data["channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        return

    entries = list(data["entries"])
    winners_count = min(data["winners"], len(entries))
    winners = random.sample(entries, winners_count) if winners_count else []

    if winners:
        mentions = ", ".join(f"<@{uid}>" for uid in winners)
        result_text = f"Congrats {mentions}! You won **{data['prize']}**."
    else:
        result_text = "No valid entries — no winner this time."

    ended_embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"**Prize:** {data['prize']}\n{result_text}",
        color=discord.Color.gold(),
    )
    await message.edit(embed=ended_embed, view=None)
    await channel.send(result_text)


@tasks.loop(seconds=30)
async def giveaway_checker():
    now = discord.utils.utcnow()
    for message_id, data in list(active_giveaways.items()):
        if not data["ended"] and now >= data["end_time"]:
            await end_giveaway(message_id)


giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands")


@giveaway_group.command(name="start", description="Start a giveaway")
@app_commands.describe(prize="What you're giving away", minutes="How long the giveaway runs, in minutes", winners="Number of winners")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_start(interaction: discord.Interaction, prize: str, minutes: int, winners: int = 1):
    end_time = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    embed = discord.Embed(
        title="🎉 Giveaway!",
        description=f"**Prize:** {prize}\nClick the button below to enter.\nEnds <t:{int(end_time.timestamp())}:R>\nWinners: {winners}",
        color=discord.Color.gold(),
    )
    view = GiveawayView()
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    active_giveaways[message.id] = {
        "channel_id": interaction.channel_id,
        "prize": prize,
        "end_time": end_time,
        "winners": winners,
        "entries": set(),
        "ended": False,
    }


@giveaway_group.command(name="end", description="End a giveaway early")
@app_commands.describe(message_id="The message ID of the giveaway to end")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("That doesn't look like a valid message ID.", ephemeral=True)
        return
    if mid not in active_giveaways:
        await interaction.response.send_message("No active giveaway with that message ID.", ephemeral=True)
        return
    await end_giveaway(mid)
    await interaction.response.send_message("Giveaway ended.", ephemeral=True)


bot.tree.add_command(giveaway_group)


# ----------------------------------------------------------------------
# EXTRA SERVER COMMANDS
# ----------------------------------------------------------------------
def has_whitelist_role(member: discord.Member) -> bool:
    return any(role.name == WHITELIST_ROLE_NAME for role in member.roles)


@bot.tree.command(name="announce", description="Post an announcement embed to a channel")
@app_commands.describe(channel="Channel to post in", title="Announcement title", message="Announcement body")
@app_commands.checks.has_permissions(manage_guild=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, title: str, message: str):
    embed = discord.Embed(title=title, description=message, color=discord.Color.red())
    embed.set_footer(text=SERVER_NAME)
    await channel.send(embed=embed)
    await interaction.response.send_message(f"Posted to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="suggest", description="Submit a suggestion (Whitelisted role only)")
@app_commands.describe(suggestion="Your suggestion")
async def suggest(interaction: discord.Interaction, suggestion: str):
    if not has_whitelist_role(interaction.user):
        await interaction.response.send_message(
            f"You need the **{WHITELIST_ROLE_NAME}** role to use this.", ephemeral=True
        )
        return
    channel = discord.utils.get(interaction.guild.text_channels, name="suggestions")
    if not channel:
        await interaction.response.send_message("Couldn't find a #suggestions channel.", ephemeral=True)
        return
    embed = discord.Embed(description=suggestion, color=discord.Color.blurple())
    embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
    msg = await channel.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")
    await interaction.response.send_message("Suggestion submitted, thanks!", ephemeral=True)


@bot.tree.command(name="report", description="Report a player (Whitelisted role only)")
@app_commands.describe(player="Who you're reporting (name or @mention)", reason="What happened", evidence="Optional screenshot/clip")
async def report(interaction: discord.Interaction, player: str, reason: str, evidence: discord.Attachment = None):
    if not has_whitelist_role(interaction.user):
        await interaction.response.send_message(
            f"You need the **{WHITELIST_ROLE_NAME}** role to use this.", ephemeral=True
        )
        return
    channel = discord.utils.get(interaction.guild.text_channels, name="player-reports")
    if not channel:
        await interaction.response.send_message("Couldn't find a #player-reports channel.", ephemeral=True)
        return
    embed = discord.Embed(title="Player Report", color=discord.Color.orange())
    embed.add_field(name="Reported by", value=interaction.user.mention, inline=False)
    embed.add_field(name="Player", value=player, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    file = await evidence.to_file() if evidence else None
    if file:
        embed.set_image(url=f"attachment://{file.filename}")
        await channel.send(embed=embed, file=file)
    else:
        await channel.send(embed=embed)
    await interaction.response.send_message("Report submitted to staff.", ephemeral=True)


# ----------------------------------------------------------------------
# APPLICATIONS
# ----------------------------------------------------------------------
class ApplicationModal(discord.ui.Modal, title="Whitelist Application"):
    age = discord.ui.TextInput(label="Age", required=True, max_length=3)
    timezone_field = discord.ui.TextInput(label="Timezone", required=True, max_length=50)
    experience = discord.ui.TextInput(label="RP Experience", style=discord.TextStyle.paragraph, required=True, max_length=500)
    why = discord.ui.TextInput(label="Why do you want to join?", style=discord.TextStyle.paragraph, required=True, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        app_channel = discord.utils.get(interaction.guild.text_channels, name=APPLICATIONS_CHANNEL_NAME)
        embed = discord.Embed(title="New Application", color=discord.Color.blurple())
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=False)
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="Timezone", value=self.timezone_field.value, inline=True)
        embed.add_field(name="RP Experience", value=self.experience.value, inline=False)
        embed.add_field(name="Why they want to join", value=self.why.value, inline=False)
        embed.set_footer(text=f"applicant_id:{interaction.user.id}")

        if app_channel:
            await app_channel.send(embed=embed, view=ApplicationReviewView())
            await interaction.response.send_message("Application submitted! We'll DM you the result.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Couldn't find a #{APPLICATIONS_CHANNEL_NAME} channel — tell staff.", ephemeral=True
            )


class ApplicationReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    def _applicant_id(interaction: discord.Interaction):
        if not interaction.message.embeds:
            return None
        footer = interaction.message.embeds[0].footer.text or ""
        if footer.startswith("applicant_id:"):
            return int(footer.split(":", 1)[1])
        return None

    async def _resolve(self, interaction: discord.Interaction, approved: bool):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        applicant_id = self._applicant_id(interaction)
        applicant = interaction.guild.get_member(applicant_id) if applicant_id else None

        if approved and applicant:
            wl_role = discord.utils.get(interaction.guild.roles, name=WHITELIST_ROLE_NAME)
            if wl_role:
                try:
                    await applicant.add_roles(wl_role, reason="Application approved")
                except discord.Forbidden:
                    pass

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        verdict = "✅ Approved" if approved else "❌ Denied"
        await interaction.channel.send(f"{verdict} by {interaction.user.mention}")

        if applicant:
            dm_text = (
                f"Your application to **{SERVER_NAME}** was approved! Welcome in."
                if approved
                else f"Your application to **{SERVER_NAME}** was not approved this time."
            )
            try:
                await applicant.send(dm_text)
            except discord.Forbidden:
                pass

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="app_approve_button")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, approved=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="app_deny_button")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, approved=False)


@bot.tree.command(name="apply", description="Apply for whitelist")
async def apply_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(ApplicationModal())


# ----------------------------------------------------------------------
# AUTO-MODERATION (banned words, invite links, spam)
# ----------------------------------------------------------------------
_spam_tracker = defaultdict(list)  # (guild_id, user_id) -> [timestamps]


async def run_automod(message: discord.Message) -> bool:
    """Returns True if the message was actioned (deleted/handled) and should stop further processing."""
    if is_staff(message.author):
        return False

    content_lower = message.content.lower()

    if AUTOMOD_BANNED_WORDS and any(word in content_lower for word in AUTOMOD_BANNED_WORDS):
        try:
            await message.delete()
        except discord.NotFound:
            pass
        try:
            await message.author.send(f"Your message in **{message.guild.name}** was removed for containing a blocked word.")
        except discord.Forbidden:
            pass
        return True

    if (
        AUTOMOD_BLOCK_INVITES
        and message.channel.name.lower() not in AUTOMOD_EXEMPT_CHANNELS
        and _INVITE_REGEX.search(message.content)
    ):
        try:
            await message.delete()
        except discord.NotFound:
            pass
        try:
            await message.author.send(f"Discord invite links aren't allowed in **{message.guild.name}**.")
        except discord.Forbidden:
            pass
        return True

    if AUTOMOD_BLOCK_ALL_LINKS and message.channel.name.lower() not in AUTOMOD_EXEMPT_CHANNELS:
        for match in _URL_REGEX.finditer(message.content):
            domain = _extract_domain(match.group(0))
            if domain and not any(domain == d or domain.endswith("." + d) for d in LINK_ALLOWED_DOMAINS):
                await handle_link_violation(message, domain)
                return True

    key = (message.guild.id, message.author.id)
    now = time.time()
    _spam_tracker[key] = [t for t in _spam_tracker[key] if now - t < AUTOMOD_SPAM_WINDOW]
    _spam_tracker[key].append(now)
    if len(_spam_tracker[key]) > AUTOMOD_SPAM_LIMIT:
        _spam_tracker[key] = []
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=AUTOMOD_SPAM_TIMEOUT_MINUTES)
            await message.author.timeout(until, reason="Automod: spamming")
            await message.channel.send(
                f"{message.author.mention} was timed out for {AUTOMOD_SPAM_TIMEOUT_MINUTES} minutes for spamming.",
                delete_after=8,
            )
        except discord.Forbidden:
            pass
        return True

    return False


def _extract_domain(url: str) -> str:
    if not url.lower().startswith("http"):
        url = "http://" + url
    try:
        return urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""


async def handle_link_violation(message: discord.Message, domain: str):
    guild = message.guild
    member = message.author

    try:
        await message.delete()
    except discord.NotFound:
        pass

    action_desc = {
        "softban": "You've been soft-banned (removed and immediately allowed back, with your recent messages cleared).",
        "ban": "You've been banned as a result.",
        "kick": "You've been kicked as a result.",
        "timeout": f"You've been timed out for {LINK_TIMEOUT_MINUTES} minutes as a result.",
        "delete": "",
    }.get(AUTOMOD_LINK_ACTION, "")
    try:
        await member.send(
            f"Your message in **{guild.name}** contained a link to `{domain}`, which isn't allowed here. {action_desc}"
        )
    except discord.Forbidden:
        pass

    outcome = "message deleted only"
    try:
        if AUTOMOD_LINK_ACTION == "softban":
            await guild.ban(member, reason=f"Automod: disallowed link ({domain})", delete_message_seconds=86400)
            await guild.unban(member, reason="Softban complete — messages cleared")
            outcome = "soft-banned (kicked + recent messages cleared)"
        elif AUTOMOD_LINK_ACTION == "ban":
            await guild.ban(member, reason=f"Automod: disallowed link ({domain})")
            outcome = "banned"
        elif AUTOMOD_LINK_ACTION == "kick":
            await member.kick(reason=f"Automod: disallowed link ({domain})")
            outcome = "kicked"
        elif AUTOMOD_LINK_ACTION == "timeout":
            until = discord.utils.utcnow() + datetime.timedelta(minutes=LINK_TIMEOUT_MINUTES)
            await member.timeout(until, reason=f"Automod: disallowed link ({domain})")
            outcome = f"timed out for {LINK_TIMEOUT_MINUTES} minutes"
    except discord.Forbidden:
        outcome = f"flagged, but I lack permission to {AUTOMOD_LINK_ACTION}"

    log_channel = discord.utils.get(guild.text_channels, name=ANTI_NUKE_LOG_CHANNEL)
    if log_channel:
        embed = discord.Embed(title="🔗 Disallowed link posted", color=discord.Color.red())
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Domain", value=domain, inline=True)
        embed.add_field(name="Action taken", value=outcome, inline=True)
        await log_channel.send(embed=embed)


def get_server_log_channel(guild: discord.Guild):
    return discord.utils.get(guild.text_channels, name=SERVER_LOG_CHANNEL_NAME)


# ----------------------------------------------------------------------
# AUTO-REACTIONS & AUTO-THREADS
# ----------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if await run_automod(message):
        return

    if message.channel.name == "suggestions":
        await message.add_reaction("👍")
        await message.add_reaction("👎")
    elif message.channel.name == "bug-reports":
        try:
            title = (message.content[:50] or "bug report").strip()
            await message.create_thread(name=f"🐛 {title}")
        except discord.HTTPException:
            pass

    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    log_channel = get_server_log_channel(message.guild)
    if not log_channel:
        return
    embed = discord.Embed(title="🗑️ Message deleted", color=discord.Color.dark_grey())
    embed.add_field(name="Author", value=message.author.mention, inline=True)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="Content", value=(message.content or "*[no text — embed/attachment]*")[:1024], inline=False)
    await log_channel.send(embed=embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    log_channel = get_server_log_channel(before.guild)
    if not log_channel:
        return
    embed = discord.Embed(title="✏️ Message edited", color=discord.Color.blurple())
    embed.add_field(name="Author", value=before.author.mention, inline=True)
    embed.add_field(name="Channel", value=before.channel.mention, inline=True)
    embed.add_field(name="Before", value=(before.content or "*empty*")[:1024], inline=False)
    embed.add_field(name="After", value=(after.content or "*empty*")[:1024], inline=False)
    await log_channel.send(embed=embed)


# ----------------------------------------------------------------------
# AUTO-ROLE ON JOIN / LEAVE LOGGING
# ----------------------------------------------------------------------
@bot.event
async def on_member_join(member: discord.Member):
    # With GATE_ENABLED, the base role is granted by clicking "I Agree" in #rules instead
    # of automatically here — see RulesGateView.
    if not GATE_ENABLED:
        role = discord.utils.get(member.guild.roles, name=AUTO_ROLE_NAME)
        if role is None:
            role = await member.guild.create_role(name=AUTO_ROLE_NAME, reason="Auto-created base role")
        try:
            await member.add_roles(role, reason="Auto-role on join")
        except discord.Forbidden:
            pass

    log_channel = get_server_log_channel(member.guild)
    if log_channel:
        embed = discord.Embed(title="📥 Member joined", color=discord.Color.green())
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await log_channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    log_channel = get_server_log_channel(member.guild)
    if not log_channel:
        return
    embed = discord.Embed(title="📤 Member left", color=discord.Color.red())
    embed.add_field(name="Member", value=str(member), inline=True)
    joined = discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "unknown"
    embed.add_field(name="Joined", value=joined, inline=True)
    await log_channel.send(embed=embed)


# ----------------------------------------------------------------------
# MODERATION
# ----------------------------------------------------------------------
def is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.manage_guild or any(r.name == STAFF_ROLE_NAME for r in member.roles)


@bot.tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    data = _load(WARNINGS_FILE)
    guild_key = str(interaction.guild_id)
    user_key = str(member.id)
    data.setdefault(guild_key, {}).setdefault(user_key, [])
    data[guild_key][user_key].append(
        {"moderator": str(interaction.user), "reason": reason, "timestamp": datetime.datetime.utcnow().isoformat()}
    )
    _save(WARNINGS_FILE, data)
    count = len(data[guild_key][user_key])

    embed = discord.Embed(title="Member warned", color=discord.Color.orange())
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Total warnings", value=str(count), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await interaction.response.send_message(embed=embed)
    try:
        await member.send(f"You were warned in **{interaction.guild.name}**: {reason}")
    except discord.Forbidden:
        pass


@bot.tree.command(name="warnings", description="View a member's warnings")
@app_commands.describe(member="Member to check")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    data = _load(WARNINGS_FILE)
    entries = data.get(str(interaction.guild_id), {}).get(str(member.id), [])
    if not entries:
        await interaction.response.send_message(f"{member.mention} has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
    for i, entry in enumerate(entries[-10:], 1):
        embed.add_field(name=f"#{i} — {entry['moderator']}", value=entry["reason"], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    await member.kick(reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"Kicked {member.mention}. Reason: {reason}")


@bot.tree.command(name="ban", description="Ban a member")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    await member.ban(reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"Banned {member.mention}. Reason: {reason}")


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="The user ID to unban")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"Unbanned {user}.")
    except (ValueError, discord.NotFound):
        await interaction.response.send_message("Couldn't find that user ID in the ban list.", ephemeral=True)


@bot.tree.command(name="timeout", description="Time out a member")
@app_commands.describe(member="Member to time out", minutes="Duration in minutes", reason="Reason")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason given"):
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minutes.")


@bot.tree.command(name="role-all", description="Give a role to every current member (runs in the background)")
@app_commands.describe(role="Role to give everyone")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_all(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    channel = interaction.channel
    await interaction.response.send_message(
        f"Starting — adding {role.mention} to every member. This can take a while on a big server; "
        f"I'll post here when it's done."
    )

    async def _run():
        added, skipped, failed = 0, 0, 0
        async for member in guild.fetch_members(limit=None):
            if member.bot or role in member.roles:
                skipped += 1
                continue
            try:
                await member.add_roles(role, reason=f"/role-all by {interaction.user}")
                added += 1
            except discord.Forbidden:
                failed += 1
            await asyncio.sleep(0.35)  # stay well under Discord's rate limits
        await channel.send(
            f"✅ /role-all finished — gave {role.mention} to **{added}** members "
            f"({skipped} already had it, {failed} failed due to missing permissions)."
        )

    bot.loop.create_task(_run())


# ----------------------------------------------------------------------
# ANTI-NUKE
# Watches for mass-destructive actions (channel deletes, role deletes, bans,
# rogue webhook creation) done in a short burst, then times the offending
# member out for ANTI_NUKE_TIMEOUT_MINUTES and pings staff.
# Server owners and any ID in OWNER_IDS are always exempt.
# ----------------------------------------------------------------------
_action_log = defaultdict(list)  # (guild_id, user_id, action) -> [timestamps]


async def _get_recent_actor(guild: discord.Guild, action: discord.AuditLogAction, target_id: int = None):
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 15:
                continue
            if target_id is not None and getattr(entry.target, "id", None) != target_id:
                continue
            return entry.user
    except discord.Forbidden:
        return None
    return None


async def _register_action(guild: discord.Guild, user: discord.abc.User, action_name: str):
    if user is None or user.bot:
        return
    if user.id in OWNER_IDS or user.id == guild.owner_id:
        return

    key = (guild.id, user.id, action_name)
    now = time.time()
    _action_log[key] = [t for t in _action_log[key] if now - t < ANTI_NUKE_WINDOW_SECONDS]
    _action_log[key].append(now)

    if len(_action_log[key]) >= ANTI_NUKE_MAX_ACTIONS:
        _action_log[key] = []
        await _punish(guild, user, action_name)


async def _punish(guild: discord.Guild, user: discord.abc.User, action_name: str):
    member = guild.get_member(user.id)
    log_channel = discord.utils.get(guild.text_channels, name=ANTI_NUKE_LOG_CHANNEL)

    if member:
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=ANTI_NUKE_TIMEOUT_MINUTES)
            await member.timeout(until, reason=f"Anti-nuke: mass {action_name}")
            outcome = f"timed out for {ANTI_NUKE_TIMEOUT_MINUTES} minutes"
        except discord.Forbidden:
            outcome = "detected, but I lack permission to time them out"
    else:
        outcome = "detected, but they're no longer in the server"

    if log_channel:
        embed = discord.Embed(
            title="🛡️ Anti-nuke triggered",
            description=f"**{user}** ({user.id}) triggered mass **{action_name}** actions.\nAction taken: **{outcome}**.",
            color=discord.Color.red(),
        )
        await log_channel.send(embed=embed)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    actor = await _get_recent_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
    await _register_action(channel.guild, actor, "channel deletion")


@bot.event
async def on_guild_role_delete(role: discord.Role):
    actor = await _get_recent_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
    await _register_action(role.guild, actor, "role deletion")


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    actor = await _get_recent_actor(guild, discord.AuditLogAction.ban, user.id)
    await _register_action(guild, actor, "member bans")


@bot.event
async def on_webhooks_update(channel: discord.abc.GuildChannel):
    actor = await _get_recent_actor(channel.guild, discord.AuditLogAction.webhook_create)
    await _register_action(channel.guild, actor, "webhook creation")


# ----------------------------------------------------------------------
# STARTUP
# ----------------------------------------------------------------------
@bot.event
async def on_ready():
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(GiveawayView())
    bot.add_view(RulesGateView())
    bot.add_view(SelfRoleView())
    if not giveaway_checker.is_running():
        giveaway_checker.start()
    if not update_member_count.is_running():
        update_member_count.start()
    if (CFX_JOIN_CODE or FIVEM_SERVER_IP) and not update_server_status.is_running():
        update_server_status.start()
    await bot.change_presence(activity=discord.Game(name=SERVER_NAME))
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
        else:
            synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands: {sorted(c.name for c in synced)}")
    except Exception as e:
        # If this prints, NO new commands will show up in Discord until it's fixed.
        print(f"!!! Slash command sync FAILED: {e!r}")
    print(f"Logged in as {bot.user} — ready for {SERVER_NAME}.")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_BOT_TOKEN in your .env file first.")
    bot.run(TOKEN)
