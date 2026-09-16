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
# PERMISSION SYSTEM - HIDE COMMANDS FROM NON-OWNERS / STAFF
# =========================================================

async def owner_only_predicate(interaction: discord.Interaction):
    if interaction.user.id not in OWNER_IDS:
        raise app_commands.CheckFailure("This command is for bot owners only.")
    return True

async def nsfw_only_predicate(interaction: discord.Interaction):
    if not interaction.channel or not interaction.channel.is_nsfw():
        raise app_commands.CheckFailure("This command can only be used in an NSFW channel.")
    return True

async def server_staff_only_predicate(interaction: discord.Interaction):
    if not interaction.guild:
        raise app_commands.CheckFailure("This command can only be used in a server.")

    member = interaction.user
    if member.id in OWNER_IDS or member.id == interaction.guild.owner_id:
        return True

    permissions = member.guild_permissions
    if (
        permissions.administrator
        or permissions.manage_messages
        or permissions.manage_roles
        or permissions.kick_members
        or permissions.ban_members
    ):
        return True

    staff_role_names = {"staff", "moderator", "mod", "admin", "administrator", "owner", "management"}
    if any(role.name.lower() in staff_role_names for role in member.roles):
        return True

    raise app_commands.CheckFailure("This command is for staff and server owners only.")

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
    command_prefix="!",
    intents=intents,
    activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="R!help"
    ),
    status=discord.Status.online,
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="R!help"
        )
    )


if __name__ == "__main__":
    bot.run(TOKEN)
