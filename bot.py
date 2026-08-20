# bot.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
import random
import re
import aiosqlite

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=['!', ',,', 'R!', 'a!'], intents=intents, help_command=None)

# Owner IDs
OWNER_IDS = [1286560808528117820, 1152424544557088849]

# Configuration
config = {
    'welcome_channel': None,
    'mod_log_channel': None,
    'mute_role': None,
    'auto_roles': [],
    'prefix': '!',
    'embed_color': 0x00ff00,
    'economy': {
        'work_cooldown': 3600,
        'beg_cooldown': 300,
        'rob_cooldown': 1800,
        'daily_cooldown': 86400,
        'weekly_cooldown': 604800,
        'monthly_cooldown': 2592000,
        'starting_balance': 100,
        'work_min': 50,
        'work_max': 200,
        'beg_min': 1,
        'beg_max': 50,
        'rob_min': 10,
        'rob_max': 100,
        'rob_fail_penalty': 50,
        'daily_reward': 100,
        'weekly_reward': 500,
        'monthly_reward': 1000
    },
    'whitelist': [],
    'blacklist': [],
    'admin_settings': {
        'rob_rig': False,
        'beg_rig': False,
        'dice_rig': False,
        'work_rig': False,
        'ghostping_enabled': False,
        'ghostping_times': 3
    }
}

ECONOMY_DB = 'economy.db'
AFK_DB = 'afk.db'

# ============= DATABASE SETUP =============

async def init_db():
    """Initialize the databases"""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS economy (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                last_work TIMESTAMP,
                last_beg TIMESTAMP,
                last_daily TIMESTAMP,
                last_weekly TIMESTAMP,
                last_monthly TIMESTAMP,
                last_rob TIMESTAMP,
                work_streak INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item TEXT,
                quantity INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES economy(user_id)
            )
        ''')
        
        await db.commit()
    
    async with aiosqlite.connect(AFK_DB) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS afk (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                reason TEXT,
                timestamp TIMESTAMP
            )
        ''')
        await db.commit()

# ============= ECONOMY DATABASE FUNCTIONS =============

async def get_economy_data(user_id):
    """Get or create economy data for a user"""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        cursor = await db.execute(
            'SELECT * FROM economy WHERE user_id = ?',
            (user_id,)
        )
        data = await cursor.fetchone()
        
        if not data:
            await db.execute(
                'INSERT INTO economy (user_id, balance) VALUES (?, ?)',
                (user_id, config['economy']['starting_balance'])
            )
            await db.commit()
            
            cursor = await db.execute(
                'SELECT * FROM economy WHERE user_id = ?',
                (user_id,)
            )
            data = await cursor.fetchone()
        
        return {
            'user_id': data[0],
            'balance': data[1],
            'bank': data[2],
            'last_work': data[3] if data[3] else None,
            'last_beg': data[4] if data[4] else None,
            'last_daily': data[5] if data[5] else None,
            'last_weekly': data[6] if data[6] else None,
            'last_monthly': data[7] if data[7] else None,
            'last_rob': data[8] if data[8] else None,
            'work_streak': data[9] or 0,
            'total_earned': data[10] or 0
        }

async def update_economy_data(user_id, **kwargs):
    """Update economy data for a user"""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        for key, value in kwargs.items():
            if key in ['balance', 'bank', 'work_streak', 'total_earned']:
                await db.execute(
                    f'UPDATE economy SET {key} = ? WHERE user_id = ?',
                    (value, user_id)
                )
            elif key in ['last_work', 'last_beg', 'last_daily', 'last_weekly', 'last_monthly', 'last_rob']:
                await db.execute(
                    f'UPDATE economy SET {key} = ? WHERE user_id = ?',
                    (value.isoformat() if value else None, user_id)
                )
        await db.commit()

async def get_global_rank(user_id):
    """Get user's global rank"""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        cursor = await db.execute(
            'SELECT COUNT(*) + 1 FROM economy WHERE (balance + bank) > (SELECT balance + bank FROM economy WHERE user_id = ?)',
            (user_id,)
        )
        rank = await cursor.fetchone()
        return rank[0] if rank else 1

# ============= AFK DATABASE FUNCTIONS =============

async def set_afk(user_id, guild_id, reason):
    """Set a user as AFK"""
    async with aiosqlite.connect(AFK_DB) as db:
        await db.execute(
            'INSERT OR REPLACE INTO afk (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?)',
            (user_id, guild_id, reason, datetime.now().isoformat())
        )
        await db.commit()

