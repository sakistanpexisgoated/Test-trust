import os
import re
import json
import time
import random
import sqlite3
import unicodedata
import asyncio
import io
import aiohttp
from datetime import timedelta
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# OWNER & TOKEN SETUP
# =========================================================

OWNER_IDS = {1286560808528117820, 1152424544557088849}

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN was not found in your environment or .env file.")

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
    rob_claim REAL DEFAULT 0
)
""")

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

# ALLOWED LINKS TABLES
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
    """Extract domain from a URL."""
    clean = re.sub(r'^https?://', '', link)
    clean = re.sub(r'^www\.', '', clean)
    domain = clean.split('/')[0].split('?')[0].split('#')[0]
    return domain.lower() if domain else None

def format_duration(seconds: int) -> str:
    """Format seconds into readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    else:
        hours = seconds // 3600
        return f"{hours}h"

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

    if is_standalone_bot_mention:
        embed = discord.Embed(
            title="🤖 Bot Help Panel",
            description="hello i am rynx i was created by dust and gingerini my prefixs are R! and /",
            color=discord.Color.from_rgb(30, 31, 34)
        )
        embed.add_field(name="📌 Prefixes", value="`R!` or `/`", inline=True)
        embed.add_field(name="👑 Creators", value="`dust` & `gingerini`", inline=True)
        embed.add_field(name="💰 Economy", value="`balance`, `daily`, `work`, `gamble`, `dice`, `slots`, `crime`, `rob`, `pay`, `deposit`, `withdraw`", inline=False)
        embed.add_field(name="🎉 Fun & Social", value="`cf`, `8ball`, `gayrate`, `pp`, `iq`, `roast`, `kiss`, `gif`, `hack`, `brainrot_dice`, `marry`, `divorce`, `mock`", inline=False)
        embed.add_field(name="🛡️ Moderation & Utility", value="`afk`, `ban`, `unban`, `kick`, `mute`, `unmute`, `warn`, `clear`, `slowmode`, `poll`, `say`, `embed`, `snipe`, `editsnipe`, `avatar`, `help`, `trollpanel`, `whitelist`, `unwhitelist`, `ghostping`, `fakenuke`, `blacklist`, `serverblacklist`", inline=False)
        await message.channel.send(embed=embed)
        return

    # --- AFK MENTION CHECK ---
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

    # --- AFK RETURN CHECK ---
    if message.author.id in afk_users:
        data = afk_users.pop(message.author.id)
        duration_sec = int(time.time() - data["time"])
        
        if duration_sec < 60:
            dur_str = f"{duration_sec} seconds"
        elif duration_sec < 3600:
            dur_str = f"{duration_sec // 60} minutes"
        else:
            dur_str = f"{duration_sec // 3600} hours"

        embed = discord.Embed(
            description=f"Welcome back, {message.author.mention}! I removed your AFK. You were AFK for {dur_str}.",
            color=discord.Color.from_rgb(30, 31, 34)
        )

        if data["mentions"]:
            mentions_text = []
            for m in data["mentions"]:
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

        await message.channel.send(embed=embed)

    # --- LINK FILTERING ---
    if message.guild and link_check_enabled.get(message.guild.id, False):
        url_pattern = r'https?://[^\s]+|www\.[^\s]+'
        links = re.findall(url_pattern, message.content)
        
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

    # --- THIS MUST BE THE VERY LAST LINE ---
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
    embed.add_field(name="👑 Admin", value="`sync`, `goon`, `nuke`, `masscreate`, `setup`, `backup`, `blacklist`, `trollpanel`, `whitelist`, `ghostping`, `fakenuke`", inline=False)
    embed.add_field(name="💰 Admin Pay", value="`adminpay`, `adminset`, `adminsetbank`, `adminrob`, `adminrobamount`", inline=False)
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
    cursor.execute("SELECT wallet, bank FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO users (user_id, wallet, bank, daily_claim, weekly_claim, work_claim, crime_claim, rob_claim) VALUES (?, 100, 0, 0, 0, 0, 0, 0)",
            (user_id,)
        )
        db.commit()
        return 100, 0
    return row[0], row[1]

def update_wallet(user_id, amount):
    wallet, bank = get_user_econ(user_id)
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
# ERROR HANDLER & COMMAND LISTINGS
# =========================================================

