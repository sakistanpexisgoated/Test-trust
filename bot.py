import os
import re
import json
import time
import random
import sqlite3
import unicodedata
import asyncio
import io as _io
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# OWNER & TOKEN SETUP
# =========================================================

OWNER_IDS = {
    1486079948778246256,
    1059150206484090990,
    1368160818620792852,
    1286560808528117820
}

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN was not found in your environment or .env file.")

# =========================================================
# PERMISSION SYSTEM - HIDE COMMANDS FROM NON-OWNERS
# =========================================================

async def owner_only_predicate(interaction: discord.Interaction):
    return interaction.user.id in OWNER_IDS

async def whitelisted_predicate(interaction: discord.Interaction):
    cursor.execute("SELECT 1 FROM troll_whitelist WHERE user_id = ?", (interaction.user.id,))
    is_whitelisted = cursor.fetchone() is not None
    return interaction.user.id in OWNER_IDS or is_whitelisted

# =========================================================
# BOT SETUP
# =========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=["R!", "r!", ",,"],
    intents=intents,
    help_command=None
)

# =========================================================
# DATABASE SETUP
# =========================================================

db = sqlite3.connect("economy.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    wallet INTEGER DEFAULT 100,
    bank INTEGER DEFAULT 0,
    daily_claim REAL DEFAULT 0,
    weekly_claim REAL DEFAULT 0,
    work_claim REAL DEFAULT 0,
    crime_claim REAL DEFAULT 0,
    rob_claim REAL DEFAULT 0,
    luck INTEGER DEFAULT 50
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS mod_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    moderator_id INTEGER NOT NULL,
    target_id INTEGER,
    guild_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    timestamp REAL NOT NULL
)
""")
db.commit()


def log_mod_action(moderator_id: int, target_id: int, guild_id: int, action: str, reason: str = "No reason provided"):
    """Record a moderation action to the DB."""
    try:
        cursor.execute(
            "INSERT INTO mod_actions (moderator_id, target_id, guild_id, action, reason, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (moderator_id, target_id, guild_id, action, reason, time.time()),
        )
        db.commit()
    except Exception:
        pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    moderator_id INTEGER,
    reason TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS marriages (
    user1_id INTEGER PRIMARY KEY,
    user2_id INTEGER NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    moderator_id INTEGER,
    reason TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS server_blacklist (
    guild_id INTEGER PRIMARY KEY,
    moderator_id INTEGER,
    reason TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS troll_whitelist (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    channel_id INTEGER,
    guild_id INTEGER,
    prize TEXT,
    host_id INTEGER,
    end_time REAL,
    winners INTEGER,
    entrants TEXT DEFAULT '[]',
    winners_list TEXT DEFAULT '[]'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    log_channel_id INTEGER,
    support_role_id INTEGER,
    panel_channel_id INTEGER,
    panel_message_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    subject TEXT,
    status TEXT DEFAULT 'open',
    created_at REAL NOT NULL,
    closed_at REAL,
    closed_by INTEGER,
    claimed_by INTEGER
)
""")

db.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS welcome_config (
    guild_id INTEGER PRIMARY KEY,
    welcome_channel_id INTEGER,
    goodbye_channel_id INTEGER,
    dm_welcome INTEGER DEFAULT 0,
    welcome_message TEXT DEFAULT 'Welcome {user} to **{server}**!',
    goodbye_message TEXT DEFAULT '{user} has left {server}.',
    welcome_embed INTEGER DEFAULT 1,
    welcome_color INTEGER DEFAULT 5793266
)
""")

db.commit()


def get_welcome_config(guild_id):
    cursor.execute(
        "SELECT welcome_channel_id, goodbye_channel_id, dm_welcome, welcome_message, goodbye_message, welcome_embed, welcome_color FROM welcome_config WHERE guild_id = ?",
        (guild_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "welcome_channel_id": row[0],
        "goodbye_channel_id": row[1],
        "dm_welcome": bool(row[2]),
        "welcome_message": row[3],
        "goodbye_message": row[4],
        "welcome_embed": bool(row[5]),
        "welcome_color": row[6],
    }


def set_welcome_config(guild_id, **kwargs):
    existing = get_welcome_config(guild_id) or {}
    merged = {**existing, **kwargs}
    cursor.execute(
        """
        INSERT INTO welcome_config
        (guild_id, welcome_channel_id, goodbye_channel_id, dm_welcome, welcome_message, goodbye_message, welcome_embed, welcome_color)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            welcome_channel_id = excluded.welcome_channel_id,
            goodbye_channel_id = excluded.goodbye_channel_id,
            dm_welcome = excluded.dm_welcome,
            welcome_message = excluded.welcome_message,
            goodbye_message = excluded.goodbye_message,
            welcome_embed = excluded.welcome_embed,
            welcome_color = excluded.welcome_color
        """,
        (
            guild_id,
            merged.get("welcome_channel_id"),
            merged.get("goodbye_channel_id"),
            1 if merged.get("dm_welcome") else 0,
            merged.get("welcome_message"),
            merged.get("goodbye_message"),
            1 if merged.get("welcome_embed", True) else 0,
            merged.get("welcome_color", 5793266),
        ),
    )
    db.commit()


def _load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "arialbd.ttf",
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_with_outline(draw, xy, text, font, fill, outline="black", outline_width=3):
    x, y = xy
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


async def build_welcome_card(member, member_count, server_name):
    W, H = 1000, 320
    img = Image.new("RGB", (W, H), (40, 80, 150))
    draw = ImageDraw.Draw(img)

    for i in range(H):
        r = int(35 + (i / H) * 20)
        g = int(70 + (i / H) * 30)
        b = int(140 + (i / H) * 30)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    avatar_size = 200
    avatar_x, avatar_y = 50, (H - avatar_size) // 2

    avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
    avatar_img = Image.open(_io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)

    border = 6
    ring_size = avatar_size + border * 2
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_size, ring_size), fill=(255, 255, 255, 255))
    ring.paste(avatar_img, (border, border), avatar_img)

    img.paste(ring, (avatar_x - border, avatar_y - border), ring)

    text_x = avatar_x + avatar_size + 60
    line1_font = _load_font(38)
    line2_font = _load_font(26)
    line3_font = _load_font(26)

    line1 = f"Welcome {member.display_name}"
    line2 = f"to {server_name}"
    line3 = f"you are the {member_count}th member!"

    lh = 45
    start_y = (H - lh * 3) // 2

    _text_with_outline(draw, (text_x, start_y), line1, line1_font, "white")
    _text_with_outline(draw, (text_x, start_y + lh), line2, line2_font, "white")
    _text_with_outline(draw, (text_x, start_y + lh * 2), line3, line3_font, "white")

    out = _io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


@bot.event
async def on_member_join(member):
    guild = member.guild
    cfg = get_welcome_config(guild.id)
    if not cfg or not cfg["welcome_channel_id"]:
        return

    channel = guild.get_channel(cfg["welcome_channel_id"])
    if not channel:
        return

    count = guild.member_count or len(guild.members)

    try:
        card = await build_welcome_card(member, count, guild.name)
        file = discord.File(card, filename="welcome.png")
        await channel.send(content=member.mention, file=file)
    except Exception as e:
        print(f"[welcome] failed to send card: {e}")
        try:
            await channel.send(f"Welcome {member.mention} to **{guild.name}**!")
        except Exception:
            pass

    if cfg["dm_welcome"]:
        try:
            dm_embed = discord.Embed(
                title=f"👋 Welcome to {guild.name}!",
                description=f"You are member **#{count}**!",
                color=discord.Color(cfg["welcome_color"]),
            )
            if guild.icon:
                dm_embed.set_thumbnail(url=guild.icon.url)
            await member.send(embed=dm_embed)
        except Exception:
            pass


@bot.event
async def on_member_remove(member):
    guild = member.guild
    cfg = get_welcome_config(guild.id)
    if not cfg or not cfg["goodbye_channel_id"]:
        return

    channel = guild.get_channel(cfg["goodbye_channel_id"])
    if not channel:
        return

    count = guild.member_count or len(guild.members)
    text = cfg["goodbye_message"].replace("{user}", member.mention).replace("{server}", guild.name).replace("{count}", str(count))

    try:
        embed = discord.Embed(description=text, color=discord.Color.red())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"{guild.name} • Now {count} members")
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[goodbye] failed: {e}")


@bot.hybrid_group(name="welcome", description="Welcome message settings", invoke_without_command=True)
async def welcome_group(ctx):
    if ctx.invoked_subcommand is None:
        embed = discord.Embed(
            title="👋 Welcome System",
            description=(
                "**Setup:**\n"
                "`/welcome channel #channel`\n"
                "`/welcome goodbye #channel`\n"
                "`/welcome dm on/off`\n"
                "`/welcome color #hex`\n"
                "`/welcome test`\n"
                "`/welcome reset`"
            ),
            color=discord.Color.blurple(),
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)


@welcome_group.command(name="channel", description="Set the welcome channel")
@commands.has_permissions(administrator=True)
async def welcome_channel(ctx, channel: discord.TextChannel):
    set_welcome_config(ctx.guild.id, welcome_channel_id=channel.id)
    embed = discord.Embed(description=f"✅ Welcome channel set to {channel.mention}", color=discord.Color.green())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@welcome_group.command(name="goodbye", description="Set the goodbye channel")
@commands.has_permissions(administrator=True)
async def welcome_goodbye(ctx, channel: discord.TextChannel):
    set_welcome_config(ctx.guild.id, goodbye_channel_id=channel.id)
    embed = discord.Embed(description=f"✅ Goodbye channel set to {channel.mention}", color=discord.Color.green())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@welcome_group.command(name="dm", description="Toggle DM welcome messages")
@app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
@commands.has_permissions(administrator=True)
async def welcome_dm(ctx, state: str):
    enable = state == "on"
    set_welcome_config(ctx.guild.id, dm_welcome=enable)
    embed = discord.Embed(description=f"✅ DM welcome **{'enabled' if enable else 'disabled'}**.", color=discord.Color.green())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@welcome_group.command(name="color", description="Set embed color")
@commands.has_permissions(administrator=True)
async def welcome_color(ctx, hex_code: str):
    hex_clean = hex_code.strip().lstrip("#")
    try:
        color_int = int(hex_clean, 16)
    except ValueError:
        embed = discord.Embed(description="❌ Invalid hex. Use `#5865F2`.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    set_welcome_config(ctx.guild.id, welcome_color=color_int)
    embed = discord.Embed(description=f"✅ Color set to `#{hex_clean.upper()}`.", color=discord.Color(color_int))
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@welcome_group.command(name="test", description="Preview the welcome card")
@commands.has_permissions(administrator=True)
async def welcome_test(ctx):
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)
    count = ctx.guild.member_count or 0
    try:
        card = await build_welcome_card(ctx.author, count, ctx.guild.name)
        file = discord.File(card, filename="welcome.png")
        if ctx.interaction:
            await ctx.interaction.followup.send("✅ Preview:", file=file, ephemeral=True)
        else:
            await ctx.send("✅ Preview:", file=file)
    except Exception as e:
        embed = discord.Embed(description=f"❌ Failed: `{e}`", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)


@welcome_group.command(name="reset", description="Reset all welcome settings")
@commands.has_permissions(administrator=True)
async def welcome_reset(ctx):
    cursor.execute("DELETE FROM welcome_config WHERE guild_id = ?", (ctx.guild.id,))
    db.commit()
    embed = discord.Embed(description="✅ Reset.", color=discord.Color.green())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

cursor.execute("""
CREATE TABLE IF NOT EXISTS allowed_links (
    guild_id INTEGER,
    link_domain TEXT,
    PRIMARY KEY (guild_id, link_domain)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS link_punishment (
    guild_id INTEGER PRIMARY KEY,
    mute_duration INTEGER DEFAULT 300
)
""")

for _owner_id in OWNER_IDS:
    cursor.execute(
        "INSERT OR IGNORE INTO troll_whitelist (user_id, added_by) VALUES (?, ?)",
        (_owner_id, _owner_id)
    )

db.commit()

# =========================================================
# TICKET SYSTEM
# =========================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    log_channel_id INTEGER,
    support_role_id INTEGER,
    panel_channel_id INTEGER,
    panel_message_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    subject TEXT,
    status TEXT DEFAULT 'open',
    created_at REAL NOT NULL,
    closed_at REAL,
    closed_by INTEGER,
    claimed_by INTEGER
)
""")

db.commit()