async def remove_afk(user_id):
    """Remove a user from AFK"""
    async with aiosqlite.connect(AFK_DB) as db:
        await db.execute('DELETE FROM afk WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_afk(user_id):
    """Get AFK status of a user"""
    async with aiosqlite.connect(AFK_DB) as db:
        cursor = await db.execute(
            'SELECT * FROM afk WHERE user_id = ?',
            (user_id,)
        )
        data = await cursor.fetchone()
        if data:
            return {
                'user_id': data[0],
                'guild_id': data[1],
                'reason': data[2],
                'timestamp': datetime.fromisoformat(data[3])
            }
        return None

def is_owner():
    """Check if user is a bot owner"""
    async def predicate(ctx):
        return ctx.author.id in OWNER_IDS
    return commands.check(predicate)

def is_owner_slash():
    """Check if user is a bot owner for slash commands"""
    async def predicate(interaction):
        return interaction.user.id in OWNER_IDS
    return app_commands.check(predicate)

# ============= EVENTS =============

@bot.event
async def on_ready():
    """Bot startup event"""
    logger.info(f'{bot.user} has connected to Discord!')
    
    await init_db()
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    
    await bot.change_presence(activity=discord.Game(name=f"{config['prefix']}help | Prefixes: ! ,, R! a!"))

@bot.event
async def on_message(message):
    """Message handler"""
    if message.author.bot:
        return
    
    # Check blacklist
    if message.author.id in config['blacklist']:
        return
    
    # AFK System - Check if user is AFK and remove it
    afk_data = await get_afk(message.author.id)
    if afk_data and afk_data['guild_id'] == message.guild.id:
        await remove_afk(message.author.id)
        time_afk = datetime.now() - afk_data['timestamp']
        seconds = int(time_afk.total_seconds())
        
        if seconds < 60:
            time_str = f"{seconds} seconds"
        elif seconds < 3600:
            time_str = f"{seconds // 60} minutes {seconds % 60} seconds"
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            time_str = f"{hours} hours {minutes} minutes"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            time_str = f"{days} days {hours} hours"
        
        await message.channel.send(
            f"Welcome back, {message.author.mention}! I removed your AFK. You were AFK for {time_str}."
        )
    
    # Check if message mentions any AFK users
    for mention in message.mentions:
        afk_data = await get_afk(mention.id)
        if afk_data and afk_data['guild_id'] == message.guild.id:
            time_afk = datetime.now() - afk_data['timestamp']
            seconds = int(time_afk.total_seconds())
            
            if seconds < 60:
                time_str = f"{seconds} seconds"
            elif seconds < 3600:
                time_str = f"{seconds // 60} minutes {seconds % 60} seconds"
            elif seconds < 86400:
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                time_str = f"{hours} hours {minutes} minutes"
            else:
                days = seconds // 86400
                hours = (seconds % 86400) // 3600
                time_str = f"{days} days {hours} hours"
            
            await message.channel.send(
                f"{mention.display_name} is currently AFK for {afk_data['reason']} - {time_str} ago."
            )
    
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    """Member join event"""
    if member.id in config['blacklist']:
        await member.kick(reason="Blacklisted user")
        return
    
    if config['welcome_channel']:
        channel = bot.get_channel(config['welcome_channel'])
        if channel:
            embed = discord.Embed(
                title="👋 Welcome!",
                description=f"Welcome to {member.guild.name}, {member.mention}!",
                color=discord.Color.green()
            )
            embed.add_field(name="Member Count", value=f"{member.guild.member_count} members")
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
    
    if config['auto_roles']:
        for role_id in config['auto_roles']:
            role = member.guild.get_role(role_id)
            if role:
                await member.add_roles(role)

@bot.event
async def on_member_remove(member):
    """Member leave event"""
    if config['mod_log_channel']:
        channel = bot.get_channel(config['mod_log_channel'])
        if channel:
            embed = discord.Embed(
                title="👋 Member Left",
                description=f"{member.name}#{member.discriminator} has left the server",
                color=discord.Color.red()
            )
            embed.add_field(name="Member Count", value=f"{member.guild.member_count} members")
            await channel.send(embed=embed)

# ============= PREFIX COMMANDS =============

@bot.command(name='afk', aliases=['a'])
async def afk(ctx, *, reason="No reason provided"):
    """Set yourself as AFK"""
    await set_afk(ctx.author.id, ctx.guild.id, reason)
    await ctx.send(f"AFK Set!\nYou are now afk in this server. Reason: {reason}")

@bot.command(name='balance', aliases=['bal'])
async def balance_prefix(ctx, member: discord.Member = None):
    """Check balance with fancy UI"""
    target = member or ctx.author
    data = await get_economy_data(target.id)
    rank = await get_global_rank(target.id)
    
    embed = discord.Embed(
        title=f"Balance",
        color=discord.Color.blue()
    )
    
    # Create the balance display with emojis
    wallet_emoji = "💳" 
    bank_emoji = "🏦"
    
    embed.add_field(
        name="Wallet",
        value=f"{wallet_emoji} {data['balance']:,}",
        inline=True
    )
    embed.add_field(
        name="Bank",
        value=f"{bank_emoji} {data['bank']:,}",
        inline=True
    )
    embed.add_field(
        name="Net",
        value=f"💰 {data['balance'] + data['bank']:,}",
        inline=True
    )
    embed.add_field(
        name="Global Rank",
        value=f"#{rank}",
        inline=False
    )
    
    # Add buttons (Withdraw/Deposit)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Withdraw", style=discord.ButtonStyle.success, custom_id="withdraw"))
    view.add_item(discord.ui.Button(label="Deposit", style=discord.ButtonStyle.primary, custom_id="deposit"))
    
    await ctx.send(embed=embed, view=view)

@bot.command(name='daily')
async def daily_prefix(ctx):
    """Claim daily reward"""
    user_id = ctx.author.id
    data = await get_economy_data(user_id)
    
    # Check cooldown
    if data['last_daily']:
        last_daily = datetime.fromisoformat(data['last_daily'])
        cooldown = config['economy']['daily_cooldown']
        time_passed = (datetime.now() - last_daily).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await ctx.send(f"⏰ You already claimed your daily reward! Come back in **{hours}h {minutes}m**.")
            return
    
    reward = config['economy']['daily_reward']
    new_balance = data['balance'] + reward
    new_streak = data['work_streak'] + 1
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_daily=datetime.now(),
        work_streak=new_streak,
        total_earned=data['total_earned'] + reward
    )
    
    await ctx.send(f"<@{ctx.author.id}>: You have claimed your daily bonus of 🟢 {reward} for a {new_streak} day streak")

@bot.command(name='weekly')
async def weekly_prefix(ctx):
    """Claim weekly reward"""
    user_id = ctx.author.id
    data = await get_economy_data(user_id)
    
    # Check cooldown
    if data['last_weekly']:
        last_weekly = datetime.fromisoformat(data['last_weekly'])
        cooldown = config['economy']['weekly_cooldown']
        time_passed = (datetime.now() - last_weekly).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            await ctx.send(f"⏰ You already claimed your weekly reward! Come back in **{days}d {hours}h**.")
            return
    
    reward = config['economy']['weekly_reward']
    new_balance = data['balance'] + reward
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_weekly=datetime.now(),
        total_earned=data['total_earned'] + reward
    )
    
    await ctx.send(f"<@{ctx.author.id}>: You have claimed your weekly bonus of 🟢 {reward}!")

@bot.command(name='monthly')
async def monthly_prefix(ctx):
    """Claim monthly reward"""
    user_id = ctx.author.id
    data = await get_economy_data(user_id)
    
    # Check cooldown
    if data['last_monthly']:
        last_monthly = datetime.fromisoformat(data['last_monthly'])
        cooldown = config['economy']['monthly_cooldown']
        time_passed = (datetime.now() - last_monthly).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            await ctx.send(f"⏰ You already claimed your monthly reward! Come back in **{days}d {hours}h**.")
            return
    
    reward = config['economy']['monthly_reward']
    new_balance = data['balance'] + reward
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_monthly=datetime.now(),
        total_earned=data['total_earned'] + reward
    )
    
    await ctx.send(f"<@{ctx.author.id}>: You have claimed your monthly bonus of 🟢 {reward}!")