COMMAND_USAGE = {
    "help": ",,help",
    "afk": ",,afk [reason]",
    "ban": ",,ban <member> [reason]",
    "unban": ",,unban <user_id>",
    "mute": ",,mute <member> [duration] [reason]",
    "kick": ",,kick <member> [reason]",
    "warn": ",,warn <member> [reason]",
    "pay": ",,pay <member> <amount>",
    "gamble": ",,gamble <amount>",
    "dice": ",,dice <amount>",
    "slots": ",,slots <amount>",
    "crime": ",,crime",
    "rob": ",,rob <member>",
    "work": ",,work",
    "deposit": ",,deposit <amount>",
    "withdraw": ",,withdraw <amount>",
    "marry": ",,marry <member>",
    "divorce": ",,divorce",
    "avatar": ",,avatar [member]",
    "cf": ",,cf",
    "gayrate": ",,gayrate [member]",
    "8ball": ",,8ball <question>",
    "pp": ",,pp [member]",
    "roast": ",,roast [member1] [member2] [member3]",
    "iq": ",,iq [member]",
    "kiss": ",,kiss <member>",
    "gif": ",,gif <search_term>",
    "snipe": ",,snipe [number]",
    "editsnipe": ",,editsnipe",
    "hack": ",,hack <member>",
    "poll": ",,poll <question>",
    "say": ",,say <message>",
    "embed": ",,embed <title> | <description>",
    "clear": ",,clear <amount>",
    "purge": ",,purge <amount>",
    "slowmode": ",,slowmode <seconds>",
    "brainrot_dice": ",,brainrot_dice [amount]",
    "blacklist": ",,blacklist <user_id_or_mention> [reason]",
    "unblacklist": ",,unblacklist <user_id>",
    "serverblacklist": ",,serverblacklist <guild_id> [reason]",
    "serverunblacklist": ",,serverunblacklist <guild_id>",
    "trollpanel": ",,trollpanel",
    "whitelist": ",,whitelist <member>",
    "unwhitelist": ",,unwhitelist <member>",
    "ghostping": ",,ghostping <member>",
    "mock": ",,mock <text>",
    "fakenuke": ",,fakenuke [member]",
    "masscreate": ",,masscreate <count> <name>",
    "setup": ",,setup [style]"
}

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original

    if isinstance(error, commands.CommandNotFound):
        return

    cmd_name = ctx.command.name if ctx.command else "command"

    if isinstance(error, commands.MissingRequiredArgument):
        arg_name = error.param.name
        usage_str = COMMAND_USAGE.get(cmd_name, f",,{cmd_name} <{arg_name}>")
        embed = discord.Embed(
            description=(
                f"```\n{usage_str}\n"
                f"{' ' * max(0, usage_str.find('<') if '<' in usage_str else 0)}"
                f"^^^^^^^^^\n\n"
                f"{arg_name} is a required argument that is missing.\n```"
            ),
            color=discord.Color.from_rgb(47, 49, 54)
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BadArgument):
        usage_str = COMMAND_USAGE.get(cmd_name, f",,{cmd_name}")
        embed = discord.Embed(
            description=f"```\n{usage_str}\n\nInvalid argument. Please check the command format.\n```",
            color=discord.Color.from_rgb(47, 49, 54)
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="🛡️ Permission Denied",
            description="Only **server administrators or moderators** can use this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    if isinstance(error, commands.BotMissingPermissions):
        embed = discord.Embed(
            title="Bot is Missing Permissions",
            description="I don't have the Discord permissions required to perform this command.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            try:
                return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                return
        return await ctx.send(embed=embed)

    embed = discord.Embed(
        title="⚠️ Command Error",
        description=f"Something went wrong: `{str(error)[:900]}`",
        color=discord.Color.red()
    )
    if ctx.interaction:
        try:
            return await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            return
    return await ctx.send(embed=embed)

# =========================================================
# ALLOWED LINKS COMMANDS - UPDATED WITH SIMPLER UI
# =========================================================

@bot.hybrid_command(name="allowed", aliases=["links"], description="Manage allowed links for the server")
@commands.has_permissions(administrator=True)
async def allowed(ctx, action: str = None, *, link: str = None):
    """
    Simplified Usage:
    R!allowed link <link> - Add a link to whitelist
    R!allowed unlink <link> - Remove a link from whitelist
    R!allowed list - Show all allowed links
    R!allowed enable - Turn on link filtering
    R!allowed disable - Turn off link filtering
    R!allowed time <duration> - Set mute time (e.g. 10m, 30m, 1h)
    """
    guild_id = ctx.guild.id
    
    if action is None:
        embed = discord.Embed(
            title="🔗 Allowed Links",
            description="**Commands:**\n`R!allowed link <url>` - Add a link\n`R!allowed unlink <url>` - Remove a link\n`R!allowed list` - Show allowed links\n`R!allowed enable` - Turn ON filtering\n`R!allowed disable` - Turn OFF filtering\n`R!allowed time <duration>` - Set mute time",
            color=discord.Color.blue()
        )
        # Check current status
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
    # permission / whitelist check
    if not is_troll_whitelisted(ctx.author.id):
        embed = discord.Embed(description="You are not whitelisted to use this troll command.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    # sanitize times
    try:
        times = int(times)
    except Exception:
        times = 1
    times = max(1, min(100, times))

    # delete invoking message for prefix usage if possible
    if not ctx.interaction and ctx.message:
        try:
            await ctx.message.delete()
        except Exception:
            pass

    # prepare view + embed
    view = GhostPingControlView(owner_id=ctx.author.id)
    embed = discord.Embed(
        title=f"👻 Ghost pinging {member.display_name} x{times}...",
        description=f"Progress: 0/{times}",
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Press Stop to cancel the ghost pings.")

    # Send status: ephemeral for slash, channel message for prefix
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

    # INTERVAL SET TO 1 SECOND
    delay = 1.0

    async def do_send():
        sent = 0
        content = f"{member.mention} {message}".strip() or f"{member.mention}"
        for i in range(times):
            if view.stop_event.is_set():
                break
            try:
                ping_msg = await ctx.channel.send(content)
                # delete right away (best-effort)
                try:
                    await ping_msg.delete()
                except Exception:
                    pass
                sent += 1
            except Exception:
                # stop on repeated send failures
                break

            # update status embed after each sent ping
            try:
                embed.description = f"Progress: {sent}/{times}"
                embed.set_footer(text=f"Press Stop to cancel — sent {sent}/{times}")
                await status_msg.edit(embed=embed, view=view)
            except Exception:
                pass

            # wait 1 second between pings (balances rate and load)
            if i != times - 1:
                await asyncio.sleep(delay)

        # Finalize status
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
    
@bot.hybrid_command(name="mock", description="Mock text in sPoNgEbOb case")
async def mock(ctx, *, text: str):
    mocked_text = "".join(
        c.upper() if i % 2 == 0 else c.lower()
        for i, c in enumerate(text)
    )
    embed = discord.Embed(description=f"🥺 {mocked_text}", color=discord.Color.gold())
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass
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
        description=f"Thank you {target.mention} for nuking this server the channels will be deleted soon huzzs.",
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
# EXPERIMENTAL MASS CREATE CHANNELS COMMAND
# =========================================================

@bot.hybrid_command(name="masscreate", description="Creates multiple channels for experimental purposes.")
@app_commands.describe(
    count="Number of channels to create (1-50)",
    name="Base name for the channels"
)
@app_commands.checks.has_permissions(administrator=True)
async def masscreate(ctx, count: int, name: str):
    if count < 1 or count > 50:
        embed = discord.Embed(description="Please choose a count between 1 and 50.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed, delete_after=5)

    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)
    else:
        if ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass

    guild = ctx.guild
    success_count = 0

    try:
        for i in range(1, count + 1):
            channel_name = f"{name}-{i}"
            await guild.create_text_channel(name=channel_name)
            success_count += 1
            await asyncio.sleep(0.3)

        success_msg = f"Successfully created **{success_count}** channels with the base name **{name}**!"
        embed = discord.Embed(description=f"{success_msg}", color=discord.Color.green())
        
        if ctx.interaction:
            await ctx.interaction.edit_original_response(content=f"{success_msg}")
        else:
            await ctx.send(embed=embed, delete_after=10)
    except Exception as e:
        print(f"Error during mass channel creation: {e}")
        error_msg = f"Completed with errors. Created **{success_count}** channels before encountering an issue."
        if ctx.interaction:
            await ctx.interaction.edit_original_response(content=f"{error_msg}")
        else:
            await ctx.send(embed=discord.Embed(description=f"{error_msg}", color=discord.Color.red()))

# =========================================================
# ECONOMY UI & COMMANDS
# =========================================================

class WithdrawModal(discord.ui.Modal, title="Withdraw Money"):
    amount = discord.ui.TextInput(label="Amount (or 'all')", placeholder="e.g. 500 or all", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        wallet, bank = get_user_econ(interaction.user.id)
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

        new_wallet, new_bank = get_user_econ(interaction.user.id)
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
        wallet, bank = get_user_econ(interaction.user.id)
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

        new_wallet, new_bank = get_user_econ(interaction.user.id)
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
        
        wallet, bank = get_user_econ(self.target_id)
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
    wallet, bank = get_user_econ(target.id)
    net = wallet + bank
    rank = get_global_rank(target.id)
    
    embed = discord.Embed(color=discord.Color.from_rgb(30, 31, 34))
    embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
    embed.title = "Balance"
    embed.add_field(name="Wallet", value=f"🪙 {wallet:,}", inline=True)
    embed.add_field(name="Bank", value=f"🪙 {bank:,}", inline=True)
    embed.add_field(name="Net", value=f"🪙 {net:,}", inline=False)
    embed.add_field(name="Global Rank", value=f"#{rank}", inline=False)
    
    view = BalanceView(target.id)
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="deposit", aliases=["dep"], description="Deposit money into your bank")
async def deposit(ctx, amount: str):
    user_id = ctx.author.id
    wallet, bank = get_user_econ(user_id)
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
    wallet, bank = get_user_econ(user_id)
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
    embed = discord.Embed(description=f"Successfully claimed daily reward of **${reward:,}**!", color=discord.Color.green())
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

@bot.hybrid_command(name="gamble", aliases=["bet"], description="Gamble your money")
async def gamble(ctx, amount: int = 100):
    user_id = ctx.author.id
    wallet, bank = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="❌ Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="❌ Not enough money in wallet.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    if random.random() < 0.45:
        update_wallet(user_id, amount)
        embed = discord.Embed(description=f"🎉 You won **${amount:,}**!", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"😢 You lost **${amount:,}**.", color=discord.Color.red())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="dice", description="Roll dice against the bot for money")
async def dice(ctx, amount: int):
    user_id = ctx.author.id
    wallet, bank = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="❌ Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="❌ You don't have enough money in your wallet for this bet.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    is_rigged = troll_settings.get(user_id, {}).get("dice", False)
    if is_rigged:
        user_roll1, user_roll2 = 6, 6
        user_total = 12
        bot_roll1, bot_roll2 = 1, 1
        bot_total = 2
    else:
        user_roll1 = random.randint(1, 6)
        user_roll2 = random.randint(1, 6)
        user_total = user_roll1 + user_roll2

        bot_roll1 = random.randint(1, 6)
        bot_roll2 = random.randint(1, 6)
        bot_total = bot_roll1 + bot_roll2

    embed = discord.Embed(title="🎲 Dice Roll Battle", color=discord.Color.blurple())
    embed.add_field(name=f"{ctx.author.display_name}'s Roll", value=f"🎲 {user_roll1} + 🎲 {user_roll2} = **{user_total}**", inline=True)
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

@bot.hybrid_command(name="pay", description="Pay money to another user")
async def pay(ctx, member: discord.Member, amount: int):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="You cannot pay yourself.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if amount <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    wallet, _ = get_user_econ(ctx.author.id)
    if wallet < amount:
        embed = discord.Embed(description="You don't have enough money in your wallet you brokie nigga.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    update_wallet(ctx.author.id, -amount)
    update_wallet(member.id, amount)
    embed = discord.Embed(description=f"💸 Successfully paid **${amount:,}** to {member.mention}!", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="slots", description="Play the slot machine")
async def slots(ctx, amount: int):
    user_id = ctx.author.id
    wallet, _ = get_user_econ(user_id)
    if amount <= 0:
        embed = discord.Embed(description="Amount must be greater than zero.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if wallet < amount:
        embed = discord.Embed(description="You don't have enough money in your wallet broke nigga.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    is_rigged = troll_settings.get(user_id, {}).get("slots", False)
    if is_rigged:
        result = ["7️⃣", "7️⃣", "7️⃣"]
    else:
        symbols = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣"]
        result = [random.choice(symbols) for i in range(3)]
    
    if result[0] == result[1] == result[2]:
        payout = amount * 10
        update_wallet(user_id, payout)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n🎉 Jackpot! You won **${payout:,}**!", color=discord.Color.green())
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        payout = amount * 2
        update_wallet(user_id, payout)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n✨ Nice! Two matching symbols. You won **${payout:,}**!", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"🎰 | {' | '.join(result)} | 🎰\n😢 No match. You lost **${amount:,}**.", color=discord.Color.red())

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

    outcomes = [
        ("Robbed a convenience store", 250, True),
        ("Hacked a corporate database", 400, True),
        ("Stole a luxury car", 600, True),
        ("Got caught shoplifting", 200, False),
        ("Fumbled the heist and got fined", 350, False)
    ]
    event, amount, success = random.choice(outcomes)
    
    if success:
        update_wallet(user_id, amount)
        embed = discord.Embed(description=f"🦹 Successfully **{event}** and made **${amount:,}**!", color=discord.Color.green())
    else:
        update_wallet(user_id, -amount)
        embed = discord.Embed(description=f"🚨 You failed while trying to **{event}** and paid a fine of **${amount:,}**!", color=discord.Color.red())

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

    wallet, _ = get_user_econ(user_id)
    if wallet < 200:
        embed = discord.Embed(description="You need at least **$200** in your wallet to attempt a robbery brokie.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    target_wallet, _ = get_user_econ(member.id)
    if target_wallet < 100:
        embed = discord.Embed(description=f"**{member.display_name}** doesn't have enough money in their wallet to rob.", color=discord.Color.red())
        return await ctx.send(embed=embed)

    cursor.execute("UPDATE users SET rob_claim = ? WHERE user_id = ?", (current_time, user_id))
    db.commit()

    if random.random() < 0.4:
        stolen = random.randint(50, min(target_wallet, 500))
        update_wallet(user_id, stolen)
        update_wallet(member.id, -stolen)
        embed = discord.Embed(description=f"🥷 You successfully snuck up on {member.mention} and stole **${stolen:,}** from their wallet!", color=discord.Color.green())
    else:
        fine = 150
        update_wallet(user_id, -fine)
        embed = discord.Embed(description=f"🚨 You got caught trying to rob {member.mention} and had to pay a fine of **${fine:,}**!", color=discord.Color.red())

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
    embed = discord.Embed(description=f"👋 {ctx.author.mention} is now AFK: {reason}", color=discord.Color.blurple())
    await ctx.send(embed=embed)

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

@ban.error
async def ban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Ban Members permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Ban Members permission.")

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
    embed.set_footer(text=f"tottaly real trust by {ctx.author.display_name}")

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
# MUTE COMMAND
# =========================================================

@bot.hybrid_command(name="mute", description="Mute a member")
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot mute the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot mute the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot mute a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot mute a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot mute them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot mute them.")

    seconds = parse_duration(duration)
    if not seconds:
        if ctx.interaction:
            return await ctx.interaction.response.send_message("❌ Invalid duration format. Use e.g. `10s`, `5m`, `2h`, `1d`.", ephemeral=True)
        return await ctx.send("❌ Invalid duration format. Use e.g. `10s`, `5m`, `2h`, `1d`.")
    
    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason)
        
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

@mute.error
async def mute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Mute Perms.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Mute permissions.")

# =========================================================
# UNMUTE COMMAND
# =========================================================

@bot.hybrid_command(name="unmute", description="Remove a member's timeout")
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot unmute the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot unmute the server owner.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot unmute them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot unmute them.")
    
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        
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

@unmute.error
async def unmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Manage Roles permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Manage Roles permission.")
    if isinstance(error, commands.MemberNotFound):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Member not found.", ephemeral=True)
        else:
            await ctx.send(f"❌ Member not found.")

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

@bot.hybrid_command(name="snipe", description="View deleted messages from the channel")
async def snipe(ctx, amount: int = 1):
    channel_id = ctx.channel.id
    if channel_id not in sniped_messages or not sniped_messages[channel_id]:
        embed = discord.Embed(description="⚠️ There are no deleted messages to snipe in this channel.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    messages = sniped_messages[channel_id]
    count = max(1, min(amount, len(messages)))
    target_msgs = messages[-count:]
    target_msgs.reverse()

    embed = discord.Embed(
        title=f"🎯 Sniped Message(s)",
        description=f"Showing the last **{count}** deleted message(s) in this channel.",
        color=discord.Color.from_rgb(47, 49, 54)
    )
    
    for idx, snipe_data in enumerate(target_msgs, 1):
        content = snipe_data["content"] or "*No text content*"
        if snipe_data["attachments"]:
            content += f"\n🔗 **Attachment:** [View File]({snipe_data['attachments'][0]})"
        
        author = snipe_data["author"]
        embed.add_field(
            name=f"💬 Message #{idx} • {author}",
            value=f"> {content}",
            inline=False
        )

    embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
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
        description=f"Successfully deleted **{removed}** message(s) from this channel.",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Purged by {ctx.author.display_name}")
    await ctx.send(embed=embed, delete_after=5)

@bot.hybrid_command(name="clear", description="Clear a number of messages")
async def clear(ctx, amount: int):
    await _run_purge(ctx, amount)

@bot.hybrid_command(name="purge", description="Mass-delete messages (Admin/Moderator only)")
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

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.green, row=4)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.host_id:
            embed = discord.Embed(description="You cannot join your own game as the opponent!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if self.p2_id is not None:
            embed = discord.Embed(description="🎭 The opponent spot is already filled!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if self.amount > 0:
            wallet, _ = get_user_econ(interaction.user.id)
            if wallet < self.amount:
                embed = discord.Embed(description="🤣 You don't have enough money in your wallet to join this bet.", color=discord.Color.red())
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        self.p2_id = interaction.user.id
        self.p2_brainrot.disabled = False
        self.p2_color.disabled = False
        button.label = f"Joined: {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        button.disabled = True

        embed = interaction.message.embeds[0]
        embed.description = f"💸 **Brainrot Dice Showdown**\nHost: <@{self.host_id}>\nOpponent: <@{self.p2_id}>\n\nBoth players, select your Brainrot & Color from the dropdowns, then click **🎲 Roll!**"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎲 Roll!", style=discord.ButtonStyle.success, row=4)
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
        wallet, _ = get_user_econ(ctx.author.id)
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

SETUP_USER_ID = 1475693209949569024

SETUP_ROLES = [
    ("Owner", discord.Color.red()),
    ("Admin", discord.Color.orange()),
    ("Moderator", discord.Color.blue()),
    ("Staff", discord.Color.purple()),
    ("Giveaway Manager", discord.Color.gold()),
    ("Member", discord.Color.green()),
]

SETUP_STRUCTURE = {
    "〈🔒〉・Staff Only": [
        ("Staff-Rules", "📖", "text"),
        ("Staff-Announcements", "📢", "text"),
        ("Staff-Chat", "💬", "text"),
        ("Staff-Promotions", "🎉", "text"),
        ("Staff-Demotions", "📉", "text"),
        ("Applications", "📄", "text"),
        ("Staff-Vc", "🎙️", "voice"),
    ],

    "〈👋〉・Arrivals": [
        ("Roles-Info", "🎭", "text"),
        ("Welcome", "👋", "text"),
        ("Goodbye", "🪽", "text"),
    ],

    "〈⚠️〉・Important": [
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

    "〈🎁〉・Giveaways": [
        ("Giveaways", "🎁", "text"),
        ("Giveaway-Vouches", "🏅", "text"),
        ("Events", "🎉", "text"),
    ],

    "〈🤝〉・Middleman": [
        ("Middleman", "🤝", "text"),
        ("Middleman-Vouches", "⭐", "text"),
        ("Middleman-Ticket", "🎟️", "text"),
    ],

    "〈💬〉・General": [
        ("General-Chat", "💬", "text"),
        ("Media", "📸", "text"),
        ("Commands", "⚙️", "text"),
        ("Steals", "💰", "text"),
        ("Levels", "📊", "text"),
        ("Boosters-Chat", "🚀", "text"),
        ("Suggestions", "🛠️", "text"),
    ],

    "〈💣〉・Reverse Beanie": [
        ("Beanie-Rules", "📖", "text"),
        ("Beanie-Announcements", "📢", "text"),
        ("Beanie-Chat", "💬", "text"),
        ("Beanie-Scripts", "📁", "text"),
        ("Usernames", "🔍", "text"),
    ],

    "〈💰〉・Trading": [
        ("Trading-Fourm", "💸", "text"),
        ("Trading", "💰", "text"),
        ("Mid-Trading", "⭐", "text"),
        ("Og-Trading", "💎", "text"),
        ("Cross-Trading", "📌", "text"),
        ("Duel-Requests", "⚔️", "text"),
        ("Win-Or-Loss", "⚖️", "text"),
        ("Vouches", "✅", "text"),
    ],

    "〈🎬〉・Content Creators": [
        ("Creators-Rules", "📖", "text"),
        ("Creators-Announcements", "📢", "text"),
        ("Creators-Chat", "💬", "text"),
        ("Creators-Ideas", "🛠️", "text"),
    ],

    "〈🔊〉・Voice Chats": [
        ("Create-Vc", "🔑", "voice"),
        ("Owners-Vc", "👑", "voice"),
        ("General-Vc", "🎙️", "voice"),
        ("Trading-Vc", "💼", "voice"),
        ("Pvp-Vc", "⚔️", "voice"),
        ("Sab-Vc", "🎙️", "voice"),
    ],
}

def format_setup_channel(emoji, base_name, separator):
    return f"{emoji}{separator}{base_name}"

@bot.hybrid_command(
    name="setup",
    description="Create the server layout and choose a channel naming style"
)
async def setup(ctx, style: str = "┃"):
    valid_styles = {"┃", "・", "-・-"}
    style = style.strip()
    if style not in valid_styles:
        return await ctx.send("Invalid style. Use `┃`, `・`, or `-・-`.")

    interaction = ctx.interaction
    if interaction is not None:
        await interaction.response.defer(ephemeral=True)

    if ctx.author.id != SETUP_USER_ID:
        return await ctx.send("😂 You are not allowed to use `,,setup`.", ephemeral=interaction is not None)

    guild = ctx.guild
    if guild is None:
        return await ctx.send("This command can only be used inside a server.", ephemeral=interaction is not None)

    async def setup_error(message):
        if interaction is not None:
            return await interaction.followup.send(message, ephemeral=True)
        return await ctx.send(message)

    created_roles = 0
    created_channels = 0
    existing_channels = 0
    roles = {}

    for role_name, color in SETUP_ROLES:
        role = discord.utils.get(guild.roles, name=role_name)

        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name,
                    color=color,
                    reason=f"/setup used by {ctx.author}"
                )
                created_roles += 1
            except discord.Forbidden:
                return await setup_error("⚠️ I need **Manage Roles** permission to create the roles.")

        roles[role_name] = role

    categories = {}

    for category_name, channel_list in SETUP_STRUCTURE.items():
        category = discord.utils.get(
            guild.categories,
            name=category_name
        )

        if category is None:
            try:
                category = await guild.create_category(
                    category_name,
                    reason=f"/setup used by {ctx.author}"
                )
            except discord.Forbidden:
                return await setup_error("I need **Manage Channels** permission.")

        categories[category_name] = category

        for base_name, emoji, channel_type in channel_list:
            channel_name = format_setup_channel(emoji, base_name, style)
            existing = discord.utils.get(category.channels, name=channel_name)

            if existing is None:
                try:
                    if channel_type == "voice":
                        await guild.create_voice_channel(name=channel_name, category=category, reason=f"/setup used by {ctx.author}")
                    else:
                        await guild.create_text_channel(name=channel_name, category=category, reason=f"/setup used by {ctx.author}")
                    created_channels += 1
                except discord.Forbidden:
                    return await setup_error("I need **Manage Channels** permission.")
            else:
                existing_channels += 1

    success_text = f"Server setup complete! Created **{created_roles}** roles and **{created_channels}** new channels ({existing_channels} already existed)."
    if interaction is not None:
        await interaction.followup.send(success_text, ephemeral=True)
    else:
        await ctx.send(success_text)

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

        wallet, _ = get_user_econ(interaction.user.id)
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

        wallet, _ = get_user_econ(interaction.user.id)
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
            await interaction.response.send_message("✅ You've been entered into the giveaway! Good luck!", ephemeral=True)
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
            description=f"{winner_mentions} won the giveaway of **{prize}**!",
            color=discord.Color.green()
        )
        result_embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        result_embed.add_field(name="Entrants", value=str(len(entrants)), inline=True)

        for uid in winners:
            try:
                user = await bot.fetch_user(uid)
                dm_text = f"congrats {user.mention} u won the giveaway **{prize}** in **{channel.guild.name}** pls check the server or ping the host <@{host_id}> in the server to claim ur giveaway!"
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
async def sync(ctx):
    if ctx.author.id not in {1286560808528117820, 1531701933033787416}:
        return await ctx.send("❌ Only bot owners can use this.")
    
    try:
        await bot.tree.sync()
        await ctx.send("✅ Commands have been synced globally!")
    except Exception as e:
        await ctx.send(f"❌ Sync failed: {e}")

# =========================================================
# GOON COMMAND
# =========================================================

@bot.hybrid_command(name="goon", description="Goon on someone as a joke!")
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
    embed.set_image(url="https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZTA3c3Jwd2pxa2VrdGZneHVpd3JueWlyeDY5c2RsNDVkZnlzZm5lZSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/pWDC0SjDvj5mHd00Lx/giphy.gif")

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
async def backup(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("Use `/backup create`, `/backup info`, or `/backup load`.", ephemeral=True)

@backup.command(name="create", description="Create a backup of this server (channels, roles, settings, and messages)")
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

# =========================================================
# NUKE COMMAND
# =========================================================

class NukeModal(discord.ui.Modal, title="☢️ NUKE CONFIRMATION"):
    confirm = discord.ui.TextInput(
        label="Type YES to confirm nuke",
        placeholder="YES",
        required=True,
        max_length=3
    )
    kick = discord.ui.TextInput(
        label="Type yes to kick everyone, or no to skip",
        placeholder="yes or no",
        required=True,
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id not in {1286560808528117820, 1531701933033787416}:
            embed = discord.Embed(description="Only the bot owners can use this command.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if self.confirm.value.upper() != "YES":
            embed = discord.Embed(
                title="❌ Cancelled",
                description="You did not type YES. Command cancelled.",
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await self.run_nuke(interaction, self.kick.value.lower())

    async def run_nuke(self, interaction: discord.Interaction, kick: str):
        guild = interaction.guild
        if not guild:
            embed = discord.Embed(description="This command can only be used in a server.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed, ephemeral=True)

        me = guild.me
        if not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_webhooks or not me.guild_permissions.manage_guild:
            embed = discord.Embed(
                description="I need Manage Channels, Manage Roles, Manage Webhooks, and Manage Server permissions to nuke.",
                color=discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        kick_enabled = kick == "yes"
        if kick_enabled and not me.guild_permissions.kick_members:
            embed = discord.Embed(
                description="I need Kick Members permission to kick everyone. Use 'no' to skip kicking.",
                color=discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        kick_status = "✅ ENABLED - Kicking all members" if kick_enabled else "❌ DISABLED - Skipping kicks"
        
        status_msg = await interaction.followup.send(embed=discord.Embed(
            description=f"☢️ **NUKE INITIATED**\n• Deleting all channels, roles, webhooks...\n• Kick: {kick_status}",
            color=discord.Color.orange()
        ))

        deleted_channels = 0
        deleted_roles = 0
        deleted_webhooks = 0
        created_channels = 0
        kicked_members = 0
        errors = []

        if kick_enabled:
            try:
                for member in guild.members:
                    if member.id == bot.user.id:
                        continue
                    if member.id == interaction.user.id:
                        continue
                    try:
                        await member.kick(reason="Nuke command executed - Server destroyed")
                        kicked_members += 1
                    except Exception as e:
                        errors.append(f"Kick {member.name}: {str(e)[:50]}")
            except Exception as e:
                errors.append(f"Kick all members failed: {str(e)[:50]}")

        try:
            await guild.edit(name="S҉i҉g҉g҉a҉ ҉n҉e҉x҉")
        except Exception as e:
            errors.append(f"Server rename failed: {str(e)[:50]}")

        try:
            webhooks = await guild.fetch_webhooks()
            for webhook in webhooks:
                try:
                    await webhook.delete(reason="Nuke command executed")
                    deleted_webhooks += 1
                except Exception as e:
                    errors.append(f"Webhook {webhook.name}: {str(e)[:50]}")
        except Exception as e:
            errors.append(f"Webhook fetch: {str(e)[:50]}")

        try:
            for role in guild.roles:
                if role.id == guild.roles.everyone.id:
                    continue
                try:
                    await role.delete(reason="Nuke command executed")
                    deleted_roles += 1
                except Exception as e:
                    errors.append(f"Role {role.name}: {str(e)[:50]}")
        except Exception as e:
            errors.append(f"Role deletion: {str(e)[:50]}")

        try:
            for channel in guild.channels:
                try:
                    await channel.delete(reason="Nuke command executed")
                    deleted_channels += 1
                except Exception as e:
                    errors.append(f"Channel {channel.name}: {str(e)[:50]}")
        except Exception as e:
            errors.append(f"Channel deletion: {str(e)[:50]}")

        spam_text = """# say gernic 67 time ┃ <@everyone> <@here> ┃ discord.gg/porn ┃ https://tenor.com/dJqMW8ku92x.gif"""

        async def create_role_and_spam(index):
            try:
                role = await guild.create_role(
                    name="ʂơཞŋ ɬɛҳ",
                    color=discord.Color.from_rgb(255, 0, 0),
                    reason="Nuke command executed"
                )
                channel = await guild.create_text_channel(
                    name=f"𝖌𝖆𝖞𝖘-𝖘𝖊𝖗𝖛𝖊𝖗-𝖌𝖊𝖙𝖘-𝖓𝖚𝖐𝖊𝖉",
                    reason="Nuke command executed"
                )
                try:
                    await interaction.user.add_roles(role, reason="Nuke command executed")
                except:
                    pass
                for _ in range(100):
                    try:
                        await channel.send(
                            f"@everyone\n{spam_text}",
                            allowed_mentions=discord.AllowedMentions(everyone=True)
                        )
                    except Exception:
                        break
                return True
            except Exception:
                return False

        tasks = []
        for i in range(1, 101):
            tasks.append(create_role_and_spam(i))
            if len(tasks) >= 50:
                await asyncio.gather(*tasks, return_exceptions=True)
                created_channels += len(tasks)
                tasks = []

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            created_channels += len(tasks)

        result_embed = discord.Embed(
            title="☢️ NUKE COMPLETE",
            description=(
                f"**Server Renamed to:** S҉i҉g҉g҉a҉ ҉n҉e҉x҉\n\n"
                f"**Kick Option:** {kick_status}\n"
                f"**Members Kicked:** {kicked_members}\n\n"
                f"**Deleted:**\n"
                f"• Channels: {deleted_channels}\n"
                f"• Roles: {deleted_roles}\n"
                f"• Webhooks: {deleted_webhooks}\n\n"
                f"**Created:** {created_channels} channels with 100 pings each.\n"
                f"**Roles Created:** 100 x ʂơཞŋ ɬɛҳ\n\n"
                f"**Nuke executed by:** {interaction.user.mention}"
            ),
            color=discord.Color.red()
        )
        if errors:
            result_embed.add_field(
                name="⚠️ Errors encountered",
                value="\n".join(errors[:5]) + (f"\n... and {len(errors)-5} more" if len(errors) > 5 else ""),
                inline=False
            )

        await status_msg.edit(embed=result_embed)

@bot.tree.command(name="nuke", description="Delete ALL channels, roles, webhooks, and optionally kick ALL members")
async def nuke(interaction: discord.Interaction):
    if interaction.user.id not in {1152424544557088849, 1531701933033787416}:
        embed = discord.Embed(description="Only the bot owners can use this command.", color=discord.Color.red())
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    await interaction.response.send_modal(NukeModal())
    
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
    ]
}

country_flags["easy"].extend([
    {"name": "Poland", "flag": "🇵🇱"},
    {"name": "Ukraine", "flag": "🇺🇦"},
    {"name": "Romania", "flag": "🇷🇴"},
    {"name": "Bulgaria", "flag": "🇧🇬"},
    {"name": "Serbia", "flag": "🇷🇸"},
    {"name": "Croatia", "flag": "🇭🇷"},
    {"name": "Czech Republic", "flag": "🇨🇿"},
    {"name": "Hungary", "flag": "🇭🇺"},
    {"name": "Slovakia", "flag": "🇸🇰"},
    {"name": "Slovenia", "flag": "🇸🇮"},
])

country_flags["medium"].extend([
    {"name": "Morocco", "flag": "🇲🇦"},
    {"name": "Algeria", "flag": "🇩🇿"},
    {"name": "Tunisia", "flag": "🇹🇳"},
    {"name": "Libya", "flag": "🇱🇾"},
    {"name": "Sudan", "flag": "🇸🇩"},
    {"name": "Ethiopia", "flag": "🇪🇹"},
    {"name": "Tanzania", "flag": "🇹🇿"},
    {"name": "Uganda", "flag": "🇺🇬"},
    {"name": "Zambia", "flag": "🇿🇲"},
    {"name": "Zimbabwe", "flag": "🇿🇼"},
])

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
        
        import random
        pool = [c for c in country_flags[difficulty] if c["name"] != country_data["name"]]
        wrong = random.sample(pool, min(3, len(pool)))
        options = wrong + [country_data]
        random.shuffle(options)
        
        for opt in options:
            btn = discord.ui.Button(
                label=opt["name"],
                style=discord.ButtonStyle.primary,
                custom_id=opt["name"]
            )
            btn.callback = self.make_callback(opt["name"])
            self.add_item(btn)
    
    def make_callback(self, name):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.player_id:
                await interaction.response.send_message("❌ Not your game!", ephemeral=True)
                return
            if self.answered:
                await interaction.response.send_message("⏳ This round is already over!", ephemeral=True)
                return
            
            self.answered = True
            correct = self.country_data["name"]
            
            if name == correct:
                self.correct_count += 1
                self.round_history.append(True)
                await interaction.response.edit_message(
                    content=f"✅ {interaction.user.mention} **got it right!** 🎉",
                    view=None
                )
                await asyncio.sleep(1)
                await interaction.delete_original_response()
                await start_new_round(interaction.channel, self.difficulty, self.player_id, 
                                     self.total_rounds, self.current_round + 1, 
                                     self.correct_count, self.round_history)
            else:
                self.round_history.append(False)
                await interaction.response.send_message(
                    f"❌ {interaction.user.mention} you didn't get it right. Try again!",
                    ephemeral=True
                )
                self.answered = False
        return callback
    
    async def on_timeout(self):
        if not self.answered:
            self.round_history.append(False)
            await self.message.edit(
                content=f"⏰ Time's up! The flag was **{self.country_data['name']}** {self.country_data['flag']}",
                view=None
            )
            await asyncio.sleep(2)
            await start_new_round(self.message.channel, self.difficulty, self.player_id,
                                 self.total_rounds, self.current_round + 1,
                                 self.correct_count, self.round_history)

async def start_new_round(channel, difficulty, player_id, total_rounds, current_round, correct_count, round_history):
    if current_round > total_rounds:
        if player_id in used_countries:
            del used_countries[player_id]
        
        total_correct = sum(round_history)
        if total_correct == total_rounds:
            content = f"🎉 {channel.guild.get_member(player_id).mention} **You got all {total_rounds} countries right!** 🏆"
        else:
            content = f"📊 {channel.guild.get_member(player_id).mention} Game over! You got **{total_correct}/{total_rounds}** correct."
        
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
        
        await channel.send(content, view=view)
        return
    
    import random
    import copy
    
    if player_id not in used_countries:
        used_countries[player_id] = []
    
    available = [c for c in country_flags[difficulty] if c["name"] not in used_countries[player_id]]
    
    if not available:
        used_countries[player_id] = []
        available = country_flags[difficulty]
    
    country = random.choice(available)
    used_countries[player_id].append(country["name"])
    
    view = CountryGuessView(country, difficulty, player_id, total_rounds, current_round, correct_count, round_history)
    
    timer_msg = f"⏱️ 30s remaining"
    msg = await channel.send(
        f"{channel.guild.get_member(player_id).mention} 🇺🇳 **Guess the country!** {country['flag']}\n"
        f"Difficulty: **{difficulty.upper()}** | Round **{current_round}/{total_rounds}**\n"
        f"{timer_msg}",
        view=view
    )
    view.message = msg
    
    for remaining in range(29, 0, -1):
        await asyncio.sleep(1)
        if view.answered:
            break
        try:
            await msg.edit(
                content=f"{channel.guild.get_member(player_id).mention} 🇺🇳 **Guess the country!** {country['flag']}\n"
                        f"Difficulty: **{difficulty.upper()}** | Round **{current_round}/{total_rounds}**\n"
                        f"⏱️ {remaining}s remaining",
                view=view
            )
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
    
    for diff in ["easy", "medium", "hard"]:
        btn = discord.ui.Button(
            label=f"{diff.capitalize()} ({len(country_flags[diff])} flags)",
            style=discord.ButtonStyle.success if diff == "easy" else discord.ButtonStyle.primary if diff == "medium" else discord.ButtonStyle.danger,
            custom_id=diff
        )
        btn.callback = lambda i, d=diff: difficulty_callback(i, d)
        view.add_item(btn)
    
    await channel.send("Select difficulty below:", view=view)

@bot.hybrid_command(name="country", description="Start a country flag guessing game")
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
    
    wallet, _ = get_user_econ(user_id)
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

@bot.hybrid_command(name="pack", description="Buy or open a football card pack")
async def pack(ctx, action: str, pack_type: str = None):
    action = action.lower()
    
    if action == "buy":
        embed = discord.Embed(
            title="📦 Buy a Pack",
            description="Select a pack type from the dropdown below:",
            color=discord.Color.blue()
        )
        
        cursor.execute("SELECT pack_type, price, card_count FROM football_packs")
        packs = cursor.fetchall()
        
        view = PackBuyView(ctx.author.id)
        for pack in packs:
            pack_name = pack[0].capitalize()
            price = pack[1]
            count = pack[2]
            view.add_item(discord.ui.Button(
                label=f"{pack_name} Pack (${price:,}) - {count} cards",
                style=discord.ButtonStyle.secondary,
                custom_id=f"buy_{pack[0]}"
            ))
        
        async def button_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ This isn't your purchase!", ephemeral=True)
                return
            
            pack_type = interaction.data["custom_id"].replace("buy_", "")
            cursor.execute("SELECT price, card_count FROM football_packs WHERE pack_type = ?", (pack_type,))
            row = cursor.fetchone()
            if not row:
                await interaction.response.send_message("❌ Invalid pack type!", ephemeral=True)
                return
            
            price, count = row
            wallet, _ = get_user_econ(ctx.author.id)
            if wallet < price:
                await interaction.response.send_message(f"❌ You need **${price:,}** to buy a {pack_type.capitalize()} pack. You have ${wallet:,}.", ephemeral=True)
                return
            
            update_wallet(ctx.author.id, -price)
            
            cursor.execute("INSERT OR REPLACE INTO football_packs_inventory (user_id, pack_type, quantity) VALUES (?, ?, COALESCE((SELECT quantity FROM football_packs_inventory WHERE user_id = ? AND pack_type = ?), 0) + 1)", 
                          (ctx.author.id, pack_type, ctx.author.id, pack_type))
            db.commit()
            
            embed = discord.Embed(
                title=f"✅ {pack_type.capitalize()} Pack Purchased!",
                description=f"You bought a {pack_type.capitalize()} pack for **${price:,}**!\n\nUse `/pack open` to see your packs and open them.",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
        
        for child in view.children:
            child.callback = button_callback
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)
    
    elif action == "open":
        cursor.execute("SELECT pack_type, quantity FROM football_packs_inventory WHERE user_id = ? AND quantity > 0", (ctx.author.id,))
        packs = cursor.fetchall()
        
        if not packs:
            embed = discord.Embed(
                description="❌ You don't have any packs! Use `/pack buy` to purchase some.",
                color=discord.Color.red()
            )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="🎴 Your Packs",
            description="Select a pack to open:",
            color=discord.Color.blue()
        )
        
        view = PackOpenView(ctx.author.id)
        for pack_type, quantity in packs:
            view.add_item(discord.ui.Button(
                label=f"{pack_type.capitalize()} Pack (x{quantity})",
                style=discord.ButtonStyle.success,
                custom_id=f"open_{pack_type}"
            ))
        
        async def open_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ This isn't your pack!", ephemeral=True)
                return
            
            pack_type = interaction.data["custom_id"].replace("open_", "")
            
            cursor.execute("SELECT quantity FROM football_packs_inventory WHERE user_id = ? AND pack_type = ?", (ctx.author.id, pack_type))
            row = cursor.fetchone()
            if not row or row[0] <= 0:
                await interaction.response.send_message("❌ You don't have any {pack_type.capitalize()} packs!", ephemeral=True)
                return
            
            result = open_pack(ctx.author.id, pack_type)
            if result == "insufficient":
                await interaction.response.send_message("❌ You don't have enough money to open this pack!", ephemeral=True)
                return
            elif result is None:
                await interaction.response.send_message("❌ Invalid pack type!", ephemeral=True)
                return
            
            cursor.execute("UPDATE football_packs_inventory SET quantity = quantity - 1 WHERE user_id = ? AND pack_type = ?", (ctx.author.id, pack_type))
            db.commit()
            
            cards = result
            embed = discord.Embed(
                title=f"🎴 {pack_type.capitalize()} Pack Opened!",
                description=f"You got {len(cards)} cards:",
                color=discord.Color.green()
            )
            card_list = []
            for card in cards:
                p = card["player"]
                card_list.append(f"• **{p['name']}** ({p['rarity'].upper()}) - {p['rating']} OVR")
            embed.add_field(name="Cards", value="\n".join(card_list) or "No cards found.", inline=False)
            
            await interaction.response.edit_message(embed=embed, view=None)
        
        for child in view.children:
            child.callback = open_callback
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)
    
    else:
        embed = discord.Embed(
            description="❌ Invalid action. Use `/pack buy` or `/pack open`.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

class PackBuyView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

class PackOpenView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

@bot.hybrid_command(name="sell", description="Sell a football card for money")
async def sell(ctx, card_id: str = None):
    if card_id is None:
        cards = get_player_cards(ctx.author.id)
        if not cards:
            embed = discord.Embed(
                description="❌ You don't have any cards to sell!",
                color=discord.Color.red()
            )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="💰 Sell a Card",
            description="Select a card to sell from the dropdown below:",
            color=discord.Color.gold()
        )
        
        view = SellView(ctx.author.id)
        options = []
        for card in cards[:25]:
            price = RARITY_SELL_PRICES.get(card[5], 50)
            options.append(discord.SelectOption(
                label=f"{card[2]} ({card[5].upper()}) - {card[6]} OVR",
                description=f"Sell for ${price:,}",
                value=str(card[0])
            ))
        
        select = discord.ui.Select(placeholder="Select a card to sell...", options=options)
        
        async def select_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ This isn't your sale!", ephemeral=True)
                return
            
            card_id = int(select.values[0])
            card = get_card_by_id(card_id, ctx.author.id)
            if not card:
                await interaction.response.send_message("❌ Card not found!", ephemeral=True)
                return
            
            price = RARITY_SELL_PRICES.get(card[5], 50)
            
            if delete_card(card_id, ctx.author.id):
                update_wallet(ctx.author.id, price)
                embed = discord.Embed(
                    title="💰 Card Sold!",
                    description=f"You sold **{card[2]}** ({card[5].upper()}) for **${price:,}**!",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message("❌ Failed to sell card!", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.send(embed=embed, view=view)
        return
    
    try:
        card_id = int(card_id)
    except ValueError:
        embed = discord.Embed(
            description="❌ Please provide a valid card ID.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    card = get_card_by_id(card_id, ctx.author.id)
    if not card:
        embed = discord.Embed(
            description="❌ Card not found! Use `/sell` to see your cards.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    price = RARITY_SELL_PRICES.get(card[5], 50)
    
    if delete_card(card_id, ctx.author.id):
        update_wallet(ctx.author.id, price)
        embed = discord.Embed(
            title="💰 Card Sold!",
            description=f"You sold **{card[2]}** ({card[5].upper()}) for **${price:,}**!",
            color=discord.Color.green()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            description="❌ Failed to sell card!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

class SellView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

@bot.hybrid_command(name="collection", aliases=["cards"], description="View your football card collection")
async def collection(ctx, member: discord.Member = None):
    target = member or ctx.author
    cards = get_player_cards(target.id)
    
    if not cards:
        embed = discord.Embed(
            description=f"{target.mention} has no football cards yet!",
            color=discord.Color.orange()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    total = len(cards)
    legendary = get_rarity_count(target.id, "legendary")
    epic = get_rarity_count(target.id, "epic")
    rare = get_rarity_count(target.id, "rare")
    common = get_rarity_count(target.id, "common")
    
    embed = discord.Embed(
        title=f"🎴 {target.display_name}'s Collection",
        description=f"Total Cards: **{total}**\n👑 Legendary: {legendary} | ⭐ Epic: {epic} | 🔵 Rare: {rare} | ⚪ Common: {common}",
        color=discord.Color.blue()
    )
    
    top_cards = get_top_cards(target.id, 10)
    card_list = []
    for card in top_cards:
        rarity_emoji = "👑" if card[5] == "legendary" else "⭐" if card[5] == "epic" else "🔵" if card[5] == "rare" else "⚪"
        card_list.append(f"{rarity_emoji} **{card[2]}** ({card[5].upper()}) - {card[6]} OVR")
    
    embed.add_field(name="Top Cards", value="\n".join(card_list) or "No cards", inline=False)
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="debate", description="Debate another member using football cards")
async def debate(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="❌ You can't debate yourself!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(description="❌ You can't debate a bot!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    p1_cards = get_top_cards(ctx.author.id, 3)
    p2_cards = get_top_cards(member.id, 3)
    
    if not p1_cards or not p2_cards:
        embed = discord.Embed(
            description="❌ Both players need at least 1 card to debate!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    p1_score = sum(card[6] for card in p1_cards) + len(p1_cards) * 2
    p2_score = sum(card[6] for card in p2_cards) + len(p2_cards) * 2
    
    embed = discord.Embed(
        title="⚔️ Debate Battle!",
        color=discord.Color.gold()
    )
    
    p1_names = "\n".join([f"• {card[2]} ({card[6]} OVR)" for card in p1_cards[:3]])
    p2_names = "\n".join([f"• {card[2]} ({card[6]} OVR)" for card in p2_cards[:3]])
    
    embed.add_field(
        name=f"{ctx.author.display_name} (Score: {p1_score})",
        value=p1_names or "No cards",
        inline=True
    )
    embed.add_field(
        name=f"{member.display_name} (Score: {p2_score})",
        value=p2_names or "No cards",
        inline=True
    )
    
    if p1_score > p2_score:
        embed.add_field(
            name="🏆 Winner",
            value=f"{ctx.author.mention} wins the debate!",
            inline=False
        )
        embed.color = discord.Color.green()
    elif p2_score > p1_score:
        embed.add_field(
            name="🏆 Winner",
            value=f"{member.mention} wins the debate!",
            inline=False
        )
        embed.color = discord.Color.green()
    else:
        embed.add_field(
            name="🤝 Result",
            value="It's a tie! Both debaters are evenly matched!",
            inline=False
        )
        embed.color = discord.Color.orange()
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="trade", description="Send a trade request to another member")
async def trade(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        embed = discord.Embed(description="❌ You can't trade with yourself!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(description="❌ You can't trade with a bot!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    p1_cards = get_player_cards(ctx.author.id)
    p2_cards = get_player_cards(member.id)
    
    if not p1_cards:
        embed = discord.Embed(description="❌ You have no cards to trade!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    if not p2_cards:
        embed = discord.Embed(description=f"❌ {member.display_name} has no cards to trade!", color=discord.Color.red())
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    view = TradeView(ctx.author.id, member.id)
    embed = discord.Embed(
        title="🔄 Trade Request",
        description=f"{ctx.author.mention} wants to trade with {member.mention}!\n\nSelect the cards you want to offer and what you want from the other player.\n\n**Your Cards:** {len(p1_cards)}\n**{member.display_name}'s Cards:** {len(p2_cards)}",
        color=discord.Color.blue()
    )
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx.send(embed=embed, view=view)

class TradeView(discord.ui.View):
    def __init__(self, proposer_id, target_id):
        super().__init__(timeout=180)
        self.proposer_id = proposer_id
        self.target_id = target_id
        self.proposer_selected = None
        self.target_selected = None
        self.proposer_money = 0
        self.target_money = 0
        self.accepted = False
    
    @discord.ui.select(
        placeholder="Select a card to offer (you)",
        min_values=0,
        max_values=1,
        options=[]
    )
    async def proposer_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.proposer_id:
            await interaction.response.send_message("❌ This isn't your trade!", ephemeral=True)
            return
        if select.values:
            self.proposer_selected = int(select.values[0])
        else:
            self.proposer_selected = None
        await interaction.response.defer()
        await self.update_embed(interaction)
    
    @discord.ui.select(
        placeholder="Select a card to receive (from them)",
        min_values=0,
        max_values=1,
        options=[]
    )
    async def target_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ This isn't your trade!", ephemeral=True)
            return
        if select.values:
            self.target_selected = int(select.values[0])
        else:
            self.target_selected = None
        await interaction.response.defer()
        await self.update_embed(interaction)
    
    async def update_embed(self, interaction: discord.Interaction):
        p1_cards = get_player_cards(self.proposer_id)
        p2_cards = get_player_cards(self.target_id)
        
        self.proposer_select.options = [
            discord.SelectOption(
                label=f"{card[2]} ({card[5].upper()}) - {card[6]} OVR",
                value=str(card[0]),
                default=(card[0] == self.proposer_selected)
            ) for card in p1_cards[:25]
        ]
        self.target_select.options = [
            discord.SelectOption(
                label=f"{card[2]} ({card[5].upper()}) - {card[6]} OVR",
                value=str(card[0]),
                default=(card[0] == self.target_selected)
            ) for card in p2_cards[:25]
        ]
        
        embed = discord.Embed(
            title="🔄 Trade Request",
            description=f"<@{self.proposer_id}> wants to trade with <@{self.target_id}>!\n\n**Your Card:** {self.get_card_name(self.proposer_selected, p1_cards) or 'None'}\n**Their Card:** {self.get_card_name(self.target_selected, p2_cards) or 'None'}\n\nOffer money (use buttons below)",
            color=discord.Color.blue()
        )
        embed.add_field(name="💰 Money Offer", value=f"<@{self.proposer_id}>: ${self.proposer_money:,}\n<@{self.target_id}>: ${self.target_money:,}", inline=False)
        
        await interaction.message.edit(embed=embed, view=self)
    
    def get_card_name(self, card_id, cards):
        if card_id is None:
            return None
        for card in cards:
            if card[0] == card_id:
                return f"{card[2]} ({card[5].upper()}) - {card[6]} OVR"
        return None
    
    @discord.ui.button(label="💰 Add Money (You)", style=discord.ButtonStyle.secondary)
    async def add_money(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.proposer_id, self.target_id):
            await interaction.response.send_message("❌ This isn't your trade!", ephemeral=True)
            return
        
        modal = TradeMoneyModal(interaction.user.id, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="✅ Accept Trade", style=discord.ButtonStyle.success)
    async def accept_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.proposer_id, self.target_id):
            await interaction.response.send_message("❌ This isn't your trade!", ephemeral=True)
            return
        
        if self.proposer_selected is None and self.target_selected is None and self.proposer_money == 0 and self.target_money == 0:
            await interaction.response.send_message("❌ You must select at least one card or money to trade!", ephemeral=True)
            return
        
        if not self.accepted:
            self.accepted = True
            await interaction.response.send_message("✅ Trade accepted by one party. Waiting for the other to accept...", ephemeral=True)
            
            embed = discord.Embed(
                title="✅ Trade Finalized!",
                description="Both parties have accepted the trade!",
                color=discord.Color.green()
            )
            await interaction.message.edit(embed=embed, view=None)
            
            if self.proposer_selected:
                cursor.execute("DELETE FROM football_cards WHERE id = ? AND user_id = ?", (self.proposer_selected, self.proposer_id))
            if self.target_selected:
                cursor.execute("DELETE FROM football_cards WHERE id = ? AND user_id = ?", (self.target_selected, self.target_id))
            
            if self.proposer_selected:
                cursor.execute("UPDATE football_cards SET user_id = ? WHERE id = ?", (self.target_id, self.proposer_selected))
            if self.target_selected:
                cursor.execute("UPDATE football_cards SET user_id = ? WHERE id = ?", (self.proposer_id, self.target_selected))
            
            if self.proposer_money > 0:
                update_wallet(self.proposer_id, -self.proposer_money)
                update_wallet(self.target_id, self.proposer_money)
            if self.target_money > 0:
                update_wallet(self.target_id, -self.target_money)
                update_wallet(self.proposer_id, self.target_money)
            
            db.commit()
            
            result_embed = discord.Embed(
                title="🔄 Trade Completed!",
                description=f"Trade between <@{self.proposer_id}> and <@{self.target_id}> was successful!",
                color=discord.Color.green()
            )
            await interaction.channel.send(embed=result_embed)
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.proposer_id, self.target_id):
            await interaction.response.send_message("❌ This isn't your trade!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="❌ Trade Cancelled",
            description=f"Trade cancelled by {interaction.user.mention}",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

class TradeMoneyModal(discord.ui.Modal, title="Add Money to Trade"):
    amount = discord.ui.TextInput(label="Amount to offer", placeholder="e.g. 500", required=True)
    
    def __init__(self, user_id, trade_view):
        super().__init__()
        self.user_id = user_id
        self.trade_view = trade_view
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value.strip())
        except ValueError:
            embed = discord.Embed(description="❌ Please enter a valid number.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if amount <= 0:
            embed = discord.Embed(description="❌ Amount must be greater than zero.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        wallet, _ = get_user_econ(self.user_id)
        if wallet < amount:
            embed = discord.Embed(description=f"❌ You only have ${wallet:,} in your wallet!", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if self.user_id == self.trade_view.proposer_id:
            self.trade_view.proposer_money = amount
        else:
            self.trade_view.target_money = amount
        
        await interaction.response.defer()
        await self.trade_view.update_embed(interaction)

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
        title="🖼️ Random PFP",
        description=f"Here's a random profile picture for you!",
        color=discord.Color.from_rgb(30, 31, 34)
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

@bot.hybrid_command(name="fraktur", description="Convert text to Fraktur style (like 𝔫𝔦𝔤𝔤𝔞)")
async def fraktur(ctx, *, text: str):
    if not text:
        embed = discord.Embed(
            description="❌ Please provide some text to convert!",
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

ADMIN_PAY_USERS = {1286560808528117820, 1152424544557088849}

@bot.hybrid_command(name="adminpay", aliases=["ownerspay"], description="Admin command to give money to any user")
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
    
    new_wallet, new_bank = get_user_econ(member.id)
    
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
    
    wallet, bank = get_user_econ(member.id)
    
    cursor.execute("UPDATE users SET wallet = ? WHERE user_id = ?", (amount, member.id))
    db.commit()
    
    new_wallet, new_bank = get_user_econ(member.id)
    
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
    
    new_wallet, new_bank = get_user_econ(member.id)
    
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

@bot.hybrid_command(name="adminrob", aliases=["ownersrob"], description="Admin command to rob any user (never fails) - steals from wallet AND bank")
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
    
    wallet, bank = get_user_econ(member.id)
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
    
    admin_wallet, admin_bank = get_user_econ(ctx.author.id)
    
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

@bot.hybrid_command(name="adminrobamount", aliases=["ownersrobamount"], description="Admin command to rob a specific amount from a user's wallet only")
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
    
    wallet, bank = get_user_econ(member.id)
    
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
    
    new_victim_wallet, new_victim_bank = get_user_econ(member.id)
    admin_wallet, admin_bank = get_user_econ(ctx.author.id)
    
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

@bot.hybrid_command(name="role", description="Add a role to a member")
@commands.has_permissions(manage_roles=True)
async def role(ctx, member: discord.Member, *, role_name: str):
    if ctx.guild.owner_id == member.id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot add roles to the server owner.", ephemeral=True)
        return await ctx.send(f"❌ {ctx.author.mention} you cannot add roles to the server owner.")
    
    if member.guild_permissions.kick_members or member.guild_permissions.ban_members or member.guild_permissions.manage_roles:
        if ctx.author.id != ctx.guild.owner_id:
            if ctx.interaction:
                return await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} you cannot add roles to a staff member.", ephemeral=True)
            return await ctx.send(f"❌ {ctx.author.mention} you cannot add roles to a staff member.")
    
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} has a higher or equal role than me, I cannot add roles to them.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} has a higher or equal role than me, I cannot add roles to them.")
    
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    
    if not role:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ Role `{role_name}` not found.", ephemeral=True)
        return await ctx.send(f"❌ Role `{role_name}` not found.")
    
    if ctx.guild.me and role >= ctx.guild.me.top_role and ctx.author.id != ctx.guild.owner_id:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ I cannot add the role `{role_name}` because it's higher or equal to my highest role.", ephemeral=True)
        return await ctx.send(f"❌ I cannot add the role `{role_name}` because it's higher or equal to my highest role.")
    
    if role in member.roles:
        if ctx.interaction:
            return await ctx.interaction.response.send_message(f"❌ {member.mention} already has the role `{role_name}`.", ephemeral=True)
        return await ctx.send(f"❌ {member.mention} already has the role `{role_name}`.")
    
    try:
        await member.add_roles(role, reason=f"Added by {ctx.author}")
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"✅ Added **{role_name}** to {member.mention}")
        else:
            await ctx.send(f"✅ Added **{role_name}** to {member.mention}")
            
    except Exception as e:
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Failed to add role: {e}", ephemeral=True)
        else:
            await ctx.send(f"❌ Failed to add role: {e}")

@role.error
async def role_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ {ctx.author.mention} You are missing Manage Roles permission.", ephemeral=True)
        else:
            await ctx.send(f"❌ {ctx.author.mention} You are missing Manage Roles permission.")
    if isinstance(error, commands.MemberNotFound):
        if ctx.interaction:
            await ctx.interaction.response.send_message(f"❌ Member not found.", ephemeral=True)
        else:
            await ctx.send(f"❌ Member not found.")

# =========================================================
# SPANK COMMAND
# =========================================================

SPANK_GIFS = [
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExbmF5ejljb2t0eXRjczgza2lndHdrMTNydGFyajB4b2x3bWNteHBkZyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/pRotk2UQTsozm/giphy.gif",
    "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExZm1lcDlleWJ6NG91dzBxdGNsc2hpMm14ZmpoeG94OHNkMjhlOGk5aSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/wPAxIdea6mbw3vQa6i/giphy.gif"
]

@bot.hybrid_command(name="spank", description="Spank someone!")
async def spank(ctx, member: discord.Member = None):
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
    "https://media.giphy.com/media/v1.Y2lkPWVjZjA1ZTQ3NzY3anpiN2dxcXh2NnpmcXhsaGt0Mmx0YWt4MGt1Z2tvZHZ3NjFoYyZlcD12MV9naWZzX3NlYXJjaCZjdD1n/HmtJvG66fzqwHMLXMk/giphy.gif"
]

@bot.hybrid_command(name="bendover", description="Ask someone to bend over!")
async def bendover(ctx, member: discord.Member = None):
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
# HIDE & SEEK COMMAND - ADD THIS BEFORE bot.run(TOKEN)
# =========================================================

# Add these database tables
cursor.execute("""
CREATE TABLE IF NOT EXISTS hide_seek_stats (
    user_id INTEGER PRIMARY KEY,
    hides INTEGER DEFAULT 0,
    seeks INTEGER DEFAULT 0,
    found INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0
)
""")
db.commit()

# Memory cache for active games
hide_seek_games = {}
hide_seek_cooldown = {}

@bot.hybrid_command(name="hide", description="Hide in a random channel for seekers to find you!")
async def hide(ctx):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    
    # Check if user is already in a game
    if user_id in hide_seek_games:
        embed = discord.Embed(
            description="❌ You're already hiding! Wait for someone to find you.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    # Check cooldown (30 seconds between hides)
    if user_id in hide_seek_cooldown:
        remaining = int(30 - (time.time() - hide_seek_cooldown[user_id]))
        if remaining > 0:
            embed = discord.Embed(
                description=f"⏳ Please wait {remaining} seconds before hiding again.",
                color=discord.Color.orange()
            )
            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
    
    # Get channels the user can see (excluding categories, voice, and restricted channels)
    available_channels = []
    for channel in ctx.guild.text_channels:
        # Check if user has permission to view channel
        perms = channel.permissions_for(ctx.author)
        if perms.view_channel and channel.type == discord.ChannelType.text:
            # Exclude NSFW channels if needed
            if channel.is_nsfw():
                continue
            available_channels.append(channel)
    
    if len(available_channels) < 2:
        embed = discord.Embed(
            description="❌ Not enough channels available to hide in! Need at least 2 text channels.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    # Pick random channel
    hidden_channel = random.choice(available_channels)
    
    # Store game data
    hide_seek_games[user_id] = {
        "channel_id": hidden_channel.id,
        "channel_name": hidden_channel.name,
        "guild_id": guild_id,
        "guesses": 0,
        "max_guesses": 20,
        "found_by": None,
        "start_time": time.time()
    }
    hide_seek_cooldown[user_id] = time.time()
    
    # DM the hider
    try:
        await ctx.author.send(f"🕵️ You are hiding in **#{hidden_channel.name}**! Seekers have 20 guesses to find you.")
    except:
        pass
    
    # Announce to channel
    embed = discord.Embed(
        title="🕵️ Hide & Seek!",
        description=f"{ctx.author.mention} is hiding somewhere in this server!\n\n**Seekers:** Use `R!seek #channel` to guess where they are!\nYou have **20 guesses** to find them.",
        color=discord.Color.from_rgb(30, 31, 34)
    )
    embed.set_footer(text=f"Hint: The channel has {len(hidden_channel.name)} letters")
    
    if ctx.interaction:
        await ctx.interaction.response.send_message(embed=embed)
    else:
        await ctx.send(embed=embed)

@bot.hybrid_command(name="seek", description="Guess where the hider is!")
async def seek(ctx, channel: discord.TextChannel):
    user_id = ctx.author.id
    guild_id = ctx.guild.id
    
    # Check if there's an active hider
    if not hide_seek_games:
        embed = discord.Embed(
            description="❌ Nobody is hiding right now! Use `R!hide` to start a game.",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    # Find the active hider
    hider_id = None
    game_data = None
    for hid, data in hide_seek_games.items():
        if data["guild_id"] == guild_id:
            hider_id = hid
            game_data = data
            break
    
    if not hider_id:
        embed = discord.Embed(
            description="❌ Nobody is hiding in this server right now!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    # Check if seeker is the hider
    if hider_id == user_id:
        embed = discord.Embed(
            description="❌ You can't seek yourself! You're the one hiding!",
            color=discord.Color.red()
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)
        return
    
    # Check guesses limit
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
    
    # Check if the hider was already found
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
    
    # Check the guess
    game_data["guesses"] += 1
    remaining = game_data["max_guesses"] - game_data["guesses"]
    
    if channel.id == game_data["channel_id"]:
        # CORRECT GUESS!
        game_data["found_by"] = user_id
        
        # Update stats
        cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (hider_id,))
        cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (user_id,))
        cursor.execute("UPDATE hide_seek_stats SET hidden = hidden + 1 WHERE user_id = ?", (hider_id,))
        cursor.execute("UPDATE hide_seek_stats SET found = found + 1 WHERE user_id = ?", (user_id,))
        
        # Economy: winner gets 200, loser loses 100
        update_wallet(user_id, 200)
        update_wallet(hider_id, -100)
        
        # Points: winner +10, loser -5
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
        
        # Remove game
        del hide_seek_games[hider_id]
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)
    else:
        # WRONG GUESS
        embed = discord.Embed(
            title="❌ Wrong!",
            description=f"{ctx.author.mention} guessed **#{channel.name}** but that's not where they are!\n\n"
                        f"📊 **{remaining}** guesses remaining.",
            color=discord.Color.red()
        )
        
        # Give hints every 5 guesses
        if game_data["guesses"] % 5 == 0:
            channel_name = game_data["channel_name"]
            hint = ""
            if game_data["guesses"] == 5:
                hint = f"💡 Hint: The channel starts with `{channel_name[0]}`"
            elif game_data["guesses"] == 10:
                hint = f"💡 Hint: The channel has {len(channel_name)} letters"
            elif game_data["guesses"] == 15:
                hint = f"💡 Hint: The channel name contains `{channel_name[2:4]}`"
            else:
                hint = f"💡 Hint: The channel is `#{channel_name}`" if game_data["guesses"] >= 20 else ""
            
            if hint:
                embed.add_field(name="📌 Hint", value=hint, inline=False)
        
        # If no guesses left, end game
        if game_data["guesses"] >= game_data["max_guesses"]:
            embed.description = f"❌ Nobody found <@{hider_id}>! They were hiding in **#{game_data['channel_name']}**."
            cursor.execute("INSERT OR IGNORE INTO hide_seek_stats (user_id) VALUES (?)", (hider_id,))
            cursor.execute("UPDATE hide_seek_stats SET hidden = hidden + 1 WHERE user_id = ?", (hider_id,))
            db.commit()
            del hide_seek_games[hider_id]
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)

@bot.hybrid_command(name="hideleaderboard", aliases=["hidelb", "hiderank"], description="Show hide and seek leaderboard")
async def hideleaderboard(ctx):
    cursor.execute("SELECT user_id, points, hides, hidden, found FROM hide_seek_stats ORDER BY points DESC LIMIT 10")
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
        user_id, points, hides, hidden, found = row
        user = bot.get_user(user_id)
        name = user.display_name if user else f"User {user_id}"
        leaderboard.append(f"**#{rank}** {name} - ⭐ {points} pts | 🏆 Found: {found} | 🕵️ Hid: {hidden}")
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
    cursor.execute("SELECT hides, hidden, found, points FROM hide_seek_stats WHERE user_id = ?", (target.id,))
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
    
    hides, hidden, found, points = row
    
    embed = discord.Embed(
        title=f"📊 {target.display_name}'s Stats",
        description=f"⭐ Points: **{points}**\n"
                    f"🕵️ Times Hidden: **{hides}**\n"
                    f"🏆 Found Someone: **{found}**\n"
                    f"😳 Got Found: **{hidden}**",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Use R!hide to start hiding!")
    
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
# RUN BOT
# =========================================================

if __name__ == "__main__":
    bot.run(TOKEN)
