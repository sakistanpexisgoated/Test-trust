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
    if not is_troll_whitelisted(ctx.author.id):
        embed = discord.Embed(description="You are not whitelisted to use this troll command.", color=discord.Color.red())
        if ctx.interaction:
            return await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        return await ctx.send(embed=embed)

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
# AFK COMMAND (Simple plain-text version)
# =========================================================

@bot.hybrid_command(name="afk", description="Set your AFK status")
async def afk(ctx, *, reason: str = "AFK"):
    await ctx.send(f"AFK Set!\nYou are now afk in this server. Reason: **{reason}**")

# =========================================================
# MODERATOR UI / PERMISSION GATE
# =========================================================

def _has_mod_perms(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator or 
        member.guild_permissions.ban_members or 
        member.guild_permissions.kick_members or 
        member.guild_permissions.manage_messages or 
        member.guild_permissions.manage_roles
    )

async def require_server_mod(ctx):
    if not isinstance(ctx.author, discord.Member) or not _has_mod_perms(ctx.author):
        msg = f"{ctx.author.mention} You are missing Ban perms"
        if ctx.interaction:
            try:
                await ctx.interaction.followup.send(msg, ephemeral=True)
            except Exception:
                pass
        else:
            await ctx.send(msg)
        return False
    return True


# =========================================================
# FAKE MODERATION GROUP (Open to everyone)
# =========================================================

@bot.hybrid_group(name="fake", description="Fake moderation commands")
async def fake(ctx):
    pass

@fake.command(name="ban", description="Fake-ban a member without actually banning them")
async def fake_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await ctx.send(f"Banned {member.name}. Reason: {reason}\nFake ban by {ctx.author.display_name}")


# =========================================================
# REAL MODERATION COMMANDS
# =========================================================

@bot.hybrid_command(name="ban", description="Ban a member from the server")
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if member.bot and member.id == ctx.bot.user.id:
        return await ctx.send("I cannot ban a server bot.")
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        return await ctx.send(f"{ctx.author.mention} {member.mention} is higher than you, you cannot ban him.")
    await member.ban(reason=reason)
    await ctx.send(f"{member.name} has been banned. Reason: {reason}")


@bot.hybrid_command(name="unban", description="Unban a user by ID")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: str, *, reason: str = "No reason provided"):
    try:
        uid = int(user_id)
        user = await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        await ctx.send(f"{user.name} successfully unbanned. Reason: {reason}")
    except Exception:
        await ctx.send("Could not find or unban that user. Check the user ID.")


@bot.hybrid_command(name="kick", description="Kick a member from the server")
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        return await ctx.send(f"{ctx.author.mention} {member.mention} is higher than you, you cannot kick him.")
    await member.kick(reason=reason)
    await ctx.send(f"{member.name} has been kicked. Reason: {reason}")


@bot.hybrid_command(name="mute", description="Mute a member")
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        return await ctx.send(f"{ctx.author.mention} {member.mention} is higher than you, you cannot mute him.")
    seconds = parse_duration(duration)
    if not seconds:
        return await ctx.send("Invalid duration format. Use e.g. `10s`, `5m`, `2h`, `1d`.")
    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason)
        await ctx.send(f"{member.name} has been muted for {duration}. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to mute member: {e}")


@bot.hybrid_command(name="unmute", description="Remove a member's timeout")
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        await ctx.send(f"{member.name} has been unmuted successfully. Unmuted by {ctx.author.display_name}")
    except Exception as e:
        await ctx.send(f"Unmute Failed: {e}")


@bot.hybrid_command(name="warn", description="Warn a member")
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not await require_server_mod(ctx):
        return
    if ctx.guild.me and member.top_role >= ctx.guild.me.top_role:
        return await ctx.send(f"{ctx.author.mention} {member.mention} is higher than you, you cannot warn him.")
    cursor.execute("INSERT INTO warnings (user_id, moderator_id, reason) VALUES (?, ?, ?)", (member.id, ctx.author.id, reason))
    db.commit()
    cursor.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (member.id,))
    total_warns = cursor.fetchone()[0]
    await ctx.send(f"{member.name} has been warned. Reason: {reason} (Total warnings: {total_warns})")


@bot.hybrid_command(name="avatar", description="Show a user's avatar")
async def avatar(ctx, member: discord.Member = None):
    target = member or ctx.author
    embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=target.color)
    embed.set_image(url=target.display_avatar.url)
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

# ---------- GIVEAWAY ----------
from typing import List

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
# RUN BOT
# =========================================================

if __name__ == "__main__":
    bot.run(TOKEN)