@bot.command(name='beg')
async def beg_prefix(ctx):
    """Beg for money"""
    user_id = ctx.author.id
    data = await get_economy_data(user_id)
    
    # Check cooldown
    if data['last_beg']:
        last_beg = datetime.fromisoformat(data['last_beg'])
        cooldown = config['economy']['beg_cooldown']
        time_passed = (datetime.now() - last_beg).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            await ctx.send(f"⏰ You need to wait **{remaining}s** before begging again!")
            return
    
    # 70% chance to get money
    if random.random() < 0.7:
        min_earn = config['economy']['beg_min']
        max_earn = config['economy']['beg_max']
        earned = random.randint(min_earn, max_earn)
        
        new_balance = data['balance'] + earned
        await update_economy_data(
            user_id,
            balance=new_balance,
            last_beg=datetime.now(),
            total_earned=data['total_earned'] + earned
        )
        
        await ctx.send(f"<@{ctx.author.id}>: You begged and received 🥺 {earned}")
    else:
        await update_economy_data(user_id, last_beg=datetime.now())
        await ctx.send(f"⚠️ <@{ctx.author.id}>: You got nothing for begging")

@bot.command(name='give')
async def give_prefix(ctx, member: discord.Member, amount: str):
    """Give money to another member"""
    user_id = ctx.author.id
    target_id = member.id
    
    user_data = await get_economy_data(user_id)
    target_data = await get_economy_data(target_id)
    
    # Handle 'all' amount
    if amount.lower() == 'all':
        amount_to_give = user_data['balance']
    else:
        try:
            amount_to_give = int(amount)
        except ValueError:
            await ctx.send("❌ Invalid amount! Use a number or 'all'")
            return
    
    if amount_to_give <= 0:
        await ctx.send("❌ You can't give 0 or negative money!")
        return
    
    if amount_to_give > user_data['balance']:
        await ctx.send(f"❌ You don't have enough money! You have ${user_data['balance']:,}")
        return
    
    # Update balances
    new_user_balance = user_data['balance'] - amount_to_give
    new_target_balance = target_data['balance'] + amount_to_give
    
    await update_economy_data(user_id, balance=new_user_balance)
    await update_economy_data(target_id, balance=new_target_balance)
    
    await ctx.send(f"<@{ctx.author.id}>: Sent ● {amount_to_give} to <@{target_id}>")

@bot.command(name='say')
async def say_prefix(ctx, *, message):
    """Make the bot say something (no embed)"""
    # Delete the command message
    try:
        await ctx.message.delete()
    except:
        pass
    
    # Send the message as the bot
    await ctx.send(message)

@bot.command(name='fakenuke')
@commands.has_permissions(administrator=True)
async def fake_nuke(ctx, member: discord.Member = None):
    """Trigger a fake server nuke alert"""
    if member:
        await ctx.send(
            f"⚠️ **WARNING: SERVER NUKE IN PROGRESS** ⚠️\n"
            f"Thank you @{member.display_name} for nuking this server! The channels will be deleted soon!"
        )
    else:
        await ctx.send(
            f"⚠️ **WARNING: SERVER NUKE IN PROGRESS** ⚠️\n"
            f"The server is being nuked! All channels will be deleted soon!"
        )

# ============= SLASH COMMANDS =============

@bot.tree.command(name="avatar", description="View a member's avatar")
@app_commands.describe(member="The member whose avatar you want to view")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    """Slash command to view a member's avatar"""
    member = member or interaction.user
    
    embed = discord.Embed(
        title=f"{member.display_name}'s Avatar",
        color=member.color or discord.Color.blue()
    )
    embed.set_image(url=member.display_avatar.url)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Download",
        style=discord.ButtonStyle.link,
        url=member.display_avatar.url
    ))
    
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="balance", description="Check your or someone else's balance")
@app_commands.describe(member="The member whose balance you want to check")
async def balance_slash(interaction: discord.Interaction, member: discord.Member = None):
    """Slash command for balance with fancy UI"""
    await interaction.response.defer()
    
    target = member or interaction.user
    data = await get_economy_data(target.id)
    rank = await get_global_rank(target.id)
    
    embed = discord.Embed(
        title=f"Balance",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Wallet",
        value=f"💳 {data['balance']:,}",
        inline=True
    )
    embed.add_field(
        name="Bank",
        value=f"🏦 {data['bank']:,}",
        inline=True
    )
    embed.add_field(
        name="Net",
        value=f"💰 {data['balance'] + data['bank']:,}",
        inline=True
    )
    embed.add_field(
        name="Global Rank",
        value=f"#{rank}",
        inline=False
    )
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Withdraw", style=discord.ButtonStyle.success, custom_id="withdraw"))
    view.add_item(discord.ui.Button(label="Deposit", style=discord.ButtonStyle.primary, custom_id="deposit"))
    
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="daily", description="Claim your daily reward")
async def daily_slash(interaction: discord.Interaction):
    """Daily reward slash command"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    data = await get_economy_data(user_id)
    
    if data['last_daily']:
        last_daily = datetime.fromisoformat(data['last_daily'])
        cooldown = config['economy']['daily_cooldown']
        time_passed = (datetime.now() - last_daily).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await interaction.followup.send(
                f"⏰ You already claimed your daily reward! Come back in **{hours}h {minutes}m**."
            )
            return
    
    reward = config['economy']['daily_reward']
    new_balance = data['balance'] + reward
    new_streak = data['work_streak'] + 1
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_daily=datetime.now(),
        work_streak=new_streak,
        total_earned=data['total_earned'] + reward
    )
    
    await interaction.followup.send(
        f"<@{interaction.user.id}>: You have claimed your daily bonus of 🟢 {reward} for a {new_streak} day streak"
    )

@bot.tree.command(name="weekly", description="Claim your weekly reward")
async def weekly_slash(interaction: discord.Interaction):
    """Weekly reward slash command"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    data = await get_economy_data(user_id)
    
    if data['last_weekly']:
        last_weekly = datetime.fromisoformat(data['last_weekly'])
        cooldown = config['economy']['weekly_cooldown']
        time_passed = (datetime.now() - last_weekly).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            await interaction.followup.send(
                f"⏰ You already claimed your weekly reward! Come back in **{days}d {hours}h**."
            )
            return
    
    reward = config['economy']['weekly_reward']
    new_balance = data['balance'] + reward
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_weekly=datetime.now(),
        total_earned=data['total_earned'] + reward
    )
    
    await interaction.followup.send(
        f"<@{interaction.user.id}>: You have claimed your weekly bonus of 🟢 {reward}!"
    )

