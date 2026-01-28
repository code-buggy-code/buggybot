import discord
from discord.ext import commands
from discord import app_commands
import datetime

# Function/Class List:
# class Logger(commands.Cog)
# - __init__(bot)
# - get_log_settings()
# - save_log_settings(settings)
# - log_to_channel(guild, embed)
# - on_message_delete(message)
# - on_message_edit(before, after)
# - on_member_remove(member)
# - setlogchannel(interaction, channel) [Slash]
# setup(bot)

BUGGY_ID = 1433003746719170560

class Logger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Server logging system."

    # --- HELPERS ---

    def get_log_settings(self):
        """Returns server-specific logging settings."""
        return self.bot.db.get_collection("log_settings")

    def save_log_settings(self, settings):
        """Saves logging settings."""
        self.bot.db.save_collection("log_settings", settings)

    async def log_to_channel(self, guild, embed):
        """Helper to send logs to the configured channel."""
        settings = self.get_log_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Logs deleted messages."""
        # 1. SPECIAL CHECK: If this was a sticky message, IGNORE IT.
        stickies = self.bot.db.get_collection("sticky_messages")
        if any(s.get('last_message_id') == message.id for s in stickies):
            return

        if message.author.bot or message.author.id == BUGGY_ID or not message.guild:
            return

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            description=f"**Author:** {message.author.mention} ({message.author.id})\n**Channel:** {message.channel.mention}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        if message.content:
            embed.add_field(name="Content", value=message.content[:1024], inline=False)
        
        if message.attachments:
            embed.add_field(name="Attachments", value=f"{len(message.attachments)} file(s)", inline=False)

        await self.log_to_channel(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Logs edited messages."""
        if before.author.bot or before.author.id == BUGGY_ID or not before.guild:
            return
        
        if before.content == after.content:
            return

        embed = discord.Embed(
            title="✏️ Message Edited",
            description=f"**Author:** {before.author.mention} ({before.author.id})\n**Channel:** {before.channel.mention}\n**Jump:** [Link]({before.jump_url})",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Before", value=before.content[:1024] or "[No Content]", inline=False)
        embed.add_field(name="After", value=after.content[:1024] or "[No Content]", inline=False)

        await self.log_to_channel(before.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Logs when a member leaves."""
        embed = discord.Embed(
            title="👋 Member Left",
            description=f"{member.mention} has left the server.",
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=member.id, inline=True)
        embed.add_field(name="Joined At", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", inline=True)

        await self.log_to_channel(member.guild, embed)

    # --- SLASH COMMANDS ---

    @app_commands.command(name="setlogchannel", description="Set the channel where server logs will be sent.")
    @app_commands.describe(channel="The channel for logs")
    @app_commands.default_permissions(administrator=True)
    async def setlogchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel where server logs will be sent."""
        settings = self.get_log_settings()
        settings = [s for s in settings if s['guild_id'] != interaction.guild_id]
        
        settings.append({"guild_id": interaction.guild_id, "log_channel_id": channel.id})
        self.save_log_settings(settings)
        await interaction.response.send_message(f"✅ Logging channel set to {channel.mention}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Logger(bot))