def get_ticket_config(guild_id):
    cursor.execute(
        "SELECT category_id, log_channel_id, support_role_id, panel_channel_id, panel_message_id FROM ticket_config WHERE guild_id = ?",
        (guild_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "category_id": row[0],
        "log_channel_id": row[1],
        "support_role_id": row[2],
        "panel_channel_id": row[3],
        "panel_message_id": row[4],
    }


def set_ticket_config(guild_id, **kwargs):
    existing = get_ticket_config(guild_id) or {}
    merged = {**existing, **kwargs}
    cursor.execute(
        """
        INSERT INTO ticket_config (guild_id, category_id, log_channel_id, support_role_id, panel_channel_id, panel_message_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id = excluded.category_id,
            log_channel_id = excluded.log_channel_id,
            support_role_id = excluded.support_role_id,
            panel_channel_id = excluded.panel_channel_id,
            panel_message_id = excluded.panel_message_id
        """,
        (
            guild_id,
            merged.get("category_id"),
            merged.get("log_channel_id"),
            merged.get("support_role_id"),
            merged.get("panel_channel_id"),
            merged.get("panel_message_id"),
        ),
    )
    db.commit()


def next_ticket_number(guild_id):
    cursor.execute("SELECT MAX(ticket_number) FROM tickets WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    return (row[0] or 0) + 1


def create_ticket_row(guild_id, channel_id, user_id, subject="No subject"):
    num = next_ticket_number(guild_id)
    cursor.execute(
        "INSERT INTO tickets (guild_id, channel_id, user_id, ticket_number, subject, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (guild_id, channel_id, user_id, num, subject, time.time()),
    )
    db.commit()
    return num


def get_ticket_by_channel(channel_id):
    cursor.execute(
        "SELECT id, guild_id, channel_id, user_id, ticket_number, subject, status, created_at, closed_at, closed_by, claimed_by FROM tickets WHERE channel_id = ?",
        (channel_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "guild_id": row[1], "channel_id": row[2], "user_id": row[3],
        "ticket_number": row[4], "subject": row[5], "status": row[6],
        "created_at": row[7], "closed_at": row[8], "closed_by": row[9], "claimed_by": row[10],
    }


def close_ticket_row(channel_id, closed_by):
    cursor.execute(
        "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? WHERE channel_id = ?",
        (time.time(), closed_by, channel_id),
    )
    db.commit()


def claim_ticket_row(channel_id, staff_id):
    cursor.execute("UPDATE tickets SET claimed_by = ? WHERE channel_id = ?", (staff_id, channel_id))
    db.commit()


def is_staff_member(member):
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_messages:
        return True
    if member.guild_permissions.manage_channels:
        return True
    staff_roles = {"Staff", "Moderator", "Mod", "Admin", "Administrator", "Owner", "Management", "Support"}
    return any(r.name in staff_roles for r in member.roles)


class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="e.g. Resolved, Duplicate, User request",
        required=False,
        max_length=200,
    )

    def __init__(self, channel_id, closed_by_id):
        super().__init__()
        self.channel_id = channel_id
        self.closed_by_id = closed_by_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(
            "🔒 Closing ticket and generating transcript...",
            ephemeral=True,
        )
        reason = self.reason.value.strip() or "No reason provided"
        asyncio.create_task(
            _do_close_ticket(
                interaction=interaction,
                channel_id=self.channel_id,
                closed_by_id=self.closed_by_id,
                reason=reason,
            )
        )


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_button(self, interaction, button):
        if not is_staff_member(interaction.user):
            ticket = get_ticket_by_channel(interaction.channel.id)
            if not ticket or ticket["user_id"] != interaction.user.id:
                return await interaction.response.send_message(
                    "❌ Only staff or the ticket opener can close this.",
                    ephemeral=True,
                )
        await interaction.response.send_modal(
            CloseReasonModal(interaction.channel.id, interaction.user.id)
        )

    @discord.ui.button(label="🙋 Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim")
    async def claim_button(self, interaction, button):
        if not is_staff_member(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can claim tickets.",
                ephemeral=True,
            )
        ticket = get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("❌ Ticket not found.", ephemeral=True)
        if ticket["claimed_by"] == interaction.user.id:
            return await interaction.response.send_message(
                "ℹ️ You've already claimed this ticket.",
                ephemeral=True,
            )
        claim_ticket_row(interaction.channel.id, interaction.user.id)
        try:
            await interaction.channel.edit(
                topic=f"Claimed by {interaction.user} • Ticket #{ticket['ticket_number']}",
            )
        except Exception:
            pass
        embed = discord.Embed(
            description=f"🙋 {interaction.user.mention} has claimed this ticket.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Open a Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def open_button(self, interaction, button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return

        cfg = get_ticket_config(guild.id)
        if not cfg or not cfg["category_id"]:
            return await interaction.followup.send(
                "❌ Ticket system isn't set up properly. Contact an admin.",
                ephemeral=True,
            )

        category = guild.get_channel(cfg["category_id"])
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send(
                "❌ The configured ticket category no longer exists.",
                ephemeral=True,
            )

        support_role = guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None

        cursor.execute(
            "SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'",
            (guild.id, interaction.user.id),
        )
        existing = cursor.fetchone()
        if existing:
            channel = guild.get_channel(existing[0])
            if channel:
                return await interaction.followup.send(
                    f"ℹ️ You already have an open ticket: {channel.mention}",
                    ephemeral=True,
                )
            else:
                cursor.execute(
                    "UPDATE tickets SET status = 'closed', closed_at = ? WHERE channel_id = ?",
                    (time.time(), existing[0]),
                )
                db.commit()

        number = next_ticket_number(guild.id)
        channel_name = f"ticket-{number:04d}-{interaction.user.name}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True,
                manage_messages=True, read_message_history=True,
            ),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, manage_messages=True,
            )

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{number} • Opened by {interaction.user}",
                reason=f"Ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ I don't have permission to create ticket channels.",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed to create ticket: `{e}`",
                ephemeral=True,
            )

        create_ticket_row(guild.id, channel.id, interaction.user.id, subject="Support request")

        mention_str = interaction.user.mention
        if support_role:
            mention_str += f" • {support_role.mention}"

        welcome = discord.Embed(
            title=f"🎟️ Ticket #{number:04d}",
            description=(
                f"Hey {interaction.user.mention}, thanks for opening a ticket!\n\n"
                "Please describe your issue in detail and a staff member will be with you shortly.\n\n"
                "**Tip:** Attach screenshots, video clips, or relevant links if possible."
            ),
            color=discord.Color.blurple(),
        )
        welcome.set_footer(text="Use the buttons below to claim or close this ticket.")

        await channel.send(content=mention_str, embed=welcome, view=TicketControlView())

        await interaction.followup.send(
            f"✅ Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )


async def _do_close_ticket(interaction, channel_id, closed_by_id, reason):
    try:
        guild = getattr(interaction, "guild", None)
        channel = None

        if guild:
            channel = guild.get_channel(channel_id)
        if channel is None and hasattr(interaction, "channel") and interaction.channel:
            channel = interaction.channel
            guild = channel.guild

        if channel is None:
            print(f"[ticket close] channel {channel_id} not found")
            return

        ticket = get_ticket_by_channel(channel_id)
        if not ticket:
            try:
                await channel.delete(reason="Ticket channel removed")
            except Exception as e:
                print(f"[ticket close] delete failed: {e}")
            return

        transcript_lines = []
        transcript_lines.append(f"Ticket #{ticket['ticket_number']:04d} — {channel.name}")
        transcript_lines.append(f"Opened by: {ticket['user_id']} at {datetime.utcfromtimestamp(ticket['created_at'])}")
        transcript_lines.append(f"Closed by: {closed_by_id} at {datetime.utcnow()}")
        transcript_lines.append(f"Reason: {reason}")
        transcript_lines.append("=" * 60)
        transcript_lines.append("")

        try:
            async for msg in channel.history(limit=500, oldest_first=True):
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                content = msg.content or ""
                if msg.embeds:
                    content += f" [embed: {msg.embeds[0].title or 'untitled'}]"
                if msg.attachments:
                    content += " " + " ".join(f"[file: {a.url}]" for a in msg.attachments)
                transcript_lines.append(f"[{ts}] {msg.author}: {content}")
        except Exception as e:
            transcript_lines.append(f"(failed to fetch full history: {e})")

        transcript_text = "\n".join(transcript_lines)
        close_ticket_row(channel_id, closed_by_id)

        cfg = get_ticket_config(guild.id) if guild else None
        log_channel = None
        if cfg and cfg.get("log_channel_id"):
            log_channel = guild.get_channel(cfg["log_channel_id"])

        if log_channel:
            try:
                file = discord.File(
                    _io.BytesIO(transcript_text.encode("utf-8")),
                    filename=f"ticket-{ticket['ticket_number']:04d}.txt",
                )
                opener = guild.get_member(ticket["user_id"])
                closer = guild.get_member(closed_by_id)
                log_embed = discord.Embed(
                    title=f"🔒 Ticket #{ticket['ticket_number']:04d} Closed",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow(),
                )
                log_embed.add_field(name="Opened by", value=opener.mention if opener else f"<@{ticket['user_id']}>", inline=True)
                log_embed.add_field(name="Closed by", value=closer.mention if closer else f"<@{closed_by_id}>", inline=True)
                log_embed.add_field(name="Reason", value=reason[:1024], inline=False)
                log_embed.add_field(name="Channel", value=f"`{channel.name}`", inline=True)
                await log_channel.send(embed=log_embed, file=file)
            except Exception as e:
                print(f"[ticket close] failed to post transcript: {e}")

        try:
            closing_embed = discord.Embed(
                title="🔒 Ticket Closing",
                description=f"Closed by <@{closed_by_id}>\n**Reason:** {reason}\n\nThis channel will be deleted in 5 seconds.",
                color=discord.Color.red(),
            )
            await channel.send(embed=closing_embed)
        except Exception as e:
            print(f"[ticket close] failed to send closing message: {e}")

        await asyncio.sleep(5)

        try:
            await channel.delete(reason=f"Ticket closed by {closed_by_id}: {reason}")
        except Exception as e:
            print(f"[ticket close] failed to delete channel: {e}")

    except Exception as e:
        print(f"[ticket close] unexpected error: {e}")
        import traceback
        traceback.print_exc()


@bot.hybrid_group(name="ticket", description="Ticket system commands", invoke_without_command=True)
async def ticket_group(ctx):
    if ctx.invoked_subcommand is None:
        embed = discord.Embed(
            title="🎟️ Ticket Commands",
            description=(
                "**Setup (admin):**\n"
                "`/ticket setup #category @support-role #log-channel`\n"
                "`/ticket panel #channel`\n"
                "`/ticket settings`\n\n"
                "**Staff:**\n"
                "`/ticket add @user`\n"
                "`/ticket remove @user`\n"
                "`/ticket close [reason]`\n\n"
                "**Users:** Click the panel button to open a ticket."
            ),
            color=discord.Color.blurple(),
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)


@ticket_group.command(name="setup", description="Configure the ticket system (admin only)")
@app_commands.describe(
    category="Category where tickets will be created",
    support_role="Role that can see and manage tickets",
    log_channel="Channel for closed ticket transcripts",
)
@commands.has_permissions(administrator=True)
async def ticket_setup(ctx, category: discord.CategoryChannel, support_role: discord.Role, log_channel: discord.TextChannel = None):
    set_ticket_config(
        ctx.guild.id,
        category_id=category.id,
        support_role_id=support_role.id,
        log_channel_id=log_channel.id if log_channel else None,
    )
    embed = discord.Embed(
        title="✅ Ticket System Configured",
        description=(
            f"**Category:** {category.mention}\n"
            f"**Support Role:** {support_role.mention}\n"
            f"**Log Channel:** {log_channel.mention if log_channel else '*(none)*'}"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Run /ticket panel #channel to post the panel")
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@ticket_group.command(name="settings", description="View current ticket configuration")
async def ticket_settings(ctx):
    cfg = get_ticket_config(ctx.guild.id)
    if not cfg:
        embed = discord.Embed(description="❌ Ticket system isn't set up yet.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    category = ctx.guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
    role = ctx.guild.get_role(cfg["support_role_id"]) if cfg["support_role_id"] else None
    log = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
    panel_ch = ctx.guild.get_channel(cfg["panel_channel_id"]) if cfg["panel_channel_id"] else None

    embed = discord.Embed(title="🎟️ Ticket Settings", color=discord.Color.blurple())
    embed.add_field(name="Category", value=category.mention if category else "*(not set)*", inline=True)
    embed.add_field(name="Support Role", value=role.mention if role else "*(not set)*", inline=True)
    embed.add_field(name="Log Channel", value=log.mention if log else "*(not set)*", inline=True)
    embed.add_field(name="Panel Channel", value=panel_ch.mention if panel_ch else "*(not set)*", inline=True)

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)


@ticket_group.command(name="panel", description="Post the ticket creation panel in a channel")
@app_commands.describe(channel="Channel to post the panel in")
@commands.has_permissions(administrator=True)
async def ticket_panel(ctx, channel: discord.TextChannel = None):
    target = channel or ctx.channel
    cfg = get_ticket_config(ctx.guild.id)
    if not cfg or not cfg["category_id"]:
        embed = discord.Embed(description="❌ Run `/ticket setup` first.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="🎟️ Support Tickets",
        description=(
            "Need help? Click the button below to open a ticket.\n\n"
            "**A private channel will be created** where you can talk to our staff team.\n\n"
            "⚠️ *Don't open tickets for fun — you may be muted.*"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Powered by {bot.user.name}")

    view = TicketPanelView()
    msg = await target.send(embed=embed, view=view)
    set_ticket_config(ctx.guild.id, panel_channel_id=target.id, panel_message_id=msg.id)

    if ctx.interaction:
        await ctx.interaction.followup.send(f"✅ Panel posted in {target.mention}.", ephemeral=True)
    else:
        await ctx.send(f"✅ Panel posted in {target.mention}.", delete_after=5)


@ticket_group.command(name="add", description="Add a user to the current ticket")
@app_commands.describe(member="User to add")
async def ticket_add(ctx, member: discord.Member):
    if not isinstance(ctx.author, discord.Member) or not is_staff_member(ctx.author):
        embed = discord.Embed(description="❌ Only staff can add users to tickets.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    ticket = get_ticket_by_channel(ctx.channel.id)
    if not ticket:
        embed = discord.Embed(description="❌ This isn't a ticket channel.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    try:
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        embed = discord.Embed(description=f"✅ {member.mention} added to the ticket.", color=discord.Color.green())
    except Exception as e:
        embed = discord.Embed(description=f"❌ Failed to add: `{e}`", color=discord.Color.red())

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@ticket_group.command(name="remove", description="Remove a user from the current ticket")
@app_commands.describe(member="User to remove")
async def ticket_remove(ctx, member: discord.Member):
    if not isinstance(ctx.author, discord.Member) or not is_staff_member(ctx.author):
        embed = discord.Embed(description="❌ Only staff can remove users from tickets.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    ticket = get_ticket_by_channel(ctx.channel.id)
    if not ticket:
        embed = discord.Embed(description="❌ This isn't a ticket channel.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if member.id == ticket["user_id"]:
        embed = discord.Embed(description="❌ Can't remove the ticket opener.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    try:
        await ctx.channel.set_permissions(member, overwrite=None)
        embed = discord.Embed(description=f"✅ {member.mention} removed from the ticket.", color=discord.Color.green())
    except Exception as e:
        embed = discord.Embed(description=f"❌ Failed to remove: `{e}`", color=discord.Color.red())

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)


@ticket_group.command(name="close", description="Close the current ticket")
@app_commands.describe(reason="Reason for closing")
async def ticket_close(ctx, *, reason: str = "No reason provided"):
    ticket = get_ticket_by_channel(ctx.channel.id)
    if not ticket:
        embed = discord.Embed(description="❌ This isn't a ticket channel.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if not is_staff_member(ctx.author) and ctx.author.id != ticket["user_id"]:
        embed = discord.Embed(description="❌ Only staff or the ticket opener can close this.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if ctx.interaction:
        await ctx.interaction.response.send_message("🔒 Closing ticket...", ephemeral=True)
    else:
        await ctx.send("🔒 Closing ticket...")

    asyncio.create_task(
        _do_close_ticket(
            interaction=ctx,
            channel_id=ctx.channel.id,
            closed_by_id=ctx.author.id,
            reason=reason,
        )
    )
# =========================================================
# MEMORY CACHE & TROLL PANEL STATES
# =========================================================

sniped_messages = {}
edited_messages = {}
afk_users = {}
troll_settings = {}
server_backups = {}
link_check_enabled = {}

def is_troll_whitelisted(user_id):
    if user_id in OWNER_IDS:
        return True
    cursor.execute("SELECT 1 FROM troll_whitelist WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

async def time_sleep_wrapper(seconds):
    await asyncio.sleep(seconds)

def _is_server_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    mod_role_names = {"Moderator", "Admin", "Owner"}
    return any(role.name in mod_role_names for role in member.roles)

def extract_domain(link: str) -> str:
    clean = re.sub(r'^https?://', '', link)
    clean = re.sub(r'^www\.', '', clean)
    domain = clean.split('/')[0].split('?')[0].split('#')[0]
    return domain.lower() if domain else None

def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    else:
        hours = seconds // 3600
        return f"{hours}h"
        
async def check_nsfw(ctx):
    if not ctx.channel.is_nsfw():
        embed = discord.Embed(
            description=f"{ctx.author.mention} This command can be only used in nsfw channels.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                try:
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                except Exception:
                    pass
        else:
            await ctx.send(embed=embed, delete_after=5)
        return False
    return True

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    if message.channel.id not in sniped_messages:
        sniped_messages[message.channel.id] = []
    sniped_messages[message.channel.id].append({
        "content": message.content,
        "author": message.author,
        "attachments": [att.url for att in message.attachments]
    })

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    if before.channel.id not in edited_messages:
        edited_messages[before.channel.id] = []
    edited_messages[before.channel.id].append({
        "before": before.content,
        "after": after.content,
        "author": before.author
    })
    if len(edited_messages[before.channel.id]) > 5:
        edited_messages[before.channel.id].pop(0)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    is_standalone_bot_mention = (
        bot.user in message.mentions
        and not message.mention_everyone
        and message.reference is None
        and message.content.strip() in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>")
    )
    
    if message.mentions:
        for member in message.mentions:
            if member.id in afk_users:
                afk_users[member.id]["mentions"].append({
                    "author_name": message.author.display_name,
                    "content": message.content,
                    "jump_url": message.jump_url,
                    "time": time.time()
                })
                data = afk_users[member.id]
                embed = discord.Embed(
                    description=f"💤 **{member.display_name}** is AFK: {data['reason']} (<t:{int(data['time'])}:R>)",
                    color=discord.Color.from_rgb(30, 31, 34)
                )
                await message.channel.send(embed=embed)
                
    if message.author.id in afk_users:
        data = afk_users.pop(message.author.id)
        duration_sec = int(time.time() - data["time"])
        
        if duration_sec < 60:
            dur_str = f"{duration_sec} second{'s' if duration_sec != 1 else ''}"
        elif duration_sec < 3600:
            minutes = duration_sec // 60
            seconds = duration_sec % 60
            dur_str = f"{minutes} minute{'s' if minutes != 1 else ''} and {seconds} second{'s' if seconds != 1 else ''}"
        else:
            hours = duration_sec // 3600
            minutes = (duration_sec % 3600) // 60
            dur_str = f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"

        embed = discord.Embed(
            description=f"👋 Welcome back, {message.author.mention}! I removed your AFK. You were AFK for **{dur_str}.**",
            color=discord.Color.gold()
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)

        if data["mentions"]:
            mentions_text = []
            for m in data["mentions"][:10]:
                time_ago = int(time.time() - m["time"])
                if time_ago < 60:
                    time_str = f"{time_ago} seconds ago"
                elif time_ago < 3600:
                    time_str = f"{time_ago // 60} minutes ago"
                else:
                    time_str = f"{time_ago // 3600} hours ago"
                
                mentions_text.append(f"**{m['author_name']}**, {time_str}\n[Click to view message]({m['jump_url']})")
            
            embed.add_field(
                name=f"You received {len(data['mentions'])} mention(s)",
                value="\n\n".join(mentions_text),
                inline=False
            )

        await message.reply(embed=embed, mention_author=False)
            
        if links:
            cursor.execute("SELECT link_domain FROM allowed_links WHERE guild_id = ?", (message.guild.id,))
            allowed = [row[0] for row in cursor.fetchall()]
            
            for link in links:
                domain = extract_domain(link)
                if domain and domain not in allowed:
                    cursor.execute("SELECT mute_duration FROM link_punishment WHERE guild_id = ?", (message.guild.id,))
                    row = cursor.fetchone()
                    duration = row[0] if row else 300
                    
                    try:
                        await message.delete()
                        await message.author.timeout(timedelta(seconds=duration), reason=f"Sent unauthorized link: {domain}")
                        await message.channel.send(f"🔇 {message.author.mention} was muted for {format_duration(duration)} for sending an unauthorized link: `{domain}`")
                    except Exception as e:
                        await message.channel.send(f"❌ Failed to mute {message.author.mention}: {e}")
                    break

    await bot.process_commands(message)

# =========================================================
# HELP COMMAND
# =========================================================
@bot.hybrid_command(name="help", description="Display all available commands")
async def help(ctx):
    embed = discord.Embed(
        title="📋 Rynx Bot Commands",
        description="**Prefix:** `R!` or `/`",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    embed.add_field(name="💰 Economy", value="`balance`, `daily`, `work`, `gamble`, `dice`, `slots`, `crime`, `rob`, `pay`, `deposit`, `withdraw`", inline=False)
    embed.add_field(name="🎉 Fun", value="`cf`, `8ball`, `gayrate`, `pp`, `iq`, `roast`, `kiss`, `pat`, `tape`, `gif`, `hack`, `mock`, `fraktur`, `pfps`, `memes`", inline=False)
    embed.add_field(name="🎮 Games", value="`brainrot_dice`, `guess`, `country`, `debate`", inline=False)
    embed.add_field(name="⚽ Football", value="`setchannel`, `spawn`, `collect`, `pack`, `sell`, `collection`, `trade`", inline=False)
    embed.add_field(name="🛡️ Moderation", value="`ban`, `unban`, `kick`, `mute`, `unmute`, `warn`, `clear`, `purge`, `slowmode`, `poll`, `say`, `embed`, `snipe`, `editsnipe`, `avatar`, `afk`, `steal`", inline=False)
    embed.add_field(name="👑 Admin", value="`goon`, `setup`, `backup`, `ghostping`, `fakenuke`", inline=False)
    embed.add_field(name="🔗 Link Filter", value="`allowed add`, `allowed remove`, `allowed list`, `allowed enable`, `allowed disable`, `allowed time`", inline=False)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_user_econ(user_id):
    cursor.execute("SELECT wallet, bank, luck FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO users (user_id, wallet, bank, daily_claim, weekly_claim, work_claim, crime_claim, rob_claim, luck) VALUES (?, 100, 0, 0, 0, 0, 0, 0, 50)",
            (user_id,)
        )
        db.commit()
        return 100, 0, 50
    return row[0], row[1], row[2]

def get_user_luck(user_id):
    cursor.execute("SELECT luck FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT INTO users (user_id, wallet, bank, daily_claim, weekly_claim, work_claim, crime_claim, rob_claim, luck) VALUES (?, 100, 0, 0, 0, 0, 0, 0, 50)", (user_id,))
        db.commit()
        return 50
    return row[0]

def update_wallet(user_id, amount):
    wallet, bank, luck = get_user_econ(user_id)
    new_wallet = wallet + amount
    cursor.execute("UPDATE users SET wallet = ? WHERE user_id = ?", (new_wallet, user_id))
    db.commit()

def get_global_rank(user_id):
    cursor.execute("SELECT user_id, (wallet + bank) as net FROM users ORDER BY net DESC")
    rows = cursor.fetchall()
    for index, row in enumerate(rows, start=1):
        if row[0] == user_id:
            return index
    return "#N/A"

def parse_duration(text):
    pattern = r"^(\d+)\s*(s|m|h|d|w)$"
    match = re.match(pattern, text.lower().strip())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return amount * multipliers[unit]

# =========================================================
# GLOBAL BLACKLIST & SERVER BLACKLIST CHECK
# =========================================================

@bot.check
async def globally_block_blacklisted(ctx):
    if ctx.guild:
        cursor.execute("SELECT 1 FROM server_blacklist WHERE guild_id = ?", (ctx.guild.id,))
        if cursor.fetchone():
            return False

    cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (ctx.author.id,))
    if cursor.fetchone():
        embed = discord.Embed(
            description=f"⚠️ {ctx.author.mention}: You are **globally blacklisted** from using the bot.",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            try:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass
        else:
            try:
                await ctx.send(embed=embed, delete_after=5)
            except Exception:
                pass
        return False
    return True
# =========================================================
# ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        if ctx.command and ctx.command.name in ["mute", "unmute"]:
            return
        embed = discord.Embed(
            title="❌ Permission Denied",
            description=f"{ctx.author.mention} You dont have moderator permissions.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.MissingRequiredArgument):
        cmd_name = ctx.command.name if ctx.command else "unknown"
        
        embed = discord.Embed(
            title="⚠️ Wrong Usage",
            color=discord.Color.orange()
        )
        
        # Command-specific error messages
        if cmd_name == "ban":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to ban."
            
        elif cmd_name == "unban":
            embed.description = f"❌ {ctx.author.mention} Please provide a user ID to unban."
            
        elif cmd_name == "mute":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to mute."
            
        elif cmd_name == "kick":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to kick."
            
        elif cmd_name == "warn":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to warn."
            
        elif cmd_name == "role":
            embed.description = f"❌ {ctx.author.mention} Please mention a user and role name."
            
        elif cmd_name == "pay":
            embed.description = f"❌ {ctx.author.mention} Please mention a user and amount."
            
        elif cmd_name == "gamble":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to gamble."
            
        elif cmd_name == "dice":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to bet."
            
        elif cmd_name == "slots":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to bet."
            
        elif cmd_name == "rob":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to rob."
            
        elif cmd_name == "deposit":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to deposit."
            
        elif cmd_name == "withdraw":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to withdraw."
            
        elif cmd_name == "marry":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to marry."
            
        elif cmd_name == "kiss":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to kiss."
            
        elif cmd_name == "slap":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to slap."
            
        elif cmd_name == "spank":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to spank."
            
        elif cmd_name == "pat":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to pat."
            
        elif cmd_name == "tape":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to tape."
            
        elif cmd_name == "hack":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to hack."
            
        elif cmd_name == "poll":
            embed.description = f"❌ {ctx.author.mention} Please provide a question for the poll."
            
        elif cmd_name == "say":
            embed.description = f"❌ {ctx.author.mention} Please provide a message to say."
            
        elif cmd_name == "embed":
            embed.description = f"❌ {ctx.author.mention} Please provide title and description."
            
        elif cmd_name == "clear":
            embed.description = f"❌ {ctx.author.mention} Please provide the number of messages to clear."
            
        elif cmd_name == "purge":
            embed.description = f"❌ {ctx.author.mention} Please provide the number of messages to purge."
            
        elif cmd_name == "slowmode":
            embed.description = f"❌ {ctx.author.mention} Please provide the seconds for slowmode."
            
        elif cmd_name == "gif":
            embed.description = f"❌ {ctx.author.mention} Please provide a search term for the GIF."
            
        elif cmd_name == "8ball":
            embed.description = f"❌ {ctx.author.mention} Please ask the 8ball a question."
            
        elif cmd_name == "mock":
            embed.description = f"❌ {ctx.author.mention} Please provide text to mock."
            
        elif cmd_name == "ghostping":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to ghost ping."
            
        elif cmd_name == "fakenuke":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to fake nuke."
            
        elif cmd_name == "masscreate":
            embed.description = f"❌ {ctx.author.mention} Please provide count and channel name."
            
        elif cmd_name == "hide":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to hide from."
            
        elif cmd_name == "seek":
            embed.description = f"❌ {ctx.author.mention} Please mention a channel to seek."
            
        elif cmd_name == "stealurl":
            embed.description = f"❌ {ctx.author.mention} Please provide an emoji link or ID."
            
        elif cmd_name == "setup":
            embed.description = f"❌ {ctx.author.mention} Please provide a style for the setup."
            
        elif cmd_name == "blacklist":
            embed.description = f"❌ {ctx.author.mention} Please provide a user to blacklist."
            
        elif cmd_name == "whitelist":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to whitelist."
            
        elif cmd_name == "unwhitelist":
            embed.description = f"❌ {ctx.author.mention} Please mention a user to unwhitelist."
            
        elif cmd_name == "country":
            embed.description = f"❌ {ctx.author.mention} Please start the country game."
            
        elif cmd_name == "brainrot_dice":
            embed.description = f"❌ {ctx.author.mention} Please provide an amount to bet."
            
        elif cmd_name == "trollpanel":
            embed.description = f"❌ {ctx.author.mention} Please open the troll panel."
            
        elif cmd_name == "afk":
            embed.description = f"❌ {ctx.author.mention} Please provide a reason for being AFK."
            
        elif cmd_name == "avatar":
            embed.description = f"❌ {ctx.author.mention} Please provide a user to check avatar."
            
        elif cmd_name == "cf":
            embed.description = f"❌ {ctx.author.mention} Please flip a coin."
            
        elif cmd_name == "gayrate":
            embed.description = f"❌ {ctx.author.mention} Please provide a user to check gay rate."
            
        elif cmd_name == "pp":
            embed.description = f"❌ {ctx.author.mention} Please provide a user to check pp size."
            
        elif cmd_name == "iq":
            embed.description = f"❌ {ctx.author.mention} Please provide a user to check IQ."
            
        elif cmd_name == "roast":
            embed.description = f"❌ {ctx.author.mention} Please mention someone to roast."
            
        elif cmd_name == "snipe":
            embed.description = f"❌ {ctx.author.mention} Please provide how many messages to snipe."
            
        elif cmd_name == "editsnipe":
            embed.description = f"❌ {ctx.author.mention} Please snipe the last edited message."
            
        elif cmd_name == "divorce":
            embed.description = f"❌ {ctx.author.mention} Please divorce your spouse."
            
        elif cmd_name == "work":
            embed.description = f"❌ {ctx.author.mention} Please start working."
            
        elif cmd_name == "crime":
            embed.description = f"❌ {ctx.author.mention} Please commit a crime."
            
        elif cmd_name == "daily":
            embed.description = f"❌ {ctx.author.mention} Please claim your daily reward."
            
        elif cmd_name == "balance":
            embed.description = f"❌ {ctx.author.mention} Please check your balance."
            
        elif cmd_name == "luck":
            embed.description = f"❌ {ctx.author.mention} Please check your luck."
            
        elif cmd_name == "pfps":
            embed.description = f"❌ {ctx.author.mention} Please get a random PFP."
            
        elif cmd_name == "memes":
            embed.description = f"❌ {ctx.author.mention} Please get a random meme."
            
        elif cmd_name == "fraktur":
            embed.description = f"❌ {ctx.author.mention} Please provide text to convert."
            
        else:
            embed.description = f"❌ {ctx.author.mention} You are missing an argument!"
        
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BadArgument):
        cmd_name = ctx.command.name if ctx.command else "unknown"
        
        embed = discord.Embed(
            title="❌ Invalid Argument",
            color=discord.Color.red()
        )
        
        if cmd_name in ["ban", "kick", "mute", "unmute", "warn", "rob", "marry", "kiss", "slap", "spank", "pat", "tape", "hack", "hide", "pay", "whitelist", "unwhitelist"]:
            embed.description = f"❌ {ctx.author.mention} Invalid member mentioned! Please mention a valid user."
        elif cmd_name == "role":
            embed.description = f"❌ {ctx.author.mention} Invalid member or role! Please check the member and role name."
        elif cmd_name in ["gamble", "dice", "slots", "pay", "deposit", "withdraw"]:
            embed.description = f"❌ {ctx.author.mention} Invalid amount! Please enter a valid number."
        elif cmd_name in ["gif", "mock", "say", "poll", "embed"]:
            embed.description = f"❌ {ctx.author.mention} Invalid input! Please check your arguments."
        elif cmd_name == "seek":
            embed.description = f"❌ {ctx.author.mention} Invalid channel! Please mention a valid text channel."
        else:
            embed.description = f"❌ {ctx.author.mention} You provided an invalid argument."
        
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BotMissingPermissions):
        embed = discord.Embed(
            title="❌ Bot Permission Error",
            description=f"{ctx.author.mention} I am missing the required permissions.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.MemberNotFound):
        embed = discord.Embed(
            title="❌ Member Not Found",
            description=f"{ctx.author.mention} I couldn't find that member.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.CommandOnCooldown):
        embed = discord.Embed(
            title="⏳ Cooldown",
            description=f"{ctx.author.mention} Please wait `{error.retry_after:.1f}` seconds.",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.RoleNotFound):
        embed = discord.Embed(
            title="❌ Role Not Found",
            description=f"{ctx.author.mention} I couldn't find that role.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.NotOwner):
        embed = discord.Embed(
            title="👑 Owner Only",
            description=f"{ctx.author.mention} Only the bot owner can use this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.NSFWChannelRequired):
        embed = discord.Embed(
            title="🔞 NSFW Only",
            description=f"{ctx.author.mention} This command can only be used in NSFW channels.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    embed = discord.Embed(
        title="❌ Error",
        description=f"{ctx.author.mention} Something went wrong.",
        color=discord.Color.red()
    )
    if ctx.interaction:
        try:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            return
    return await ctx.send(embed=embed)
# =========================================================
# ALLOWED LINKS COMMANDS
# =========================================================

@bot.hybrid_command(name="allowed", aliases=["links"], description="Manage allowed links for the server")
@commands.has_permissions(administrator=True)
async def allowed(ctx, action: str = None, *, link: str = None):
    guild_id = ctx.guild.id
    
    if action is None:
        embed = discord.Embed(
            title="🔗 Allowed Links",
            description="**Commands:**\n`R!allowed link <url>` - Add a link\n`R!allowed unlink <url>` - Remove a link\n`R!allowed list` - Show allowed links\n`R!allowed enable` - Turn ON filtering\n`R!allowed disable` - Turn OFF filtering\n`R!allowed time <duration>` - Set mute time",
            color=discord.Color.blue()
        )
        status = "✅ ENABLED" if link_check_enabled.get(guild_id, False) else "❌ DISABLED"
        cursor.execute("SELECT mute_duration FROM link_punishment WHERE guild_id = ?", (guild_id,))
        row = cursor.fetchone()
        duration = format_duration(row[0]) if row else "5m (default)"
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Mute Duration", value=duration, inline=True)
        embed.set_footer(text="Server admins only")
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if action.lower() == "link":
        if not link:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Please provide a link to add.\nExample: `R!allowed link roblox.com`", ephemeral=True)
            return await ctx.send("❌ Please provide a link to add.\nExample: `R!allowed link roblox.com`")
        
        domain = extract_domain(link)
        if not domain:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Invalid link format. Try: `R!allowed link roblox.com`", ephemeral=True)
            return await ctx.send("❌ Invalid link format. Try: `R!allowed link roblox.com`")
        
        cursor.execute("INSERT OR IGNORE INTO allowed_links (guild_id, link_domain) VALUES (?, ?)", (guild_id, domain))
        db.commit()
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"✅ `{domain}` added to allowed links!")
        else:
            await ctx.send(f"✅ `{domain}` added to allowed links!")
    
    elif action.lower() == "unlink":
        if not link:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Please provide a link to remove.\nExample: `R!allowed unlink roblox.com`", ephemeral=True)
            return await ctx.send("❌ Please provide a link to remove.\nExample: `R!allowed unlink roblox.com`")
        
        domain = extract_domain(link)
        if not domain:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Invalid link format.", ephemeral=True)
            return await ctx.send("❌ Invalid link format.")
        
        cursor.execute("DELETE FROM allowed_links WHERE guild_id = ? AND link_domain = ?", (guild_id, domain))
        db.commit()
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"✅ `{domain}` removed from allowed links!")
        else:
            await ctx.send(f"✅ `{domain}` removed from allowed links!")
    
    elif action.lower() == "list":
        cursor.execute("SELECT link_domain FROM allowed_links WHERE guild_id = ?", (guild_id,))
        rows = cursor.fetchall()
        
        if not rows:
            if ctx.interaction:
                await ctx.interaction.response.send_message("📋 No links are allowed. All links will be blocked.", ephemeral=True)
            else:
                await ctx.send("📋 No links are allowed. All links will be blocked.")
            return
        
        domains = "\n".join([f"• `{row[0]}`" for row in rows])
        embed = discord.Embed(
            title="📋 Allowed Links",
            description=domains,
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Total: {len(rows)} allowed links")
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
    
    elif action.lower() == "enable":
        link_check_enabled[guild_id] = True
        if ctx.interaction:
            await ctx.interaction.response.send_message("✅ Link filtering **ENABLED**! Links not in the whitelist will be muted.")
        else:
            await ctx.send("✅ Link filtering **ENABLED**! Links not in the whitelist will be muted.")
    
    elif action.lower() == "disable":
        link_check_enabled[guild_id] = False
        if ctx.interaction:
            await ctx.interaction.response.send_message("✅ Link filtering **DISABLED**! All links are allowed.")
        else:
            await ctx.send("✅ Link filtering **DISABLED**! All links are allowed.")
    
    elif action.lower() == "time":
        if not link:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Please provide a duration.\nExample: `R!allowed time 10m`", ephemeral=True)
            return await ctx.send("❌ Please provide a duration.\nExample: `R!allowed time 10m`")
        
        seconds = parse_duration(link)
        if not seconds:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Invalid duration. Use: `10m`, `30m`, `1h`", ephemeral=True)
            return await ctx.send("❌ Invalid duration. Use: `10m`, `30m`, `1h`")
        
        if seconds < 60:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Minimum is 1 minute.", ephemeral=True)
            return await ctx.send("❌ Minimum is 1 minute.")
        
        if seconds > 3600:
            if ctx.interaction:
                return await ctx.interaction.response.send_message("❌ Maximum is 1 hour.", ephemeral=True)
            return await ctx.send("❌ Maximum is 1 hour.")
        
        cursor.execute("INSERT OR REPLACE INTO link_punishment (guild_id, mute_duration) VALUES (?, ?)", (guild_id, seconds))
        db.commit()
        
        duration_str = format_duration(seconds)
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"✅ Mute duration set to **{duration_str}**!")
        else:
            await ctx.send(f"✅ Mute duration set to **{duration_str}**!")
    
    else:
        if ctx.interaction:
            await ctx.interaction.response.send_message("❌ Invalid command.\nUse: `link`, `unlink`, `list`, `enable`, `disable`, or `time`", ephemeral=True)
        else:
            await ctx.send("❌ Invalid command.\nUse: `link`, `unlink`, `list`, `enable`, `disable`, or `time`")

# =========================================================
# LUCK COMMAND
# =========================================================

@bot.hybrid_command(name="luck", description="Check or set a user's luck (Owner only)")
@app_commands.check(owner_only_predicate)
async def luck(ctx, member: discord.Member = None, amount: int = None):
    target = member or ctx.author
    
    if amount is not None:
        if ctx.author.id not in OWNER_IDS:
            embed = discord.Embed(description="❌ Only the bot owner can set luck!", color=discord.Color.red())
            if ctx.interaction:
                return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            return await ctx.send(embed=embed)
        
        if amount < 0:
            amount = 0
        elif amount > 100:
            amount = 100
        
        cursor.execute("UPDATE users SET luck = ? WHERE user_id = ?", (amount, target.id))
        db.commit()
        
        embed = discord.Embed(
            title="🍀 Luck Set",
            description=f"{target.mention}'s luck has been set to **{amount}%**!",
            color=discord.Color.green()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed)
        return await ctx.send(embed=embed)
    
    luck_val = get_user_luck(target.id)
    embed = discord.Embed(
        title="🍀 Luck",
        description=f"{target.mention} has **{luck_val}%** luck!",
        color=discord.Color.gold()
    )
    embed.set_footer(text="Higher luck = better gambling odds")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# TROLL PANEL MODALS & VIEW
# =========================================================

class GhostPingModal(discord.ui.Modal, title="Ghost Ping Tool"):
    member_input = discord.ui.TextInput(label="Member ID or Mention", placeholder="e.g. 10080330264 or @User", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        val = self.member_input.value.strip()
        target_id = re.sub(r'\D', '', val)
        try:
            target = await interaction.guild.fetch_member(int(target_id))
        except Exception:
            target = None

        if not target:
            return await interaction.response.send_message("Could not find that member.", ephemeral=True)

        await interaction.response.send_message("😂 Ghost pinging...", ephemeral=True)
        msg = await interaction.channel.send(f"{target.mention}")
        try:
            await msg.delete()
        except Exception:
            pass

class MockModal(discord.ui.Modal, title="Mock Text Tool"):
    text_input = discord.ui.TextInput(label="Text to Mock", placeholder="Type something...", style=discord.TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.text_input.value
        mocked_text = "".join(
            c.upper() if i % 2 == 0 else c.lower()
            for i, c in enumerate(text)
        )
        embed = discord.Embed(description=f"🚿 {mocked_text}", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

class TrollPanelView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id
        if self.user_id not in troll_settings:
            troll_settings[self.user_id] = {"dice": False, "slots": False, "brainrot": False}
        self.update_buttons()

    def update_buttons(self):
        settings = troll_settings[self.user_id]
        self.dice_btn.label = f"Dice Rig: {'ON 🟢' if settings['dice'] else 'OFF 🔴'}"
        self.slots_btn.label = f"Slots Rig: {'ON 🟢' if settings['slots'] else 'OFF 🔴'}"
        self.brainrot_btn.label = f"Brainrot Rig: {'ON 🟢' if settings['brainrot'] else 'OFF 🔴'}"

    @discord.ui.button(label="Dice Rig", style=discord.ButtonStyle.secondary, row=0)
    async def dice_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        troll_settings[self.user_id]["dice"] = not troll_settings[self.user_id]["dice"]
        self.update_buttons()
        embed = interaction.message.embeds[0]
        embed.description = f"🎛️ **Troll Panel Settings for <@{self.user_id}>**\nConfigure your rigged game outcomes & troll tools below:"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Slots Rig", style=discord.ButtonStyle.secondary, row=0)
    async def slots_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        troll_settings[self.user_id]["slots"] = not troll_settings[self.user_id]["slots"]
        self.update_buttons()
        embed = interaction.message.embeds[0]
        embed.description = f"🎛️ **Troll Panel Settings for <@{self.user_id}>**\nConfigure your rigged game outcomes & troll tools below:"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Brainrot Rig", style=discord.ButtonStyle.secondary, row=0)
    async def brainrot_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        troll_settings[self.user_id]["brainrot"] = not troll_settings[self.user_id]["brainrot"]
        self.update_buttons()
        embed = interaction.message.embeds[0]
        embed.description = f"🎛️ **Troll Panel Settings for <@{self.user_id}>**\nConfigure your rigged game outcomes & troll tools below:"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="👻 Ghost Ping", style=discord.ButtonStyle.danger, row=1)
    async def ghostping_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        await interaction.response.send_modal(GhostPingModal())

    @discord.ui.button(label="🚿 Mock Text", style=discord.ButtonStyle.primary, row=1)
    async def mock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        await interaction.response.send_modal(MockModal())

    @discord.ui.button(label="🚨 Fake Nuke", style=discord.ButtonStyle.danger, row=1)
    async def fakenuke_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your troll panel!", ephemeral=True)
        
        embed = discord.Embed(
            title="🚨 **WARNING: SERVER NUKE IN PROGRESS** 🚨",
            description=f"Thank you {interaction.user.mention} for nuking this server the channels will be deleted soon huzzs.",
            color=discord.Color.red()
        )
        embed.set_image(url="https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbTZibHhwcTd0c2k1a3dta3JrOHY4ZjVsdWZsZjJlMnIzNW96ajVsaiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3oKIPiqfUtLCnIKxRS/giphy.gif")
        await interaction.response.send_message("🚨 Initiating fake nuke...", ephemeral=True)
        await interaction.channel.send(embed=embed)

@bot.hybrid_command(name="whitelist", description="Whitelist a member to use the Troll Panel and troll tools")
@app_commands.check(owner_only_predicate)
async def whitelist(ctx, member: discord.Member):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="👑 Only the two bot owners can manage the Troll Panel whitelist.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if member.bot:
        embed = discord.Embed(description="Bots cannot be added to the Troll Panel whitelist.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    cursor.execute("INSERT OR IGNORE INTO troll_whitelist (user_id, added_by) VALUES (?, ?)", (member.id, ctx.author.id))
    db.commit()

    if cursor.rowcount == 0:
        description = f"ℹ️ {member.mention} is already whitelisted for the Troll Panel."
        color = discord.Color.blurple()
    else:
        description = f"🎉 {member.mention} has been **whitelisted** for the Troll Panel and troll tools."
        color = discord.Color.green()

    embed = discord.Embed(description=description, color=color)
    if ctx.interaction:
        return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unwhitelist", description="Remove a member from the Troll Panel whitelist")
@app_commands.check(owner_only_predicate)
async def unwhitelist(ctx, member: discord.Member):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="👑 Only the two bot owners can manage the Troll Panel whitelist.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if member.id in OWNER_IDS:
        embed = discord.Embed(description="👑 The two bot owners are permanently whitelisted and cannot be removed.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    cursor.execute("DELETE FROM troll_whitelist WHERE user_id = ?", (member.id,))
    db.commit()

    if cursor.rowcount == 0:
        description = f"ℹ️ {member.mention} is not currently whitelisted."
        color = discord.Color.blurple()
    else:
        description = f"{member.mention} has been **removed** from the Troll Panel whitelist."
        color = discord.Color.green()

    embed = discord.Embed(description=description, color=color)
    if ctx.interaction:
        return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="trollpanel", description="Open the troll panel to rig games and troll members")
async def trollpanel(ctx):
    if not is_troll_whitelisted(ctx.author.id):
        embed = discord.Embed(description="You are not whitelisted to access the Troll Panel.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    
    view = TrollPanelView(ctx.author.id)
    embed = discord.Embed(
        title="🃏 Troll Panel & Game Rigging",
        description=f"🎛️ **Troll Panel Settings for <@{ctx.author.id}>**\nConfigure your rigged game outcomes & troll tools below:",
        color=discord.Color.dark_embed()
    )
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await ctx.send(embed=embed, view=view, delete_after=120)

class GhostPingControlView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.stop_event = asyncio.Event()
        self.stopped_by = None

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id and interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message("Only the command invoker or bot owners can stop this.", ephemeral=True)

        if self.stop_event.is_set():
            return await interaction.response.send_message("Already stopping...", ephemeral=True)

        self.stop_event.set()
        self.stopped_by = interaction.user.id

        for child in self.children:
            child.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            try:
                await interaction.response.send_message("Stopping...", ephemeral=True)
            except Exception:
                pass

@bot.hybrid_command(
    name="ghostping",
    description="Ghost ping a user with stop button."
)
@app_commands.describe(member="The member to ghost-ping", times="How many times (1-100)", message="Optional message after the mention")
async def ghostping(ctx, member: discord.Member, times: int = 1, *, message: str = ""):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(
            description="🔒 You're not allowed to use this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed, delete_after=5)

    try:
        times = int(times)
    except Exception:
        times = 1
    times = max(1, min(100, times))

    if not ctx.interaction and ctx.message:
        try:
            await ctx.message.delete()
        except Exception:
            pass

    view = GhostPingControlView(owner_id=ctx.author.id)
    embed = discord.Embed(
        title=f"👻 Ghost pinging {member.display_name} x{times}...",
        description=f"Progress: 0/{times}",
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Press Stop to cancel the ghost pings.")

    status_msg = None
    if ctx.interaction:
        try:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            status_msg = await ctx.interaction.original_response()
        except Exception:
            try:
                await ctx.interaction.followup.send(embed=embed, view=view, ephemeral=True)
                status_msg = await ctx.interaction.original_response()
            except Exception:
                status_msg = await ctx.channel.send(embed=embed, view=view)
    else:
        status_msg = await ctx.channel.send(embed=embed, view=view)

    delay = 1.0

    async def do_send():
        sent = 0
        content = f"{member.mention} {message}".strip() or f"{member.mention}"
        for i in range(times):
            if view.stop_event.is_set():
                break
            try:
                ping_msg = await ctx.channel.send(content)
                try:
                    await ping_msg.delete()
                except Exception:
                    pass
                sent += 1
            except Exception:
                break

            try:
                embed.description = f"Progress: {sent}/{times}"
                embed.set_footer(text=f"Press Stop to cancel — sent {sent}/{times}")
                await status_msg.edit(embed=embed, view=view)
            except Exception:
                pass

            if i != times - 1:
                await asyncio.sleep(delay)

        if view.stop_event.is_set():
            embed.title = "🛑 Ghost pinging stopped"
            if view.stopped_by:
                embed.description = f"Stopped by <@{view.stopped_by}> • Progress: {sent}/{times}"
            else:
                embed.description = f"Stopped • Progress: {sent}/{times}"
        else:
            embed.title = "✅ Ghost pinging completed"
            embed.description = f"Completed: {sent}/{times}"

        for child in view.children:
            child.disabled = True
        try:
            await status_msg.edit(embed=embed, view=view)
        except Exception:
            try:
                await status_msg.edit(embed=embed)
            except Exception:
                pass

        if ctx.interaction:
            try:
                await ctx.interaction.followup.send(f"✅ Ghost ping finished: {sent}/{times}", ephemeral=True)
            except Exception:
                pass

    asyncio.create_task(do_send())
    
@bot.hybrid_command(name="mock", description="Mock a member")
async def mock(ctx, member: discord.Member = None, *, text: str = None):
    if member is None or not text:
        embed = discord.Embed(
            description="❌ Usage: `,,mock @member <text>`",
            color=discord.Color.red()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if not ctx.channel.permissions_for(ctx.guild.me).manage_webhooks:
        embed = discord.Embed(
            description="☄️ I need **Manage Webhooks** permission to do this!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # Build the mocked text
    mocked_text = "".join(
        c.upper() if i % 2 == 0 else c.lower()
        for i, c in enumerate(text)
    )

    # Acknowledge the interaction (slash) or delete the user's message (prefix)
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass

    # Create a temporary webhook
    try:
        webhook = await ctx.channel.create_webhook(name="Rynx Mock")

        await webhook.send(
            content=mocked_text,
            username=member.display_name,
            avatar_url=member.display_avatar.url
        )

        # Delete the webhook afterward
        try:
            await webhook.delete()
        except Exception:
            pass

        if ctx.interaction:
            try:
                await ctx.interaction.followup.send("✅ Mocked!", ephemeral=True)
            except Exception:
                pass

    except discord.Forbidden:
        embed = discord.Embed(
            description="❌ I don't have permission to create webhooks here!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"❌ Error: {str(e)[:100]}",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

@bot.hybrid_command(name="fakenuke", description="Trigger a fake server nuke alert message for a member")
async def fakenuke(ctx, member: discord.Member = None):
    if not is_troll_whitelisted(ctx.author.id):
        embed = discord.Embed(description="You are not whitelisted to use this troll command.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    target = member or ctx.author
    embed = discord.Embed(
        title="🚨 **WARNING: SERVER NUKE IN PROGRESS** 🚨",
        description=f"Thank you {target.mention} for nuking this server the channels will be deleted soon.",
        color=discord.Color.red()
    )
    embed.set_image(url="https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbTZibHhwcTd0c2k1a3dta3JrOHY4ZjVsdWZsZjJlMnIzNW96ajVsaiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3oKIPiqfUtLCnIKxRS/giphy.gif")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message("🚨 Initiating fake nuke...", ephemeral=True)
        await ctx.channel.send(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.channel.send(embed=embed)
# =========================================================
# ECONOMY UI & COMMANDS
# =========================================================

class WithdrawModal(discord.ui.Modal, title="Withdraw Money"):
    amount = discord.ui.TextInput(label="Amount (or 'all')", placeholder="e.g. 500 or all", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        wallet, bank, luck = get_user_econ(interaction.user.id)
        val = self.amount.value.strip().lower()

        if val == "all":
            amount = bank
        else:
            try:
                amount = int(val)
            except ValueError:
                embed = discord.Embed(description="Please enter a valid number or 'all'.", color=discord.Color.red())
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        if amount <= 0:
            embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if bank < amount:
            embed = discord.Embed(description="You don't have that much money in your bank.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cursor.execute("UPDATE users SET wallet = wallet + ?, bank = bank - ? WHERE user_id = ?", (amount, amount, interaction.user.id))
        db.commit()

        new_wallet, new_bank, new_luck = get_user_econ(interaction.user.id)
        net = new_wallet + new_bank
        rank = get_global_rank(interaction.user.id)

        if interaction.message:
            try:
                embed = interaction.message.embeds[0]
                embed.clear_fields()
                embed.add_field(name="Wallet", value=f"🪙 {new_wallet:,}", inline=True)
                embed.add_field(name="Bank", value=f"🪙 {new_bank:,}", inline=True)
                embed.add_field(name="Net", value=f"🪙 {net:,}", inline=False)
                embed.add_field(name="Global Rank", value=f"#{rank}", inline=False)
                await interaction.message.edit(embed=embed)
            except Exception:
                pass

        success_embed = discord.Embed(description=f"🏦 Successfully withdrew **${amount:,}** from your bank.", color=discord.Color.green())
        await interaction.response.send_message(embed=success_embed, ephemeral=True)

class DepositModal(discord.ui.Modal, title="Deposit Money"):
    amount = discord.ui.TextInput(label="Amount (or 'all')", placeholder="e.g. 500 or all", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        wallet, bank, luck = get_user_econ(interaction.user.id)
        val = self.amount.value.strip().lower()

        if val == "all":
            amount = wallet
        else:
            try:
                amount = int(val)
            except ValueError:
                embed = discord.Embed(description="Please enter a valid number or 'all'.", color=discord.Color.red())
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        if amount <= 0:
            embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if wallet < amount:
            embed = discord.Embed(description="You don't have that much money in your wallet.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cursor.execute("UPDATE users SET wallet = wallet - ?, bank = bank + ? WHERE user_id = ?", (amount, amount, interaction.user.id))
        db.commit()

        new_wallet, new_bank, new_luck = get_user_econ(interaction.user.id)
        net = new_wallet + new_bank
        rank = get_global_rank(interaction.user.id)

        if interaction.message:
            try:
                embed = interaction.message.embeds[0]
                embed.clear_fields()
                embed.add_field(name="Wallet", value=f"🪙 {new_wallet:,}", inline=True)
                embed.add_field(name="Bank", value=f"🪙 {new_bank:,}", inline=True)
                embed.add_field(name="Net", value=f"🪙 {net:,}", inline=False)
                embed.add_field(name="Global Rank", value=f"#{rank}", inline=False)
                await interaction.message.edit(embed=embed)
            except Exception:
                pass

        success_embed = discord.Embed(description=f"🏦 Successfully deposited **${amount:,}** into your bank.", color=discord.Color.green())
        await interaction.response.send_message(embed=success_embed, ephemeral=True)

class BalanceView(discord.ui.View):
    def __init__(self, target_id):
        super().__init__(timeout=60)
        self.target_id = target_id

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.secondary)
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            embed = discord.Embed(description="This isn't your balance panel!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(WithdrawModal())

    @discord.ui.button(label="Deposit", style=discord.ButtonStyle.secondary)
    async def deposit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            embed = discord.Embed(description="This isn't your balance panel!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(DepositModal())

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            embed = discord.Embed(description="This isn't your balance panel!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        wallet, bank, luck = get_user_econ(self.target_id)
        net = wallet + bank
        rank = get_global_rank(self.target_id)
        
        embed = interaction.message.embeds[0]
        embed.clear_fields()
        embed.add_field(name="Wallet", value=f"🪙 {wallet:,}", inline=True)
        embed.add_field(name="Bank", value=f"🪙 {bank:,}", inline=True)
        embed.add_field(name="Net", value=f"🪙 {net:,}", inline=False)
        embed.add_field(name="Global Rank", value=f"#{rank}", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)

@bot.hybrid_command(name="balance", aliases=["bal"], description="Check your or another user's balance")
async def balance(ctx, member: discord.Member = None):
    target = member or ctx.author
    wallet, bank, luck = get_user_econ(target.id)
    net = wallet + bank
    rank = get_global_rank(target.id)
    
    embed = discord.Embed(color=discord.Color.from_rgb(88, 101, 242))
    embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
    embed.title = "Balance"
    embed.add_field(name="Wallet", value=f"🪙 {wallet:,}", inline=True)
    embed.add_field(name="Bank", value=f"🪙 {bank:,}", inline=True)
    embed.add_field(name="Net", value=f"🪙 {net:,}", inline=False)
    embed.add_field(name="Global Rank", value=f"#{rank}", inline=False)
    embed.add_field(name="🍀 Luck", value=f"{luck}%", inline=False)
    
    view = BalanceView(target.id)
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="deposit", aliases=["dep"], description="Deposit money into your bank")
async def deposit(ctx, amount: str):
    user_id = ctx.author.id
    wallet, bank, luck = get_user_econ(user_id)
    if amount.lower() == "all":
        val = wallet
    else:
        try:
            val = int(amount)
        except ValueError:
            embed = discord.Embed(description="Please enter a valid number or 'all'.", color=discord.Color.red())
            return await ctx.send(embed=embed)
    if val <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < val:
        embed = discord.Embed(description="😂 You don't have that much money in your wallet.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("UPDATE users SET wallet = wallet - ?, bank = bank + ? WHERE user_id = ?", (val, val, user_id))
    db.commit()
    embed = discord.Embed(description=f"🏦 Successfully deposited **${val:,}** into your bank.", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="withdraw", aliases=["with"], description="Withdraw money from your bank")
async def withdraw(ctx, amount: str):
    user_id = ctx.author.id
    wallet, bank, luck = get_user_econ(user_id)
    if amount.lower() == "all":
        val = bank
    else:
        try:
            val = int(amount)
        except ValueError:
            embed = discord.Embed(description="Please enter a valid number or 'all'.", color=discord.Color.red())
            return await ctx.send(embed=embed)
    if val <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if bank < val:
        embed = discord.Embed(description="You don't have that much money in your bank.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("UPDATE users SET wallet = wallet + ?, bank = bank - ? WHERE user_id = ?", (val, val, user_id))
    db.commit()
    embed = discord.Embed(description=f"🏦 Successfully withdrew **${val:,}** from your bank.", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="daily", description="Claim your daily reward")
async def daily(ctx):
    user_id = ctx.author.id
    cursor.execute("SELECT daily_claim FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_time = time.time()
    cooldown = 86400

    if row and current_time - row[0] < cooldown:
        remaining = int(cooldown - (current_time - row[0]))
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        embed = discord.Embed(description=f"⏳ Already claimed daily reward. Try again in **{hours}h {minutes}m**.", color=discord.Color.orange())
        return await ctx.send(embed=embed)

    reward = 500
    update_wallet(user_id, reward)
    cursor.execute("UPDATE users SET daily_claim = ? WHERE user_id = ?", (current_time, user_id))
    db.commit()
    embed = discord.Embed(description=f"💸 Successfully claimed daily reward of **${reward:,}**!", color=discord.Color.green())
    await ctx.send(embed=embed)
# =========================================================
# WORK COMMAND MEDIA
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_MEDIA_DIR = os.path.join(BASE_DIR, "work_media")

WORK_MEDIA = {
    "Chef": os.path.join(WORK_MEDIA_DIR, "chef.gif"),
    "Cashier": os.path.join(WORK_MEDIA_DIR, "cashier.gif"),
    "Roblox Scripter": os.path.join(WORK_MEDIA_DIR, "roblox_scripter.gif"),
    "Discord Moderator": os.path.join(WORK_MEDIA_DIR, "discord_moderator.gif"),
    "Software Developer": os.path.join(WORK_MEDIA_DIR, "software_developer.gif"),
    "Streamer": os.path.join(WORK_MEDIA_DIR, "streamer.gif"),
}

@bot.hybrid_command(name="work", description="Work to earn cash")
async def work(ctx):
    user_id = ctx.author.id
    cursor.execute("SELECT work_claim FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_time = time.time()
    cooldown = 60

    if row and current_time - row[0] < cooldown:
        remaining = int(cooldown - (current_time - row[0]))
        minutes, seconds = divmod(remaining, 60)
        cooldown_text = f"{minutes} minute(s)" if minutes else f"{seconds} second(s)"
        embed = discord.Embed(
            description=f"⏳ Rest for another **{cooldown_text}** before working again.",
            color=discord.Color.orange()
        )
        return await ctx.send(embed=embed)

    jobs = list(WORK_MEDIA.keys())
    earned = random.randint(100, 350)
    job_name = random.choice(jobs)

    update_wallet(user_id, earned)
    cursor.execute("UPDATE users SET work_claim = ? WHERE user_id = ?", (current_time, user_id))
    db.commit()

    embed = discord.Embed(
        title=f"💼 {job_name}",
        description=f"{ctx.author.mention} worked as a **{job_name}** and earned **${earned:,}**!",
        color=discord.Color.green()
    )
    embed.set_footer(text="Come back in 1 minute to work again.")

    media_path = WORK_MEDIA[job_name]
    if os.path.isfile(media_path):
        file = discord.File(media_path, filename=os.path.basename(media_path))
        embed.set_image(url=f"attachment://{os.path.basename(media_path)}")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)

# =========================================================
# GAMBLE, DICE, SLOTS, CRIME, ROB - WITH LUCK SYSTEM
# =========================================================

@bot.hybrid_command(name="gamble", aliases=["bet"], description="Gamble your money")
async def gamble(ctx, amount: int = 100):
    user_id = ctx.author.id
    wallet, bank, luck = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="❌ Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="❌ Not enough money in wallet.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    luck_bonus = (luck - 50) / 100 * 0.4
    win_chance = 0.55 + luck_bonus
    win_chance = max(0.35, min(0.75, win_chance))

    if random.random() < win_chance:
        update_wallet(user_id, amount)
        embed = discord.Embed(description=f"🎉 You won **${amount:,}**! (Luck: {luck}%)", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"😢 You lost **${amount:,}**. (Luck: {luck}%)", color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="dice", description="Roll dice against the bot for money")
async def dice(ctx, amount: int):
    user_id = ctx.author.id
    wallet, bank, luck = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="❌ Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="❌ You don't have enough money in your wallet for this bet.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    luck_bonus = int((luck - 50) / 8)
    is_rigged = troll_settings.get(user_id, {}).get("dice", False)
    
    if is_rigged:
        user_roll1, user_roll2 = 6, 6
        user_total = 12
        bot_roll1, bot_roll2 = 1, 1
        bot_total = 2
    else:
        user_roll1 = random.randint(1, 6) + luck_bonus
        user_roll2 = random.randint(1, 6) + luck_bonus
        user_total = user_roll1 + user_roll2
        bot_roll1 = random.randint(1, 6)
        bot_roll2 = random.randint(1, 6)
        bot_total = bot_roll1 + bot_roll2

    embed = discord.Embed(title="🎲 Dice Roll Battle", color=discord.Color.blurple())
    embed.add_field(name=f"{ctx.author.display_name}'s Roll (Luck: {luck}%)", value=f"🎲 {user_roll1} + 🎲 {user_roll2} = **{user_total}**", inline=True)
    embed.add_field(name="Bot's Roll", value=f"🎲 {bot_roll1} + 🎲 {bot_roll2} = **{bot_total}**", inline=True)

    if user_total > bot_total:
        update_wallet(user_id, amount)
        embed.description = f"🎉 You won **${amount:,}**!"
        embed.color = discord.Color.green()
    elif user_total < bot_total:
        update_wallet(user_id, -amount)
        embed.description = f"😢 You lost **${amount:,}**."
        embed.color = discord.Color.red()
    else:
        embed.description = f"🤝 It's a tie! Your money has been returned."
        embed.color = discord.Color.gold()

    await ctx.send(embed=embed)

@bot.hybrid_command(name="slots", description="Play the slot machine")
async def slots(ctx, amount: int):
    user_id = ctx.author.id
    wallet, bank, luck = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="You don't have enough money in your wallet broke nigga.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    luck_bonus = (luck - 50) / 100 * 0.4
    is_rigged = troll_settings.get(user_id, {}).get("slots", False)
    
    if is_rigged:
        result = ["7️⃣", "7️⃣", "7️⃣"]
    else:
        symbols = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣"]
        if random.random() < 0.35 + luck_bonus:
            good_symbols = ["💎", "7️⃣", "🔔"]
            result = [random.choice(good_symbols) for i in range(3)]
        else:
            result = [random.choice(symbols) for i in range(3)]
    
    if result[0] == result[1] == result[2]:
        payout = amount * 10
        update_wallet(user_id, payout)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n🎉 Jackpot! You won **${payout:,}**! (Luck: {luck}%)", color=discord.Color.green())
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        payout = amount * 2
        update_wallet(user_id, payout)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n✨ Nice! Two matching symbols. You won **${payout:,}**! (Luck: {luck}%)", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n😢 No match. You lost **${amount:,}**. (Luck: {luck}%)", color=discord.Color.red())

    await ctx.send(embed=embed)

@bot.hybrid_command(name="crime", description="Commit a crime to earn cash (or get fined)")
async def crime(ctx):
    user_id = ctx.author.id
    cursor.execute("SELECT crime_claim FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_time = time.time()
    cooldown = 1800

    if row and current_time - row[0] < cooldown:
        remaining = int(cooldown - (current_time - row[0]))
        minutes = remaining // 60
        embed = discord.Embed(description=f"⏳ The cops are still looking for you! Wait **{minutes} minutes**.", color=discord.Color.orange())
        return await ctx.send(embed=embed)

    luck = get_user_luck(user_id)
    luck_bonus = (luck - 50) / 100 * 0.35

    outcomes = [
        ("Robbed a convenience store", 250, True),
        ("Hacked a corporate database", 400, True),
        ("Stole a luxury car", 600, True),
        ("Got caught shoplifting", 200, False),
        ("Fumbled the heist and got fined", 350, False)
    ]
    
    if random.random() < 0.65 + luck_bonus:
        success_outcomes = [o for o in outcomes if o[2] == True]
        event, amount, success = random.choice(success_outcomes)
    else:
        event, amount, success = random.choice(outcomes)
    
    if success:
        update_wallet(user_id, amount)
        embed = discord.Embed(description=f"🦹 Successfully **{event}** and made **${amount:,}**! (Luck: {luck}%)", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"🚨 You failed while trying to **{event}** and paid a fine of **${amount:,}**! (Luck: {luck}%)", color=discord.Color.red())

    cursor.execute("UPDATE users SET crime_claim = ? WHERE user_id = ?", (current_time, user_id))
    db.commit()
    await ctx.send(embed=embed)

@bot.hybrid_command(name="rob", description="Rob another user")
async def rob(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="You cannot rob yourself.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    user_id = ctx.author.id
    cursor.execute("SELECT rob_claim FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_time = time.time()
    cooldown = 3600

    if row and current_time - row[0] < cooldown:
        remaining = int(cooldown - (current_time - row[0]))
        minutes = remaining // 60
        embed = discord.Embed(description=f"You are too tired to rob someone. Wait **{minutes} minutes**.", color=discord.Color.orange())
        return await ctx.send(embed=embed)

    wallet, bank, luck = get_user_econ(user_id)
    if wallet < 200:
        embed = discord.Embed(description="You need at least **$200** in your wallet to attempt a robbery brokie.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    target_wallet, target_bank, target_luck = get_user_econ(member.id)
    if target_wallet < 100:
        embed = discord.Embed(description=f"**{member.display_name}** doesn't have enough money in their wallet to rob.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("UPDATE users SET rob_claim = ? WHERE user_id = ?", (current_time, user_id))
    db.commit()

    luck_bonus = (luck - 50) / 100 * 0.35
    rob_chance = 0.45 + luck_bonus
    rob_chance = max(0.25, min(0.65, rob_chance))

    if random.random() < rob_chance:
        stolen = random.randint(50, min(target_wallet, 500))
        update_wallet(user_id, stolen)
        update_wallet(member.id, -stolen)
        embed = discord.Embed(description=f"🥷 You successfully snuck up on {member.mention} and stole **${stolen:,}** from their wallet! (Luck: {luck}%)", color=discord.Color.green())
    else:
        fine = 150
        update_wallet(user_id, -fine)
        embed = discord.Embed(description=f"🚨 You got caught trying to rob {member.mention} and had to pay a fine of **${fine:,}**! (Luck: {luck}%)", color=discord.Color.red())

    await ctx.send(embed=embed)

# =========================================================
# PAY COMMAND
# =========================================================

@bot.hybrid_command(name="pay", description="Pay money to another user")
async def pay(ctx, member: discord.Member, amount: int):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="You cannot pay yourself.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if amount <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    wallet, bank, luck = get_user_econ(ctx.author.id)
    if wallet < amount:
        embed = discord.Embed(description="You don't have enough money in your wallet you brokie nigga.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    update_wallet(ctx.author.id, -amount)
    update_wallet(member.id, amount)
    embed = discord.Embed(description=f"💸 Successfully paid **${amount:,}** to {member.mention}!", color=discord.Color.green())
    await ctx.send(embed=embed)

# =========================================================
# PUBLIC FUN & SOCIAL COMMANDS
# =========================================================

@bot.hybrid_command(name="cf", aliases=["coinflip"], description="Flip a coin")
async def cf(ctx):
    result = random.choice(["HEADS", "TAILS"])
    embed = discord.Embed(
        title="-Coin Flip-",
        description=f"Coin Landed on **{result}**!",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Flipped by: {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="gayrate", description="Check someone's gay percentage")
async def gayrate(ctx, member: discord.Member = None):
    target = member or ctx.author
    rate = random.randint(0, 100)
    embed = discord.Embed(description=f"🏳️‍🌈 **{target.display_name}** is **{rate}%** gay!", color=discord.Color.from_rgb(255, 105, 180))
    await ctx.send(embed=embed)

@bot.hybrid_command(name="8ball", description="Ask the magic 8ball a question")
async def eight_ball(ctx, *, question: str):
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.",
        "Yes definitely.", "You may rely on it.", "As I see it, yes <3>.",
        "Most likely.", "Outlook good.", "Yes.", "Signs point to yes :3.",
        "Reply hazy, try again.", "Ask again later.", "Better not tell you now heh.",
        "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no sussy baka.",
        "Outlook not so good nigga.", "Very doubtful sir."
    ]
    answer = random.choice(responses)
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=answer, inline=False)
    embed.set_footer(text=f"Asked by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="pp", description="Check someone's pp size")
async def pp(ctx, member: discord.Member = None):
    target = member or ctx.author
    size = random.randint(0, 15)
    shaft = "=" * size
    pp_display = f"8{shaft}D"
    embed = discord.Embed(description=f"🍆 **{target.display_name}'s PP size:**\n{pp_display}", color=discord.Color.blurple())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="roast", description="Roast one or multiple users")
async def roast(ctx, member1: discord.Member = None, member2: discord.Member = None, member3: discord.Member = None):
    targets = [m for m in (member1, member2, member3) if m is not None]
    if not targets:
        if ctx.message and ctx.message.mentions:
            targets = ctx.message.mentions
        else:
            targets = [ctx.author]

    roasts = [
        "Your ass comeback is still loading.",
        "You bring tutorial-level confidence to boss-level problems.",
        "I've seen loading screens with more personality.",
        "Your strategy appears to be hoping nobody notices.",
        "You have a talent for making simple things look advanced.",
        "Even autocorrect would give up trying to fix that.",
        "You really turned confidence into a full-time job.",
        "Your Wi-Fi has better decision-making skills than you.",
        "That was a bold move for someone with zero backup plans.",
        "You somehow manage to be confidently incorrect.",
        "Your brain has 37 tabs open and none of them are responding.",
        "You make every easy task feel like a side quest.",
        "The confidence is impressive. The results, not so much.",
        "You don't miss opportunities. You just miss the point.",
    ]

    lines = []
    for target in targets:
        selected_roast = random.choice(roasts)
        lines.append(f"🔥 {target.mention} {selected_roast}")

    embed = discord.Embed(description="\n".join(lines), color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="iq", description="Check someone's IQ score")
async def iq(ctx, member: discord.Member = None):
    target = member or ctx.author
    score = random.randint(40, 160)
    embed = discord.Embed(description=f"🧠 **{target.display_name}'s IQ:** {score}", color=discord.Color.blurple())
    await ctx.send(embed=embed)

# =========================================================
# KISS COMMAND
# =========================================================

KISS_GIFS = [
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMXV3a29uZG05MjRmZml2czN2bDJvaWQxaDNkeHoyamMwYTZ1ZWU0aSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/FgWNX7NK6SpzqwmOWe/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWx3YTB2NTJyMmF6YXN0YWFybHRtNG44YzlhbHliZ2t4NGJzeDMweSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/2fLX7xDEhleyubyBmv/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWx3YTB2NTJyMmF6YXN0YWFybHRtNG44YzlhbHliZ2t4NGJzeDMweSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/Mo122cd9G2xmKymanO/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWx3YTB2NTJyMmF6YXN0YWFybHRtNG44YzlhbHliZ2t4NGJzeDMweSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/zkppEMFvRX5FC/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWx3YTB2NTJyMmF6YXN0YWFybHRtNG44YzlhbHliZ2t4NGJzeDMweSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/vUrwEOLtBUnJe/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPWVjZjA1ZTQ3azBmYWpwMWZ0dGo1Y3JrNmdzd29xZ20yODIyazFoa3R4ajQycDN1MSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/Ka2NAhphLdqXC/giphy.gif"
]

@bot.hybrid_command(name="kiss", description="Kiss another user")
async def kiss(ctx, member: discord.Member = None):
    if not member:
        embed = discord.Embed(
            description="❌ You need to specify someone to kiss!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"😘 {ctx.author.mention} kisses themselves... that's a bit weird but okay!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(KISS_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't kiss a bot!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"💋 {ctx.author.mention} kisses {member.mention}! ❤️",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.set_image(url=random.choice(KISS_GIFS))
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
        
@bot.hybrid_command(name="gif", description="Search and send a GIF")
async def gif(ctx, *, search: str):
    import aiohttp
    tenor_key = os.getenv("TENOR_API_KEY", "LIVDSRZULELA")
    url = f"https://g.tenor.com/v1/search?q={search}&key={tenor_key}&limit=10"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    gif_url = random.choice(results)["media"][0]["gif"]["url"]
                    embed = discord.Embed(color=discord.Color.blurple())
                    embed.set_image(url=gif_url)
                    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
                    if ctx.interaction:
                        return await ctx.interaction.response.send_message(embed=embed)
                    else:
                        if ctx.message:
                            try:
                                await ctx.message.delete()
                            except Exception:
                                pass
                        return await ctx.send(embed=embed)
            
    embed = discord.Embed(description=f"Could not find any GIFs for `{search}`.", color=discord.Color.red())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed, delete_after=5)

@bot.hybrid_command(name="hack", description="Fictional hack command for fun")
async def hack(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="You can't hack yourself!", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    embed = discord.Embed(description=f"💻 Hacking **{member.display_name}**...", color=discord.Color.blurple())
    msg = await ctx.send(embed=embed)
    await time_sleep_wrapper(1.5)
    
    embed.description = "🔍 Finding IP address... `677.677.6.69`"
    await msg.edit(embed=embed)
    await time_sleep_wrapper(1.5)
    
    embed.description = "📂 Downloading private Discord DMs..."
    await msg.edit(embed=embed)
    await time_sleep_wrapper(1.5)
    
    embed.description = "💳 Stealing bank credit card info..."
    await msg.edit(embed=embed)
    await time_sleep_wrapper(1.5)
    
    embed.description = f"💻 Successfully hacked **{member.display_name}**! (Totally real, trust)"
    embed.color = discord.Color.green()
    await msg.edit(embed=embed)

# =========================================================
# MODERATOR UI / PERMISSION GATE
# =========================================================

async def require_server_mod(ctx):
    if not isinstance(ctx.author, discord.Member) or not _is_server_mod(ctx.author):
        embed = discord.Embed(
            title="🛡️ Permission Denied",
            description="Only **server administrators or moderators** can use this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass
        else:
            await ctx.send(embed=embed)
        return False
    return True
# =========================================================
# HELP, MODERATION & UTILITY COMMANDS
# =========================================================
@bot.hybrid_command(name="afk", description="Set your AFK status")
async def afk(ctx, *, reason: str = "AFK"):
    afk_users[ctx.author.id] = {"reason": reason, "time": time.time(), "mentions": []}
    
    embed = discord.Embed(
        title="AFK Set!",
        description=f"You are now afk in this server. Reason: **{reason}**",
        color=discord.Color.blue()
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.reply(embed=embed, mention_author=False)
# =========================================================
# BAN COMMAND
# =========================================================
@bot.hybrid_command(name="ban", description="Ban a member from the server")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot ban the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot ban the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot ban a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot ban a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot ban them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot ban them.")

    await member.ban(reason=reason)
    log_mod_action(ctx.author.id, member.id, ctx.guild.id, "ban", reason)
    
    embed = discord.Embed(
        title="👋 Successfully Banned",
        color=discord.Color.red()
    )
    embed.add_field(name="Member", value=f"{member.mention}", inline=False)
    embed.add_field(name="📄 Reason", value=reason, inline=False)
    embed.set_footer(text=f"Banned by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
# =========================================================
# UNBAN COMMAND
# =========================================================

@bot.hybrid_command(name="unban", description="Unban a user by ID")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: str):
    try:
        uid = int(user_id)
        user = await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        
        embed = discord.Embed(
            title="✅ Successfully Unbanned",
            color=discord.Color.green()
        )
        embed.add_field(name="User", value=f"{user}", inline=False)
        embed.set_footer(text=f"Unbanned by {ctx.author.display_name}")
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
            
    except discord.NotFound:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ User ID `{user_id}` not found.", ephemeral=True)
        else:
            await ctx.send(f"❌ User ID `{user_id}` not found.")
    except Exception:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Could not find or unban that user. Check the user ID.", ephemeral=True)
        else:
            await ctx.send(f"❌ Could not find or unban that user. Check the user ID.")

@unban.error
async def unban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing UnBan Members permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing UnBan Members permission.")
            
@bot.hybrid_group(name="fake", description="Fake moderation commands")
async def fake(ctx):
    pass

@fake.command(name="ban", description="Fake-ban a member without actually banning them")
async def fake_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not isinstance(ctx.author, discord.Member) or not _is_server_mod(ctx.author):
        embed = discord.Embed(
            title="🛡️ Permission Denied",
            description="Only **server administrators or moderators** can use this command.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed, ephemeral=True)

    embed = discord.Embed(
        description=f"Banned **{member.display_name}**",
        color=discord.Color.red()
    )
    if reason != "No reason provided":
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"totally real trust by {ctx.author.display_name}")

    await ctx.send(embed=embed)

# =========================================================
# KICK COMMAND
# =========================================================

@bot.hybrid_command(name="kick", description="Kick a member from the server")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot kick the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot kick the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot kick a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot kick a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot kick them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot kick them.")

    await member.kick(reason=reason)
    log_mod_action(ctx.author.id, member.id, ctx.guild.id, "kick", reason)
    
    embed = discord.Embed(
        title="⭐️ Successfully Kicked",
        color=discord.Color.orange()
    )
    embed.add_field(name="Member", value=f"{member.mention}", inline=False)
    embed.add_field(name="📄 Reason", value=reason, inline=False)
    embed.set_footer(text=f"Kicked by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@kick.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Kick Members permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Kick Members permission.")

# =========================================================
# MUTE COMMAND - SILENT FAIL FOR NON-MODS
# =========================================================

@bot.hybrid_command(name="mute", description="Mute a member")
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
    is_mod = ctx.author.guild_permissions.manage_roles or ctx.author.id in OWNER_IDS
    
    if not is_mod:
        return
    
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ You cannot mute the server owner.", ephemeral=True)
        return await ctx.send(f"❌ You cannot mute the server owner.")
    
    if member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ You cannot mute a staff member.", ephemeral=True)
            return await ctx.send(f"❌ You cannot mute a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me.")

    seconds = parse_duration(duration)
    if not seconds:
        if ctx.interaction:
            return await ctx.interaction.response.send_message("❌ Invalid duration. Use: `10s`, `5m`, `2h`, `1d`.", ephemeral=True)
        return await ctx.send("❌ Invalid duration. Use: `10s`, `5m`, `2h`, `1d`.")
    
    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason)
        log_mod_action(ctx.author.id, member.id, ctx.guild.id, "mute", reason)
        
        embed = discord.Embed(
            title="✨ Successfully Muted",
            color=discord.Color.green()
        )
        embed.add_field(name="Member", value=f"{member.mention}", inline=False)
        embed.add_field(name="📄 Reason", value=reason, inline=False)
        embed.add_field(name="⏱️ Duration", value=duration, inline=False)
        embed.set_footer(text=f"Muted by {ctx.author.display_name}")
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
            
    except Exception as e:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Failed to mute member: {e}", ephemeral=True)
        else:
            await ctx.send(f"❌ Failed to mute member: {e}")

# =========================================================
# UNMUTE COMMAND - SILENT FAIL FOR NON-MODS
# =========================================================

@bot.hybrid_command(name="unmute", description="Remove a member's timeout")
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    is_mod = ctx.author.guild_permissions.manage_roles or ctx.author.id in OWNER_IDS
    
    if not is_mod:
        return
    
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ You cannot unmute the server owner.", ephemeral=True)
        return await ctx.send(f"❌ You cannot unmute the server owner.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me.")
    
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        log_mod_action(ctx.author.id, member.id, ctx.guild.id, "unmute", "Removed timeout")
        
        embed = discord.Embed(
            title="☄️ Successfully Unmuted",
            color=discord.Color.green()
        )
        embed.add_field(name="Member", value=f"{member.mention}", inline=False)
        embed.set_footer(text=f"Unmuted by {ctx.author.display_name}")
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
            
    except Exception as e:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Failed to unmute member: {e}", ephemeral=True)
        else:
            await ctx.send(f"❌ Failed to unmute member: {e}")

# =========================================================
# WARN COMMAND
# =========================================================

@bot.hybrid_command(name="warn", description="Warn a member")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot warn the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot warn the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot warn a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot warn a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot warn them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot warn them.")

    cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (member.id,))
    warn_count = cursor.fetchone()[0] + 1
    
    cursor.execute("INSERT INTO warnings (user_id, moderator_id, reason) VALUES (?, ?, ?)", (member.id, ctx.author.id, reason))
    db.commit()
    log_mod_action(ctx.author.id, member.id, ctx.guild.id, "warn", reason)
    
    embed = discord.Embed(
        title="⚠️ Successfully Warned",
        color=discord.Color.orange()
    )
    embed.add_field(name="Member", value=f"{member.mention}", inline=False)
    embed.add_field(name="📄 Reason", value=reason, inline=False)
    embed.add_field(name="⚠️ Warning Count", value=f"#{warn_count}", inline=False)
    embed.set_footer(text=f"Warned by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@warn.error
async def warn_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Manage Messages permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Manage Messages permission.")
            
@bot.hybrid_command(name="avatar", description="Show a user's avatar")
async def avatar(ctx, member: discord.Member = None):
    target = member or ctx.author
    embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=target.color)
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)

class MarriageRequestView(discord.ui.View):
    def __init__(self, proposer_id, target_id, action="marry"):
        super().__init__(timeout=60)
        self.proposer_id = proposer_id
        self.target_id = target_id
        self.action = action
        self.answered = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("This request isn't for you.", ephemeral=True)
            return False
        if self.answered:
            await interaction.response.send_message("This request has already been answered.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.answered:
            return
        self.answered = True
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answered = True
        for child in self.children:
            child.disabled = True

        if self.action == "marry":
            cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (self.proposer_id, self.proposer_id))
            if cursor.fetchone():
                return await interaction.response.edit_message(content="💍 The person who proposed is already married.", view=self)
            cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (self.target_id, self.target_id))
            if cursor.fetchone():
                return await interaction.response.edit_message(content="💍 You are already married.", view=self)
            cursor.execute("INSERT INTO marriages (user1_id, user2_id) VALUES (?, ?)", (self.proposer_id, self.target_id))
            db.commit()
            embed = discord.Embed(description=f"💍 Congratulations! <@{self.proposer_id}> and <@{self.target_id}> are now married! ❤️", color=discord.Color.from_rgb(255, 105, 180))
        else:
            cursor.execute("SELECT * FROM marriages WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)", (self.proposer_id, self.target_id, self.target_id, self.proposer_id))
            if not cursor.fetchone():
                return await interaction.response.edit_message(content="👏 You are no longer married to this person.", view=self)
            cursor.execute("DELETE FROM marriages WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)", (self.proposer_id, self.target_id, self.target_id, self.proposer_id))
            db.commit()
            embed = discord.Embed(description=f"💔 <@{self.proposer_id}> and <@{self.target_id}> are now divorced.", color=discord.Color.blurple())

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.answered = True
        for child in self.children:
            child.disabled = True
        if self.action == "marry":
            text = f"💔 <@{self.target_id}> declined <@{self.proposer_id}>'s marriage proposal."
        else:
            text = f"❤️ <@{self.target_id}> declined <@{self.proposer_id}>'s divorce request."
        await interaction.response.edit_message(content=text, view=self)

@bot.hybrid_command(name="marry", description="Ask another user to marry you")
async def marry(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        return await ctx.send(embed=discord.Embed(description="😭 You cannot marry yourself weirdo.", color=discord.Color.red()))
    if member.bot:
        return await ctx.send(embed=discord.Embed(description="☠️ You cannot marry a bot son.", color=discord.Color.red()))
    cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (ctx.author.id, ctx.author.id))
    if cursor.fetchone():
        return await ctx.send(embed=discord.Embed(description="💍 You are already married!", color=discord.Color.red()))
    cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (member.id, member.id))
    if cursor.fetchone():
        return await ctx.send(embed=discord.Embed(description=f"🥺 **{member.display_name}** is already married You crackhead!", color=discord.Color.red()))

    embed = discord.Embed(title="💍 Marriage Proposal", description=f"{ctx.author.mention} wants to marry {member.mention}!\n\n{member.mention}, do you accept?", color=discord.Color.from_rgb(255, 105, 180))
    embed.set_footer(text="This request expires in 60 seconds.")
    await ctx.send(embed=embed, view=MarriageRequestView(ctx.author.id, member.id, "marry"))

@bot.hybrid_command(name="divorce", description="Get divorced from your spouse")
async def divorce(ctx):
    cursor.execute("SELECT user1_id, user2_id FROM marriages WHERE user1_id = ? OR user2_id = ?", (ctx.author.id, ctx.author.id))
    row = cursor.fetchone()
    if not row:
        return await ctx.send(embed=discord.Embed(description="You are not married to anyone crackhead.", color=discord.Color.red()))

    spouse_id = row[1] if row[0] == ctx.author.id else row[0]
    cursor.execute(
        "DELETE FROM marriages WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)",
        (ctx.author.id, spouse_id, spouse_id, ctx.author.id)
    )
    db.commit()

    embed = discord.Embed(
        title="💔 Divorce",
        description=f"{ctx.author.mention} filed a divorce and got divorced with <@{spouse_id}>.",
        color=discord.Color.blurple()
    )
    await ctx.send(embed=embed)

@bot.hybrid_command(name="snipe", aliases=["s"], description="View deleted messages from the channel")
async def snipe(ctx, amount: int = 1):
    channel_id = ctx.channel.id
    if channel_id not in sniped_messages or not sniped_messages[channel_id]:
        embed = discord.Embed(
            description="⚠️ There are no deleted messages to snipe in this channel.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    messages = sniped_messages[channel_id]
    count = max(1, min(amount, len(messages)))
    target_msgs = messages[-count:]
    target_msgs.reverse()

    # Build the message list
    lines = []
    for snipe_data in target_msgs:
        content = snipe_data["content"] or "*No text content*"
        if snipe_data["attachments"]:
            content += f" [📎 Attachment]({snipe_data['attachments'][0]})"
        
        author = snipe_data["author"]
        lines.append(f"{author.mention} : {content}")

    # Create ONE embed with all messages
    embed = discord.Embed(
        title=f"Sniped messages in #{ctx.channel.name}",
        description="\n".join(lines),
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
        
@bot.hybrid_command(name="editsnipe", description="View the last edited message")
async def editsnipe(ctx):
    channel_id = ctx.channel.id
    if channel_id not in edited_messages or not edited_messages[channel_id]:
        embed = discord.Embed(description="⚠️ There are no edited messages to snipe in this channel.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    edit_data = edited_messages[channel_id][-1]
    embed = discord.Embed(title="Edited Message", color=discord.Color.orange())
    embed.set_author(name=str(edit_data["author"]), icon_url=edit_data["author"].display_avatar.url)
    
    embed.add_field(
        name=f"┌ 👤 **{edit_data['author']}** (Edited Message)",
        value=f"├ 🛑 **Before:** {edit_data['before']}\n└ ✅ **After:** {edit_data['after']}",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="poll", description="Create a simple poll")
@commands.has_permissions(manage_messages=True)
async def poll(ctx, *, question: str):
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blurple())
    embed.set_footer(text=f"Poll created by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
    if ctx.interaction:
        response_embed = discord.Embed(description="📊 Poll created!", color=discord.Color.green())
        await ctx.interaction.response.send_message(embed=response_embed, ephemeral=True)
        msg = await ctx.channel.send(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.hybrid_command(name="say", description="Make the bot say something")
@commands.has_permissions(manage_messages=True)
async def say(ctx, *, message: str):
    if ctx.interaction:
        await ctx.interaction.response.send_message("Message sent!", ephemeral=True)
        await ctx.channel.send(message)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(message)

@bot.hybrid_command(name="embed", description="Send a custom embed message")
@commands.has_permissions(manage_messages=True)
async def custom_embed(ctx, *, content: str):
    parts = content.split("|")
    title = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else ""
    
    embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
    if ctx.interaction:
        response_embed = discord.Embed(description="🤑 Embed sent!", color=discord.Color.green())
        await ctx.interaction.response.send_message(embed=response_embed, ephemeral=True)
        await ctx.channel.send(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

async def _run_purge(ctx, amount: int):
    if not isinstance(ctx.author, discord.Member) or not _is_server_mod(ctx.author):
        embed = discord.Embed(
            title="☠️ Permission Denied",
            description="Only **server administrators or moderators** can use this command.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed, ephemeral=True)

    if amount < 1 or amount > 100:
        embed = discord.Embed(
            title="Invalid Amount",
            description="Choose an amount between **1 and 100** messages.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed, ephemeral=True)

    extra = 1 if ctx.message is not None and ctx.interaction is None else 0
    deleted = await ctx.channel.purge(limit=amount + extra)
    removed = len(deleted) - extra

    embed = discord.Embed(
        title="Messages Purged",
        description=f"✨ Successfully deleted **{removed}** message(s) from this channel.",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Purged by {ctx.author.display_name}")
    await ctx.send(embed=embed, delete_after=5)

@bot.hybrid_command(name="clear", description="Clear a number of messages")
async def clear(ctx, amount: int):
    await _run_purge(ctx, amount)

@bot.hybrid_command(name="purge", description="Mass delete messages (Admin/Moderator only)")
async def purge(ctx, amount: int):
    await _run_purge(ctx, amount)

@bot.hybrid_command(name="slowmode", description="Set channel slowmode")
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    if seconds < 0:
        embed = discord.Embed(description="Seconds cannot be negative.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    await ctx.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        embed = discord.Embed(description="Slowmode has been disabled.", color=discord.Color.green())
    else:
        embed = discord.Embed(description=f"Slowmode has been set to **{seconds} seconds**.", color=discord.Color.green())
    await ctx.send(embed=embed)

# =========================================================
# BRAINROT DICE COMMAND & UI
# =========================================================

BRAINROT_CHOICES = [
    {"label": "Strawberry Elephant", "emoji": "🍓", "bonus": 3},
    {"label": "Meowl", "emoji": "🐱", "bonus": 1},
    {"label": "Skibidi Toilet", "emoji": "🚽", "bonus": 3},
    {"label": "Dragon Cannelloni", "emoji": "🐉", "bonus": 2},
    {"label": "Spaghetti Tualetti", "emoji": "🍝", "bonus": 2},
    {"label": "Garama and Madundung", "emoji": "🗿", "bonus": 2},
    {"label": "Ketchuru and Musturu", "emoji": "🍅", "bonus": 1},
    {"label": "La Supreme Combinasion", "emoji": "👑", "bonus": 4},
    {"label": "Los Bros", "emoji": "👥", "bonus": 1},
    {"label": "Ketupat Kepat", "emoji": "🟢", "bonus": 1},
    {"label": "Tralaledon", "emoji": "🎶", "bonus": 2},
    {"label": "Los Hotspotsitos", "emoji": "🔥", "bonus": 2},
    {"label": "Nuclearo Dinossauro", "emoji": "☢️", "bonus": 3},
    {"label": "La Grande Combinasion", "emoji": "🌟", "bonus": 4},
    {"label": "Graipuss Medussi", "emoji": "🐙", "bonus": 2},
    {"label": "Las Vaquitas Saturnitas", "emoji": "🪐", "bonus": 2},
    {"label": "Job Job Job Sahur", "emoji": "🌙", "bonus": 1},
    {"label": "Las Tralaleritas", "emoji": "✨", "bonus": 2},
    {"label": "Agarrini La Palini", "emoji": "🤌", "bonus": 1},
    {"label": "Torrtuginni Dragonfrutini", "emoji": "🐢", "bonus": 2},
    {"label": "Sammyni Spyderini", "emoji": "🕷️", "bonus": 2},
    {"label": "Los Spyderinis", "emoji": "🕸️", "bonus": 1},
    {"label": "Blackhole Goat", "emoji": "🐐", "bonus": 3},
    {"label": "Fragola la la la", "emoji": "🍓", "bonus": 2},
    {"label": "Bisonte Giuppitere", "emoji": "🐃", "bonus": 3}
]

COLOR_CHOICES = [
    {"label": "Red", "color_name": "Red", "discord_color": discord.Color.red(), "emoji": "🔴", "bonus": 2},
    {"label": "Blue", "color_name": "Blue", "discord_color": discord.Color.blue(), "emoji": "🔵", "bonus": 2},
    {"label": "Green", "color_name": "Green", "discord_color": discord.Color.green(), "emoji": "🟢", "bonus": 2},
    {"label": "Yellow", "color_name": "Yellow", "discord_color": discord.Color.gold(), "emoji": "🟡", "bonus": 2},
    {"label": "Purple", "color_name": "Purple", "discord_color": discord.Color.purple(), "emoji": "🟣", "bonus": 2}
]

class BrainrotSelect(discord.ui.Select):
    def __init__(self, placeholder, row_num):
        options = [
            discord.SelectOption(label=item["label"], description=f"Brainrot Bonus: +{item['bonus']}", emoji=item["emoji"])
            for item in BRAINROT_CHOICES
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row_num)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

class ColorSelect(discord.ui.Select):
    def __init__(self, placeholder, row_num):
        options = [
            discord.SelectOption(label=item["label"], description=f"Color Bonus: +{item['bonus']}", emoji=item["emoji"])
            for item in COLOR_CHOICES
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row_num)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

class BrainrotDiceView(discord.ui.View):
    def __init__(self, host_id, amount):
        super().__init__(timeout=120)
        self.host_id = host_id
        self.amount = amount
        
        self.p1_id = host_id
        self.p2_id = None
        
        self.p1_brainrot = BrainrotSelect("Choose YOUR Brainrot (Host)...", row_num=0)
        self.p1_color = ColorSelect("Choose YOUR Color (Host)...", row_num=1)
        
        self.p2_brainrot = BrainrotSelect("Choose YOUR Brainrot (Opponent)...", row_num=2)
        self.p2_color = ColorSelect("Choose YOUR Color (Opponent)...", row_num=3)
        
        self.p2_brainrot.disabled = True
        self.p2_color.disabled = True

        self.add_item(self.p1_brainrot)
        self.add_item(self.p1_color)
        self.add_item(self.p2_brainrot)
        self.add_item(self.p2_color)

    @discord.ui.button(label="🎮 Join Game", style=discord.ButtonStyle.success, row=4)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.host_id:
            embed = discord.Embed(description="You cannot join your own game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if self.p2_id is not None:
            embed = discord.Embed(description="🎭 The opponent spot is already filled!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if self.amount > 0:
            wallet, _, _ = get_user_econ(interaction.user.id)
            if wallet < self.amount:
                embed = discord.Embed(description="🤣 You don't have enough money to join this bet!", color=discord.Color.red())
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        self.p2_id = interaction.user.id
        self.p2_brainrot.disabled = False
        self.p2_color.disabled = False
        button.label = f"✅ Joined: {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        button.disabled = True

        embed = interaction.message.embeds[0]
        embed.description = f"💸 **Brainrot Dice Showdown**\nHost: <@{self.host_id}>\nOpponent: <@{self.p2_id}>\n\nBoth players, select your Brainrot & Color from the dropdowns, then click **🎲 Roll!**"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎲 Roll!", style=discord.ButtonStyle.primary, row=4)
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.host_id, self.p2_id if self.p2_id else -1):
            if self.p2_id is None:
                embed = discord.Embed(description="🎮 Someone needs to click **Join Game** first!", color=discord.Color.red())
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            embed = discord.Embed(description="This isn't your game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if self.p2_id is None:
            embed = discord.Embed(description="⏰ Waiting for an opponent to join the game first!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if not self.p1_brainrot.values or not self.p1_color.values:
            embed = discord.Embed(description="⚠️ Host must select their Brainrot and Color first!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if not self.p2_brainrot.values or not self.p2_color.values:
            embed = discord.Embed(description="⚠️ Opponent must select their Brainrot and Color first!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        p1_b_name = self.p1_brainrot.values[0]
        p1_c_name = self.p1_color.values[0]
        p2_b_name = self.p2_brainrot.values[0]
        p2_c_name = self.p2_color.values[0]

        b1_bonus = next((item["bonus"] for item in BRAINROT_CHOICES if item["label"] == p1_b_name), 0)
        c1_bonus = next((item["bonus"] for item in COLOR_CHOICES if item["label"] == p1_c_name), 0)
        b2_bonus = next((item["bonus"] for item in BRAINROT_CHOICES if item["label"] == p2_b_name), 0)
        c2_bonus = next((item["bonus"] for item in COLOR_CHOICES if item["label"] == p2_c_name), 0)

        p1_rigged = troll_settings.get(self.host_id, {}).get("brainrot", False)
        p2_rigged = troll_settings.get(self.p2_id, {}).get("brainrot", False)

        if p1_rigged and not p2_rigged:
            p1_roll = 50
            p2_roll = 1
        elif p2_rigged and not p1_rigged:
            p2_roll = 50
            p1_roll = 1
        else:
            p1_roll = random.randint(1, 6) + b1_bonus + c1_bonus
            p2_roll = random.randint(1, 6) + b2_bonus + c2_bonus

        embed = discord.Embed(title="💰 Brainrot Dice Battle Results", color=discord.Color.blurple())
        embed.add_field(name=f"<@{self.host_id}>", value=f"Brainrot: {p1_b_name}\nColor: {p1_c_name}\nTotal Roll: **{p1_roll}**", inline=True)
        embed.add_field(name=f"<@{self.p2_id}>", value=f"Brainrot: {p2_b_name}\nColor: {p2_c_name}\nTotal Roll: **{p2_roll}**", inline=True)

        if p1_roll > p2_roll:
            if self.amount > 0:
                update_wallet(self.host_id, self.amount)
                update_wallet(self.p2_id, -self.amount)
            embed.description = f"🎉 <@{self.host_id}> wins the battle!"
            embed.color = discord.Color.green()
        elif p2_roll > p1_roll:
            if self.amount > 0:
                update_wallet(self.p2_id, self.amount)
                update_wallet(self.host_id, -self.amount)
            embed.description = f"🎉 <@{self.p2_id}> wins the battle!"
            embed.color = discord.Color.green()
        else:
            embed.description = "🤝 It's a tie! No money exchanged."
            embed.color = discord.Color.gold()

        await interaction.response.send_message(embed=embed)

@bot.hybrid_command(name="brainrot_dice", description="Play a 2-player brainrot dice game")
async def brainrot_dice(ctx, amount: int = 0):
    if amount < 0:
        embed = discord.Embed(description="Amount cannot be negative.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if amount > 0:
        wallet, _, _ = get_user_econ(ctx.author.id)
        if wallet < amount:
            embed = discord.Embed(description="You don't have enough money in your wallet.", color=discord.Color.red())
            return await ctx.send(embed=embed)
    
    view = BrainrotDiceView(ctx.author.id, amount)
    embed = discord.Embed(
        title="🧠 Brainrot Dice Showdown",
        description=f"Host: {ctx.author.mention}\nOpponent: *Waiting for player...*\n\nClick **Join Game** to play!",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)

# =========================================================
# BLACKLIST & SERVER BLACKLIST MANAGEMENT COMMANDS
# =========================================================

@bot.hybrid_command(name="blacklist", description="Globally blacklist a user or server from using the bot")
@app_commands.check(owner_only_predicate)
async def blacklist(ctx, target: str, *, reason: str = "No reason provided"):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="You do not have permission to use this command.", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)
    
    if target.lower() in ("srv", "server"):
        gid = ctx.guild.id if ctx.guild else 0
        gname = ctx.guild.name if ctx.guild else "Unknown Server"
        if not gid:
            return await ctx.send("No valid guild found to blacklist.", ephemeral=True)
        cursor.execute("INSERT OR REPLACE INTO server_blacklist (guild_id, moderator_id, reason) VALUES (?, ?, ?)", (gid, ctx.author.id, reason))
        db.commit()
        embed = discord.Embed(description=f"Blacklisted server name: **{gname}** and server id: `{gid}` from using the bot. Reason: {reason}", color=discord.Color.orange())
        return await ctx.send(embed=embed)

    uid_str = re.sub(r'\D', '', target)
    if not uid_str:
        embed = discord.Embed(description="⭐️ Please provide a valid user ID, mention, or 'srv' keyword.", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)
    
    uid = int(uid_str)
    if uid in OWNER_IDS:
        embed = discord.Embed(description="☠️ You cannot blacklist a bot owner!", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)

    cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, moderator_id, reason) VALUES (?, ?, ?)", (uid, ctx.author.id, reason))
    db.commit()
    embed = discord.Embed(description=f"🚫 User ID `{uid}` has been **globally blacklisted** from using the bot. Reason: {reason}", color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unblacklist", description="Remove a user ID from the global bot blacklist")
@app_commands.check(owner_only_predicate)
async def unblacklist(ctx, user_id: str):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="🚿 You do not have permission to use this command.", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)
    
    try:
        uid = int(re.sub(r'\D', '', user_id))
    except ValueError:
        embed = discord.Embed(description="Please provide a valid numeric user ID.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (uid,))
    if not cursor.fetchone():
        embed = discord.Embed(description=f"User ID `{uid}` is not currently blacklisted.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (uid,))
    db.commit()
    embed = discord.Embed(description=f"🎉 User ID `{uid}` has been successfully removed from the global blacklist.", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="serverblacklist", description="Blacklist an entire server (Guild ID) from using the bot")
@app_commands.check(owner_only_predicate)
async def serverblacklist(ctx, guild_id: str, *, reason: str = "No reason provided"):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="You do not have **permission** to use this command.", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)
    
    try:
        gid = int(re.sub(r'\D', '', guild_id))
    except ValueError:
        embed = discord.Embed(description="Please provide a valid numeric server ID.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    target_guild = bot.get_guild(gid)
    gname = target_guild.name if target_guild else "Unknown Server"

    cursor.execute("INSERT OR REPLACE INTO server_blacklist (guild_id, moderator_id, reason) VALUES (?, ?, ?)", (gid, ctx.author.id, reason))
    db.commit()
    embed = discord.Embed(description=f"Blacklisted server name: **{gname}** and server id: `{gid}` from using the bot. Reason: {reason}", color=discord.Color.orange())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="serverunblacklist", description="Remove a server ID from the server blacklist")
@app_commands.check(owner_only_predicate)
async def serverunblacklist(ctx, guild_id: str):
    if ctx.author.id not in OWNER_IDS:
        embed = discord.Embed(description="You do not have permission to use this command.", color=discord.Color.red())
        return await ctx.send(embed=embed, ephemeral=True)
    
    try:
        gid = int(re.sub(r'\D', '', guild_id))
    except ValueError:
        embed = discord.Embed(description="Please provide a valid numeric server ID.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("SELECT 1 FROM server_blacklist WHERE guild_id = ?", (gid,))
    if not cursor.fetchone():
        embed = discord.Embed(description=f"❌ Server ID `{gid}` is not currently blacklisted.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("DELETE FROM server_blacklist WHERE guild_id = ?", (gid,))
    db.commit()
    embed = discord.Embed(description=f"✅ Server ID `{gid}` has been removed from the server blacklist.", color=discord.Color.green())
    await ctx.send(embed=embed)

# =========================================================
# SERVER SETUP
# =========================================================

SETUP_ROLES = [
    ("Owner", discord.Color.red()),
    ("Admin", discord.Color.orange()),
    ("Moderator", discord.Color.blue()),
    ("Staff", discord.Color.purple()),
    ("Giveaway Manager", discord.Color.gold()),
    ("Member", discord.Color.green()),
]

SETUP_STRUCTURE = {
    "Staff Only": [
        ("Staff-Rules", "📖", "text"),
        ("Staff-Announcements", "📢", "text"),
        ("Staff-Chat", "💬", "text"),
        ("Staff-Promotions", "🎉", "text"),
        ("Staff-Demotions", "📉", "text"),
        ("Applications", "📄", "text"),
        ("Staff-Vc", "🎙️", "voice"),
    ],
    "Arrivals": [
        ("Roles-Info", "🎭", "text"),
        ("Welcome", "👋", "text"),
        ("Goodbye", "🪽", "text"),
    ],
    "Important": [
        ("Verify", "✅", "text"),
        ("Rules", "📖", "text"),
        ("Announcements", "📢", "text"),
        ("Owners-Vouches", "📸", "text"),
        ("Owners-Trading", "👑", "text"),
        ("Applications", "📄", "text"),
        ("Server-Updates", "🔄", "text"),
        ("Spreader-Videos", "📺", "text"),
        ("Staff-Feedbacks", "🏅", "text"),
        ("Sab-Leaks", "👀", "text"),
        ("Partnerships", "⭐", "text"),
        ("Boosts", "🚀", "text"),
        ("Booster-Perks", "🏅", "text"),
        ("Polls", "📊", "text"),
        ("Hall-Of-Fame", "🥇", "text"),
        ("Help-Ticket", "🎟️", "text"),
    ],
    "Giveaways": [
        ("Giveaways", "🎁", "text"),
        ("Giveaway-Vouches", "🏅", "text"),
        ("Events", "🎉", "text"),
    ],
    "Middleman": [
        ("Middleman", "🤝", "text"),
        ("Middleman-Vouches", "⭐", "text"),
        ("Middleman-Ticket", "🎟️", "text"),
    ],
    "General": [
        ("General-Chat", "💬", "text"),
        ("Media", "📸", "text"),
        ("Commands", "⚙️", "text"),
        ("Steals", "💰", "text"),
        ("Levels", "📊", "text"),
        ("Boosters-Chat", "🚀", "text"),
        ("Suggestions", "🛠️", "text"),
    ],
    "Trading": [
        ("Trading-Fourm", "💸", "text"),
        ("Trading", "💰", "text"),
        ("Mid-Trading", "⭐", "text"),
        ("Og-Trading", "💎", "text"),
        ("Cross-Trading", "📌", "text"),
        ("Duel-Requests", "⚔️", "text"),
        ("Win-Or-Loss", "⚖️", "text"),
        ("Vouches", "✅", "text"),
    ],
    "Content Creators": [
        ("Creators-Rules", "📖", "text"),
        ("Creators-Announcements", "📢", "text"),
        ("Creators-Chat", "💬", "text"),
        ("Creators-Ideas", "🛠️", "text"),
    ],
    "Voice Chats": [
        ("Create-Vc", "🔑", "voice"),
        ("Owners-Vc", "👑", "voice"),
        ("General-Vc", "🎙️", "voice"),
        ("Trading-Vc", "💼", "voice"),
        ("Pvp-Vc", "⚔️", "voice"),
        ("Sab-Vc", "🎙️", "voice"),
    ],
}

# =========================================================
# THE TWO DESIGN STYLES
# =========================================================

DESIGNS = {
    "double_bracket_dot": {
        "label": "〚✅〛・syncs",
        "description": "〚🔒〛・staff-only  •  〚💬〛・general",
        "cat_fmt": lambda e, n: f"〚{e}〛・{n}",
        "ch_fmt":  lambda e, n: f"〚{e}〛・{n}",
    },
    "single_bracket": {
        "label": "〔👑〕",
        "description": "〔🔒〕staff-only  •  〔💬〕general",
        "cat_fmt": lambda e, n: f"〔{e}〕{n}",
        "ch_fmt":  lambda e, n: f"〔{e}〕{n}",
    },
}


class SetupStyleView(discord.ui.View):
    def __init__(self, user_id, timeout=180):
        super().__init__(timeout=timeout)
        self.user_id = user_id

        options = []
        for key, data in DESIGNS.items():
            options.append(
                discord.SelectOption(
                    label=data["label"],
                    description=data["description"][:100],
                    value=key,
                )
            )

        select = discord.ui.Select(
            placeholder="Choose a design style...",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This setup menu isn't for you. Run `/setup` yourself.",
                ephemeral=True,
            )
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction):
        design_key = interaction.data["values"][0]

        await interaction.response.defer()

        for child in self.children:
            child.disabled = True

        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        await run_server_setup(interaction, design_key)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


async def run_server_setup(interaction: discord.Interaction, design_key: str):
    guild = interaction.guild
    author = interaction.user
    design = DESIGNS[design_key]
    cat_fmt = design["cat_fmt"]
    ch_fmt = design["ch_fmt"]

    created_roles = 0
    created_channels = 0
    existing_channels = 0

    async def setup_error(message):
        try:
            return await interaction.followup.send(message, ephemeral=True)
        except Exception:
            return

    # --- ROLES ---
    for role_name, color in SETUP_ROLES:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                await guild.create_role(
                    name=role_name,
                    color=color,
                    reason=f"/setup used by {author}",
                )
                created_roles += 1
            except discord.Forbidden:
                return await setup_error("⚠️ I need **Manage Roles** permission to create the roles.")

    # --- CATEGORIES + CHANNELS ---
    CATEGORY_EMOJIS = {
        "Staff Only": "🔒",
        "Arrivals": "👋",
        "Important": "⚠️",
        "Giveaways": "🎁",
        "Middleman": "🤝",
        "General": "💬",
        "Trading": "💰",
        "Content Creators": "🎬",
        "Voice Chats": "🔊",
    }

    for category_name, channel_list in SETUP_STRUCTURE.items():
        cat_emoji = CATEGORY_EMOJIS.get(category_name, "📁")
        formatted_cat_name = cat_fmt(cat_emoji, category_name)

        category = discord.utils.get(guild.categories, name=formatted_cat_name)

        if category is None:
            try:
                category = await guild.create_category(
                    formatted_cat_name,
                    reason=f"/setup used by {author}",
                )
            except discord.Forbidden:
                return await setup_error("I need **Manage Channels** permission.")

        for base_name, emoji, channel_type in channel_list:
            channel_name = ch_fmt(emoji, base_name)
            safe_name = channel_name.replace(" ", "-").lower()
            existing = discord.utils.get(category.channels, name=safe_name)

            if existing is None:
                try:
                    if channel_type == "voice":
                        await guild.create_voice_channel(
                            name=safe_name,
                            category=category,
                            reason=f"/setup used by {author}",
                        )
                    else:
                        await guild.create_text_channel(
                            name=safe_name,
                            category=category,
                            reason=f"/setup used by {author}",
                        )
                    created_channels += 1
                except discord.Forbidden:
                    return await setup_error("I need **Manage Channels** permission.")
            else:
                existing_channels += 1

    success_embed = discord.Embed(
        title="✅ Setup Complete",
        description=(
            f"Created **{created_roles}** roles and **{created_channels}** new channels "
            f"({existing_channels} already existed).\n\n"
            f"**Design used:** `{design['label']}`"
        ),
        color=discord.Color.green(),
    )

    try:
        await interaction.followup.send(embed=success_embed, ephemeral=True)
    except Exception:
        pass


@bot.hybrid_command(name="setup", description="Create the server layout and choose a design style")
async def setup(ctx):
    try:
        guild = ctx.guild
        if guild is None:
            return await ctx.send("This command can only be used inside a server.")

        if ctx.author.id != guild.owner_id:
            embed = discord.Embed(
                description="👑 Only the **server owner** can use `/setup`.",
                color=discord.Color.red(),
            )
            if ctx.interaction:
                return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            return await ctx.send(embed=embed)

        embed = discord.Embed(
            title="🏗️ Server Setup — Choose a Design",
            description=(
                "Pick a design style from the dropdown below.\n"
                "The bot will build out all categories, channels, and roles automatically.\n\n"
                "**Options:**\n"
                "`〚🔒〛・staff-only`  •  `〔🔒〕staff-only`"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Select a design from the dropdown to begin")

        view = SetupStyleView(ctx.author.id)

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)

    except Exception as e:
        import traceback
        traceback.print_exc()
        err = discord.Embed(description=f"❌ Setup error: `{e}`", color=discord.Color.red())
        try:
            if ctx.interaction:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(embed=err, ephemeral=True)
                else:
                    await ctx.interaction.followup.send(embed=err, ephemeral=True)
            else:
                await ctx.send(embed=err)
        except Exception:
            pass
# =========================================================
# GUESS A NUMBER COMMAND & UI
# =========================================================

guess_number_games = {}

class BetModal(discord.ui.Modal, title="Set Your Bet"):
    amount = discord.ui.TextInput(label="Bet Amount", placeholder="e.g. 100", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bet_amount = int(self.amount.value.strip())
        except ValueError:
            embed = discord.Embed(description="Please enter a valid number.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if bet_amount <= 0:
            embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        wallet, _, _ = get_user_econ(interaction.user.id)
        if wallet < bet_amount:
            embed = discord.Embed(description="You dont have enough money you brokie.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.user.id in guess_number_games:
            embed = discord.Embed(description="You already have a game running!", color=discord.Color.orange())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        view = ModeSelectView(interaction.user.id, bet_amount)
        embed = discord.Embed(
            title="🎮 Guess A Number - Select Mode",
            description=f"Bet Amount: **${bet_amount:,}**\n\nWould you like to play with **AI** or a **Human**?",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ModeSelectView(discord.ui.View):
    def __init__(self, user_id, bet_amount):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.bet_amount = bet_amount

    async def on_timeout(self):
        guess_number_games.pop(self.user_id, None)

    @discord.ui.button(label="🤖 Play with AI", style=discord.ButtonStyle.primary)
    async def ai_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            embed = discord.Embed(description="This isn't your game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="🎮 Pick Your Secret Number",
            description="Enter your secret number (1-100):\n💰 Bet: **$" + str(self.bet_amount) + "**",
            color=discord.Color.blurple()
        )
        modal = PlayerNumberModal(self.user_id, self.bet_amount, is_ai=True)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="👤 Play with Human", style=discord.ButtonStyle.success)
    async def human_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            embed = discord.Embed(description="This isn't your game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        guess_number_games[self.user_id] = {"state": "waiting", "bet": self.bet_amount}
        
        embed = discord.Embed(
            title="👤 Waiting for Opponent...",
            description=f"{interaction.user.mention} is trying to play Guess A Number!\n💰 Bet: **${self.bet_amount:,}**\n\nClick below to accept the challenge!",
            color=discord.Color.gold()
        )
        view = HumanOpponentView(self.user_id, self.bet_amount)
        await interaction.response.send_message(embed=embed, view=view)

class HumanOpponentView(discord.ui.View):
    def __init__(self, proposer_id, bet_amount):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.bet_amount = bet_amount
        self.opponent_id = None
        self.answered = False

    async def on_timeout(self):
        guess_number_games.pop(self.proposer_id, None)

    @discord.ui.button(label="✅ Accept Challenge", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.proposer_id:
            embed = discord.Embed(description="You can't play against yourself!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if self.answered:
            embed = discord.Embed(description="Someone already accepted this challenge.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.user.id in guess_number_games:
            embed = discord.Embed(description="You're already in a game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        wallet, _, _ = get_user_econ(interaction.user.id)
        if wallet < self.bet_amount:
            embed = discord.Embed(description="You dont have enough money you brokie.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        self.answered = True
        self.opponent_id = interaction.user.id
        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="🎮 Pick Your Secret Number",
            description=f"Enter your secret number (1-100):\n💰 Bet: **${self.bet_amount:,}**",
            color=discord.Color.blurple()
        )
        modal = PlayerNumberModal(self.opponent_id, self.bet_amount, is_ai=False, opponent_id=self.proposer_id)
        await interaction.response.send_modal(modal)
        await interaction.message.edit(view=self)

class PlayerNumberModal(discord.ui.Modal, title="Pick Your Secret Number"):
    number = discord.ui.TextInput(label="Number (1-100)", placeholder="e.g. 42", required=True)

    def __init__(self, player_id, bet_amount, is_ai=True, opponent_id=None):
        super().__init__()
        self.player_id = player_id
        self.bet_amount = bet_amount
        self.is_ai = is_ai
        self.opponent_id = opponent_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            secret_number = int(self.number.value.strip())
        except ValueError:
            embed = discord.Embed(description="Please enter a valid number.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if secret_number < 1 or secret_number > 100:
            embed = discord.Embed(description="Number must be between 1 and 100.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if self.is_ai:
            ai_number = random.randint(1, 100)
            guess_number_games[self.player_id] = {
                "player_secret": secret_number,
                "ai_secret": ai_number,
                "player_attempts": 0,
                "ai_attempts": 0,
                "bet": self.bet_amount,
                "opponent": None,
                "is_ai": True,
                "low": 1,
                "high": 100,
                "game_over": False,
                "start_time": time.time(),
                "channel_id": interaction.channel_id
            }
            
            view = GameView(self.player_id, self.bet_amount, is_ai=True, channel_id=interaction.channel_id)
            embed = discord.Embed(
                title="🎮 Guess A Number vs AI",
                description=f"🤖 The AI has chosen a number between **1-100**.\n💰 Bet: **${self.bet_amount:,}**\n⏱️ Time: 3:20 | 🔢 Guesses: 20\n\n👉 **Your turn!** Guess the AI's number!",
                color=discord.Color.blurple()
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            guess_number_games[self.player_id] = {
                "player_secret": secret_number,
                "opponent_secret": None,
                "player_attempts": 0,
                "opponent_attempts": 0,
                "bet": self.bet_amount,
                "opponent": self.opponent_id,
                "is_ai": False,
                "low": 1,
                "high": 100,
                "game_over": False,
                "start_time": time.time(),
                "channel_id": interaction.channel_id
            }
            
            view = GameView(self.player_id, self.bet_amount, is_ai=False, opponent_id=self.opponent_id, channel_id=interaction.channel_id)
            embed = discord.Embed(
                title="🎮 Guess A Number vs Human",
                description=f"<@{self.opponent_id}> has chosen a number between **1-100**.\n💰 Bet: **${self.bet_amount:,}**\n⏱️ Time: 3:20 | 🔢 Guesses: 20\n\n👉 **<@{self.player_id}>'s turn!** Guess the opponent's number!",
                color=discord.Color.blurple()
            )
            await interaction.response.send_message(embed=embed, view=view)

class GameView(discord.ui.View):
    def __init__(self, player_id, bet_amount, is_ai=True, opponent_id=None, channel_id=None):
        super().__init__(timeout=200)
        self.player_id = player_id
        self.bet_amount = bet_amount
        self.is_ai = is_ai
        self.opponent_id = opponent_id
        self.game_over = False
        self.channel_id = channel_id

    async def on_timeout(self):
        if self.game_over:
            return
        guess_number_games.pop(self.player_id, None)
        if self.opponent_id:
            guess_number_games.pop(self.opponent_id, None)

    @discord.ui.button(label="🎯 Make a Guess", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = guess_number_games.get(self.player_id)
        if not game:
            embed = discord.Embed(description="Game not found!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        elapsed = time.time() - game["start_time"]
        if elapsed > 200:
            self.game_over = True
            embed = discord.Embed(title="⏰ Time's Up!", description="Game ended due to timeout.", color=discord.Color.red())
            guess_number_games.pop(self.player_id, None)
            if self.opponent_id:
                guess_number_games.pop(self.opponent_id, None)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if game["player_attempts"] >= 20:
            embed = discord.Embed(description="You've used all 20 guesses!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.user.id != self.player_id:
            embed = discord.Embed(description="This isn't your turn!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        modal = GuessModal(self, self.player_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🛑 Stop Game", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.player_id, self.opponent_id if self.opponent_id else self.player_id):
            embed = discord.Embed(description="You're not part of this game!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        self.game_over = True
        embed = discord.Embed(
            title="🛑 Game Stopped",
            description=f"{interaction.user.mention} stopped the game.\n\n💰 No money was exchanged. Bets returned.",
            color=discord.Color.orange()
        )
        
        guess_number_games.pop(self.player_id, None)
        if self.opponent_id:
            guess_number_games.pop(self.opponent_id, None)
        
        await interaction.response.edit_message(embed=embed, view=None)

class GuessModal(discord.ui.Modal, title="Enter Your Guess"):
    guess = discord.ui.TextInput(label="Your Guess (1-100)", placeholder="e.g. 50", required=True)

    def __init__(self, game_view, player_id):
        super().__init__()
        self.game_view = game_view
        self.player_id = player_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            guess_num = int(self.guess.value.strip())
        except ValueError:
            embed = discord.Embed(description="Please enter a valid number.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if guess_num < 1 or guess_num > 100:
            embed = discord.Embed(description="Number must be between 1 and 100.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        game = guess_number_games.get(self.player_id)
        if not game or game["game_over"]:
            return

        elapsed = int(time.time() - game["start_time"])
        remaining_secs = max(0, 200 - elapsed)
        remaining_time = f"{remaining_secs // 60}:{remaining_secs % 60:02d}"

        if self.game_view.is_ai:
            secret = game["ai_secret"]
            game["player_attempts"] += 1

            if guess_num == secret:
                game["game_over"] = True
                update_wallet(self.player_id, self.game_view.bet_amount)
                embed = discord.Embed(
                    title="🎉 Correct!",
                    description=f"<@{self.player_id}> guessed **{guess_num}** and got it **CORRECT**!\n\n🏆 You won!\n💰 Winnings: **${self.game_view.bet_amount:,}**\n📊 Your Attempts: **{game['player_attempts']}**",
                    color=discord.Color.green()
                )
                guess_number_games.pop(self.player_id, None)
                await interaction.response.send_message(embed=embed)
            elif guess_num < secret:
                embed = discord.Embed(
                    description=f"<@{self.player_id}> guessed **{guess_num}** and got it **WRONG**! 📈 Too low!\n\n**Your Attempts: {game['player_attempts']}/20 | ⏱️ {remaining_time}**\n\n👉 **🤖 Bot's turn next!**",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                await asyncio.sleep(2)
                await self.bot_guess(interaction, game)
            else:
                embed = discord.Embed(
                    description=f"<@{self.player_id}> guessed **{guess_num}** and got it **WRONG**! 📉 Too high!\n\n**Your Attempts: {game['player_attempts']}/20 | ⏱️ {remaining_time}**\n\n👉 **🤖 Bot's turn next!**",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                await asyncio.sleep(2)
                await self.bot_guess(interaction, game)

    async def bot_guess(self, interaction: discord.Interaction, game):
        if game["game_over"] or game["ai_attempts"] >= 20:
            return

        bot_guess = random.randint(1, 100)
        secret = game["player_secret"]
        game["ai_attempts"] += 1

        elapsed = int(time.time() - game["start_time"])
        remaining_secs = max(0, 200 - elapsed)
        remaining_time = f"{remaining_secs // 60}:{remaining_secs % 60:02d}"

        turn_embed = discord.Embed(
            description="🤖 **Bot's turn!**",
            color=discord.Color.blurple()
        )
        await interaction.channel.send(embed=turn_embed, ephemeral=True)
        await asyncio.sleep(1)

        if bot_guess == secret:
            game["game_over"] = True
            update_wallet(self.player_id, -self.game_view.bet_amount)
            embed = discord.Embed(
                title="🤖 Bot Guessed Correct!",
                description=f"🤖 Bot guessed **{bot_guess}** and got it **CORRECT**!\n\n🏆 Bot won!\n💰 You lost: **${self.game_view.bet_amount:,}**\n📊 Bot Attempts: **{game['ai_attempts']}**",
                color=discord.Color.red()
            )
            guess_number_games.pop(self.player_id, None)
            await interaction.channel.send(embed=embed, ephemeral=True)
        elif bot_guess < secret:
            embed = discord.Embed(
                description=f"🤖 Bot guessed **{bot_guess}** and got it **WRONG**! 📈 Too low!\n\n**Bot Attempts: {game['ai_attempts']}/20 | ⏱️ {remaining_time}**\n\n👉 **Your turn next!**",
                color=discord.Color.orange()
            )
            await interaction.channel.send(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                description=f"🤖 Bot guessed **{bot_guess}** and got it **WRONG**! 📉 Too high!\n\n**Bot Attempts: {game['ai_attempts']}/20 | ⏱️ {remaining_time}**\n\n👉 **Your turn next!**",
                color=discord.Color.orange()
            )
            await interaction.channel.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name="guess", description="Play Guess A Number for money!")
async def guess(ctx):
    if ctx.author.id in guess_number_games:
        embed = discord.Embed(description="You already have a game running!", color=discord.Color.orange())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    modal = BetModal()
    if ctx.interaction:
        await ctx.interaction.response.send_modal(modal)

# ---------- GIVEAWAY (button entry) + REROLL SUPPORT ----------

@bot.hybrid_group(name="giveaway", description="Giveaway commands")
async def giveaway_group(ctx):
    pass

class GiveawayEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter Giveaway 🎉", style=discord.ButtonStyle.primary, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot:
            return await interaction.response.send_message("Bots can't join giveaways.", ephemeral=True)

        try:
            await interaction.response.send_message("🎉 You have successfully entered the giveaway good luck", ephemeral=True)
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        async def do_join(message_id: int, user_id: int):
            try:
                cursor.execute("SELECT entrants FROM giveaways WHERE message_id = ?", (message_id,))
                row = cursor.fetchone()
                if not row:
                    return
                try:
                    entrants = json.loads(row[0] or "[]")
                except Exception:
                    entrants = []

                if user_id in entrants:
                    return

                entrants.append(user_id)
                cursor.execute("UPDATE giveaways SET entrants = ? WHERE message_id = ?", (json.dumps(entrants), message_id))
                db.commit()

                try:
                    cursor.execute("SELECT channel_id FROM giveaways WHERE message_id = ?", (message_id,))
                    chrow = cursor.fetchone()
                    if chrow:
                        channel_id = chrow[0]
                        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                        msg = await channel.fetch_message(message_id)
                        if msg and msg.embeds:
                            embed = msg.embeds[0]
                            found = False
                            for i, f in enumerate(embed.fields):
                                if f.name == "Entrants":
                                    embed.set_field_at(i, name="Entrants", value=str(len(entrants)), inline=True)
                                    found = True
                                    break
                            if not found:
                                embed.add_field(name="Entrants", value=str(len(entrants)), inline=True)
                            await msg.edit(embed=embed, view=self)
                except Exception:
                    pass
            except Exception:
                pass

        message_id = interaction.message.id if interaction.message else None
        if message_id:
            bot.loop.create_task(do_join(message_id, interaction.user.id))

@giveaway_group.command(name="create", description="Create a giveaway (button entry)")
async def giveaway_create(
    ctx,
    duration: str,
    winners: int,
    prize: str,
    channel: discord.TextChannel = None
):
    if ctx.guild is None:
        return await ctx.send(embed=discord.Embed(description="This command must be used in a server.", color=discord.Color.red()))
    if not ctx.author.guild_permissions.manage_guild and ctx.author.id not in OWNER_IDS:
        return await ctx.send(embed=discord.Embed(description="You need Manage Server permission to create giveaways.", color=discord.Color.red()))

    seconds = parse_duration(duration)
    if seconds is None or seconds <= 0:
        return await ctx.send(embed=discord.Embed(description="Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d`.", color=discord.Color.red()))
    if winners < 1:
        return await ctx.send(embed=discord.Embed(description="Winners must be at least 1.", color=discord.Color.red()))
    if winners > 25:
        return await ctx.send(embed=discord.Embed(description="Winners too high (max 25).", color=discord.Color.red()))

    target_channel = channel or ctx.channel
    if target_channel.guild.id != ctx.guild.id:
        return await ctx.send(embed=discord.Embed(description="Channel must be in this server.", color=discord.Color.red()))

    end_ts = int(time.time() + seconds)
    end_ts_discord = f"<t:{end_ts}:R>"

    embed = discord.Embed(
        title=f"🎉 Giveaway: {prize}",
        description=(
            f"Hosted by: {ctx.author.mention}\n"
            f"Ends: {end_ts_discord}\n"
            f"Winners: **{winners}**\n\n"
            "Click the button below to enter!"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Giveaway created by {ctx.author.display_name}")

    try:
        view = GiveawayEntryView()
        giveaway_msg = await target_channel.send(embed=embed, view=view)
    except Exception as e:
        return await ctx.send(embed=discord.Embed(description=f"Failed to post giveaway: {e}", color=discord.Color.red()))

    cursor.execute(
        "INSERT INTO giveaways (message_id, channel_id, guild_id, prize, host_id, end_time, winners, entrants, winners_list) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (giveaway_msg.id, target_channel.id, ctx.guild.id, prize, ctx.author.id, end_ts, winners, json.dumps([]), json.dumps([]))
    )
    db.commit()

    try:
        embed.add_field(name="Entrants", value="0", inline=True)
        await giveaway_msg.edit(embed=embed, view=view)
    except Exception:
        pass

    confirm = discord.Embed(description=f"🎉 Giveaway created in {target_channel.mention} and will end {end_ts_discord}.", color=discord.Color.green())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=confirm, ephemeral=True)
    else:
        await ctx.send(embed=confirm, delete_after=10)

    bot.loop.create_task(_handle_giveaway_end(giveaway_msg.id, target_channel.id, ctx.guild.id, prize, winners, end_ts, ctx.author.id))

async def _handle_giveaway_end(message_id: int, channel_id: int, guild_id: int, prize: str, winners_count: int, end_time_unix: int, host_id: int):
    wait_for = max(0, end_time_unix - int(time.time()))
    await asyncio.sleep(wait_for)

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    cursor.execute("SELECT entrants, winners_list FROM giveaways WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    entrants: List[int] = []
    previous_winners: List[int] = []
    if row:
        try:
            entrants = json.loads(row[0] or "[]")
        except Exception:
            entrants = []
        try:
            previous_winners = json.loads(row[1] or "[]")
        except Exception:
            previous_winners = []

    winner_mentions = "None"
    winners = []
    if not entrants:
        result_embed = discord.Embed(title="🎉 Giveaway Ended", description=f"No valid entrants for **{prize}**. No winners were chosen.", color=discord.Color.orange())
        await channel.send(embed=result_embed)
    else:
        pick_count = min(winners_count, len(entrants))
        winners = random.sample(entrants, k=pick_count)
        winner_mentions = ", ".join(f"<@{w}>" for w in winners)

        cursor.execute("UPDATE giveaways SET winners_list = ? WHERE message_id = ?", (json.dumps(winners), message_id))
        db.commit()

        result_embed = discord.Embed(
            title="🎉 Giveaway Ended — Congratulations!",
            description=f"{winner_mentions} 🎁 won the giveaway of **{prize}**!",
            color=discord.Color.green()
        )
        result_embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        result_embed.add_field(name="Entrants", value=str(len(entrants)), inline=True)

        for uid in winners:
            try:
                user = await bot.fetch_user(uid)
                dm_text = f"🎉 Congrats {user.mention} u won the giveaway **{prize}** in **{channel.guild.name}** pls check the server or ping the host <@{host_id}> in the server to claim ur giveaway!"
                await user.send(dm_text)
            except Exception:
                pass

        await channel.send(embed=result_embed)

    try:
        if message.embeds:
            ended_embed = message.embeds[0]
            ended_embed.title = f"🎉 Giveaway (ENDED): {prize}"
            ended_embed.color = discord.Color.dark_gray()
            ended_embed.set_footer(text="This giveaway has ended.")
            ended_embed.description = (ended_embed.description or "") + f"\n\nWinners: {winner_mentions}"
            await message.edit(embed=ended_embed, view=None)
    except Exception:
        pass

@giveaway_group.command(name="reroll", description="Reroll winners for a giveaway by message ID (host only)")
async def giveaway_reroll(ctx, message_id: int, count: int = 1):
    cursor.execute("SELECT channel_id, prize, host_id, entrants, winners_list FROM giveaways WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        return await ctx.send(embed=discord.Embed(description="Could not find a giveaway with that message ID.", color=discord.Color.red()))

    channel_id, prize, host_id, entrants_json, winners_json = row
    if ctx.author.id != host_id and ctx.author.id not in OWNER_IDS:
        return await ctx.send(embed=discord.Embed(description="Only the giveaway host or bot owners can reroll this giveaway.", color=discord.Color.red()))

    try:
        entrants = json.loads(entrants_json or "[]")
    except Exception:
        entrants = []
    try:
        previous_winners = json.loads(winners_json or "[]")
    except Exception:
        previous_winners = []

    if not entrants:
        return await ctx.send(embed=discord.Embed(description="No entrants to pick from.", color=discord.Color.orange()))

    pool = [u for u in entrants if u not in previous_winners]
    if not pool:
        pool = entrants.copy()

    pick_count = max(1, min(count, len(pool)))
    new_winners = random.sample(pool, k=pick_count)

    updated_winners = previous_winners + new_winners
    cursor.execute("UPDATE giveaways SET winners_list = ? WHERE message_id = ?", (json.dumps(updated_winners), message_id))
    db.commit()

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        mentions = ", ".join(f"<@{w}>" for w in new_winners)
        if len(new_winners) == 1:
            title = "🎉 Reroll — New Winner!"
            desc = f"Congrats — new winner of **{prize}** is {mentions}!"
        else:
            title = "🎉 Reroll — New Winners!"
            desc = f"Congrats — new winners of **{prize}** are {mentions}!"

        reroll_embed = discord.Embed(title=title, description=desc, color=discord.Color.green())
        reroll_embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        reroll_embed.set_footer(text=f"Rerolled by {ctx.author.display_name}")

        await channel.send(embed=reroll_embed)

        for uid in new_winners:
            try:
                user = await bot.fetch_user(uid)
                dm_text = f"congrats {user.mention} u won the giveaway **{prize}** in **{channel.guild.name}** pls check the server or ping the host <@{host_id}> in the server to claim ur giveaway!"
                await user.send(dm_text)
            except Exception:
                pass

        try:
            original_msg = await channel.fetch_message(message_id)
            if original_msg and original_msg.embeds:
                ed = original_msg.embeds[0]
                ed.description = (ed.description or "") + f"\n\nReroll winners: {mentions}"
                await original_msg.edit(embed=ed)
        except Exception:
            pass

        await ctx.send(embed=discord.Embed(description=f"Rerolled — new winner(s): {mentions}", color=discord.Color.green()))
    except Exception as e:
        await ctx.send(embed=discord.Embed(description=f"Failed to announce reroll: {e}", color=discord.Color.red()))

# =========================================================
# SYNC COMMAND
# =========================================================

@bot.hybrid_command(name="sync", description="Force sync slash commands")
@app_commands.check(owner_only_predicate)
async def sync(ctx):
    try:
        await bot.tree.sync()
        await ctx.send("✅ Commands have been synced globally!")
    except Exception as e:
        await ctx.send(f"❌ Sync failed: {e}")

# =========================================================
# GOON COMMAND
# =========================================================

@bot.hybrid_command(name="goon", description="Goon on someone as a joke!")
@app_commands.check(owner_only_predicate)
async def goon(ctx, member: discord.Member):
    author = ctx.author
    target = member

    if author.id == target.id:
        response_text = f"{author.mention} tried to goon to themselves but that's too weird... so they gooned to the air instead! 🫠"
    else:
        response_text = f"{author.mention} gooned to {target.mention} and felt amazing!"

    embed = discord.Embed(
        description=response_text,
        color=discord.Color.purple()
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1478277367029039115/1549139595638341712/togif.f686f4ae.gif?ex=6aaa44ed&is=6aa8f36d&hm=744c9b882dbac52cfbbacf98f015b05506ddf138c398aa694500388e25846f4d")

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except:
                pass
        await ctx.send(embed=embed)

# =========================================================
# ADVANCED FREE SERVER BACKUP & RESTORE SYSTEM
# =========================================================

from discord.ui import View, Select

class RestoreSelectView(View):
    def __init__(self, ctx, backup_data):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.backup_data = backup_data
        self.selected_options = ["Delete Roles", "Delete Channels", "Load Roles", "Load Channels", "Load Settings", "Load Messages"]

        self.select = Select(
            placeholder="Select options to load from backup...",
            min_values=1,
            max_values=6,
            options=[
                discord.SelectOption(label="Delete Roles", value="Delete Roles", default=True, description="Wipes current non-managed roles"),
                discord.SelectOption(label="Delete Channels", value="Delete Channels", default=True, description="Wipes current server channels"),
                discord.SelectOption(label="Load Roles", value="Load Roles", default=True, description="Recreates roles with exact permissions & colors"),
                discord.SelectOption(label="Load Channels", value="Load Channels", default=True, description="Recreates categories, text, and voice channels"),
                discord.SelectOption(label="Load Settings", value="Load Settings", default=True, description="Restores server name, icon, and region settings"),
                discord.SelectOption(label="Load Messages", value="Load Messages", default=True, description="Restores backed-up messages into channels")
            ]
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
        self.selected_options = self.select.values
        await interaction.response.defer()

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.green)
    async def continue_button(self, interaction: discord.Interaction, button: discord.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
        
        confirm_view = ConfirmRestoreView(self.ctx, self.backup_data, self.selected_options)
        
        roles_up = "will be updated" if "Load Roles" in self.selected_options else "will be skipped"
        ch_del = len(self.ctx.guild.channels) if "Delete Channels" in self.selected_options else 0
        ch_cre = len(self.backup_data["channels"]) if "Load Channels" in self.selected_options else 0

        embed = discord.Embed(
            title="⚠️ Warning",
            description=(
                "**Hey, be careful!** The following actions will be taken on this server and **can not be undone**:\n\n"
                f"• **1** roles {roles_up}\n"
                f"• **{ch_del}** channels will be **deleted**\n"
                f"• **{ch_cre}** channels will be created\n"
                f"• Server settings will {'be updated' if 'Load Settings' in self.selected_options else 'remain unchanged'}"
            ),
            color=discord.Color.gold()
        )
        await interaction.response.edit_message(embed=embed, view=confirm_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
        embed = discord.Embed(title="❌ Restore Cancelled", description="Server restoration was aborted safely.", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

class ConfirmRestoreView(View):
    def __init__(self, ctx, backup_data, options):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.backup_data = backup_data
        self.options = options

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)

        guild = self.ctx.guild
        await interaction.response.edit_message(embed=discord.Embed(title="♻️ Restoring Server...", description="Processing backup payload. Please wait...", color=discord.Color.blue()), view=None)

        try:
            if "Load Settings" in self.options:
                try:
                    await guild.edit(name=self.backup_data.get("name", guild.name))
                except Exception:
                    pass

            if "Delete Channels" in self.options:
                for channel in guild.channels:
                    try:
                        await channel.delete(reason="Server Restore: Wiping old channels")
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

            if "Delete Roles" in self.options:
                for role in guild.roles:
                    if role != guild.default_role and not role.managed and role < guild.me.top_role:
                        try:
                            await role.delete(reason="Server Restore: Wiping old roles")
                            await asyncio.sleep(0.2)
                        except Exception:
                            pass

            role_mapping = {}
            if "Load Roles" in self.options:
                for r_data in self.backup_data["roles"]:
                    try:
                        new_role = await guild.create_role(
                            name=r_data["name"],
                            color=discord.Color(r_data["color"]),
                            permissions=discord.Permissions(r_data["permissions"]),
                            hoist=r_data["hoist"],
                            mentionable=r_data["mentionable"],
                            reason="Server Restore: Restoring role"
                        )
                        role_mapping[r_data["name"]] = new_role
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

            category_mapping = {}
            channel_mapping = {}

            if "Load Channels" in self.options:
                for c_data in self.backup_data["channels"]:
                    if c_data["type"] == "category":
                        try:
                            new_cat = await guild.create_category(name=c_data["name"], position=c_data["position"])
                            category_mapping[c_data["name"]] = new_cat
                            channel_mapping[c_data["name"]] = new_cat
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass

                for c_data in self.backup_data["channels"]:
                    if c_data["type"] == "text":
                        cat = category_mapping.get(c_data["category"]) if c_data["category"] else None
                        try:
                            new_ch = await guild.create_text_channel(name=c_data["name"], category=cat, position=c_data["position"])
                            channel_mapping[c_data["name"]] = new_ch
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                    elif c_data["type"] == "voice":
                        cat = category_mapping.get(c_data["category"]) if c_data["category"] else None
                        try:
                            new_ch = await guild.create_voice_channel(name=c_data["name"], category=cat, position=c_data["position"])
                            channel_mapping[c_data["name"]] = new_ch
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass

            if "Load Messages" in self.options and "messages" in self.backup_data:
                for ch_name, msgs in self.backup_data["messages"].items():
                    target_ch = channel_mapping.get(ch_name)
                    if target_ch and isinstance(target_ch, discord.TextChannel):
                        for m in reversed(msgs):
                            try:
                                author_tag = m["author"]
                                content = f"**[Backup Archive] {author_tag}:** {m['content']}"
                                
                                if m.get("attachments"):
                                    content += "\n" + "\n".join(m["attachments"])
                                    
                                await target_ch.send(content)
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass

            success_embed = discord.Embed(
                title="✅ Success",
                description="Server restoration completed successfully with all layouts, styles, and settings copied perfectly!",
                color=discord.Color.green()
            )
            await self.ctx.send(embed=success_embed)

        except Exception as e:
            err_embed = discord.Embed(title="❌ Error", description=f"An error occurred during restoration: `{e}`", color=discord.Color.red())
            await self.ctx.send(embed=err_embed)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
        await interaction.response.edit_message(embed=discord.Embed(title="❌ Cancelled", description="Restoration cancelled.", color=discord.Color.red()), view=None)

@bot.hybrid_group(name="backup", description="Server backup management commands")
@app_commands.check(owner_only_predicate)
async def backup(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("Use `/backup create`, `/backup info`, or `/backup load`.", ephemeral=True)

@backup.command(name="create", description="Create a backup of this server (channels, roles, settings, and messages)")
@app_commands.check(owner_only_predicate)
async def backup_create(ctx, message_count: int = 25):
    if ctx.author != ctx.guild.owner and ctx.author.id not in OWNER_IDS:
        return await ctx.send("Only the server owner can create backups.", ephemeral=True)

    guild = ctx.guild
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)

    import random, string, datetime
    backup_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=11))
    
    channels_data = []
    for c in sorted(guild.channels, key=lambda x: x.position):
        channels_data.append({
            "name": c.name,
            "type": str(c.type),
            "category": c.category.name if c.category else None,
            "position": c.position
        })

    roles_data = []
    for r in guild.roles:
        if r != guild.default_role and not r.managed:
            roles_data.append({
                "name": r.name,
                "color": r.color.value,
                "permissions": r.permissions.value,
                "hoist": r.hoist,
                "mentionable": r.mentionable
            })

    messages_data = {}
    if message_count > 0:
        for channel in guild.text_channels:
            try:
                ch_msgs = []
                async for message in channel.history(limit=message_count):
                    ch_msgs.append({
                        "author": str(message.author),
                        "content": message.content,
                        "attachments": [att.url for att in message.attachments]
                    })
                if ch_msgs:
                    messages_data[channel.name] = ch_msgs
            except Exception:
                pass

    server_backups[backup_id] = {
        "name": guild.name,
        "channels": channels_data,
        "roles": roles_data,
        "messages": messages_data,
        "created_at": datetime.datetime.now().strftime("%d. %b %Y - %H:%M")
    }

    date_str = datetime.datetime.now().strftime("%d. %b %Y - %H:%M")
    backup_label = f"{guild.name} | {date_str} ({backup_id})"

    embed = discord.Embed(
        title="✅ Success",
        description=(
            f"Successfully **created backup** with the id `{backup_id}`.\n\n"
            f"This backup contains full server settings, channels, roles, and message archives!\n\n"
            f"**Usage**\n"
            f"`/backup info backup_id: {backup_id}`\n"
            f"`/backup load backup_id: {backup_id}`"
        ),
        color=discord.Color.green()
    )

    if ctx.interaction:
        await ctx.interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

@backup.command(name="info", description="View details of a specific backup id")
@app_commands.check(owner_only_predicate)
async def backup_info(ctx, backup_id: str):
    bdata = server_backups.get(backup_id)
    if not bdata:
        return await ctx.send("❌ No backup found with that ID.", ephemeral=True)

    embed = discord.Embed(
        title=f"📦 Backup Info: {backup_id}",
        description=(
            f"**Server Name:** {bdata['name']}\n"
            f"**Created At:** {bdata['created_at']}\n"
            f"**Channels Saved:** {len(bdata['channels'])}\n"
            f"**Roles Saved:** {len(bdata['roles'])}\n"
            f"**Message Archives:** {len(bdata['messages'])} channels archived"
        ),
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, ephemeral=True)

@backup.command(name="load", description="Load and restore a backup into the current server")
@app_commands.check(owner_only_predicate)
async def backup_load(ctx, backup_id: str):
    if ctx.author != ctx.guild.owner and ctx.author.id not in OWNER_IDS:
        return await ctx.send("Only the server owner can load backups.", ephemeral=True)

    bdata = server_backups.get(backup_id)
    if not bdata:
        return await ctx.send("❌ Invalid backup ID or backup does not exist.", ephemeral=True)

    embed = discord.Embed(
        title="⚠️ Warning",
        description=(
            "**What do you want to load from the backup?**\n\n"
            "Select below what actions you would like to perform. In the next menu, "
            "you will be able to see a detailed list of changes before continuing."
        ),
        color=discord.Color.gold()
    )

    view = RestoreSelectView(ctx, bdata)
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await ctx.send(embed=embed, view=view)
        
@bot.hybrid_command(name="memes", description="Get a random meme GIF")
async def memes(ctx):
    import random
    meme_list = [
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZncyZ3YzanY3YmVqcDc2NzI0Zm1wNTloZnRmYmJxcTAyYXlkemlqYiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/s5wFafpHxqKbIEERl9/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExMWtucnBobGJlMzZ4YTZrOHZ5ejdncjN3dWYxM3VyN3k4NGxqdnUwOCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/HAi4i45T0pAdGERov4/giphy.gif",
        "https://media.giphy.com/media/1rPynGFeM7zcvMwm4k/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZmQzZHd3eWM1ZncwcGk3ZTEzajgxeHZ5bXNqbjhkeHJ6ZjlsZzAxciZlcD12MV9naWZzX3NlYXJjaCZjdD1n/DMVPvOIRovYfc2jYMO/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZmQzZHd3eWM1ZncwcGk3ZTEzajgxeHZ5bXNqbjhkeHJ6ZjlsZzAxciZlcD12MV9naWZzX3NlYXJjaCZjdD1n/gbwNUZEPU58BscyIqO/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExODM1M2lqd3I0eHhuYTd3NjdrMTFoOGNscW8zYzgxM2N6ZXdldnh2ZCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/0SH6U6rfZaUGKWElMi/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExODM1M2lqd3I0eHhuYTd3NjdrMTFoOGNscW8zYzgxM2N6ZXdldnh2ZCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/4mLMHnkZUBgyCA9Smb/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExaHZuczMybm9uaGx0M3owZHVhY2ttdmNydzV1dDVsN3JmbmMyMW4waiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/S2JEtjACyne6DaEPse/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExajM5bjVrbjJ6Mm81cW9vcXA5ajAxbWRjbnIzMTEzcDUxenB1MnQ3eSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/PvhUjFp3M4hCzdwI0r/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExdm40bTZyeDdrZWlvaW5lemt6MjlnMTVzZTBhand5ZDRxY3d2Z3Q0diZlcD12MV9naWZzX3NlYXJjaCZjdD1n/kMZJErKgZtONJZOQE6/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExOHp1ZXYyMzhld2wwMTd6NXowbGp0aTUzNXh6dGkwaHBzOHM4dWw1dCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/uv97PA6qJXfa4unM2f/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExYzIzYmZhNHplOXFiN2RobWI1M3h3ejNjaWpvajIzbThkanVuZ3c0YSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/j26LBfouLB4x29PE4b/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPWVjZjA1ZTQ3MXB0cjVpdGZtYzRyaTVtZWRkemY5OGRpMHl0cGhpaWV5NGxjbzA3eCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/2g6sCTsSoVuSfSxK4W/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExOHVsOHlsaHFuaGVsMGtpc29nMzU1Y2t6bzc0bzdwbmlkc2c5cDlpdyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/jGgC8JjZfLurTJSxQ8/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExOHVsOHlsaHFuaGVsMGtpc29nMzU1Y2t6bzc0bzdwbmlkc2c5cDlpdyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/ffTEJW8xipu8Lao3Nz/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExOHVsOHlsaHFuaGVsMGtpc29nMzU1Y2t6bzc0bzdwbmlkc2c5cDlpdyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/mD1GlEW658iW4H32BC/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExOHVsOHlsaHFuaGVsMGtpc29nMzU1Y2t6bzc0bzdwbmlkc2c5cDlpdyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/K72cKlnwNPUHvXxoNt/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExa2Z0d2ppd3V6cHAwMzY1dzVraDd0cHZqeXJ1NGZpZTNtbnlncWU3NSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/YSD04aQmVadOQen7rH/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExeDdxejRoaWpzajZ2bDY1MXN2OWxrYXBsc3BxODB1aDBmejU0Z2tibyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/iPD4BGASjKxHUib1FA/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExeDdxejRoaWpzajZ2bDY1MXN2OWxrYXBsc3BxODB1aDBmejU0Z2tibyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/HwGL9KXTl1UmpZcSX6/giphy.gif",
        "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcGp3bW03YzdpMnRmbzgxOGV3YXg4cG1jYXUyM29qamNvZjB1bHJzYiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/lEhwRSGkOtjBHzqbtf/giphy.gif"
    ]
    special = "https://media.giphy.com/media/v1.Y2lkPWVjZjA1ZTQ3a2Z4djFjNHA2MWh0YWp1d2M2MzBjOXJ6MTRhMjl2eXlicmRmaDg3eCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/9KzYVEbsWoIJJawG0Y/giphy.gif"
    
    if random.random() < 0.10:
        await ctx.send(special)
        return
    
    await ctx.send(random.choice(meme_list))

# =========================================================
# COUNTRY FLAGS GAME
# =========================================================

country_flags = {
    "easy": [
        {"name": "United States", "flag": "🇺🇸"},
        {"name": "Canada", "flag": "🇨🇦"},
        {"name": "United Kingdom", "flag": "🇬🇧"},
        {"name": "Germany", "flag": "🇩🇪"},
        {"name": "France", "flag": "🇫🇷"},
        {"name": "Italy", "flag": "🇮🇹"},
        {"name": "Spain", "flag": "🇪🇸"},
        {"name": "Portugal", "flag": "🇵🇹"},
        {"name": "Netherlands", "flag": "🇳🇱"},
        {"name": "Belgium", "flag": "🇧🇪"},
        {"name": "Switzerland", "flag": "🇨🇭"},
        {"name": "Austria", "flag": "🇦🇹"},
        {"name": "Sweden", "flag": "🇸🇪"},
        {"name": "Norway", "flag": "🇳🇴"},
        {"name": "Denmark", "flag": "🇩🇰"},
        {"name": "Finland", "flag": "🇫🇮"},
        {"name": "Ireland", "flag": "🇮🇪"},
        {"name": "Greece", "flag": "🇬🇷"},
        {"name": "Turkey", "flag": "🇹🇷"},
        {"name": "Russia", "flag": "🇷🇺"},
        {"name": "Poland", "flag": "🇵🇱"},
        {"name": "Ukraine", "flag": "🇺🇦"},
        {"name": "Romania", "flag": "🇷🇴"},
        {"name": "Bulgaria", "flag": "🇧🇬"},
        {"name": "Croatia", "flag": "🇭🇷"},
        {"name": "Czech Republic", "flag": "🇨🇿"},
        {"name": "Hungary", "flag": "🇭🇺"},
        {"name": "Slovakia", "flag": "🇸🇰"},
        {"name": "Slovenia", "flag": "🇸🇮"},
    ],
    "medium": [
        {"name": "Brazil", "flag": "🇧🇷"},
        {"name": "Argentina", "flag": "🇦🇷"},
        {"name": "Mexico", "flag": "🇲🇽"},
        {"name": "Australia", "flag": "🇦🇺"},
        {"name": "New Zealand", "flag": "🇳🇿"},
        {"name": "South Africa", "flag": "🇿🇦"},
        {"name": "Egypt", "flag": "🇪🇬"},
        {"name": "Nigeria", "flag": "🇳🇬"},
        {"name": "Kenya", "flag": "🇰🇪"},
        {"name": "Ghana", "flag": "🇬🇭"},
        {"name": "India", "flag": "🇮🇳"},
        {"name": "China", "flag": "🇨🇳"},
        {"name": "Japan", "flag": "🇯🇵"},
        {"name": "South Korea", "flag": "🇰🇷"},
        {"name": "Indonesia", "flag": "🇮🇩"},
        {"name": "Pakistan", "flag": "🇵🇰"},
        {"name": "Bangladesh", "flag": "🇧🇩"},
        {"name": "Vietnam", "flag": "🇻🇳"},
        {"name": "Thailand", "flag": "🇹🇭"},
        {"name": "Philippines", "flag": "🇵🇭"},
        {"name": "Morocco", "flag": "🇲🇦"},
        {"name": "Algeria", "flag": "🇩🇿"},
        {"name": "Tunisia", "flag": "🇹🇳"},
        {"name": "Libya", "flag": "🇱🇾"},
        {"name": "Ethiopia", "flag": "🇪🇹"},
        {"name": "Tanzania", "flag": "🇹🇿"},
        {"name": "Uganda", "flag": "🇺🇬"},
        {"name": "Zambia", "flag": "🇿🇲"},
        {"name": "Zimbabwe", "flag": "🇿🇼"},
    ],
    "hard": [
        {"name": "Kazakhstan", "flag": "🇰🇿"},
        {"name": "Uzbekistan", "flag": "🇺🇿"},
        {"name": "Azerbaijan", "flag": "🇦🇿"},
        {"name": "Armenia", "flag": "🇦🇲"},
        {"name": "Georgia", "flag": "🇬🇪"},
        {"name": "Mongolia", "flag": "🇲🇳"},
        {"name": "Nepal", "flag": "🇳🇵"},
        {"name": "Sri Lanka", "flag": "🇱🇰"},
        {"name": "Myanmar", "flag": "🇲🇲"},
        {"name": "Cambodia", "flag": "🇰🇭"},
        {"name": "Saudi Arabia", "flag": "🇸🇦"},
        {"name": "United Arab Emirates", "flag": "🇦🇪"},
        {"name": "Qatar", "flag": "🇶🇦"},
        {"name": "Kuwait", "flag": "🇰🇼"},
        {"name": "Oman", "flag": "🇴🇲"},
        {"name": "Bahrain", "flag": "🇧🇭"},
        {"name": "Lebanon", "flag": "🇱🇧"},
        {"name": "Jordan", "flag": "🇯🇴"},
        {"name": "Iraq", "flag": "🇮🇶"},
        {"name": "Syria", "flag": "🇸🇾"},
        {"name": "Yemen", "flag": "🇾🇪"},
        {"name": "Palestine", "flag": "🇵🇸"},
        {"name": "Iran", "flag": "🇮🇷"},
        {"name": "Afghanistan", "flag": "🇦🇫"},
        {"name": "Turkmenistan", "flag": "🇹🇲"},
        {"name": "Kyrgyzstan", "flag": "🇰🇬"},
        {"name": "Tajikistan", "flag": "🇹🇯"},
        {"name": "Maldives", "flag": "🇲🇻"},
        {"name": "Bhutan", "flag": "🇧🇹"},
        {"name": "Laos", "flag": "🇱🇦"},
        {"name": "Brunei", "flag": "🇧🇳"},
        {"name": "East Timor", "flag": "🇹🇱"},
        {"name": "Papua New Guinea", "flag": "🇵🇬"},
    ],
    "impossible": [
        {"name": "Seychelles", "flag": "🇸🇨"},
        {"name": "Comoros", "flag": "🇰🇲"},
        {"name": "São Tomé and Príncipe", "flag": "🇸🇹"},
        {"name": "Eswatini", "flag": "🇸🇿"},
        {"name": "Kiribati", "flag": "🇰🇮"},
        {"name": "Nauru", "flag": "🇳🇷"},
        {"name": "Tuvalu", "flag": "🇹🇻"},
        {"name": "Palau", "flag": "🇵🇼"},
        {"name": "Marshall Islands", "flag": "🇲🇭"},
        {"name": "Dominica", "flag": "🇩🇲"},
        {"name": "Saint Kitts and Nevis", "flag": "🇰🇳"},
        {"name": "Antigua and Barbuda", "flag": "🇦🇬"},
        {"name": "Saint Vincent and the Grenadines", "flag": "🇻🇨"},
        {"name": "Solomon Islands", "flag": "🇸🇧"},
        {"name": "Vanuatu", "flag": "🇻🇺"},
        {"name": "Tonga", "flag": "🇹🇴"},
    ]
}

# =========================================================
# COUNTRY ABBREVIATIONS
# =========================================================

COUNTRY_ABBREVIATIONS = {
    "united states": ["us", "usa", "america"],
    "united kingdom": ["uk", "britain", "england"],
    "united arab emirates": ["uae"],
    "south korea": ["korea"],
    "czech republic": ["czechia"],
    "new zealand": ["nz"],
    "south africa": ["sa"],
    "saudi arabia": ["ksa"],
    "papua new guinea": ["png"],
    "saint kitts and nevis": ["st kitts", "saint kitts"],
    "saint vincent and the grenadines": ["st vincent"],
    "sao tome and principe": ["sao tome"],
    "antigua and barbuda": ["antigua"],
    "marshall islands": ["marshall"],
    "solomon islands": ["solomon"],
}

active_games = {}
used_countries = {}

class CountryGuessView(discord.ui.View):
    def __init__(self, country_data, difficulty, player_id, total_rounds, current_round, correct_count, round_history, timeout=30):
        super().__init__(timeout=timeout)
        self.country_data = country_data
        self.difficulty = difficulty
        self.player_id = player_id
        self.total_rounds = total_rounds
        self.current_round = current_round
        self.correct_count = correct_count
        self.round_history = round_history
        self.answered = False
        self.start_time = time.time()
        self.timeout_seconds = timeout
        self.game_cancelled = False
        
        guess_btn = discord.ui.Button(
            label="✏️ Guess",
            style=discord.ButtonStyle.primary,
            row=0
        )
        guess_btn.callback = self.guess_callback
        self.add_item(guess_btn)
        
        stop_btn = discord.ui.Button(
            label="🛑 Stop Game",
            style=discord.ButtonStyle.danger,
            row=0
        )
        stop_btn.callback = self.stop_callback
        self.add_item(stop_btn)
    
    async def guess_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return
        if self.answered:
            await interaction.response.send_message("⏳ This round is already over!", ephemeral=True)
            return
        if self.game_cancelled:
            await interaction.response.send_message("🛑 This game has been stopped!", ephemeral=True)
            return
        
        modal = GuessCountryModal(self)
        await interaction.response.send_modal(modal)
    
    async def stop_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return
        
        self.game_cancelled = True
        self.answered = True
        for child in self.children:
            child.disabled = True
        
        embed = discord.Embed(
            title="🛑 Game Stopped",
            description=f"{interaction.user.mention} stopped the country guessing game.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="📊 Your Score",
            value=f"**{self.correct_count}/{self.total_rounds}** correct",
            inline=False
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        
        if self.player_id in used_countries:
            del used_countries[self.player_id]
    
    async def on_timeout(self):
        if not self.answered and not self.game_cancelled:
            self.round_history.append(False)
            await self.message.edit(
                content=f"⏰ Time's up! The flag was **{self.country_data['name']}** {self.country_data['flag']}",
                view=None
            )
            await asyncio.sleep(2)
            await start_new_round(
                self.message.channel,
                self.difficulty,
                self.player_id,
                self.total_rounds,
                self.current_round + 1,
                self.correct_count,
                self.round_history
            )

class GuessCountryModal(discord.ui.Modal, title="🌍 Guess the Country"):
    guess = discord.ui.TextInput(
        label="Enter the country name or abbreviation:",
        placeholder="e.g. USA, UK, France...",
        required=True,
        max_length=100
    )
    
    def __init__(self, view):
        super().__init__()
        self.view = view
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.view.answered:
            await interaction.response.send_message("⏳ This round is already over!", ephemeral=True)
            return
        if self.view.game_cancelled:
            await interaction.response.send_message("🛑 This game has been stopped!", ephemeral=True)
            return
        
        self.view.answered = True
        user_guess = self.guess.value.strip()
        correct = self.view.country_data["name"]
        
        # Check if guess matches a country or its abbreviation
        def is_country_match(guess, country_name):
            guess_lower = guess.lower().strip()
            country_lower = country_name.lower().strip()
            
            if guess_lower == country_lower:
                return True
            
            for country_key, abbrevs in COUNTRY_ABBREVIATIONS.items():
                if country_lower == country_key.lower():
                    for abbrev in abbrevs:
                        if guess_lower == abbrev.lower():
                            return True
            return False
        
        match_found = is_country_match(user_guess, correct)
        
        if match_found:
            self.view.correct_count += 1
            self.view.round_history.append(True)
            
            embed = discord.Embed(
                title="✅ Correct!",
                description=f"**{user_guess}** is correct! 🎉",
                color=discord.Color.green()
            )
            embed.add_field(
                name="📊 Score",
                value=f"**{self.view.correct_count}/{self.view.total_rounds}** correct",
                inline=False
            )
            await interaction.response.send_message(embed=embed)
            
            await asyncio.sleep(2)
            await start_new_round(
                interaction.channel,
                self.view.difficulty,
                self.view.player_id,
                self.view.total_rounds,
                self.view.current_round + 1,
                self.view.correct_count,
                self.view.round_history
            )
        else:
            self.view.round_history.append(False)
            
            embed = discord.Embed(
                title="❌ Wrong!",
                description=f"**{user_guess}** is not correct.",
                color=discord.Color.red()
            )
            embed.add_field(
                name="💡 Hint",
                value=f"The country starts with **{correct[0]}**",
                inline=False
            )
            embed.add_field(
                name="📊 Score",
                value=f"**{self.view.correct_count}/{self.view.total_rounds}** correct",
                inline=False
            )
            await interaction.response.send_message(embed=embed)
            
            await asyncio.sleep(2)
            await start_new_round(
                interaction.channel,
                self.view.difficulty,
                self.view.player_id,
                self.view.total_rounds,
                self.view.current_round + 1,
                self.view.correct_count,
                self.view.round_history
            )

async def start_new_round(channel, difficulty, player_id, total_rounds, current_round, correct_count, round_history):
    if current_round > total_rounds:
        if player_id in used_countries:
            del used_countries[player_id]
        
        total_correct = sum(round_history)
        if total_correct == total_rounds:
            content = f"🎉 {channel.guild.get_member(player_id).mention} **You got all {total_rounds} countries right!** 🏆"
        else:
            content = f"📊 {channel.guild.get_member(player_id).mention} Game over! You got **{total_correct}/{total_rounds}** correct."
        
        embed = discord.Embed(
            title="🏁 Game Over!",
            description=content,
            color=discord.Color.green() if total_correct == total_rounds else discord.Color.orange()
        )
        embed.add_field(
            name="📊 Final Score",
            value=f"**{total_correct}/{total_rounds}** correct",
            inline=True
        )
        embed.add_field(
            name="📈 Accuracy",
            value=f"**{round(total_correct / total_rounds * 100)}%**",
            inline=True
        )
        
        view = discord.ui.View()
        restart_btn = discord.ui.Button(label="🔄 Start Again", style=discord.ButtonStyle.success)
        
        async def restart_callback(interaction: discord.Interaction):
            if interaction.user.id != player_id:
                await interaction.response.send_message("❌ Not your game!", ephemeral=True)
                return
            await interaction.response.edit_message(content="🔄 Starting new game...", view=None)
            await asyncio.sleep(1)
            await start_country_setup(interaction.channel, player_id)
        
        restart_btn.callback = restart_callback
        view.add_item(restart_btn)
        
        await channel.send(embed=embed, view=view)
        return
    
    if player_id not in used_countries:
        used_countries[player_id] = []
    
    available = [c for c in country_flags[difficulty] if c["name"] not in used_countries[player_id]]
    
    if not available:
        used_countries[player_id] = []
        available = country_flags[difficulty]
    
    country = random.choice(available)
    used_countries[player_id].append(country["name"])
    
    view = CountryGuessView(country, difficulty, player_id, total_rounds, current_round, correct_count, round_history)
    
    flag_display = country["flag"]
    embed = discord.Embed(
        title="🌍 Guess the Country!",
        description=f"**Round {current_round}/{total_rounds}**\nDifficulty: **{difficulty.upper()}**\n\nGuess the country based on the flag!",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Flag", value=flag_display, inline=False)
    embed.set_footer(text=f"⏱️ {view.timeout_seconds}s remaining • {channel.guild.get_member(player_id).display_name}'s turn")
    
    msg = await channel.send(
        f"{channel.guild.get_member(player_id).mention}",
        embed=embed,
        view=view
    )
    
    view.message = msg
    
    for remaining in range(view.timeout_seconds - 1, 0, -1):
        await asyncio.sleep(1)
        if view.answered or view.game_cancelled:
            break
        try:
            embed = msg.embeds[0]
            embed.set_footer(text=f"⏱️ {remaining}s remaining • {channel.guild.get_member(player_id).display_name}'s turn")
            await msg.edit(embed=embed, view=view)
        except:
            break

async def start_country_setup(channel, player_id):
    if player_id in used_countries:
        del used_countries[player_id]
    
    await channel.send(f"{channel.guild.get_member(player_id).mention} 🌍 **Country Flag Guessing Game** - Select your difficulty:")
    
    view = discord.ui.View(timeout=60)
    
    async def difficulty_callback(interaction: discord.Interaction, diff: str):
        if interaction.user.id != player_id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return
        
        round_view = discord.ui.View(timeout=30)
        round_options = [3, 5, 10, 15, 20]
        
        async def round_callback(interaction2: discord.Interaction, rounds: int):
            if interaction2.user.id != player_id:
                await interaction2.response.send_message("❌ Not your game!", ephemeral=True)
                return
            if rounds > len(country_flags[diff]):
                await interaction2.response.send_message(
                    f"❌ Not enough countries in **{diff.upper()}** mode for {rounds} rounds. Max: {len(country_flags[diff])}",
                    ephemeral=True
                )
                return
            await interaction2.response.edit_message(
                content=f"{interaction2.user.mention} 🎯 Starting **{diff.upper()}** mode with **{rounds}** rounds! Get ready...",
                view=None
            )
            await asyncio.sleep(1)
            await start_new_round(channel, diff, player_id, rounds, 1, 0, [])
        
        for r in round_options:
            if r <= len(country_flags[diff]):
                btn = discord.ui.Button(label=f"{r} rounds", style=discord.ButtonStyle.secondary)
                btn.callback = lambda i, rounds=r: round_callback(i, rounds)
                round_view.add_item(btn)
        
        await interaction.response.edit_message(
            content=f"{interaction.user.mention} 🎯 **{diff.upper()}** mode selected! How many rounds? (Max: {len(country_flags[diff])})",
            view=round_view
        )
    
    for diff in ["easy", "medium", "hard", "impossible"]:
        btn = discord.ui.Button(
            label=f"🌍 {diff.capitalize()} ({len(country_flags[diff])} flags)",
            style=discord.ButtonStyle.secondary,
            custom_id=diff
        )
        btn.callback = lambda i, d=diff: difficulty_callback(i, d)
        view.add_item(btn)
    
    await channel.send("Select difficulty below:", view=view)

@bot.hybrid_command(name="triva", description="Start a Flag Triva")
async def country(ctx):
    await start_country_setup(ctx.channel, ctx.author.id)

# =========================================================
# FOOTBALL CARDS SYSTEM
# =========================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS football_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    card_id TEXT,
    player_name TEXT,
    club TEXT,
    nationality TEXT,
    position TEXT,
    rating INTEGER,
    rarity TEXT,
    purchased_at REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS football_packs (
    pack_type TEXT PRIMARY KEY,
    price INTEGER,
    card_count INTEGER,
    rarity_rates TEXT
)
""")

cursor.execute("INSERT OR IGNORE INTO football_packs VALUES ('bronze', 500, 3, '{\"common\":0.7,\"rare\":0.25,\"epic\":0.04,\"legendary\":0.01}')")
cursor.execute("INSERT OR IGNORE INTO football_packs VALUES ('silver', 1500, 5, '{\"common\":0.4,\"rare\":0.35,\"epic\":0.2,\"legendary\":0.05}')")
cursor.execute("INSERT OR IGNORE INTO football_packs VALUES ('gold', 5000, 7, '{\"common\":0.15,\"rare\":0.35,\"epic\":0.35,\"legendary\":0.15}')")
cursor.execute("INSERT OR IGNORE INTO football_packs VALUES ('legendary', 20000, 10, '{\"common\":0.05,\"rare\":0.15,\"epic\":0.35,\"legendary\":0.45}')")
db.commit()

FOOTBALL_CHANNEL = {}
FOOTBALL_SPAWN_COOLDOWN = {}

FOOTBALL_PLAYERS = [
    {"name": "Lionel Messi", "club": "Inter Miami", "nationality": "Argentina", "position": "Forward", "rating": 95, "rarity": "legendary"},
    {"name": "Cristiano Ronaldo", "club": "Al Nassr", "nationality": "Portugal", "position": "Forward", "rating": 94, "rarity": "legendary"},
    {"name": "Kylian Mbappé", "club": "Real Madrid", "nationality": "France", "position": "Forward", "rating": 93, "rarity": "legendary"},
    {"name": "Erling Haaland", "club": "Manchester City", "nationality": "Norway", "position": "Forward", "rating": 93, "rarity": "legendary"},
    {"name": "Vinícius Júnior", "club": "Real Madrid", "nationality": "Brazil", "position": "Forward", "rating": 91, "rarity": "epic"},
    {"name": "Jude Bellingham", "club": "Real Madrid", "nationality": "England", "position": "Midfielder", "rating": 90, "rarity": "epic"},
    {"name": "Harry Kane", "club": "Bayern Munich", "nationality": "England", "position": "Forward", "rating": 90, "rarity": "epic"},
    {"name": "Mohamed Salah", "club": "Liverpool", "nationality": "Egypt", "position": "Forward", "rating": 89, "rarity": "epic"},
    {"name": "Kevin De Bruyne", "club": "Manchester City", "nationality": "Belgium", "position": "Midfielder", "rating": 89, "rarity": "epic"},
    {"name": "Bukayo Saka", "club": "Arsenal", "nationality": "England", "position": "Forward", "rating": 88, "rarity": "rare"},
    {"name": "Phil Foden", "club": "Manchester City", "nationality": "England", "position": "Midfielder", "rating": 88, "rarity": "rare"},
    {"name": "Declan Rice", "club": "Arsenal", "nationality": "England", "position": "Midfielder", "rating": 87, "rarity": "rare"},
    {"name": "Victor Osimhen", "club": "Galatasaray", "nationality": "Nigeria", "position": "Forward", "rating": 87, "rarity": "rare"},
    {"name": "Rafael Leão", "club": "AC Milan", "nationality": "Portugal", "position": "Forward", "rating": 86, "rarity": "rare"},
    {"name": "Lautaro Martínez", "club": "Inter Milan", "nationality": "Argentina", "position": "Forward", "rating": 86, "rarity": "rare"},
    {"name": "Alessandro Bastoni", "club": "Inter Milan", "nationality": "Italy", "position": "Defender", "rating": 85, "rarity": "common"},
    {"name": "Jurriën Timber", "club": "Arsenal", "nationality": "Netherlands", "position": "Defender", "rating": 84, "rarity": "common"},
    {"name": "Pedri", "club": "Barcelona", "nationality": "Spain", "position": "Midfielder", "rating": 84, "rarity": "common"},
    {"name": "Gavi", "club": "Barcelona", "nationality": "Spain", "position": "Midfielder", "rating": 83, "rarity": "common"},
    {"name": "Nuno Mendes", "club": "PSG", "nationality": "Portugal", "position": "Defender", "rating": 83, "rarity": "common"},
    {"name": "Rasmus Højlund", "club": "Manchester United", "nationality": "Denmark", "position": "Forward", "rating": 82, "rarity": "common"},
    {"name": "Alejandro Garnacho", "club": "Manchester United", "nationality": "Argentina", "position": "Forward", "rating": 82, "rarity": "common"},
    {"name": "Kobbie Mainoo", "club": "Manchester United", "nationality": "England", "position": "Midfielder", "rating": 81, "rarity": "common"},
    {"name": "Jérémy Doku", "club": "Manchester City", "nationality": "Belgium", "position": "Forward", "rating": 81, "rarity": "common"},
    {"name": "Sávio", "club": "Manchester City", "nationality": "Brazil", "position": "Forward", "rating": 80, "rarity": "common"},
]

RARITY_ORDER = {"common": 0, "rare": 1, "epic": 2, "legendary": 3}
RARITY_COLORS = {"common": 0x808080, "rare": 0x1E90FF, "epic": 0x9B59B6, "legendary": 0xF1C40F}
RARITY_SELL_PRICES = {"common": 50, "rare": 200, "epic": 800, "legendary": 5000}

def get_player_cards(user_id):
    cursor.execute("SELECT * FROM football_cards WHERE user_id = ?", (user_id,))
    return cursor.fetchall()

def get_card_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM football_cards WHERE user_id = ?", (user_id,))
    return cursor.fetchone()[0]

def get_rarity_count(user_id, rarity):
    cursor.execute("SELECT COUNT(*) FROM football_cards WHERE user_id = ? AND rarity = ?", (user_id, rarity))
    return cursor.fetchone()[0]

def create_card_for_user(user_id, player_data):
    import random, time
    card_id = f"FC{random.randint(10000, 99999)}"
    cursor.execute(
        "INSERT INTO football_cards (user_id, card_id, player_name, club, nationality, position, rating, rarity, purchased_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, card_id, player_data["name"], player_data["club"], player_data["nationality"], player_data["position"], player_data["rating"], player_data["rarity"], time.time())
    )
    db.commit()
    return card_id

def open_pack(user_id, pack_type):
    cursor.execute("SELECT price, card_count, rarity_rates FROM football_packs WHERE pack_type = ?", (pack_type,))
    row = cursor.fetchone()
    if not row:
        return None
    price, card_count, rates_json = row
    rates = json.loads(rates_json)
    
    wallet, _, _ = get_user_econ(user_id)
    if wallet < price:
        return "insufficient"
    
    update_wallet(user_id, -price)
    
    cards = []
    for _ in range(card_count):
        roll = random.random()
        cumulative = 0
        chosen_rarity = "common"
        for rarity, prob in rates.items():
            cumulative += prob
            if roll <= cumulative:
                chosen_rarity = rarity
                break
        
        pool = [p for p in FOOTBALL_PLAYERS if p["rarity"] == chosen_rarity]
        if not pool:
            pool = [p for p in FOOTBALL_PLAYERS if p["rarity"] == "common"]
        player = random.choice(pool)
        card_id = create_card_for_user(user_id, player)
        cards.append({"card_id": card_id, "player": player})
    
    return cards

def get_top_cards(user_id, limit=5):
    cursor.execute(
        "SELECT * FROM football_cards WHERE user_id = ? ORDER BY rating DESC, card_id LIMIT ?",
        (user_id, limit)
    )
    return cursor.fetchall()

def get_card_by_id(card_id, user_id):
    cursor.execute("SELECT * FROM football_cards WHERE id = ? AND user_id = ?", (card_id, user_id))
    return cursor.fetchone()

def delete_card(card_id, user_id):
    cursor.execute("DELETE FROM football_cards WHERE id = ? AND user_id = ?", (card_id, user_id))
    db.commit()
    return cursor.rowcount > 0

@bot.hybrid_command(name="setchannel", description="Set the channel for football card spawns")
@commands.has_permissions(administrator=True)
async def setchannel(ctx, channel: discord.TextChannel = None):
    target = channel or ctx.channel
    FOOTBALL_CHANNEL[ctx.guild.id] = target.id
    embed = discord.Embed(
        description=f"✅ Football spawn channel set to {target.mention}",
        color=discord.Color.green()
    )
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="spawn", description="Spawn a random football player card in the channel")
@commands.has_permissions(administrator=True)
async def spawn(ctx):
    guild_id = ctx.guild.id
    channel_id = FOOTBALL_CHANNEL.get(guild_id)
    if not channel_id:
        embed = discord.Embed(
            description="❌ No spawn channel set. Use `/setchannel` first.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    last_spawn = FOOTBALL_SPAWN_COOLDOWN.get(guild_id, 0)
    if time.time() - last_spawn < 300:
        remaining = int(300 - (time.time() - last_spawn))
        embed = discord.Embed(
            description=f"⏳ Please wait {remaining} seconds before spawning again.",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    FOOTBALL_SPAWN_COOLDOWN[guild_id] = time.time()
    
    player = random.choice(FOOTBALL_PLAYERS)
    color = RARITY_COLORS.get(player["rarity"], 0x808080)
    
    embed = discord.Embed(
        title=f"⚽ {player['name']} has spawned!",
        description=f"**Club:** {player['club']}\n**Nationality:** {player['nationality']}\n**Position:** {player['position']}\n**Rating:** {player['rating']}\n**Rarity:** {player['rarity'].upper()}",
        color=color
    )
    embed.set_footer(text="Use /collect to claim this card!")
    
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(embed=embed)
        
        global current_spawn
        current_spawn = {"guild_id": guild_id, "player": player, "claimed_by": None, "claimed_at": None}
        
        if ctx.interaction:
            await ctx.interaction.response.send_message("✅ Player spawned successfully!", ephemeral=True)
        else:
            await ctx.send("✅ Player spawned successfully!", delete_after=5)
    else:
        embed = discord.Embed(
            description="❌ Spawn channel not found. Set a new channel with `/setchannel`.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

current_spawn = {"guild_id": None, "player": None, "claimed_by": None, "claimed_at": None}

@bot.hybrid_command(name="collect", description="Collect the currently spawned football card")
async def collect(ctx):
    if current_spawn["guild_id"] != ctx.guild.id:
        embed = discord.Embed(
            description="❌ No player is currently spawned in this server!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if current_spawn["claimed_by"] is not None:
        embed = discord.Embed(
            description=f"❌ This card was already claimed by <@{current_spawn['claimed_by']}>!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    player = current_spawn["player"]
    card_id = create_card_for_user(ctx.author.id, player)
    current_spawn["claimed_by"] = ctx.author.id
    current_spawn["claimed_at"] = time.time()
    
    embed = discord.Embed(
        title="✅ Card Collected!",
        description=f"{ctx.author.mention} collected **{player['name']}** ({player['rarity'].upper()})!\nCard ID: `{card_id}`",
        color=RARITY_COLORS.get(player["rarity"], 0x808080)
    )
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# FOOTBALL AUTO-SPAWN SYSTEM
# =========================================================

football_spawn_task = None
current_football_spawn = None

async def auto_spawn_football():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if FOOTBALL_CHANNEL:
                guild_id = random.choice(list(FOOTBALL_CHANNEL.keys()))
                channel_id = FOOTBALL_CHANNEL[guild_id]
                channel = bot.get_channel(channel_id)
                
                if channel:
                    player = random.choice(FOOTBALL_PLAYERS)
                    color = RARITY_COLORS.get(player["rarity"], 0x808080)
                    
                    embed = discord.Embed(
                        title=f"⚽ {player['name']} has spawned!",
                        description=f"**Club:** {player['club']}\n**Nationality:** {player['nationality']}\n**Position:** {player['position']}\n**Rating:** {player['rating']}\n**Rarity:** {player['rarity'].upper()}",
                        color=color
                    )
                    
                    # --- ADD GIF SUPPORT FOR FOOTBALL ---
                    # Try to use player-specific GIF or fallback to default flag
                    player_gifs = {
                        "Lionel Messi": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbWVzc2lnZWZmbGFnJmVjPWdpcGh5JmNpZD1jb20lMkZnaXBoeSUyRm1lc3NpLWdpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Cristiano Ronaldo": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExY3Jpc3RpYW5vcm9uYWxkb2dpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Kylian Mbappé": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbWJhcHBlZ29pZmluZ2lmJmVjPWdpcGh5JmNpZD1jb20lMkZnaXBoeSUyRm1iYXBwZS1naWYmcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Erling Haaland": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExaGFhbGFuZ2dpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Vinícius Júnior": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExdmluaWNpdXNnaWYmbWNpZD1jb20lMkZnaXBoeSUyRnZpbmljaXVzLWdpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Jude Bellingham": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExanVkZWJlbGxpbmdhbWdpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Harry Kane": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExaGFycnlrYW5lZ2lmJmVjPWdpcGh5JmNpZD1jb20lMkZnaXBoeSUyRmhhcnJ5LWthbmUtZ2lmJmVwPXYxX2dpZnNfc2VhcmNoJmNkPWc/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Mohamed Salah": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbW9oYW1lZHNhbGFoZ2lmJmVjPWdpcGh5JmNpZD1jb20lMkZnaXBoeSUyRm1vaGFtZWQtc2FsYWgtZ2lmJmVwPXYxX2dpZnNfc2VhcmNoJmNkPWc/3o7TKM7tKzKxLzKxLz/giphy.gif",
                        "Kevin De Bruyne": "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExa2V2aW5kZWJydXluZWdpZiZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3o7TKM7tKzKxLzKxLz/giphy.gif",
                    }
                    
                    gif_url = player_gifs.get(player['name'], "https://media.discordapp.net/attachments/1539633658707845160/1541642892987211776/usa-usa-flag.png")
                    embed.set_image(url=gif_url)
                    embed.set_footer(text="Use /collect to claim this card!")
                    
                    global current_football_spawn
                    current_football_spawn = {"guild_id": guild_id, "player": player, "claimed_by": None}
                    
                    await channel.send(embed=embed)
                    
                    # Reset after 30 seconds if not claimed
                    await asyncio.sleep(30)
                    if current_football_spawn and current_football_spawn.get("claimed_by") is None:
                        if current_football_spawn.get("guild_id") == guild_id:
                            embed = discord.Embed(
                                description=f"⏰ {player['name']} has disappeared!",
                                color=discord.Color.orange()
                            )
                            await channel.send(embed=embed)
                            current_football_spawn = None
                
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Auto-spawn error: {e}")
            await asyncio.sleep(5)
# =========================================================
# PAT COMMAND
# =========================================================

PAT_GIFS = [
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcDBuZWZkajg3cmw2cHA4dTZjcWo1aGFvMTFrem5mMm42cncwZnc5aSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/ye7OTQgwmVuVy/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcDBuZWZkajg3cmw2cHA4dTZjcWo1aGFvMTFrem5mMm42cncwZnc5aSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/AomVL3N8lTxiuYtI2I/giphy.gif"
]

@bot.hybrid_command(name="pat", description="Pat someone with a cute GIF")
async def pat(ctx, member: discord.Member = None):
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to pat!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description="🫂 You pat yourself... that's kinda sad but okay!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(PAT_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"🫳 {ctx.author.mention} pats {member.mention}! How wholesome!",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_image(url=random.choice(PAT_GIFS))
    embed.set_footer(text="Pat pat!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# TAPE COMMAND
# =========================================================

TAPE_GIFS = [
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZzduYnZ6ZDhwamNndTEwZXdoMm00MjQ4aTR4Nzd0Yjl5eDgwOWw4OCZlcD12MV9naWZzX3NlYXJjaCZjdD1n/3orieTvZ8aH6fQAg6c/giphy.gif"
]

@bot.hybrid_command(name="tape", description="Tape someone shut!")
async def tape(ctx, member: discord.Member = None):
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to tape!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"🤐 {ctx.author.mention} tapes themselves... that's weird but okay!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(TAPE_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"📼 {ctx.author.mention} taped {member.mention} 💤 shh",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    embed.set_image(url=random.choice(TAPE_GIFS))
    embed.set_footer(text="Tape! Tape! Tape!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# PFPS COMMAND
# =========================================================

PFPS = [
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540485204093829200/7fe79c89936adbfbfdec5ae1dfff9a4b.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540484967937740830/8f27a0cc3a0f2781ad74efd4008558a9.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540485025127206952/18a826db626f61d6cda10c9d408ac1d2.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540485132392206397/f546b535e5d9e9136b91256284887a58.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540485265062363136/7b7f8abe8f534427053f9006f22c4e1e.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540485361979887746/b4182bff5b7505a6d35bc95413ea181e.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540496506199478273/a97d1dd51b9b2b71c35183ff3ca7464e.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540496612340797482/24cd5b1ba6d806349693f8da2ec9abe4.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540496650907160607/03380f5dd4d8a4030b243407a1434b82.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540496797963915366/515a558bee8ad52e389fff071f5eb243.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540497026876317776/62bbfac65dc9de498308f35c16c0a7c7.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540497133994778644/27943627bc3ecff837111b03ed600dba.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540497257386737735/b2d0d08376d624288d4dfee9dbdf28a1.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540497313552670830/6df443c27082110376ec88c3644a64f9.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540497340979355679/8615036d762bc981d7d6daa613a6a185.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540499415754739722/85c604a54b33c55f67c71254c4b474b3.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540499488060215317/f29d9a6419fd2ad5b0e9aeaf1fdd2761.png",
    "https://cdn.discordapp.com/attachments/1489131525743182008/1540499558981697576/062c9bb7268ba112dc0d30058600d6dc.png"
]

@bot.hybrid_command(name="pfps", description="Get a random profile picture")
async def pfps(ctx):
    if not PFPS:
        embed = discord.Embed(
            description="❌ No PFPs have been added yet! Ask the bot owner to add some.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    pfp_url = random.choice(PFPS)
    
    embed = discord.Embed(
    title="👀 Random PFP",
    description=f"Here's a random profile picture for you!",
    color=discord.Color.from_rgb(220, 20, 60)
)
    embed.set_image(url=pfp_url)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# FRAKTUR COMMAND
# =========================================================

FRAKTUR_MAP = {
    'a': '𝔞', 'b': '𝔟', 'c': '𝔠', 'd': '𝔡', 'e': '𝔢', 'f': '𝔣', 'g': '𝔤',
    'h': '𝔥', 'i': '𝔦', 'j': '𝔧', 'k': '𝔨', 'l': '𝔩', 'm': '𝔪', 'n': '𝔫',
    'o': '𝔬', 'p': '𝔭', 'q': '𝔮', 'r': '𝔯', 's': '𝔰', 't': '𝔱', 'u': '𝔲',
    'v': '𝔳', 'w': '𝔴', 'x': '𝔵', 'y': '𝔶', 'z': '𝔷',
    'A': '𝔄', 'B': '𝔅', 'C': 'ℭ', 'D': '𝔇', 'E': '𝔈', 'F': '𝔉', 'G': '𝔊',
    'H': 'ℌ', 'I': 'ℑ', 'J': '𝔍', 'K': '𝔎', 'L': '𝔏', 'M': '𝔐', 'N': '𝔑',
    'O': '𝔒', 'P': '𝔓', 'Q': '𝔔', 'R': 'ℜ', 'S': '𝔖', 'T': '𝔗', 'U': '𝔘',
    'V': '𝔙', 'W': '𝔚', 'X': '𝔛', 'Y': '𝔜', 'Z': 'ℨ',
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
    '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
}

def convert_to_fraktur(text):
    result = []
    for char in text:
        if char in FRAKTUR_MAP:
            result.append(FRAKTUR_MAP[char])
        else:
            result.append(char)
    return ''.join(result)

@bot.hybrid_command(name="fraktur", description="Convert a text to Fraktur style (like ℌ𝔢𝔩𝔩𝔬)")
async def fraktur(ctx, *, text: str):
    if not text:
        embed = discord.Embed(
            description="⚙️ Please provide a text to convert!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    converted = convert_to_fraktur(text)
    
    if not ctx.interaction and ctx.message:
        try:
            await ctx.message.delete()
        except Exception:
            pass
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(converted)
    else:
        await ctx.send(converted)

# =========================================================
# ADMIN PAY COMMANDS
# =========================================================

ADMIN_PAY_USERS = OWNER_IDS

@bot.hybrid_command(name="adminpay", aliases=["ownerspay"], description="Admin command to give money to any user")
@app_commands.check(owner_only_predicate)
async def adminpay(ctx, member: discord.Member, amount: int):
    if ctx.author.id not in ADMIN_PAY_USERS:
        embed = discord.Embed(
            description="❌ You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if amount <= 0:
        embed = discord.Embed(
            description="❌ Amount must be greater than zero.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    update_wallet(member.id, amount)
    
    new_wallet, new_bank, new_luck = get_user_econ(member.id)
    
    embed = discord.Embed(
        title="💰 Admin Payment",
        description=f"**${amount:,}** has been added to {member.mention}'s wallet!",
        color=discord.Color.green()
    )
    embed.add_field(
        name="New Balance",
        value=f"🪙 Wallet: ${new_wallet:,}\n🏦 Bank: ${new_bank:,}",
        inline=False
    )
    embed.set_footer(text=f"Transaction by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

@bot.hybrid_command(name="adminset", aliases=["ownersset"], description="Admin command to set a user's exact wallet balance (including 0)")
@app_commands.check(owner_only_predicate)
async def adminset(ctx, member: discord.Member, amount: int):
    if ctx.author.id not in ADMIN_PAY_USERS:
        embed = discord.Embed(
            description="❌ You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if amount < 0:
        embed = discord.Embed(
            description="❌ Amount cannot be negative.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    wallet, bank, luck = get_user_econ(member.id)
    
    cursor.execute("UPDATE users SET wallet = ? WHERE user_id = ?", (amount, member.id))
    db.commit()
    
    new_wallet, new_bank, new_luck = get_user_econ(member.id)
    
    embed = discord.Embed(
        title="💰 Admin Wallet Set",
        description=f"{member.mention}'s wallet has been set to **${amount:,}**!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Updated Balance",
        value=f"🪙 Wallet: ${new_wallet:,}\n🏦 Bank: ${new_bank:,}",
        inline=False
    )
    embed.set_footer(text=f"Transaction by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

@bot.hybrid_command(name="adminsetbank", aliases=["ownerssetbank"], description="Admin command to set a user's exact bank balance (including 0)")
@app_commands.check(owner_only_predicate)
async def adminsetbank(ctx, member: discord.Member, amount: int):
    if ctx.author.id not in ADMIN_PAY_USERS:
        embed = discord.Embed(
            description="❌ You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if amount < 0:
        embed = discord.Embed(
            description="❌ Amount cannot be negative.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    cursor.execute("UPDATE users SET bank = ? WHERE user_id = ?", (amount, member.id))
    db.commit()
    
    new_wallet, new_bank, new_luck = get_user_econ(member.id)
    
    embed = discord.Embed(
        title="🏦 Admin Bank Set",
        description=f"{member.mention}'s bank has been set to **${amount:,}**!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="Updated Balance",
        value=f"🪙 Wallet: ${new_wallet:,}\n🏦 Bank: ${new_bank:,}",
        inline=False
    )
    embed.set_footer(text=f"Transaction by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

@bot.hybrid_command(name="adminrob", aliases=["ownersrob"], description="Admin command to rob any user (never fails)")
@app_commands.check(owner_only_predicate)
async def adminrob(ctx, member: discord.Member):
    if ctx.author.id not in ADMIN_PAY_USERS:
        embed = discord.Embed(
            description="❌ You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description="❌ You can't rob yourself!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    wallet, bank, luck = get_user_econ(member.id)
    total_money = wallet + bank
    
    if total_money <= 0:
        embed = discord.Embed(
            description=f"❌ {member.mention} has no money to rob! They're broke!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    stolen_wallet = wallet
    stolen_bank = bank
    
    cursor.execute("UPDATE users SET wallet = 0, bank = 0 WHERE user_id = ?", (member.id,))
    db.commit()
    
    total_stolen = stolen_wallet + stolen_bank
    update_wallet(ctx.author.id, total_stolen)
    
    admin_wallet, admin_bank, admin_luck = get_user_econ(ctx.author.id)
    
    embed = discord.Embed(
        title="🔫 Admin Robbery",
        description=f"**{ctx.author.mention}** successfully robbed **{member.mention}** and stole **${total_stolen:,}**!",
        color=discord.Color.red()
    )
    embed.add_field(
        name="💰 Stolen Breakdown",
        value=f"🪙 From Wallet: ${stolen_wallet:,}\n🏦 From Bank: ${stolen_bank:,}",
        inline=False
    )
    embed.add_field(
        name=f"📊 {member.display_name}'s New Balance",
        value=f"🪙 Wallet: $0\n🏦 Bank: $0",
        inline=True
    )
    embed.add_field(
        name=f"📊 {ctx.author.display_name}'s New Balance",
        value=f"🪙 Wallet: ${admin_wallet:,}\n🏦 Bank: ${admin_bank:,}",
        inline=True
    )
    embed.set_footer(text=f"Admin robbery by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

@bot.hybrid_command(name="adminrobamount", aliases=["ownersrobamount"], description="Admin command to rob a specific amount")
@app_commands.check(owner_only_predicate)
async def adminrobamount(ctx, member: discord.Member, amount: int):
    if ctx.author.id not in ADMIN_PAY_USERS:
        embed = discord.Embed(
            description="❌ You do not have permission to use this command!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description="❌ You can't rob yourself!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if amount <= 0:
        embed = discord.Embed(
            description="❌ Amount must be greater than zero.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    wallet, bank, luck = get_user_econ(member.id)
    
    if wallet < amount:
        embed = discord.Embed(
            description=f"❌ {member.mention} only has **${wallet:,}** in their wallet, not enough to steal **${amount:,}**.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    cursor.execute("UPDATE users SET wallet = wallet - ? WHERE user_id = ?", (amount, member.id))
    db.commit()
    
    update_wallet(ctx.author.id, amount)
    
    new_victim_wallet, new_victim_bank, new_victim_luck = get_user_econ(member.id)
    admin_wallet, admin_bank, admin_luck = get_user_econ(ctx.author.id)
    
    embed = discord.Embed(
        title="🔫 Admin Robbery (Specific Amount)",
        description=f"**{ctx.author.mention}** successfully robbed **${amount:,}** from {member.mention}'s wallet!",
        color=discord.Color.red()
    )
    embed.add_field(
        name=f"📊 {member.display_name}'s New Balance",
        value=f"🪙 Wallet: ${new_victim_wallet:,}\n🏦 Bank: ${new_victim_bank:,}",
        inline=True
    )
    embed.add_field(
        name=f"📊 {ctx.author.display_name}'s New Balance",
        value=f"🪙 Wallet: ${admin_wallet:,}\n🏦 Bank: ${admin_bank:,}",
        inline=True
    )
    embed.set_footer(text=f"Admin robbery by {ctx.author.display_name}")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)

# =========================================================
# EMOJI STEALER COMMAND
# =========================================================

@bot.hybrid_command(name="stealurl", aliases=["surl", "steal"], description="Steal an emoji using its Discord link, ID, or the emoji itself")
@commands.has_permissions(administrator=True)
async def stealurl(ctx, *, input_text: str):
    if not ctx.author.guild_permissions.administrator:
        embed = discord.Embed(description="❌ You need Administrator permissions!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if not ctx.guild.me.guild_permissions.manage_emojis:
        embed = discord.Embed(description="❌ I need **Manage Emojis** permission!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if ctx.interaction:
        await ctx.interaction.response.defer()
    
    input_text = input_text.strip()
    image_url = None
    emoji_name = None
    
    if "cdn.discordapp.com/emojis/" in input_text or "media.discordapp.net/emojis/" in input_text:
        image_url = input_text.split('?')[0]
        filename = image_url.split('/')[-1]
        emoji_name = filename.split('.')[0]
        if emoji_name.isdigit():
            emoji_name = "emoji"
    
    elif '<' in input_text and '>' in input_text:
        match = re.search(r'<a?:([^:]+):(\d+)>', input_text)
        if match:
            emoji_name = match.group(1)
            emoji_id = match.group(2)
            is_animated = '<a:' in input_text
            ext = ".gif" if is_animated else ".png"
            image_url = f"https://cdn.discordapp.com/emojis/{emoji_id}{ext}"
    
    elif input_text.isdigit():
        emoji_id = input_text
        image_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
        emoji_name = "emoji"
    
    else:
        emoji_name = input_text
        existing_emoji = discord.utils.get(ctx.guild.emojis, name=emoji_name)
        if existing_emoji:
            image_url = existing_emoji.url
        else:
            for guild in bot.guilds:
                existing_emoji = discord.utils.get(guild.emojis, name=emoji_name)
                if existing_emoji:
                    image_url = existing_emoji.url
                    break
        
        if not image_url:
            numbers = re.findall(r'\d+', input_text)
            if numbers:
                emoji_id = numbers[0]
                image_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
                emoji_name = "emoji"
    
    if not image_url:
        embed = discord.Embed(
            description="❌ Could not find an emoji! Try:\n• Emoji ID: `,,steal 123456789012345678`\n• Emoji link: `,,steal https://cdn.discordapp.com/emojis/123456789.png`\n• The emoji itself: `,,steal :peepo:`",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if emoji_name:
        emoji_name = re.sub(r'[^a-zA-Z0-9_]', '_', emoji_name)
        if not emoji_name or emoji_name.isdigit():
            emoji_name = "emoji"
    else:
        emoji_name = "emoji"
    
    if len(emoji_name) > 32:
        emoji_name = emoji_name[:32]
    
    original_name = emoji_name
    counter = 1
    while discord.utils.get(ctx.guild.emojis, name=emoji_name):
        emoji_name = f"{original_name}_{counter}"
        counter += 1
        if counter > 100:
            emoji_name = f"emoji_{int(time.time())}"
            break
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=10) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                else:
                    if ".png" in image_url:
                        gif_url = image_url.replace(".png", ".gif")
                        async with session.get(gif_url, timeout=10) as resp2:
                            if resp2.status == 200:
                                image_data = await resp2.read()
                            else:
                                embed = discord.Embed(description="❌ Failed to download the emoji! The ID might be invalid.", color=discord.Color.red())
                                if ctx.interaction:
                                    await ctx.interaction.followup.send(embed=embed)
                                else:
                                    await ctx.send(embed=embed)
                                return
                    else:
                        embed = discord.Embed(description="❌ Failed to download the emoji!", color=discord.Color.red())
                        if ctx.interaction:
                            await ctx.interaction.followup.send(embed=embed)
                        else:
                            await ctx.send(embed=embed)
                        return
        
        new_emoji = await ctx.guild.create_custom_emoji(
            name=emoji_name[:32],
            image=image_data,
            reason=f"Stolen by {ctx.author.display_name}"
        )
        
        name_changed = original_name != emoji_name
        name_message = f" (renamed to `:{emoji_name}:` because `:{original_name}:` already existed)" if name_changed else ""
        
        embed = discord.Embed(
            title="✅ Emoji Stolen!",
            description=f"Successfully stole {new_emoji} (`:{new_emoji.name}:`){name_message}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Stolen by {ctx.author.display_name}")
        
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            if ctx.message:
                try:
                    await ctx.message.delete()
                except Exception:
                    pass
            await ctx.send(embed=embed)
            
    except discord.Forbidden:
        embed = discord.Embed(description="❌ I don't have permission to create emojis!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)
    except discord.HTTPException as e:
        if "Maximum number of emojis" in str(e):
            embed = discord.Embed(description=f"❌ Your server has reached the emoji limit ({ctx.guild.emoji_limit})! Delete some emojis first.", color=discord.Color.red())
        else:
            embed = discord.Embed(description=f"❌ Failed to create emoji: {str(e)[:100]}", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)
    except asyncio.TimeoutError:
        embed = discord.Embed(description="❌ Download timed out! The emoji might not exist.", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f"❌ Error: {str(e)[:100]}", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)
# =========================================================
# ROLE COMMAND
# =========================================================

@bot.hybrid_command(name="role", description="Add roles to a member")
@commands.has_permissions(manage_roles=True)
async def role(ctx, member: discord.Member, *, role_name: str):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot modify roles of the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot modify roles of the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot modify roles of a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot modify roles of a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me.")
    
    # --- SMART ROLE FINDER ---
    role = None
    search = role_name.strip()
    
    # Method 1: Role mention <@&123456789>
    mention_match = re.match(r'^<@&(\d+)>$', search)
    if mention_match:
        role_id = int(mention_match.group(1))
        role = ctx.guild.get_role(role_id)
    
    # Method 2: Exact name match
    if not role:
        role = discord.utils.get(ctx.guild.roles, name=search)
    
    # Method 3: Case-insensitive exact
    if not role:
        role = discord.utils.find(lambda r: r.name.lower() == search.lower(), ctx.guild.roles)
    
    # Method 4: Partial / fuzzy match
    if not role:
        role = discord.utils.find(lambda r: search.lower() in r.name.lower(), ctx.guild.roles)
    
    # Method 5: Strip @ prefix
    if not role and search.startswith("@"):
        stripped = search[1:].strip()
        role = discord.utils.find(lambda r: r.name.lower() == stripped.lower(), ctx.guild.roles)
        if not role:
            role = discord.utils.find(lambda r: stripped.lower() in r.name.lower(), ctx.guild.roles)
    
    if not role:
        embed = discord.Embed(
            description=f"❌ Role `{role_name}` not found.\n\n**Tip:** You can use the role name, mention it with `@`, or use part of the name.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    
    if ctx.guild.me and role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        embed = discord.Embed(
            description=f"❌ I cannot modify the role **{role.name}** because it's higher or equal to my highest role.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    
    try:
        if role in member.roles:
            # --- REMOVE ROLE ---
            await member.remove_roles(role, reason=f"Removed by {ctx.author}")
            
            embed = discord.Embed(
                title="✨ Role Removed",
                description=f"Removed **{role.name}** from {member.mention}",
                color=discord.Color.red()
            )
            embed.set_footer(text=f"Removed by {ctx.author.display_name}")
            
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed)
            else:
                await ctx.send(embed=embed)
        else:
            # --- ADD ROLE ---
            await member.add_roles(role, reason=f"Added by {ctx.author}")
            
            embed = discord.Embed(
                title="🎭 Role Added",
                description=f"Gave **{role.name}** to {member.mention}",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Added by {ctx.author.display_name}")
            
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed)
            else:
                await ctx.send(embed=embed)
                
    except Exception as e:
        embed = discord.Embed(
            description=f"❌ Failed to modify role: {e}",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
# =========================================================
# SPANK COMMAND
# =========================================================

SPANK_GIFS = [
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541230113180614666/bad-girl-spank.gif?ex=6a8cd5e5&is=6a8b8465&hm=d3ed6a6854f20f6c0ca16a29a6a0ccf4dc2a26f66e7df668bb886c64e00a67f4",
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541230109363675176/rikka-takanashi-chunibyo.gif?ex=6a8cd5e4&is=6a8b8464&hm=509840270093c68677f16726ce3dd9d8670d2d681b92ac261fcfa6cd32887836",
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541230149612212354/american-dad-cartoon.gif?ex=6a8cd5ee&is=6a8b846e&hm=1c93e265be5433b8ae3af608b0878bfa32ed6ecfb3d2ee0f3513b0ac57699aff",
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541230890447933470/anime-spanking.gif?ex=6a8cd69e&is=6a8b851e&hm=494fbffbc9dee0b95cfd435f4a6f76f11287b59c4079d0ca3a23384b9105a6c8"
]

@bot.hybrid_command(name="spank", description="Spank someone!")
async def spank(ctx, member: discord.Member = None):
    if not await check_nsfw(ctx):
        return
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to spank!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"🖐️ {ctx.author.mention} spanks themselves... their a werido...!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(SPANK_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't spank a bot!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"🖐️ {ctx.author.mention} spanked {member.mention} 🍑",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_image(url=random.choice(SPANK_GIFS))
    embed.set_footer(text="Spank spank!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# BENDOVER COMMAND
# =========================================================

BENDOVER_GIFS = [
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541229274613424189/bend-over-bed.gif?ex=6a8cd51d&is=6a8b839d&hm=bcc868baa77481a867b295a99bc4ec7efe533795c7cec5aa23517fd211729d75",
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541231362344747078/ilyshkin.gif?ex=6a8cd70f&is=6a8b858f&hm=1e2cd60e7f265ba8c766fc48a3135f51f7677d2a353af44e5a744a974421d987",
]

@bot.hybrid_command(name="bendover", description="Ask someone to bend over!")
async def bendover(ctx, member: discord.Member = None):
    if not await check_nsfw(ctx):
        return
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to bend over🤤!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"🫣 {ctx.author.mention} asked themselves to bend over... that's odd uhm!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(BENDOVER_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't ask a bot to bend over!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"🫣 {ctx.author.mention} asked {member.mention} to bend over and they said alr 😳",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_image(url=random.choice(BENDOVER_GIFS))
    embed.set_footer(text="Bend over!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# LINKS HELP COMMAND
# =========================================================

@bot.hybrid_command(name="linkshelp", aliases=["linkhelp", "lh"], description="Show help for link filtering commands")
@commands.has_permissions(administrator=True)
async def linkshelp(ctx):
    embed = discord.Embed(
        title="🔗 Link Filtering Commands",
        description="**Manage allowed links and link filtering settings**",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    
    embed.add_field(
        name="📥 Add a Link",
        value="`R!allowed link <domain>`\nExample: `R!allowed link roblox.com`",
        inline=False
    )
    
    embed.add_field(
        name="📤 Remove a Link",
        value="`R!allowed unlink <domain>`\nExample: `R!allowed unlink roblox.com`",
        inline=False
    )
    
    embed.add_field(
        name="📋 List Allowed Links",
        value="`R!allowed list`",
        inline=False
    )
    
    embed.add_field(
        name="🟢 Enable Filtering",
        value="`R!allowed enable`\nTurns on link filtering. Unallowed links will get muted.",
        inline=False
    )
    
    embed.add_field(
        name="🔴 Disable Filtering",
        value="`R!allowed disable`\nTurns off link filtering. All links are allowed.",
        inline=False
    )
    
    embed.add_field(
        name="⏱️ Set Mute Duration",
        value="`R!allowed time <duration>`\nOptions: `5m`, `10m`, `15m`, `20m`, `30m`, `1h`\nExample: `R!allowed time 10m`",
        inline=False
    )
    
    embed.add_field(
        name="📊 Check Status",
        value="`R!allowed`\nShows current filtering status and mute duration",
        inline=False
    )
    
    embed.add_field(
        name="🔄 How It Works",
        value="1. Add allowed links with `R!allowed link`\n2. Enable filtering with `R!allowed enable`\n3. Users sending unallowed links get muted\n4. Set mute time with `R!allowed time`",
        inline=False
    )
    
    embed.set_footer(text="Admin only commands • Use R!allowed for quick status")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

# =========================================================
# HIDE & SEEK COMMAND - WITH INVITE SYSTEM
# =========================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS hide_seek_stats (
    user_id INTEGER PRIMARY KEY,
    hides INTEGER DEFAULT 0,
    seeks INTEGER DEFAULT 0,
    found INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0
)
""")
db.commit()

hide_seek_games = {}
hide_seek_invites = {}

@bot.hybrid_command(name="hide", description="Invite someone to play hide and seek!")
async def hide(ctx, member: discord.Member = None):
    if member is None:
        embed = discord.Embed(
            description="❌ You need to invite someone to play!\nUsage: `R!hide @member`",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description="❌ You can't play hide and seek with yourself!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't play hide and seek with a bot!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if ctx.author.id in hide_seek_games:
        embed = discord.Embed(
            description="❌ You're already in a hide and seek game!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id in hide_seek_games:
        embed = discord.Embed(
            description=f"❌ {member.mention} is already in a hide and seek game!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if ctx.author.id in hide_seek_invites:
        remaining = int(30 - (time.time() - hide_seek_invites[ctx.author.id]))
        if remaining > 0:
            embed = discord.Embed(
                description=f"⏳ Please wait {remaining} seconds before sending another invite.",
                color=discord.Color.orange()
            )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
    
    hide_seek_invites[ctx.author.id] = time.time()
    
    view = HideSeekInviteView(ctx.author.id, member.id, ctx.channel.id)
    
    embed = discord.Embed(
        title="🕵️ Hide & Seek Invite",
        description=f"{ctx.author.mention} is inviting {member.mention} to play **Hide and Seek**!\n\n"
                    f"**How to play:**\n"
                    f"1️⃣ {ctx.author.mention} will hide in a random channel\n"
                    f"2️⃣ {member.mention} has **20 guesses** to find them\n"
                    f"3️⃣ Hints given every 5 guesses\n"
                    f"4️⃣ Winner gets **$200** and **+10 points**!\n\n"
                    f"**Do you accept the challenge?**",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    embed.set_footer(text="This invite expires in 60 seconds")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)

class HideSeekInviteView(discord.ui.View):
    def __init__(self, hider_id, seeker_id, channel_id):
        super().__init__(timeout=60)
        self.hider_id = hider_id
        self.seeker_id = seeker_id
        self.channel_id = channel_id
        self.answered = False
    
    async def on_timeout(self):
        if not self.answered:
            for child in self.children:
                child.disabled = True
            try:
                embed = discord.Embed(
                    description="⏰ Invite expired! The game has been cancelled.",
                    color=discord.Color.orange()
                )
                await self.message.edit(embed=embed, view=self)
            except:
                pass
    
    @discord.ui.button(label="✅ Yes, Let's Play!", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.seeker_id:
            await interaction.response.send_message("❌ This invite isn't for you!", ephemeral=True)
            return
        
        if self.answered:
            await interaction.response.send_message("⏳ This invite has already been answered!", ephemeral=True)
            return
        
        self.answered = True
        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ {interaction.user.mention} accepted the challenge! Starting game...",
            view=self
        )
        
        await start_hide_seek_game(interaction.channel, self.hider_id, self.seeker_id)
    
    @discord.ui.button(label="❌ No, Maybe Later", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.seeker_id:
            await interaction.response.send_message("❌ This invite isn't for you!", ephemeral=True)
            return
        
        if self.answered:
            await interaction.response.send_message("⏳ This invite has already been answered!", ephemeral=True)
            return
        
        self.answered = True
        for child in self.children:
            child.disabled = True
        
        embed = discord.Embed(
            description=f"❌ {interaction.user.mention} declined the invite. Game cancelled.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=self)

async def start_hide_seek_game(channel, hider_id, seeker_id):
    guild = channel.guild
    hider = guild.get_member(hider_id)
    seeker = guild.get_member(seeker_id)
    
    if not hider or not seeker:
        embed = discord.Embed(
            description="❌ One of the players is no longer in the server!",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return
    
    available_channels = []
    for ch in guild.text_channels:
        perms = ch.permissions_for(hider)
        if perms.view_channel and ch.type == discord.ChannelType.text:
            if ch.is_nsfw():
                continue
            available_channels.append(ch)
    
    if len(available_channels) < 2:
        embed = discord.Embed(
            description="❌ Not enough channels available to hide in! Need at least 2 text channels.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return
    
    hidden_channel = random.choice(available_channels)
    
    hide_seek_games[hider_id] = {
        "channel_id": hidden_channel.id,
        "channel_name": hidden_channel.name,
        "guild_id": guild.id,
        "guesses": 0,
        "max_guesses": 20,
        "found_by": None,
        "seeker_id": seeker_id,
        "start_time": time.time()
    }
    
    try:
        await hider.send(f"🕵️ You are hiding in **#{hidden_channel.name}**! {seeker.display_name} has 20 guesses to find you.")
    except:
        pass
    
    embed = discord.Embed(
        title="🕵️ Hide & Seek Started!",
        description=f"{hider.mention} is hiding somewhere in this server!\n\n"
                    f"**Seeker:** {seeker.mention}\n"
                    f"**Guesses:** 20\n"
                    f"**Prize:** $200 + 10 points!\n\n"
                    f"Use `R!seek #channel` to guess where they are!",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    embed.set_footer(text=f"Hint: The channel has {len(hidden_channel.name)} letters")
    
    await channel.send(embed=embed)

@bot.hybrid_command(name="seek", description="Guess where the hider is!")
async def seek(ctx, channel: discord.TextChannel):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    
    hider_id = None
    game_data = None
    for hid, data in hide_seek_games.items():
        if data["guild_id"] == guild_id:
            hider_id = hid
            game_data = data
            break
    
    if not hider_id:
        embed = discord.Embed(
            description="❌ Nobody is hiding in this server right now! Use `R!hide @member` to start a game.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if user_id != game_data["seeker_id"]:
        embed = discord.Embed(
            description=f"❌ Only <@{game_data['seeker_id']}> can make guesses in this game!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if game_data["guesses"] >= game_data["max_guesses"]:
        embed = discord.Embed(
            description=f"❌ The hider has already been found or the game ended!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if game_data.get("found_by") is not None:
        embed = discord.Embed(
            description=f"❌ The hider was already found by <@{game_data['found_by']}>!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    game_data["guesses"] += 1
    remaining = game_data["max_guesses"] - game_data["guesses"]
    
    if channel.id == game_data["channel_id"]:
        game_data["found_by"] = user_id
        
        cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (hider_id,))
        cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (user_id,))
        cursor.execute("UPDATE hide_seek_stats SET hidden = hidden + 1, losses = losses + 1 WHERE user_id = ?", (hider_id,))
        cursor.execute("UPDATE hide_seek_stats SET found = found + 1, wins = wins + 1 WHERE user_id = ?", (user_id,))
        
        update_wallet(user_id, 200)
        update_wallet(hider_id, -100)
        
        cursor.execute("UPDATE hide_seek_stats SET points = points + 10 WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE hide_seek_stats SET points = points - 5 WHERE user_id = ?", (hider_id,))
        db.commit()
        
        embed = discord.Embed(
            title="🎉 FOUND!",
            description=f"{ctx.author.mention} found <@{hider_id}> hiding in **#{channel.name}**!\n\n"
                        f"💰 {ctx.author.mention} won **$200**!\n"
                        f"💰 <@{hider_id}> lost **$100**!\n"
                        f"⭐ Points: {ctx.author.mention} +10, <@{hider_id}> -5",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Found in {game_data['guesses']} guesses!")
        
        del hide_seek_games[hider_id]
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="❌ Wrong!",
            description=f"{ctx.author.mention} guessed **#{channel.name}** but that's not where they are!\n\n"
                        f"📊 **{remaining}** guesses remaining.",
            color=discord.Color.red()
        )
        
        if game_data["guesses"] % 5 == 0:
            channel_name = game_data["channel_name"]
            hint = ""
            if game_data["guesses"] == 5:
                hint = f"💡 Hint: The channel starts with `{channel_name[0]}`"
            elif game_data["guesses"] == 10:
                hint = f"💡 Hint: The channel has {len(channel_name)} letters"
            elif game_data["guesses"] == 15:
                hint = f"💡 Hint: The channel name contains `{channel_name[2:4]}`"
            elif game_data["guesses"] >= 20:
                hint = f"💡 Hint: The channel is `#{channel_name}`"
            
            if hint:
                embed.add_field(name="📌 Hint", value=hint, inline=False)
        
        if game_data["guesses"] >= game_data["max_guesses"]:
            embed.description = f"❌ Nobody found <@{hider_id}>! They were hiding in **#{game_data['channel_name']}**."
            cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (hider_id,))
            cursor.execute("UPDATE hide_seek_stats SET hidden = hidden + 1, wins = wins + 1 WHERE user_id = ?", (hider_id,))
            db.commit()
            del hide_seek_games[hider_id]
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)

@bot.hybrid_command(name="hideleaderboard", aliases=["hidelb", "hiderank"], description="Show hide and seek leaderboard")
async def hideleaderboard(ctx):
    cursor.execute("SELECT user_id, points, hides, hidden, found, wins, losses FROM hide_seek_stats ORDER BY points DESC LIMIT 10")
    rows = cursor.fetchall()
    
    if not rows:
        embed = discord.Embed(
            description="📋 No hide and seek stats yet! Be the first to play!",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    leaderboard = []
    rank = 1
    for row in rows:
        user_id, points, hides, hidden, found, wins, losses = row
        user = bot.get_user(user_id)
        name = user.display_name if user else f"User {user_id}"
        leaderboard.append(f"**#{rank}** {name} - ⭐ {points} pts | 🏆 {wins}W/{losses}L | 🕵️ Hid: {hidden} | Found: {found}")
        rank += 1
    
    embed = discord.Embed(
        title="🏆 Hide & Seek Leaderboard",
        description="\n".join(leaderboard),
        color=discord.Color.gold()
    )
    embed.set_footer(text="Points: +10 for finding | -5 for being found")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="hidestats", description="Check your hide and seek stats")
async def hidestats(ctx, member: discord.Member = None):
    target = member or ctx.author
    cursor.execute("SELECT hides, hidden, found, points, wins, losses FROM hide_seek_stats WHERE user_id = ?", (target.id,))
    row = cursor.fetchone()
    
    if not row:
        embed = discord.Embed(
            description=f"{target.mention} hasn't played hide and seek yet!",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    hides, hidden, found, points, wins, losses = row
    
    embed = discord.Embed(
        title=f"📊 {target.display_name}'s Stats",
        description=f"⭐ Points: **{points}**\n"
                    f"🏆 Wins: **{wins}** | Losses: **{losses}**\n"
                    f"🕵️ Times Hidden: **{hides}**\n"
                    f"🏆 Found Someone: **{found}**\n"
                    f"😳 Got Found: **{hidden}**",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Use R!hide @member to play!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="endhide", description="Force end the current hide and seek game (admin only)")
@commands.has_permissions(administrator=True)
async def endhide(ctx):
    guild_id = ctx.guild.id
    
    for hid, data in hide_seek_games.items():
        if data["guild_id"] == guild_id:
            embed = discord.Embed(
                description=f"✅ Game ended! <@{hid}> was hiding in **#{data['channel_name']}**.",
                color=discord.Color.green()
            )
            del hide_seek_games[hid]
            
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed)
            else:
                await ctx.send(embed=embed)
            return
    
    embed = discord.Embed(
        description="❌ No hide and seek game is active in this server!",
        color=discord.Color.red()
    )
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)

# =========================================================
# RAPE COMMAND
# =========================================================

RAPE_GIFS = [
    "https://cdn.discordapp.com/attachments/1062920364079984710/1313736716934316063/4B136022-E20D-4147-8176-83236A3B38C1.gif?ex=6aa97e69&is=6aa82ce9&hm=8614f5008b701ff74d5c59659c9003193b20565247ccdffaa794f6d5c6757c96"
]

@bot.hybrid_command(name="rape", description="rape someone with a cute GIF!")
async def rape(ctx, member: discord.Member = None):
    if not await check_nsfw(ctx):
        return
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to rape!\nUsage: `R!rape @member`",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f"🫂 {ctx.author.mention} rapes themselves... that's kinda sad but okay!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(RAPE_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't rape a bot weirdo!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"🍑 {ctx.author.mention} rapes {member.mention}!🍆",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_image(url=random.choice(RAPE_GIFS))
    embed.set_footer(text="get graped!")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

# =========================================================
# SLAP COMMAND
# =========================================================

SLAP_GIFS = [
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541232784352477184/slap-jjk.gif?ex=6a8cd862&is=6a8b86e2&hm=423dbe8e7f07e27725c275b44dd1db3aab881ecb1995d84c2503f752840decdb",
    "https://cdn.discordapp.com/attachments/1525052130266841090/1541233495492403231/mushoku-tensei-boy-hit-girl.gif?ex=6a8cd90c&is=6a8b878c&hm=c6a594663908cfa3dae2cd3546dab0e90a17e055b0dee6ec84db840cc695954d",
    "https://cdn.discordapp.com/attachments/1536352076383395911/1541233998838235217/slap-riku.gif?ex=6a8cd984&is=6a8b8804&hm=f921ae6fd357608db839cf8e73586e95133f429d5b6524f13ecc4921d29d494d",
    "https://cdn.discordapp.com/attachments/1536352076383395911/1541234656819683429/cats-cat-slap.gif?ex=6a8cda20&is=6a8b88a0&hm=4c59102980908ad2a6e2c241da147368dd0c491f3f6eff994faca7fd7c2040a5"
]

@bot.hybrid_command(name="slap", description="Slap someone!")
async def slap(ctx, member: discord.Member = None):
    if member is None:
        embed = discord.Embed(
            description="❌ You need to specify someone to slap!\nUsage: `R!slap @member`",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            description=f" {ctx.author.mention} slaps themselves... that's just sad!",
            color=discord.Color.orange()
        )
        embed.set_image(url=random.choice(SLAP_GIFS))
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            description="❌ You can't slap a bot!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        description=f"{ctx.author.mention} Slaps {member.mention}!",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    embed.set_image(url=random.choice(SLAP_GIFS))
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
@bot.command(name='rules')
async def rules(ctx):
    """Sends the server rules"""
    
    embed = discord.Embed(
        title="📜 SERVER RULES",
        description="Follow these rules to keep the server safe and enjoyable for everyone!",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="1. No NSFW or Gore",
        value="You will be immediately banned if you spam this or it's obviously not a joke.",
        inline=False
    )
    
    embed.add_field(
        name="2. No Spamming",
        value="Unless permitted (this is not allowed even if you have no cooldown).",
        inline=False
    )
    
    embed.add_field(
        name="3. No Doxxing/Sharing Personal Information",
        value="This is not cool.",
        inline=False
    )
    
    embed.add_field(
        name="4. Use Channels for Their Dedicated Purpose",
        value="Even if you have access to do something in a channel that you're not supposed to, do not use the channel as your playground.",
        inline=False
    )
    
    embed.add_field(
        name="5. Do Not Use Profanity",
        value="Even if you are saying mean things indirectly, that's not allowed.",
        inline=False
    )
    
    embed.add_field(
        name="6. No Advertising",
        value="Unless given permission or in a dedicated channel for it.",
        inline=False
    )
    
    embed.add_field(
        name="7. Follow Discord's TOS",
        value="Don't get the server deleted. [Click here](https://discord.com/guidelines)",
        inline=False
    )
    
    embed.set_footer(text="Use common sense 💡")
    
    await ctx.send(embed=embed)
    
    print(f'✅ Logged in as {bot.user}')
    print(f'📡 Connected to {len(bot.guilds)} servers')
    print(f'👥 Serving {len(bot.users)} users')
    print(f'📋 Loaded rules for {len(rules_cache)} servers')
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{len(bot.guilds)} servers | R!help"
        )
    )
# =========================================================
# PERMISSION SYSTEM & ERROR HANDLER - PASTE THIS ENTIRE BLOCK
# =========================================================

# ---------- PERMISSION CHECKS ----------

def is_bot_owner(user_id):
    return user_id in OWNER_IDS

async def slash_bot_owner_only(interaction: discord.Interaction):
    return interaction.user.id in OWNER_IDS

async def slash_staff_only(interaction: discord.Interaction):
    if not interaction.guild:
        return False
    member = interaction.user
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_messages:
        return True
    if member.guild_permissions.manage_roles:
        return True
    if member.guild_permissions.kick_members:
        return True
    if member.guild_permissions.ban_members:
        return True
    staff_roles = ["Staff", "Moderator", "Mod", "Admin", "Administrator", "Owner", "Management"]
    for role in member.roles:
        if role.name in staff_roles:
            return True
    return False

# ---------- ERROR HANDLER ----------

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description=f"{ctx.author.mention} You do not have moderator or moderator permissions.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.MissingRequiredArgument):
        cmd_name = ctx.command.name if ctx.command else "unknown"
        
        embed = discord.Embed(
            title="⚠️ Wrong Usage",
            color=discord.Color.orange()
        )
        
        error_messages = {
            "ban": f"❌ {ctx.author.mention} Please mention a user to ban.",
            "unban": f"❌ {ctx.author.mention} Please provide a user ID to unban.",
            "mute": f"❌ {ctx.author.mention} Please mention a user to mute.",
            "kick": f"❌ {ctx.author.mention} Please mention a user to kick.",
            "warn": f"❌ {ctx.author.mention} Please mention a user to warn.",
            "role": f"❌ {ctx.author.mention} Please mention a user and role name.",
            "pay": f"❌ {ctx.author.mention} Please mention a user and amount.",
            "gamble": f"❌ {ctx.author.mention} Please provide an amount to gamble.",
            "dice": f"❌ {ctx.author.mention} Please provide an amount to bet.",
            "slots": f"❌ {ctx.author.mention} Please provide an amount to bet.",
            "rob": f"❌ {ctx.author.mention} Please mention a user to rob.",
            "deposit": f"❌ {ctx.author.mention} Please provide an amount to deposit.",
            "withdraw": f"❌ {ctx.author.mention} Please provide an amount to withdraw.",
            "marry": f"❌ {ctx.author.mention} Please mention someone to marry.",
            "kiss": f"❌ {ctx.author.mention} Please mention someone to kiss.",
            "slap": f"❌ {ctx.author.mention} Please mention someone to slap.",
            "spank": f"❌ {ctx.author.mention} Please mention someone to spank.",
            "pat": f"❌ {ctx.author.mention} Please mention someone to pat.",
            "tape": f"❌ {ctx.author.mention} Please mention someone to tape.",
            "hack": f"❌ {ctx.author.mention} Please mention someone to hack.",
            "poll": f"❌ {ctx.author.mention} Please provide a question for the poll.",
            "say": f"❌ {ctx.author.mention} Please provide a message to say.",
            "embed": f"❌ {ctx.author.mention} Please provide title and description.",
            "clear": f"❌ {ctx.author.mention} Please provide the number of messages to clear.",
            "purge": f"❌ {ctx.author.mention} Please provide the number of messages to purge.",
            "slowmode": f"❌ {ctx.author.mention} Please provide the seconds for slowmode.",
            "gif": f"❌ {ctx.author.mention} Please provide a search term for the GIF.",
            "8ball": f"❌ {ctx.author.mention} Please ask the 8ball a question.",
            "mock": f"❌ {ctx.author.mention} Please provide text to mock.",
            "ghostping": f"❌ {ctx.author.mention} Please mention someone to ghost ping.",
            "fakenuke": f"❌ {ctx.author.mention} Please mention someone to fake nuke.",
            "masscreate": f"❌ {ctx.author.mention} Please provide count and channel name.",
            "hide": f"❌ {ctx.author.mention} Please mention someone to hide from.",
            "seek": f"❌ {ctx.author.mention} Please mention a channel to seek.",
            "stealurl": f"❌ {ctx.author.mention} Please provide an emoji link or ID.",
            "setup": f"❌ {ctx.author.mention} Please provide a style for the setup.",
            "blacklist": f"❌ {ctx.author.mention} Please provide a user to blacklist.",
            "whitelist": f"❌ {ctx.author.mention} Please mention a user to whitelist.",
            "unwhitelist": f"❌ {ctx.author.mention} Please mention a user to unwhitelist.",
            "country": f"❌ {ctx.author.mention} Please start the country game.",
            "brainrot_dice": f"❌ {ctx.author.mention} Please provide an amount to bet.",
            "trollpanel": f"❌ {ctx.author.mention} Please open the troll panel.",
            "afk": f"❌ {ctx.author.mention} Please provide a reason for being AFK.",
            "avatar": f"❌ {ctx.author.mention} Please provide a user to check avatar.",
            "cf": f"❌ {ctx.author.mention} Please flip a coin.",
            "gayrate": f"❌ {ctx.author.mention} Please provide a user to check gay rate.",
            "pp": f"❌ {ctx.author.mention} Please provide a user to check pp size.",
            "iq": f"❌ {ctx.author.mention} Please provide a user to check IQ.",
            "roast": f"❌ {ctx.author.mention} Please mention someone to roast.",
            "snipe": f"❌ {ctx.author.mention} Please provide how many messages to snipe.",
            "editsnipe": f"❌ {ctx.author.mention} Please snipe the last edited message.",
            "divorce": f"❌ {ctx.author.mention} Please divorce your spouse.",
            "work": f"❌ {ctx.author.mention} Please start working.",
            "crime": f"❌ {ctx.author.mention} Please commit a crime.",
            "daily": f"❌ {ctx.author.mention} Please claim your daily reward.",
            "balance": f"❌ {ctx.author.mention} Please check your balance.",
            "luck": f"❌ {ctx.author.mention} Please check your luck.",
            "pfps": f"❌ {ctx.author.mention} Please get a random PFP.",
            "memes": f"❌ {ctx.author.mention} Please get a random meme.",
            "fraktur": f"❌ {ctx.author.mention} Please provide text to convert.",
        }
        
        embed.description = error_messages.get(cmd_name, f"❌ {ctx.author.mention} You are missing an argument!")
        
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BadArgument):
        cmd_name = ctx.command.name if ctx.command else "unknown"
        
        embed = discord.Embed(
            title="❌ Invalid Argument",
            color=discord.Color.red()
        )
        
        if cmd_name in ["ban", "kick", "mute", "unmute", "warn", "rob", "marry", "kiss", "slap", "spank", "pat", "tape", "hack", "hide", "pay", "whitelist", "unwhitelist"]:
            embed.description = f"❌ {ctx.author.mention} Invalid member mentioned! Please mention a valid user."
        elif cmd_name == "role":
            embed.description = f"❌ {ctx.author.mention} Invalid member or role! Please check the member and role name."
        elif cmd_name in ["gamble", "dice", "slots", "pay", "deposit", "withdraw"]:
            embed.description = f"❌ {ctx.author.mention} Invalid amount! Please enter a valid number."
        elif cmd_name in ["gif", "mock", "say", "poll", "embed"]:
            embed.description = f"❌ {ctx.author.mention} Invalid input! Please check your arguments."
        elif cmd_name == "seek":
            embed.description = f"❌ {ctx.author.mention} Invalid channel! Please mention a valid text channel."
        else:
            embed.description = f"❌ {ctx.author.mention} You provided an invalid argument."
        
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BotMissingPermissions):
        embed = discord.Embed(
            title="❌ Bot Permission Error",
            description=f"{ctx.author.mention} I am missing the required permissions.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.MemberNotFound):
        embed = discord.Embed(
            title="❌ Member Not Found",
            description=f"{ctx.author.mention} I couldn't find that member.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.CommandOnCooldown):
        embed = discord.Embed(
            title="⏳ Cooldown",
            description=f"{ctx.author.mention} Please wait `{error.retry_after:.1f}` seconds.",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.RoleNotFound):
        embed = discord.Embed(
            title="❌ Role Not Found",
            description=f"{ctx.author.mention} I couldn't find that role.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.NotOwner):
        embed = discord.Embed(
            title="👑 Owner Only",
            description=f"{ctx.author.mention} Only the bot owner can use this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.NSFWChannelRequired):
        embed = discord.Embed(
            title="🔞 NSFW Only",
            description=f"{ctx.author.mention} This command can only be used in NSFW channels.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    embed = discord.Embed(
        title="❌ Error",
        description=f"{ctx.author.mention} Something went wrong.",
        color=discord.Color.red()
    )
    if ctx.interaction:
        try:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            return
    return await ctx.send(embed=embed)
# =========================================================
# CHANNEL LOCK / UNLOCK COMMANDS
# =========================================================

@bot.hybrid_command(name="lock", description="Locks the current channel to prevent members from sending messages.")
@app_commands.describe(channel="The channel to lock (defaults to current channel)")
async def lock(ctx: commands.Context, channel: discord.TextChannel = None):
    # Check permissions
    if not await require_server_mod(ctx):
        return

    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)

    if overwrite.send_messages is False:
        return await ctx.send(f"🔒 {channel.mention} is already locked.")

    overwrite.send_messages = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Channel locked by {ctx.author}")

    embed = discord.Embed(
        title="🔒 Channel Locked",
        description=f"{channel.mention} has been locked by {ctx.author.mention}.",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(name="unlock", description="Unlocks the current channel to allow members to send messages.")
@app_commands.describe(channel="The channel to unlock (defaults to current channel)")
async def unlock(ctx: commands.Context, channel: discord.TextChannel = None):
    # Check permissions
    if not await require_server_mod(ctx):
        return

    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)

    if overwrite.send_messages is True or overwrite.send_messages is None:
        return await ctx.send(f"🔓 {channel.mention} is already unlocked.")

    overwrite.send_messages = None  # Resets to default role settings
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Channel unlocked by {ctx.author}")

    embed = discord.Embed(
        title="🔓 Channel Unlocked",
        description=f"{channel.mention} has been unlocked by {ctx.author.mention}.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)
# =========================================================
# LEADERBOARD COMMAND (top 1-100)
# =========================================================

LEADERBOARD_MAX = 100
PAGE_SIZE = 25


LEADERBOARD_REWARD_ROLES = {
    "net": [
        (1, "🏆 Top 1"),
        (2, "🥈 Top 2"),
        (3, "🥉 Top 3"),
    ],
    "wallet": [
        (1, "💰 Richest Wallet"),
    ],
    "bank": [
        (1, "🏦 Biggest Bank"),
    ],
    "luck": [
        (1, "🍀 Luckiest"),
    ],
}


async def grant_leaderboard_roles(guild: discord.Guild):
    """Grant reward roles for each leaderboard category. Requires Manage Roles."""
    if guild is None:
        return
    if not guild.me.guild_permissions.manage_roles:
        return

    for mode, rewards in LEADERBOARD_REWARD_ROLES.items():
        order = {
            "net": "wallet + bank",
            "wallet": "wallet",
            "bank": "bank",
            "luck": "luck",
        }[mode]

        cursor.execute(f"SELECT user_id FROM users ORDER BY {order} DESC LIMIT 3")
        rows = cursor.fetchall()

        for rank, (required_rank, role_name) in enumerate(rewards, start=1):
            if rank > len(rows):
                break
            winner_id = rows[rank - 1][0]

            role = discord.utils.get(guild.roles, name=role_name)
            if role is None:
                try:
                    role = await guild.create_role(
                        name=role_name,
                        color=discord.Color.gold(),
                        mentionable=False,
                        reason="Leaderboard reward role",
                    )
                except Exception:
                    continue

            try:
                member = guild.get_member(winner_id) or await guild.fetch_member(winner_id)
            except Exception:
                member = None

            if member is None:
                continue

            for m in guild.members:
                if role in m.roles and m.id != winner_id:
                    try:
                        await m.remove_roles(role, reason="Leaderboard role refresh")
                    except Exception:
                        pass

            if role not in member.roles:
                try:
                    await member.add_roles(role, reason="Leaderboard reward")
                except Exception:
                    pass


class LeaderboardView(discord.ui.View):
    def __init__(self, user_id: int, guild: discord.Guild = None, timeout=180):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.guild = guild
        self.page = 0
        self.mode = "net"
        self.last_updated = datetime.utcnow()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This leaderboard isn't for you. Run `/leaderboard` yourself.",
                ephemeral=True,
            )
            return False
        return True

    def _order_clause(self):
        return {
            "net": "wallet + bank",
            "wallet": "wallet",
            "bank": "bank",
            "luck": "luck",
        }[self.mode]

    def fetch_page(self, offset: int, limit: int = PAGE_SIZE):
        cursor.execute(
            f"SELECT user_id, wallet, bank, luck FROM users ORDER BY {self._order_clause()} DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return cursor.fetchall()

    def get_caller_rank(self):
        cursor.execute(
            f"SELECT user_id FROM users ORDER BY {self._order_clause()} DESC LIMIT ?",
            (LEADERBOARD_MAX,),
        )
        for idx, row in enumerate(cursor.fetchall(), start=1):
            if row[0] == self.user_id:
                return idx
        return None

    def get_total_users(self):
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

    def total_pages(self):
        total = self.get_total_users()
        effective = min(total, LEADERBOARD_MAX)
        if effective == 0:
            return 1
        return max(1, (effective + PAGE_SIZE - 1) // PAGE_SIZE)

    def _progress_bar(self, value: int, max_value: int, length: int = 10) -> str:
        if max_value <= 0:
            return "░" * length
        filled = int((value / max_value) * length)
        filled = max(0, min(length, filled))
        return "█" * filled + "░" * (length - filled)

    def build_embed(self):
        offset = self.page * PAGE_SIZE
        remaining = LEADERBOARD_MAX - offset
        if remaining <= 0:
            self.page = 0
            offset = 0
            remaining = LEADERBOARD_MAX
        fetch_limit = min(PAGE_SIZE, remaining)

        rows = self.fetch_page(offset, fetch_limit)
        total_users = self.get_total_users()
        pages = self.total_pages()

        title_map = {
            "net": "🏆 Net Worth Leaderboard",
            "wallet": "💰 Wallet Leaderboard",
            "bank": "🏦 Bank Leaderboard",
            "luck": "🍀 Luck Leaderboard",
        }

        embed = discord.Embed(
            title=title_map[self.mode],
            color=discord.Color.gold(),
            timestamp=self.last_updated,
        )

        if not rows:
            embed.description = "📋 No users found yet."
            return embed

        def value_of(row):
            _, w, b, l = row
            if self.mode == "net":
                return w + b
            if self.mode == "wallet":
                return w
            if self.mode == "bank":
                return b
            return l

        max_value = max(value_of(r) for r in rows) or 1
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}

        body_lines = []
        for i, (user_id, wallet, bank, luck) in enumerate(rows, start=offset + 1):
            user = bot.get_user(user_id)
            name = user.display_name if user else f"User {user_id}"

            if self.mode == "net":
                value = wallet + bank
                val_str = f"🪙 {value:,}"
            elif self.mode == "wallet":
                value = wallet
                val_str = f"🪙 {value:,}"
            elif self.mode == "bank":
                value = bank
                val_str = f"🪙 {value:,}"
            else:
                value = luck
                val_str = f"🍀 {value}%"

            medal = medals.get(i, "")
            prefix = f"{medal} " if medal else ""
            bar = self._progress_bar(value, max_value, length=10)
            body_lines.append(f"{prefix}**#{i}** {name}\n{bar}  {val_str}")

        chunk = []
        chunk_size = 0
        field_index = 1

        def flush_chunk():
            nonlocal chunk, chunk_size, field_index
            if not chunk:
                return
            embed.add_field(
                name="📊 Ranks" if field_index == 1 else "📊 Ranks (cont.)",
                value="\n\n".join(chunk),
                inline=False,
            )
            chunk = []
            chunk_size = 0
            field_index += 1

        for line in body_lines:
            if chunk_size + len(line) + 2 > 1000:
                flush_chunk()
            chunk.append(line)
            chunk_size += len(line) + 2

        flush_chunk()

        caller_rank = self.get_caller_rank()
        footer_parts = [f"Page {self.page + 1}/{pages}"]
        if caller_rank:
            footer_parts.append(f"Your rank: #{caller_rank}")
        footer_parts.append(f"Showing top {LEADERBOARD_MAX} of {total_users}")
        embed.set_footer(text=" • ".join(footer_parts))
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self.last_updated = datetime.utcnow()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page + 1 < self.total_pages():
            self.page += 1
            self.last_updated = datetime.utcnow()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.last_updated = datetime.utcnow()
        try:
            await grant_leaderboard_roles(self.guild)
        except Exception:
            pass
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        placeholder="Switch leaderboard...",
        options=[
            discord.SelectOption(label="Net Worth", value="net", emoji="🏆"),
            discord.SelectOption(label="Wallet", value="wallet", emoji="💰"),
            discord.SelectOption(label="Bank", value="bank", emoji="🏦"),
            discord.SelectOption(label="Luck", value="luck", emoji="🍀"),
        ],
        row=1,
    )
    async def mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.mode = select.values[0]
        self.page = 0
        self.last_updated = datetime.utcnow()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


@bot.hybrid_command(
    name="leaderboard",
    aliases=["lb", "top"],
    description="View the top 100 richest users in the bots Economy",
)
async def leaderboard(ctx):
    get_user_econ(ctx.author.id)

    view = LeaderboardView(ctx.author.id, guild=ctx.guild)
    embed = view.build_embed()

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)
# =========================================================
# WEATHER COMMAND (Open-Meteo — free, no API key)
# =========================================================

WEATHER_CODES = {
    0: ("☀️", "Clear sky"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Depositing rime fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Moderate drizzle"),
    55: ("🌧️", "Dense drizzle"),
    56: ("🌧️", "Light freezing drizzle"),
    57: ("🌧️", "Dense freezing drizzle"),
    61: ("🌧️", "Slight rain"),
    63: ("🌧️", "Moderate rain"),
    65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Light freezing rain"),
    67: ("🌧️", "Heavy freezing rain"),
    71: ("🌨️", "Slight snow"),
    73: ("🌨️", "Moderate snow"),
    75: ("❄️", "Heavy snow"),
    77: ("🌨️", "Snow grains"),
    80: ("🌦️", "Slight rain showers"),
    81: ("🌧️", "Moderate rain showers"),
    82: ("⛈️", "Violent rain showers"),
    85: ("🌨️", "Slight snow showers"),
    86: ("❄️", "Heavy snow showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm with slight hail"),
    99: ("⛈️", "Thunderstorm with heavy hail"),
}


def _weather_emoji(code: int):
    return WEATHER_CODES.get(code, ("🌡️", "Unknown"))


@bot.hybrid_command(name="weather", description="Get the current weather for a city")
@app_commands.describe(city="City name (e.g. Tokyo, New York, London)")
async def weather(ctx, *, city: str):
    if ctx.interaction:
        await ctx.interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        # Step 1: geocode the city name -> lat/lon
        try:
            async with session.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            ) as resp:
                geo = await resp.json()
        except Exception as e:
            embed = discord.Embed(
                description=f"❌ Geocoding failed: `{str(e)[:150]}`",
                color=discord.Color.red(),
            )
            if ctx.interaction:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            return await ctx.send(embed=embed)

        results = geo.get("results") or []
        if not results:
            embed = discord.Embed(
                description=f"❌ Couldn't find a city called `{city}`.",
                color=discord.Color.red(),
            )
            if ctx.interaction:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            return await ctx.send(embed=embed)

        place = results[0]
        lat = place["latitude"]
        lon = place["longitude"]
        display_name = place["name"]
        country = place.get("country", "")
        admin = place.get("admin1", "")
        timezone = place.get("timezone", "auto")

        # Step 2: fetch current weather
        try:
            async with session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
                    "timezone": timezone,
                    "forecast_days": 1,
                },
                timeout=10,
            ) as resp:
                data = await resp.json()
        except Exception as e:
            embed = discord.Embed(
                description=f"❌ Weather fetch failed: `{str(e)[:150]}`",
                color=discord.Color.red(),
            )
            if ctx.interaction:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            return await ctx.send(embed=embed)

    current = data.get("current", {})
    daily = data.get("daily", {})

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")
    wind_dir = current.get("wind_direction_10m")
    precip = current.get("precipitation")
    code = current.get("weather_code", 0)
    is_day = current.get("is_day", 1)

    emoji, condition = _weather_emoji(code)
    if not is_day and code == 0:
        emoji, condition = "🌙", "Clear sky (night)"

    # wind direction arrow
    if wind_dir is not None:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        arrow = dirs[int((wind_dir + 22.5) % 360 // 45)]
    else:
        arrow = ""

    location_str = display_name
    if admin and admin != display_name:
        location_str += f", {admin}"
    if country:
        location_str += f", {country}"

    # Temperature color
    if temp is None:
        color = discord.Color.blurple()
    elif temp <= 0:
        color = discord.Color.from_rgb(120, 190, 255)
    elif temp <= 10:
        color = discord.Color.from_rgb(90, 170, 255)
    elif temp <= 20:
        color = discord.Color.from_rgb(120, 220, 180)
    elif temp <= 30:
        color = discord.Color.from_rgb(255, 190, 100)
    else:
        color = discord.Color.from_rgb(255, 110, 90)

    embed = discord.Embed(
        title=f"{emoji} {condition}",
        description=f"**{location_str}**",
        color=color,
    )

    embed.add_field(
        name="🌡️ Temperature",
        value=f"**{temp}°C**\nFeels like **{feels}°C**" if temp is not None else "—",
        inline=True,
    )
    embed.add_field(
        name="💧 Humidity",
        value=f"**{humidity}%**" if humidity is not None else "—",
        inline=True,
    )
    embed.add_field(
        name="💨 Wind",
        value=f"**{wind} km/h** {arrow}" if wind is not None else "—",
        inline=True,
    )
    embed.add_field(
        name="🌧️ Precipitation",
        value=f"**{precip} mm**" if precip is not None else "—",
        inline=True,
    )

    if daily:
        tmax = daily.get("temperature_2m_max", [None])[0]
        tmin = daily.get("temperature_2m_min", [None])[0]
        sunrise_raw = (daily.get("sunrise") or [None])[0]
        sunset_raw = (daily.get("sunset") or [None])[0]

        sunrise = sunrise_raw.split("T")[1] if sunrise_raw else "—"
        sunset = sunset_raw.split("T")[1] if sunset_raw else "—"

        embed.add_field(
            name="📈 Today",
            value=f"Max **{tmax}°C** / Min **{tmin}°C**" if tmax is not None else "—",
            inline=True,
        )
        embed.add_field(
            name="🌅 Sunrise",
            value=f"**{sunrise}**",
            inline=True,
        )
        embed.add_field(
            name="🌇 Sunset",
            value=f"**{sunset}**",
            inline=True,
        )

    embed.set_footer(
        text=f"Requested by {ctx.author.display_name} • Powered by Open-Meteo",
        icon_url=ctx.author.display_avatar.url,
    )

    if ctx.interaction:
        await ctx.interaction.followup.send(embed=embed)
    else:
        await ctx.send(embed=embed)
# =========================================================
# USERINFO COMMAND
# =========================================================

@bot.hybrid_command(name="userinfo", aliases=["whois", "ui"], description="Show detailed information about a user")
@app_commands.describe(member="The user to look up (defaults to yourself)")
async def userinfo(ctx, member: discord.Member = None):
    target = member or ctx.author

    # If this is a guild member, get richer info
    is_member = isinstance(target, discord.Member) and ctx.guild is not None

    # ---- Build roles string ----
    roles_str = "None"
    top_role_str = "None"
    if is_member and target.roles:
        # Exclude @everyone
        role_list = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
        if role_list:
            roles_str = ", ".join(role_list[:15])
            if len(role_list) > 15:
                roles_str += f" +{len(role_list) - 15} more"
        top_role_str = target.top_role.mention if target.top_role else "None"

    # ---- Status ----
    status_map = {
        discord.Status.online: "🟢 Online",
        discord.Status.idle: "🟡 Idle",
        discord.Status.dnd: "🔴 Do Not Disturb",
        discord.Status.offline: "⚫ Offline",
    }
    status_str = status_map.get(target.status, "⚫ Unknown") if is_member else "❔ Unknown"

    # ---- Badges ----
    badge_map = {
        "staff": "👨‍💼 Discord Staff",
        "partner": "🤝 Partner",
        "hypesquad": "🎉 HypeSquad Events",
        "bug_hunter": "🐛 Bug Hunter",
        "hypesquad_bravery": "🦁 Bravery",
        "hypesquad_brilliance": "🦄 Brilliance",
        "hypesquad_balance": "⚖️ Balance",
        "early_supporter": "⏰ Early Supporter",
        "verified_bot_developer": "🤖 Verified Bot Dev",
        "active_developer": "🛠️ Active Developer",
        "premium_early_supporter": "🚀 Premium Early Supporter",
        "certified_moderator": "🛡️ Certified Moderator",
    }
    badges = []
    for flag_name, label in badge_map.items():
        if getattr(target.public_flags, flag_name, False):
            badges.append(label)
    if target.bot:
        badges.append("🤖 Bot")
    badges_str = " • ".join(badges) if badges else "None"

    # ---- Embed ----
    embed = discord.Embed(
        title=f"👤 {target.display_name}",
        color=target.color if is_member and target.color.value else discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(
        name="📛 Username",
        value=f"`{target.name}`",
        inline=True,
    )
    embed.add_field(
        name="🆔 User ID",
        value=f"`{target.id}`",
        inline=True,
    )
    embed.add_field(
        name="🤖 Bot?",
        value="Yes" if target.bot else "No",
        inline=True,
    )

    if is_member:
        embed.add_field(
            name="📅 Account Created",
            value=f"<t:{int(target.created_at.timestamp())}:F>\n(<t:{int(target.created_at.timestamp())}:R>)",
            inline=True,
        )
        embed.add_field(
            name="📥 Joined Server",
            value=(
                f"<t:{int(target.joined_at.timestamp())}:F>\n(<t:{int(target.joined_at.timestamp())}:R>)"
                if target.joined_at else "Unknown"
            ),
            inline=True,
        )
        embed.add_field(
            name="💤 Status",
            value=status_str,
            inline=True,
        )

        if target.nick:
            embed.add_field(
                name="🏷️ Nickname",
                value=f"`{target.nick}`",
                inline=True,
            )

        # Boosting
        if target.premium_since:
            embed.add_field(
                name="🚀 Boosting Since",
                value=f"<t:{int(target.premium_since.timestamp())}:R>",
                inline=True,
            )

        # Top role
        embed.add_field(
            name="👑 Top Role",
            value=top_role_str,
            inline=True,
        )

        embed.add_field(
            name="🎭 Roles",
            value=roles_str if len(roles_str) < 1024 else roles_str[:1020] + "...",
            inline=False,
        )
    else:
        embed.add_field(
            name="📅 Account Created",
            value=f"<t:{int(target.created_at.timestamp())}:F>\n(<t:{int(target.created_at.timestamp())}:R>)",
            inline=False,
        )

    embed.add_field(
        name="🏅 Badges",
        value=badges_str,
        inline=False,
    )

    embed.set_footer(
        text=f"Requested by {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url,
    )

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
# =========================================================
# TRANSLATE COMMAND (supports every Google language)
# =========================================================

TRANSLATE_LANGS = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic",
    "hy": "Armenian", "as": "Assamese", "ay": "Aymara", "az": "Azerbaijani",
    "bm": "Bambara", "eu": "Basque", "be": "Belarusian", "bn": "Bengali",
    "bho": "Bhojpuri", "bs": "Bosnian", "bg": "Bulgarian", "ca": "Catalan",
    "ceb": "Cebuano", "ny": "Chichewa", "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)", "co": "Corsican", "hr": "Croatian",
    "cs": "Czech", "da": "Danish", "dv": "Dhivehi", "doi": "Dogri",
    "nl": "Dutch", "en": "English", "eo": "Esperanto", "et": "Estonian",
    "ee": "Ewe", "tl": "Filipino", "fi": "Finnish", "fr": "French",
    "fy": "Frisian", "gl": "Galician", "ka": "Georgian", "de": "German",
    "el": "Greek", "gn": "Guarani", "gu": "Gujarati", "ht": "Haitian Creole",
    "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew", "hi": "Hindi",
    "hmn": "Hmong", "hu": "Hungarian", "is": "Icelandic", "ig": "Igbo",
    "ilo": "Ilocano", "id": "Indonesian", "ga": "Irish", "it": "Italian",
    "ja": "Japanese", "jv": "Javanese", "kn": "Kannada", "kk": "Kazakh",
    "km": "Khmer", "rw": "Kinyarwanda", "gom": "Konkani", "ko": "Korean",
    "kri": "Krio", "ku": "Kurdish (Kurmanji)", "ckb": "Kurdish (Sorani)",
    "ky": "Kyrgyz", "lo": "Lao", "la": "Latin", "lv": "Latvian",
    "ln": "Lingala", "lt": "Lithuanian", "lg": "Luganda",
    "lb": "Luxembourgish", "mk": "Macedonian", "mai": "Maithili",
    "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam", "mt": "Maltese",
    "mi": "Maori", "mr": "Marathi", "mni-Mtei": "Meiteilon (Manipuri)",
    "lus": "Mizo", "mn": "Mongolian", "my": "Myanmar (Burmese)",
    "ne": "Nepali", "no": "Norwegian", "or": "Odia (Oriya)", "om": "Oromo",
    "ps": "Pashto", "fa": "Persian", "pl": "Polish", "pt": "Portuguese",
    "pa": "Punjabi", "qu": "Quechua", "ro": "Romanian", "ru": "Russian",
    "sm": "Samoan", "sa": "Sanskrit", "gd": "Scots Gaelic", "nso": "Sepedi",
    "sr": "Serbian", "st": "Sesotho", "sn": "Shona", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "es": "Spanish", "su": "Sundanese", "sw": "Swahili", "sv": "Swedish",
    "tg": "Tajik", "ta": "Tamil", "tt": "Tatar", "te": "Telugu",
    "th": "Thai", "ti": "Tigrinya", "ts": "Tsonga", "tr": "Turkish",
    "tk": "Turkmen", "ak": "Twi", "uk": "Ukrainian", "ur": "Urdu",
    "ug": "Uyghur", "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh",
    "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu",
}


def _build_alias_map():
    """Auto-generate an alias map from TRANSLATE_LANGS + manual extras."""
    aliases = {}

    # Auto-add the lowercase display name as an alias
    for code, name in TRANSLATE_LANGS.items():
        aliases[name.lower()] = code
        # Also add the base name without parentheses (e.g. "chinese" → zh-CN)
        base = name.split("(")[0].strip().lower()
        if base:
            aliases.setdefault(base, code)

    # Manual extras — common short names / slang
    aliases.update({
        "chinese": "zh-CN", "mandarin": "zh-CN", "cantonese": "zh-TW",
        "traditional chinese": "zh-TW", "simplified chinese": "zh-CN",
        "farsi": "fa", "tagalog": "tl", "filipino": "tl",
        "burmese": "my", "myanmar": "my",
        "kurdish": "ku", "sorani": "ckb", "kurmanji": "ku",
        "moldovan": "ro", "moldavian": "ro",
        "castilian": "es", "brazilian": "pt",
        "brazilian portuguese": "pt", "european portuguese": "pt",
        "mexican spanish": "es", "latin american spanish": "es",
        "dutch": "nl", "flemish": "nl",
        "haitian": "ht", "hawaiian": "haw",
        "gaelic": "gd", "scottish gaelic": "gd", "irish gaelic": "ga",
        "swiss german": "de",
        "egyptian arabic": "ar",
        "uk english": "en", "us english": "en", "american": "en",
        "british": "en",
        "kreyol": "ht", "creole": "ht",
        "mizo": "lus", "manipuri": "mni-Mtei", "meitei": "mni-Mtei",
        "konkani": "gom", "bhojpuri": "bho", "maithili": "mai",
        "dogri": "doi", "santali": "sa",
        "chichewa": "ny", "nyanja": "ny",
        "aymara": "ay", "quechua": "qu", "guarani": "gn",
        "twi": "ak", "akan": "ak",
    })

    return aliases


TRANSLATE_ALIASES = _build_alias_map()


def _resolve_lang(text: str):
    """Resolve a language name/code to a valid code, or None. Case-insensitive."""
    if not text:
        return None

    t = text.strip().lower()

    # 1. Direct code match (case-insensitive against TRANSLATE_LANGS keys)
    for code in TRANSLATE_LANGS:
        if code.lower() == t:
            return code

    # 2. Alias map
    if t in TRANSLATE_ALIASES:
        return TRANSLATE_ALIASES[t]

    # 3. Loose match — "chinese" matches "Chinese (Simplified)" etc.
    for alias, code in TRANSLATE_ALIASES.items():
        if t == alias or t in alias or alias in t:
            return code

    return None


@bot.hybrid_command(name="translate", aliases=["tr"], description="Translate text to another language")
@app_commands.describe(
    language="Target language (e.g. spanish, japanese, fr, english)",
    text="The text to translate (leave empty to translate a replied message)",
)
async def translate(ctx, language: str, *, text: str = None):
    # If no text provided, try to grab it from a replied-to message
    if not text:
        if not ctx.interaction and ctx.message and ctx.message.reference:
            try:
                ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                text = ref.content
            except Exception:
                text = None

    if not text:
        embed = discord.Embed(
            description=(
                "❌ Please provide text to translate.\n"
                "**Examples:**\n"
                "`R!translate spanish hello world`\n"
                "Reply to a message with `R!translate japanese`"
            ),
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if len(text) > 1500:
        embed = discord.Embed(
            description="❌ Text too long (max 1500 characters).",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    target_code = _resolve_lang(language)
    if not target_code:
        embed = discord.Embed(
            description=(
                f"❌ Unknown language `{language}`.\n"
                "Try `,,translate <lang> <text>` — most language names work:\n"
                "`english`, `spanish`, `french`, `japanese`, `chinese`, `arabic`, `hindi`, etc.\n"
                "Or use a code: `en`, `es`, `ja`, `zh-CN`."
            ),
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if ctx.interaction:
        await ctx.interaction.response.defer()

    # Google Translate free endpoint
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_code,
        "dt": "t",
        "q": text,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    embed = discord.Embed(
                        description=f"❌ Translate failed (status {resp.status}).",
                        color=discord.Color.red(),
                    )
                    if ctx.interaction:
                        return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                    return await ctx.send(embed=embed)
                data = await resp.json()
    except asyncio.TimeoutError:
        embed = discord.Embed(description="⏰ Translate timed out, try again.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"❌ Error: `{str(e)[:150]}`",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # Google's response is a nested array; join the translated segments
    try:
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        detected_code = data[2] if len(data) > 2 else "auto"
    except Exception:
        translated = None
        detected_code = "auto"

    if not translated:
        embed = discord.Embed(description="❌ Couldn't translate that text.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    detected_name = TRANSLATE_LANGS.get(detected_code, detected_code.upper() if detected_code else "Auto")
    target_name = TRANSLATE_LANGS.get(target_code, target_code.upper())

    # Trim if it got too long
    if len(translated) > 1024:
        translated = translated[:1020] + "..."

    embed = discord.Embed(
        title="🌐 Translation",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="📝 Original", value=text[:1024], inline=False)
    embed.add_field(name="✅ Translated", value=translated, inline=False)
    embed.set_footer(
        text=f"{detected_name} → {target_name} • Requested by {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url,
    )

    if ctx.interaction:
        await ctx.interaction.followup.send(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        await ctx.send(embed=embed)


@bot.hybrid_command(name="languages", aliases=["langs"], description="List supported translation languages")
async def languages(ctx):
    # Build a compact list of common languages
    common = [
        "English (`en`)", "Spanish (`es`)", "French (`fr`)", "German (`de`)",
        "Italian (`it`)", "Portuguese (`pt`)", "Russian (`ru`)", "Japanese (`ja`)",
        "Korean (`ko`)", "Chinese (`zh-CN`)", "Arabic (`ar`)", "Hindi (`hi`)",
        "Dutch (`nl`)", "Polish (`pl`)", "Turkish (`tr`)", "Greek (`el`)",
        "Hebrew (`he`)", "Swedish (`sv`)", "Norwegian (`no`)", "Danish (`da`)",
        "Finnish (`fi`)", "Ukrainian (`uk`)", "Vietnamese (`vi`)", "Thai (`th`)",
        "Indonesian (`id`)", "Filipino (`tl`)", "Bengali (`bn`)", "Urdu (`ur`)",
        "Persian (`fa`)", "Tamil (`ta`)", "Telugu (`te`)", "Romanian (`ro`)",
    ]

    embed = discord.Embed(
        title="🌐 Supported Languages",
        description=(
            "**Common languages:**\n" + " • ".join(common)
            + "\n\n**All languages:** The bot supports **every Google Translate language** "
              "(~135 languages). Just type the language name — `spanish`, `japanese`, `french`, etc. — "
              "or use a code like `en`, `es`, `ja`."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{len(TRANSLATE_LANGS)} languages supported • Example: ,,translate japanese hello")

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await ctx.send(embed=embed)
# =========================================================
# SERVERINFO COMMAND
# =========================================================

@bot.hybrid_command(name="serverinfo", aliases=["si", "server"], description="Show detailed information about this server")
async def serverinfo(ctx):
    guild = ctx.guild
    if guild is None:
        embed = discord.Embed(
            description="❌ This command can only be used inside a server.",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # ---- Counts ----
    total_members = guild.member_count or len(guild.members)
    humans = sum(1 for m in guild.members if not m.bot)
    bots = sum(1 for m in guild.members if m.bot)

    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)
    stage_channels = len(guild.stage_channels) if hasattr(guild, "stage_channels") else 0
    forum_channels = len(guild.forums) if hasattr(guild, "forums") else 0
    total_channels = text_channels + voice_channels + stage_channels + forum_channels

    total_roles = len(guild.roles) - 1  # exclude @everyone
    total_emojis = len(guild.emojis)
    animated_emojis = sum(1 for e in guild.emojis if e.animated)
    static_emojis = total_emojis - animated_emojis
    total_stickers = len(guild.stickers) if hasattr(guild, "stickers") else 0

    # ---- Boosts ----
    boost_count = guild.premium_subscription_count or 0
    boost_tier = guild.premium_tier

    tier_map = {0: "No Tier", 1: "Tier 1", 2: "Tier 2", 3: "Tier 3"}
    boost_str = f"**{tier_map.get(boost_tier, 'Unknown')}** ({boost_count} boost{'s' if boost_count != 1 else ''})"

    # ---- Verification ----
    verification_map = {
        discord.VerificationLevel.none: "None",
        discord.VerificationLevel.low: "Low",
        discord.VerificationLevel.medium: "Medium",
        discord.VerificationLevel.high: "High",
        discord.VerificationLevel.highest: "Highest",
    }
    verification_str = verification_map.get(guild.verification_level, "Unknown")

    # ---- Content filter ----
    filter_map = {
        discord.ContentFilter.disabled: "Disabled",
        discord.ContentFilter.no_role: "No Role",
        discord.ContentFilter.all_members: "All Members",
    }
    filter_str = filter_map.get(guild.explicit_content_filter, "Unknown")

    # ---- Notifications ----
    notif_map = {
        discord.NotificationLevel.all_messages: "All Messages",
        discord.NotificationLevel.only_mentions: "Only @mentions",
    }
    notif_str = notif_map.get(guild.default_notifications, "Unknown")

    # ---- Features ----
    feature_map = {
        "COMMUNITY": "🏘️ Community",
        "VERIFIED": "✅ Verified",
        "PARTNERED": "🤝 Partnered",
        "DISCOVERABLE": "🔍 Discoverable",
        "ANIMATED_ICON": "🎨 Animated Icon",
        "BANNER": "🖼️ Banner",
        "VANITY_URL": "🔗 Vanity URL",
        "NEWS": "📰 News Channels",
        "WELCOME_SCREEN_ENABLED": "👋 Welcome Screen",
        "MEMBER_VERIFICATION_GATE_ENABLED": "🚪 Membership Screening",
        "PREVIEW_ENABLED": "👀 Preview Enabled",
        "ROLE_ICONS": "🎭 Role Icons",
        "SOUNDBOARD": "🔊 Soundboard",
        "THREADS_ENABLED": "🧵 Threads",
    }
    features = []
    for f in guild.features:
        label = feature_map.get(f)
        if label:
            features.append(label)
    features_str = " • ".join(features) if features else "None"

    # ---- Embed ----
    embed = discord.Embed(
        title=f"🏠 {guild.name}",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if guild.banner:
        embed.set_image(url=guild.banner.url)

    embed.add_field(
        name="🆔 Server ID",
        value=f"`{guild.id}`",
        inline=True,
    )
    embed.add_field(
        name="👑 Owner",
        value=guild.owner.mention if guild.owner else "Unknown",
        inline=True,
    )
    embed.add_field(
        name="📅 Created",
        value=f"<t:{int(guild.created_at.timestamp())}:F>\n(<t:{int(guild.created_at.timestamp())}:R>)",
        inline=True,
    )

    embed.add_field(
        name=f"👥 Members ({total_members:,})",
        value=f"🧑 Humans: **{humans:,}**\n🤖 Bots: **{bots:,}**",
        inline=True,
    )
    embed.add_field(
        name=f"💬 Channels ({total_channels})",
        value=(
            f"📝 Text: **{text_channels}**\n"
            f"🔊 Voice: **{voice_channels}**\n"
            f"📁 Categories: **{categories}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="🎭 Roles",
        value=f"**{total_roles}**",
        inline=True,
    )

    embed.add_field(
        name="🚀 Boosts",
        value=boost_str,
        inline=True,
    )
    embed.add_field(
        name="🔒 Verification",
        value=verification_str,
        inline=True,
    )
    embed.add_field(
        name="🛡️ Content Filter",
        value=filter_str,
        inline=True,
    )

    emoji_value = f"**{total_emojis}**"
    if total_emojis > 0:
        emoji_value += f" (🎨 {animated_emojis} • 🖼️ {static_emojis})"
    if total_stickers > 0:
        emoji_value += f"\n🎯 Stickers: **{total_stickers}**"

    embed.add_field(
        name="😀 Emojis",
        value=emoji_value,
        inline=True,
    )
    embed.add_field(
        name="🔔 Notifications",
        value=notif_str,
        inline=True,
    )

    # AFK
    if guild.afk_channel:
        afk_timeout_min = guild.afk_timeout // 60
        embed.add_field(
            name="💤 AFK",
            value=f"{guild.afk_channel.mention} ({afk_timeout_min} min)",
            inline=True,
        )

    embed.add_field(
        name="✨ Features",
        value=features_str if len(features_str) < 1024 else features_str[:1020] + "...",
        inline=False,
    )

    embed.set_footer(
        text=f"Requested by {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url,
    )

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)
# =========================================================
# MODSTATS COMMAND
# =========================================================

MODSTATS_ACTIONS = ["ban", "kick", "mute", "unmute", "warn", "timeout", "clear", "purge", "slowmode", "lock", "unlock"]


def _is_staff_member(member: discord.Member) -> bool:
    """Return True if member is a mod/admin/staff."""
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_messages:
        return True
    if member.guild_permissions.manage_roles:
        return True
    if member.guild_permissions.kick_members:
        return True
    if member.guild_permissions.ban_members:
        return True
    staff_roles = {"Staff", "Moderator", "Mod", "Admin", "Administrator", "Owner", "Management", "Moderator"}
    return any(r.name in staff_roles for r in member.roles)


async def require_staff(ctx) -> bool:
    """Check if the caller is staff. Sends ephemeral error if not."""
    if not isinstance(ctx.author, discord.Member) or not _is_staff_member(ctx.author):
        embed = discord.Embed(
            title="🛡️ Permission Denied",
            description="Only **staff members** can use this command.",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            try:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass
        else:
            await ctx.send(embed=embed)
        return False
    return True


class ModStatsView(discord.ui.View):
    def __init__(self, user_id: int, guild: discord.Guild, timeout=120):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.guild = guild
        self.mode = "self"  # self | leaderboard

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This menu isn't for you. Run `/modstats` yourself.",
                ephemeral=True,
            )
            return False
        return True

    def _fetch_user_stats(self, user_id: int, guild_id: int):
        cursor.execute(
            "SELECT action, COUNT(*) FROM mod_actions WHERE moderator_id = ? AND guild_id = ? GROUP BY action",
            (user_id, guild_id),
        )
        return dict(cursor.fetchall())

    def _fetch_user_total(self, user_id: int, guild_id: int):
        cursor.execute(
            "SELECT COUNT(*) FROM mod_actions WHERE moderator_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return cursor.fetchone()[0] or 0

    def _fetch_top_mods(self, guild_id: int, limit: int = 10):
        cursor.execute(
            "SELECT moderator_id, COUNT(*) as total FROM mod_actions WHERE guild_id = ? GROUP BY moderator_id ORDER BY total DESC LIMIT ?",
            (guild_id, limit),
        )
        return cursor.fetchall()

    def build_self_embed(self):
        guild = self.guild
        stats = self._fetch_user_stats(self.user_id, guild.id)
        total = self._fetch_user_total(self.user_id, guild.id)

        embed = discord.Embed(
            title=f"🛡️ Mod Stats — {guild.get_member(self.user_id).display_name if guild.get_member(self.user_id) else 'You'}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        if total == 0:
            embed.description = "No moderation actions recorded yet for you in this server."
            return embed

        embed.description = f"**Total actions:** `{total}`"

        action_emojis = {
            "ban": "🔨",
            "kick": "👢",
            "mute": "🔇",
            "unmute": "🔊",
            "warn": "⚠️",
            "timeout": "⏱️",
            "clear": "🧹",
            "purge": "🧹",
            "slowmode": "🐢",
            "lock": "🔒",
            "unlock": "🔓",
        }

        lines = []
        for action, count in sorted(stats.items(), key=lambda x: -x[1]):
            emoji = action_emojis.get(action.lower(), "•")
            lines.append(f"{emoji} **{action.title()}**: `{count}`")

        embed.add_field(
            name="📊 Breakdown",
            value="\n".join(lines),
            inline=False,
        )

        # Last 5 actions
        cursor.execute(
            "SELECT action, target_id, reason, timestamp FROM mod_actions WHERE moderator_id = ? AND guild_id = ? ORDER BY timestamp DESC LIMIT 5",
            (self.user_id, guild.id),
        )
        recent = cursor.fetchall()
        if recent:
            recent_lines = []
            for action, target_id, reason, ts in recent:
                target_str = f"<@{target_id}>" if target_id else "—"
                ago = f"<t:{int(ts)}:R>"
                recent_lines.append(f"**{action.title()}** {target_str} • {ago}\n┗ *{(reason or 'No reason')[:60]}*")
            embed.add_field(
                name="🕒 Recent Actions",
                value="\n".join(recent_lines)[:1024],
                inline=False,
            )

        embed.set_footer(
            text=f"Requested by {guild.get_member(self.user_id).display_name if guild.get_member(self.user_id) else 'Unknown'}",
        )
        return embed

    def build_leaderboard_embed(self):
        guild = self.guild
        top = self._fetch_top_mods(guild.id, limit=10)

        embed = discord.Embed(
            title="🏆 Mod Leaderboard",
            description=f"Top moderators in **{guild.name}**",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )

        if not top:
            embed.description = "No moderation actions have been logged yet."
            return embed

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, (mod_id, total) in enumerate(top, start=1):
            member = guild.get_member(mod_id)
            name = member.display_name if member else f"User {mod_id}"
            medal = medals.get(i, f"**#{i}**")
            lines.append(f"{medal} {name} — `{total}` action{'s' if total != 1 else ''}")

        embed.add_field(
            name="📊 Top 10",
            value="\n".join(lines),
            inline=False,
        )
        embed.set_footer(text=f"Requested by {guild.get_member(self.user_id).display_name if guild.get_member(self.user_id) else 'Unknown'}")
        return embed

    def build_embed(self):
        if self.mode == "self":
            return self.build_self_embed()
        return self.build_leaderboard_embed()

    @discord.ui.button(label="👤 My Stats", style=discord.ButtonStyle.primary, row=0)
    async def self_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "self"
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🏆 Leaderboard", style=discord.ButtonStyle.success, row=0)
    async def lb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = "leaderboard"
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


@bot.hybrid_command(name="modstats", aliases=["ms", "modlog"], description="View moderation stats (staff only)")
@app_commands.describe(member="Look up another moderator's stats (staff only)")
async def modstats(ctx, member: discord.Member = None):
    if not await require_staff(ctx):
        return

    if ctx.guild is None:
        embed = discord.Embed(
            description="❌ This command can only be used inside a server.",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    target = member or ctx.author

    # If looking up someone else, show their stats directly
    if member and member.id != ctx.author.id:
        view = ModStatsView(ctx.author.id, ctx.guild)
        view.user_id = target.id
        embed = view.build_self_embed()
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
        return

    # Own stats: show with view (self/leaderboard toggle)
    view = ModStatsView(ctx.author.id, ctx.guild)
    embed = view.build_embed()

    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await ctx.send(embed=embed, view=view)
# =========================================================
# LYRICS COMMAND (lyrics.ovh — free, no API key)
# =========================================================

@bot.hybrid_command(name="lyrics", aliases=["lyric"], description="Get the lyrics of a song")
@app_commands.describe(query="Song name — use 'artist - title' for best results")
async def lyrics(ctx, *, query: str):
    if ctx.interaction:
        await ctx.interaction.response.defer()

    # Accept "artist - title" or "title artist"
    if " - " in query:
        artist, title = query.split(" - ", 1)
        artist = artist.strip()
        title = title.strip()
    else:
        # Try to guess: last word is artist? No — just try whole thing as title
        # First attempt: split by "-" without spaces
        if "-" in query:
            parts = query.split("-", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = ""
            title = query.strip()

    # If no artist, use lyrics.ovh's suggest endpoint via search
    if not artist:
        # Try search API to find the artist
        search_url = f"https://api.lyrics.ovh/suggest/{aiohttp.helpers.quote(title)}" if hasattr(aiohttp, "helpers") else None

        # Simpler: try the direct endpoint by assuming 'title' is 'artist - title' style
        # Fall back to search
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.lyrics.ovh/suggest/{title}",
                    timeout=10,
                ) as resp:
                    if resp.status == 200:
                        sugg = await resp.json()
                        data = sugg.get("data", [])
                        if data:
                            first = data[0]
                            artist = first.get("artist", {}).get("name", "")
                            title = first.get("title", title)
        except Exception:
            pass

    if not artist:
        embed = discord.Embed(
            description=(
                "❌ Couldn't figure out the artist.\n"
                "Try: `,,lyrics Artist - Title` (e.g. `,,lyrics Taylor Swift - Blank Space`)"
            ),
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # Fetch lyrics from lyrics.ovh
    url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    embed = discord.Embed(
                        description=f"❌ No lyrics found for **{artist} - {title}**.",
                        color=discord.Color.red(),
                    )
                    if ctx.interaction:
                        return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                    return await ctx.send(embed=embed)
                data = await resp.json()
    except asyncio.TimeoutError:
        embed = discord.Embed(description="⏰ Lyrics fetch timed out, try again.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            description=f"❌ Error: `{str(e)[:150]}`",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    raw_lyrics = data.get("lyrics", "").strip()
    if not raw_lyrics:
        embed = discord.Embed(
            description=f"❌ No lyrics found for **{artist} - {title}**.",
            color=discord.Color.red(),
        )
        if ctx.interaction:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # Clean up the lyrics text (remove the leading "Paroles de la chanson ... par ..." line if present)
    lines = raw_lyrics.split("\n")
    if lines and "paroles" in lines[0].lower():
        lines = lines[1:]
    clean_lyrics = "\n".join(lines).strip()

    # Cap at ~3900 chars to fit embed limit (4096)
    max_len = 3900
    truncated = False
    if len(clean_lyrics) > max_len:
        clean_lyrics = clean_lyrics[:max_len].rsplit("\n", 1)[0] + "\n\n*…lyrics truncated…*"
        truncated = True

    embed = discord.Embed(
        title=f"🎵 {title}",
        description=f"**Artist:** {artist}\n\n{clean_lyrics}",
        color=discord.Color.from_rgb(29, 185, 84),
    )
    embed.set_footer(
        text=f"Requested by {ctx.author.display_name} • Powered by lyrics.ovh",
        icon_url=ctx.author.display_avatar.url,
    )

    # If lyrics are super long, attach as a file too
    file = None
    if truncated:
        full_text = f"{title} — {artist}\n\n{raw_lyrics}"
        file = discord.File(
            _io.BytesIO(full_text.encode("utf-8")),
            filename=f"{artist} - {title} lyrics.txt",
        )
        embed.add_field(
            name="📎 Full lyrics",
            value="Full lyrics attached as a file (they were too long to fit in the embed).",
            inline=False,
        )

    if ctx.interaction:
        if file:
            await ctx.interaction.followup.send(embed=embed, file=file)
        else:
            await ctx.interaction.followup.send(embed=embed)
    else:
        if file:
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)
# =========================================================
# RUN BOT
# =========================================================

if __name__ == "__main__":
    bot.run(TOKEN)