@bot.tree.command(name="monthly", description="Claim your monthly reward")
async def monthly_slash(interaction: discord.Interaction):
    """Monthly reward slash command"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    data = await get_economy_data(user_id)
    
    if data['last_monthly']:
        last_monthly = datetime.fromisoformat(data['last_monthly'])
        cooldown = config['economy']['monthly_cooldown']
        time_passed = (datetime.now() - last_monthly).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            await interaction.followup.send(
                f"⏰ You already claimed your monthly reward! Come back in **{days}d {hours}h**."
            )
            return
    
    reward = config['economy']['monthly_reward']
    new_balance = data['balance'] + reward
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_monthly=datetime.now(),
        total_earned=data['total_earned'] + reward
    )
    
    await interaction.followup.send(
        f"<@{interaction.user.id}>: You have claimed your monthly bonus of 🟢 {reward}!"
    )

@bot.tree.command(name="beg", description="Beg for money")
async def beg_slash(interaction: discord.Interaction):
    """Beg command slash"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    data = await get_economy_data(user_id)
    
    if data['last_beg']:
        last_beg = datetime.fromisoformat(data['last_beg'])
        cooldown = config['economy']['beg_cooldown']
        time_passed = (datetime.now() - last_beg).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            await interaction.followup.send(
                f"⏰ You need to wait **{remaining}s** before begging again!"
            )
            return
    
    if random.random() < 0.7:
        min_earn = config['economy']['beg_min']
        max_earn = config['economy']['beg_max']
        earned = random.randint(min_earn, max_earn)
        
        new_balance = data['balance'] + earned
        await update_economy_data(
            user_id,
            balance=new_balance,
            last_beg=datetime.now(),
            total_earned=data['total_earned'] + earned
        )
        
        await interaction.followup.send(
            f"<@{interaction.user.id}>: You begged and received 🥺 {earned}"
        )
    else:
        await update_economy_data(user_id, last_beg=datetime.now())
        await interaction.followup.send(
            f"⚠️ <@{interaction.user.id}>: You got nothing for begging"
        )

@bot.tree.command(name="give", description="Give money to another member")
@app_commands.describe(member="The member to give money to", amount="Amount to give (or 'all')")
async def give_slash(interaction: discord.Interaction, member: discord.Member, amount: str):
    """Give money slash command"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    target_id = member.id
    
    user_data = await get_economy_data(user_id)
    target_data = await get_economy_data(target_id)
    
    if amount.lower() == 'all':
        amount_to_give = user_data['balance']
    else:
        try:
            amount_to_give = int(amount)
        except ValueError:
            await interaction.followup.send("❌ Invalid amount! Use a number or 'all'")
            return
    
    if amount_to_give <= 0:
        await interaction.followup.send("❌ You can't give 0 or negative money!")
        return
    
    if amount_to_give > user_data['balance']:
        await interaction.followup.send(f"❌ You don't have enough money! You have ${user_data['balance']:,}")
        return
    
    new_user_balance = user_data['balance'] - amount_to_give
    new_target_balance = target_data['balance'] + amount_to_give
    
    await update_economy_data(user_id, balance=new_user_balance)
    await update_economy_data(target_id, balance=new_target_balance)
    
    await interaction.followup.send(
        f"<@{interaction.user.id}>: Sent ● {amount_to_give} to <@{target_id}>"
    )

@bot.tree.command(name="say", description="Make the bot say something")
@app_commands.describe(message="The message to say")
async def say_slash(interaction: discord.Interaction, message: str):
    """Say command (no embed)"""
    await interaction.response.send_message("Message sent!", ephemeral=True)
    await interaction.channel.send(message)

@bot.tree.command(name="fakenuke", description="Trigger a fake server nuke alert")
@app_commands.describe(member="The member to blame for the nuke")
async def fake_nuke_slash(interaction: discord.Interaction, member: discord.Member = None):
    """Fake nuke slash command"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions!", ephemeral=True)
        return
    
    await interaction.response.send_message("💥 Nuke alert sent!", ephemeral=True)
    
    if member:
        await interaction.channel.send(
            f"⚠️ **WARNING: SERVER NUKE IN PROGRESS** ⚠️\n"
            f"Thank you @{member.display_name} for nuking this server! The channels will be deleted soon!"
        )
    else:
        await interaction.channel.send(
            f"⚠️ **WARNING: SERVER NUKE IN PROGRESS** ⚠️\n"
            f"The server is being nuked! All channels will be deleted soon!"
        )

@bot.tree.command(name="work", description="Work to earn money")
async def work_slash(interaction: discord.Interaction):
    """Work command"""
    await interaction.response.defer()
    
    user_id = interaction.user.id
    data = await get_economy_data(user_id)
    
    if data['last_work']:
        last_work = datetime.fromisoformat(data['last_work'])
        cooldown = config['economy']['work_cooldown']
        time_passed = (datetime.now() - last_work).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await interaction.followup.send(
                f"⏰ You need to wait **{hours}h {minutes}m** before working again!"
            )
            return
    
    min_earn = config['economy']['work_min']
    max_earn = config['economy']['work_max']
    earned = random.randint(min_earn, max_earn)
    
    new_balance = data['balance'] + earned
    new_streak = data['work_streak'] + 1
    
    await update_economy_data(
        user_id,
        balance=new_balance,
        last_work=datetime.now(),
        work_streak=new_streak,
        total_earned=data['total_earned'] + earned
    )
    
    work_messages = [
        f"💼 You worked as a developer and earned **${earned}**!",
        f"🔧 You fixed some bugs and earned **${earned}**!",
        f"📊 You analyzed data and earned **${earned}**!",
        f"🎨 You designed a logo and earned **${earned}**!",
        f"📝 You wrote documentation and earned **${earned}**!",
        f"🤝 You helped a client and earned **${earned}**!"
    ]
    
    embed = discord.Embed(
        title="💼 Work Complete!",
        description=random.choice(work_messages),
        color=discord.Color.green()
    )
    embed.add_field(name="New Balance", value=f"${new_balance:,}", inline=True)
    embed.set_footer(text=f"Streak: {new_streak} days")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="leaderboard", description="View the wealth leaderboard")
async def leaderboard_slash(interaction: discord.Interaction):
    """Show top 10 richest users"""
    await interaction.response.defer()
    
    async with aiosqlite.connect(ECONOMY_DB) as db:
        cursor = await db.execute(
            'SELECT user_id, balance, bank FROM economy ORDER BY (balance + bank) DESC LIMIT 10'
        )
        top_users = await cursor.fetchall()
    
    if not top_users:
        await interaction.followup.send("📊 No users found in the economy system!")
        return
    
    embed = discord.Embed(
        title="🏆 Wealth Leaderboard",
        color=discord.Color.gold()
    )
    
    for i, (user_id, balance, bank) in enumerate(top_users, 1):
        total = balance + bank
        user = bot.get_user(user_id)
        name = user.display_name if user else f"Unknown User ({user_id})"
        
        medal = ""
        if i == 1:
            medal = "🥇 "
        elif i == 2:
            medal = "🥈 "
        elif i == 3:
            medal = "🥉 "
        
        embed.add_field(
            name=f"{medal}#{i} {name}",
            value=f"💰 ${total:,} (Wallet: ${balance:,} | Bank: ${bank:,})",
            inline=False
        )
    
    embed.set_footer(text="Top 10 richest members")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="rob", description="Rob another member")
@app_commands.describe(member="The member you want to rob")
async def rob_slash(interaction: discord.Interaction, member: discord.Member):
    """Rob command"""
    await interaction.response.defer()
    
    if member.id == interaction.user.id:
        await interaction.followup.send("❌ You can't rob yourself!")
        return
    
    if member.bot:
        await interaction.followup.send("❌ You can't rob a bot!")
        return
    
    user_id = interaction.user.id
    target_id = member.id
    
    user_data = await get_economy_data(user_id)
    target_data = await get_economy_data(target_id)
    
    if user_data['last_rob']:
        last_rob = datetime.fromisoformat(user_data['last_rob'])
        cooldown = config['economy']['rob_cooldown']
        time_passed = (datetime.now() - last_rob).total_seconds()
        
        if time_passed < cooldown:
            remaining = int(cooldown - time_passed)
            minutes = remaining // 60
            await interaction.followup.send(
                f"⏰ You need to wait **{minutes}m** before robbing again!"
            )
            return
    
    if target_data['balance'] < 10:
        await interaction.followup.send(f"❌ {member.display_name} is too poor to rob!")
        await update_economy_data(user_id, last_rob=datetime.now())
        return
    
    if random.random() < 0.5:
        min_rob = config['economy']['rob_min']
        max_rob = min(config['economy']['rob_max'], target_data['balance'] // 2)
        rob_amount = random.randint(min_rob, max_rob)
        
        new_user_balance = user_data['balance'] + rob_amount
        new_target_balance = target_data['balance'] - rob_amount
        
        await update_economy_data(user_id, balance=new_user_balance, last_rob=datetime.now())
        await update_economy_data(target_id, balance=new_target_balance)
        
        embed = discord.Embed(
            title="🔫 Robbery Successful!",
            description=f"You successfully robbed **${rob_amount}** from {member.mention}!",
            color=discord.Color.green()
        )
        embed.add_field(name="Your New Balance", value=f"${new_user_balance:,}", inline=True)
        embed.add_field(name="Their New Balance", value=f"${new_target_balance:,}", inline=True)
    else:
        penalty = config['economy']['rob_fail_penalty']
        new_user_balance = max(0, user_data['balance'] - penalty)
        
        await update_economy_data(user_id, balance=new_user_balance, last_rob=datetime.now())
        
        embed = discord.Embed(
            title="😰 Robbery Failed!",
            description=f"You got caught trying to rob {member.mention}! You lost **${penalty}**!",
            color=discord.Color.red()
        )
        embed.add_field(name="Your New Balance", value=f"${new_user_balance:,}", inline=True)
    
    await interaction.followup.send(embed=embed)

# ============= BUTTON HANDLERS =============

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        if interaction.user.id not in OWNER_IDS:
            # Handle economy buttons for all users
            if interaction.data.get("custom_id") in ["withdraw", "deposit"]:
                await handle_economy_button(interaction)
            return
        
        custom_id = interaction.data.get("custom_id")
        
        if custom_id == "toggle_dice":
            config['admin_settings']['dice_rig'] = not config['admin_settings']['dice_rig']
            await interaction.response.send_message(f"✅ Dice Rig set to: {config['admin_settings']['dice_rig']}", ephemeral=True)
            
        elif custom_id == "toggle_work":
            config['admin_settings']['work_rig'] = not config['admin_settings']['work_rig']
            await interaction.response.send_message(f"✅ Work Rig set to: {config['admin_settings']['work_rig']}", ephemeral=True)
            
        elif custom_id == "toggle_beg":
            config['admin_settings']['beg_rig'] = not config['admin_settings']['beg_rig']
            await interaction.response.send_message(f"✅ Beg Rig set to: {config['admin_settings']['beg_rig']}", ephemeral=True)
            
        elif custom_id == "toggle_rob":
            config['admin_settings']['rob_rig'] = not config['admin_settings']['rob_rig']
            await interaction.response.send_message(f"✅ Rob Rig set to: {config['admin_settings']['rob_rig']}", ephemeral=True)
            
        elif custom_id == "ghostping_settings":
            await ghostping_settings(interaction)
            
        elif custom_id == "view_whitelist":
            if config['whitelist']:
                users = []
                for uid in config['whitelist'][:20]:
                    user = bot.get_user(uid)
                    users.append(f"{user.mention if user else uid} ({uid})")
                await interaction.response.send_message(f"📋 Whitelisted Users:\n" + "\n".join(users), ephemeral=True)
            else:
                await interaction.response.send_message("📋 No users in whitelist.", ephemeral=True)
                
        elif custom_id == "view_blacklist":
            if config['blacklist']:
                users = []
                for uid in config['blacklist'][:20]:
                    user = bot.get_user(uid)
                    users.append(f"{user.mention if user else uid} ({uid})")
                await interaction.response.send_message(f"📋 Blacklisted Users:\n" + "\n".join(users), ephemeral=True)
            else:
                await interaction.response.send_message("📋 No users in blacklist.", ephemeral=True)
        
        elif custom_id in ["withdraw", "deposit"]:
            await handle_economy_button(interaction)

async def handle_economy_button(interaction: discord.Interaction):
    """Handle withdraw/deposit button clicks"""
    class EconomyModal(discord.ui.Modal, title="Transaction"):
        amount = discord.ui.TextInput(
            label="Amount",
            placeholder="Enter amount or 'all'",
            required=True
        )
        
        async def on_submit(self, submit_interaction: discord.Interaction):
            user_id = submit_interaction.user.id
            data = await get_economy_data(user_id)
            action = submit_interaction.data.get("custom_id") if hasattr(submit_interaction, 'data') else "withdraw"
            
            amount_str = self.amount.value.lower()
            if amount_str == 'all':
                if action == "withdraw":
                    amount_to_transfer = data['bank']
                else:
                    amount_to_transfer = data['balance']
            else:
                try:
                    amount_to_transfer = int(amount_str)
                except ValueError:
                    await submit_interaction.response.send_message("❌ Invalid amount!", ephemeral=True)
                    return
            
            if amount_to_transfer <= 0:
                await submit_interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
                return
            
            if action == "withdraw":
                if amount_to_transfer > data['bank']:
                    await submit_interaction.response.send_message(f"❌ You don't have that much in the bank! You have ${data['bank']:,}", ephemeral=True)
                    return
                
                new_balance = data['balance'] + amount_to_transfer
                new_bank = data['bank'] - amount_to_transfer
                await update_economy_data(user_id, balance=new_balance, bank=new_bank)
                await submit_interaction.response.send_message(f"✅ Withdrew ${amount_to_transfer:,} from the bank!", ephemeral=True)
                
            else:  # deposit
                if amount_to_transfer > data['balance']:
                    await submit_interaction.response.send_message(f"❌ You don't have that much in your wallet! You have ${data['balance']:,}", ephemeral=True)
                    return
                
                new_balance = data['balance'] - amount_to_transfer
                new_bank = data['bank'] + amount_to_transfer
                await update_economy_data(user_id, balance=new_balance, bank=new_bank)
                await submit_interaction.response.send_message(f"✅ Deposited ${amount_to_transfer:,} to the bank!", ephemeral=True)
    
    await interaction.response.send_modal(EconomyModal())

async def ghostping_settings(interaction: discord.Interaction):
    """Ghostping settings modal"""
    class GhostpingModal(discord.ui.Modal, title="Ghostping Settings"):
        enabled = discord.ui.TextInput(
            label="Enable Ghostping (yes/no)",
            placeholder="yes or no",
            required=True
        )
        times = discord.ui.TextInput(
            label="Number of times to ping",
            placeholder="Enter a number (1-10)",
            required=True
        )
        
        async def on_submit(self, submit_interaction: discord.Interaction):
            try:
                enabled = self.enabled.value.lower() == "yes"
                times = int(self.times.value)
                if times < 1 or times > 10:
                    await submit_interaction.response.send_message("❌ Times must be between 1 and 10!", ephemeral=True)
                    return
                
                config['admin_settings']['ghostping_enabled'] = enabled
                config['admin_settings']['ghostping_times'] = times
                
                await submit_interaction.response.send_message(
                    f"✅ Ghostping settings updated!\nEnabled: {enabled}\nTimes: {times}",
                    ephemeral=True
                )
            except ValueError:
                await submit_interaction.response.send_message("❌ Invalid input! Please enter valid values.", ephemeral=True)
    
    await interaction.response.send_modal(GhostpingModal())

# ============= WHITELIST/BLACKLIST COMMANDS =============

@bot.command(name='whitelist')
@is_owner()
async def whitelist_cmd(ctx, user: discord.User):
    """Add a user to whitelist (Owner only)"""
    if user.id in config['whitelist']:
        await ctx.send(f"❌ {user.mention} is already whitelisted.")
        return
    
    config['whitelist'].append(user.id)
    if user.id in config['blacklist']:
        config['blacklist'].remove(user.id)
    
    embed = discord.Embed(
        title="✅ User Whitelisted",
        description=f"{user.mention} has been added to the whitelist.",
        color=discord.Color.green()
    )
    embed.add_field(name="User ID", value=user.id)
    await ctx.send(embed=embed)

@bot.command(name='blacklist')
@is_owner()
async def blacklist_cmd(ctx, user: discord.User):
    """Add a user to blacklist (Owner only)"""
    if user.id in config['blacklist']:
        await ctx.send(f"❌ {user.mention} is already blacklisted.")
        return
    
    config['blacklist'].append(user.id)
    if user.id in config['whitelist']:
        config['whitelist'].remove(user.id)
    
    embed = discord.Embed(
        title="⛔ User Blacklisted",
        description=f"{user.mention} has been added to the blacklist.",
        color=discord.Color.red()
    )
    embed.add_field(name="User ID", value=user.id)
    await ctx.send(embed=embed)

@bot.command(name='removewhitelist')
@is_owner()
async def remove_whitelist(ctx, user: discord.User):
    """Remove a user from whitelist (Owner only)"""
    if user.id not in config['whitelist']:
        await ctx.send(f"❌ {user.mention} is not whitelisted.")
        return
    
    config['whitelist'].remove(user.id)
    await ctx.send(f"✅ {user.mention} removed from whitelist.")

@bot.command(name='removeblacklist')
@is_owner()
async def remove_blacklist(ctx, user: discord.User):
    """Remove a user from blacklist (Owner only)"""
    if user.id not in config['blacklist']:
        await ctx.send(f"❌ {user.mention} is not blacklisted.")
        return
    
    config['blacklist'].remove(user.id)
    await ctx.send(f"✅ {user.mention} removed from blacklist.")

# ============= ADMIN PANEL =============

@bot.tree.command(name="admin", description="Admin panel for bot owners")
@is_owner_slash()
async def admin_panel(interaction: discord.Interaction):
    """Admin panel with all controls"""
    if interaction.user.id not in OWNER_IDS:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔧 Admin Control Panel",
        description="Manage bot settings and features",
        color=discord.Color.blue()
    )
    
    settings = config['admin_settings']
    embed.add_field(
        name="Current Settings",
        value=f"""
        🎲 Dice Rig: {settings['dice_rig']}
        💼 Work Rig: {settings['work_rig']}
        🙏 Beg Rig: {settings['beg_rig']}
        🔫 Rob Rig: {settings['rob_rig']}
        👻 Ghostping: {settings['ghostping_enabled']} ({settings['ghostping_times']}x)
        """,
        inline=False
    )
    
    embed.add_field(
        name="Whitelist",
        value=f"Users: {len(config['whitelist'])}",
        inline=True
    )
    embed.add_field(
        name="Blacklist",
        value=f"Users: {len(config['blacklist'])}",
        inline=True
    )
    
    embed.set_footer(text="Use buttons below to control settings")
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🔄 Toggle Dice Rig", style=discord.ButtonStyle.primary, custom_id="toggle_dice"))
    view.add_item(discord.ui.Button(label="🔄 Toggle Work Rig", style=discord.ButtonStyle.primary, custom_id="toggle_work"))
    view.add_item(discord.ui.Button(label="🔄 Toggle Beg Rig", style=discord.ButtonStyle.primary, custom_id="toggle_beg"))
    view.add_item(discord.ui.Button(label="🔄 Toggle Rob Rig", style=discord.ButtonStyle.primary, custom_id="toggle_rob"))
    view.add_item(discord.ui.Button(label="👻 Ghostping Settings", style=discord.ButtonStyle.secondary, custom_id="ghostping_settings"))
    view.add_item(discord.ui.Button(label="📊 View Whitelist", style=discord.ButtonStyle.success, custom_id="view_whitelist"))
    view.add_item(discord.ui.Button(label="📊 View Blacklist", style=discord.ButtonStyle.danger, custom_id="view_blacklist"))
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ============= OWNER COMMANDS =============

@bot.command(name='ghostping')
@is_owner()
async def ghostping(ctx, member: discord.Member):
    """Ghost ping a member (Owner only)"""
    if not config['admin_settings']['ghostping_enabled']:
        await ctx.send("❌ Ghostping is disabled. Enable it in the admin panel.")
        return
    
    times = config['admin_settings']['ghostping_times']
    
    await ctx.send(f"👻 Ghostpinning {member.mention} {times} times...")
    
    for i in range(times):
        await ctx.send(member.mention)
        await asyncio.sleep(0.5)
    
    await asyncio.sleep(1)
    messages = await ctx.channel.history(limit=times + 2).flatten()
    for msg in messages:
        if msg.author == bot.user and msg.content == member.mention:
            await msg.delete()

@bot.command(name='ghostpingall')
@is_owner()
async def ghostping_all(ctx):
    """Ghost ping all members in the server (Owner only)"""
    if not config['admin_settings']['ghostping_enabled']:
        await ctx.send("❌ Ghostping is disabled. Enable it in the admin panel.")
        return
    
    members = [m for m in ctx.guild.members if not m.bot]
    times = config['admin_settings']['ghostping_times']
    
    await ctx.send(f"👻 Ghostpinning {len(members)} members {times} times...")
    
    for member in members[:10]:
        for i in range(times):
            await ctx.send(member.mention)
            await asyncio.sleep(0.3)
    
    await asyncio.sleep(1)
    messages = await ctx.channel.history(limit=times * 10 + 2).flatten()
    for msg in messages:
        if msg.author == bot.user:
            await msg.delete()

# ============= SYNC SLASH COMMANDS =============

@bot.command(name='sync')
@is_owner()
async def sync_commands(ctx):
    """Sync slash commands (Owner only)"""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} slash commands!")
    except Exception as e:
        await ctx.send(f"❌ Failed to sync commands: {e}")

# ============= UTILITY COMMANDS =============

@bot.command(name='ping')
async def ping(ctx):
    """Check bot latency"""
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: {latency}ms",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='userinfo')
async def userinfo(ctx, member: discord.Member = None):
    """Get information about a user"""
    member = member or ctx.author
    
    embed = discord.Embed(
        title=f"User Info - {member.name}",
        color=member.color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=False)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Joined Discord", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Roles", value=", ".join([role.mention for role in member.roles[1:10]]) or "None", inline=False)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='serverinfo')
async def serverinfo(ctx):
    """Get information about the server"""
    guild = ctx.guild
    
    embed = discord.Embed(
        title=f"Server Info - {guild.name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Boost Level", value=guild.premium_tier, inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='help')
async def help_command(ctx):
    """Display help information"""
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Here are all available commands:",
        color=config['embed_color']
    )
    
    economy_commands = """
    `!balance` or `!bal` - Check balance with UI
    `!daily` - Claim daily reward ($100)
    `!weekly` - Claim weekly reward ($500)
    `!monthly` - Claim monthly reward ($1000)
    `!beg` - Beg for money
    `!work` - Work to earn money
    `!give @user <amount/all>` - Give money to someone
    `!rob @user` - Rob someone
    `/dice <amount>` - Roll dice to gamble
    """
    embed.add_field(name="💰 Economy Commands", value=economy_commands, inline=False)
    
    moderation_commands = """
    `!kick @user [reason]` - Kick a member
    `!ban @user [reason]` - Ban a member
    `!mute @user [reason]` - Mute a member
    `!unmute @user` - Unmute a member
    `!clear <amount>` - Clear messages (max 100)
    `!fakenuke [@user]` - Fake server nuke alert
    """
    embed.add_field(name="🛡️ Moderation", value=moderation_commands, inline=False)
    
    utility_commands = """
    `!afk <reason>` - Set yourself as AFK
    `!say <message>` - Make the bot say something
    `!avatar [@user]` - View avatar
    `!ping` - Check bot latency
    `!userinfo [@user]` - Get user information
    `!serverinfo` - Get server information
    """
    embed.add_field(name="📊 Utility", value=utility_commands, inline=False)
    
    owner_commands = """
    `!whitelist @user` - Add user to whitelist
    `!blacklist @user` - Add user to blacklist
    `!removewhitelist @user` - Remove from whitelist
    `!removeblacklist @user` - Remove from blacklist
    `/admin` - Admin control panel
    `!ghostping @user` - Ghost ping a user
    `!ghostpingall` - Ghost ping all users
    `!sync` - Sync slash commands
    """
    embed.add_field(name="👑 Owner Commands", value=owner_commands, inline=False)
    
    embed.set_footer(text="Prefixes: ! ,, R! a! | Slash commands also available (/command)")
    await ctx.send(embed=embed)

# ============= HELPER FUNCTIONS =============

async def log_mod_action(ctx, action, member, reason):
    """Log moderation actions to the mod log channel"""
    if not config['mod_log_channel']:
        return
    
    channel = bot.get_channel(config['mod_log_channel'])
    if not channel:
        return
    
    embed = discord.Embed(
        title=f"📝 Mod Action: {action}",
        color=discord.Color.blue()
    )
    embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Channel", value=ctx.channel.mention, inline=True)
    embed.set_footer(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    await channel.send(embed=embed)

# ============= MODERATION COMMANDS =============

@bot.command(name='kick')
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    """Kick a member from the server"""
    if member.id in OWNER_IDS:
        await ctx.send("❌ You cannot kick a bot owner!")
        return
    
    try:
        await member.kick(reason=reason)
        embed = discord.Embed(
            title="✅ Member Kicked",
            description=f"{member.mention} has been kicked",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.send(embed=embed)
        await log_mod_action(ctx, "Kick", member, reason)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to kick that member.")

@bot.command(name='ban')
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    """Ban a member from the server"""
    if member.id in OWNER_IDS:
        await ctx.send("❌ You cannot ban a bot owner!")
        return
    
    try:
        await member.ban(reason=reason)
        embed = discord.Embed(
            title="✅ Member Banned",
            description=f"{member.mention} has been banned",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.send(embed=embed)
        await log_mod_action(ctx, "Ban", member, reason)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban that member.")

@bot.command(name='mute')
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, *, reason="No reason provided"):
    """Mute a member"""
    if member.id in OWNER_IDS:
        await ctx.send("❌ You cannot mute a bot owner!")
        return
    
    if not config['mute_role']:
        await ctx.send("❌ Mute role not configured. Use `!setmuterole` to set it.")
        return
    
    mute_role = ctx.guild.get_role(config['mute_role'])
    if not mute_role:
        await ctx.send("❌ Mute role not found. Please reconfigure.")
        return
    
    try:
        await member.add_roles(mute_role, reason=reason)
        embed = discord.Embed(
            title="🔇 Member Muted",
            description=f"{member.mention} has been muted",
            color=discord.Color.greyple()
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.send(embed=embed)
        await log_mod_action(ctx, "Mute", member, reason)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to mute that member.")

@bot.command(name='unmute')
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    """Unmute a member"""
    if not config['mute_role']:
        await ctx.send("❌ Mute role not configured.")
        return
    
    mute_role = ctx.guild.get_role(config['mute_role'])
    if not mute_role:
        await ctx.send("❌ Mute role not found.")
        return
    
    try:
        await member.remove_roles(mute_role)
        embed = discord.Embed(
            title="🔊 Member Unmuted",
            description=f"{member.mention} has been unmuted",
            color=discord.Color.green()
        )
        embed.add_field(name="Moderator", value=ctx.author.mention)
        await ctx.send(embed=embed)
        await log_mod_action(ctx, "Unmute", member, "Unmuted by moderator")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unmute that member.")

@bot.command(name='clear')
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    """Clear messages in the channel"""
    if amount <= 0:
        await ctx.send("❌ Please specify a positive number.")
        return
    
    if amount > 100:
        amount = 100
        await ctx.send("⚠️ Maximum 100 messages can be deleted at once.")
    
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"✅ Deleted {len(deleted) - 1} messages.")
    await asyncio.sleep(3)
    await msg.delete()

@bot.command(name='setwelcome')
@commands.has_permissions(administrator=True)
async def set_welcome(ctx, channel: discord.TextChannel):
    """Set the welcome channel"""
    config['welcome_channel'] = channel.id
    await ctx.send(f"✅ Welcome channel set to {channel.mention}")

@bot.command(name='setmodlog')
@commands.has_permissions(administrator=True)
async def set_modlog(ctx, channel: discord.TextChannel):
    """Set the moderation log channel"""
    config['mod_log_channel'] = channel.id
    await ctx.send(f"✅ Mod log channel set to {channel.mention}")

@bot.command(name='setmuterole')
@commands.has_permissions(administrator=True)
async def set_muterole(ctx, role: discord.Role):
    """Set the mute role"""
    config['mute_role'] = role.id
    await ctx.send(f"✅ Mute role set to {role.mention}")

@bot.command(name='addautorole')
@commands.has_permissions(administrator=True)
async def add_autorole(ctx, role: discord.Role):
    """Add an auto-role for new members"""
    if role.id not in config['auto_roles']:
        config['auto_roles'].append(role.id)
        await ctx.send(f"✅ {role.mention} added to auto-roles")
    else:
        await ctx.send(f"ℹ️ {role.mention} is already an auto-role")

@bot.command(name='removeautorole')
@commands.has_permissions(administrator=True)
async def remove_autorole(ctx, role: discord.Role):
    """Remove an auto-role"""
    if role.id in config['auto_roles']:
        config['auto_roles'].remove(role.id)
        await ctx.send(f"✅ {role.mention} removed from auto-roles")
    else:
        await ctx.send(f"ℹ️ {role.mention} is not an auto-role")

# ============= ERROR HANDLING =============

@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Use `!help` for correct usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Use `!help` for correct usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        logger.error(f"Error in command {ctx.command}: {error}")
        await ctx.send("❌ An error occurred while processing your command.")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for slash commands"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        logger.error(f"Error in slash command {interaction.command.name}: {error}")
        await interaction.response.send_message("❌ An error occurred while processing your command.", ephemeral=True)

# ============= BOT STARTUP =============

if __name__ == "__main__":
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        logger.error("Invalid Discord token provided.")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
