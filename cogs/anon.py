import discord
from discord import app_commands
from discord.ext import commands
import datetime

# Function/Class List:
# class Anon(commands.Cog)
# - __init__(bot)
# - anon(interaction, message, name) [Slash - Public]
# - anonset(ctx) [Prefix]
# - anonunset(ctx) [Prefix]
# - setup(bot)

class Anon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Anonymous messaging commands."

    @app_commands.command(name="anon", description="Send a message anonymously.", extras={'public': True})
    @app_commands.describe(message="The message you want to send", name="The name to display (optional)")
    async def anon(self, interaction: discord.Interaction, message: str, name: str = None):
        """Sends a message anonymously to the current channel."""
        # 1. Check if allowed in this channel
        settings = self.bot.db.get_collection("anon_settings")
        # Ensure list
        if not isinstance(settings, list): settings = []
        
        guild_data = next((d for d in settings if d.get('guild_id') == interaction.guild_id), None)
        
        if guild_data and guild_data.get('channels'):
            if interaction.channel_id not in guild_data['channels']:
                return await interaction.response.send_message("❌ Anonymous messages are not allowed in this channel.", ephemeral=True)

        # Defer the interaction ephemerally to prevent timeout errors while processing
        await interaction.response.defer(ephemeral=True)
        
        # Send the actual message
        if name:
            await interaction.channel.send(f"**{name}**: {message}")
        else:
            await interaction.channel.send(message)
        
        # Delete the hidden loading state so the command looks invisible
        await interaction.delete_original_response()

        # --- LOGGING LOGIC ---
        try:
            log_settings = self.bot.db.get_collection("log_settings")
            if not isinstance(log_settings, list): log_settings = []
            
            log_data = next((s for s in log_settings if s.get('guild_id') == interaction.guild_id), None)
            
            if log_data and log_data.get('log_channel_id'):
                log_channel = self.bot.get_channel(log_data['log_channel_id'])
                if log_channel:
                    embed = discord.Embed(
                        title="🕵️ Anonymous Message Sent",
                        description=f"**Author:** {interaction.user.mention} ({interaction.user.id})\n**Channel:** {interaction.channel.mention}",
                        color=discord.Color.dark_grey(),
                        timestamp=datetime.datetime.now()
                    )
                    
                    # If a name was used, show it
                    if name:
                        embed.add_field(name="Display Name", value=name, inline=True)
                    
                    embed.add_field(name="Content", value=message[:1024], inline=False)
                    
                    await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to log anon message: {e}")

    @commands.command(name="anonset")
    @commands.has_permissions(administrator=True)
    async def anonset(self, ctx):
        """Allow /anon messages in this channel."""
        settings = self.bot.db.get_collection("anon_settings")
        if not isinstance(settings, list): settings = []
        
        guild_data = next((d for d in settings if d.get('guild_id') == ctx.guild.id), None)
        
        if not guild_data:
            guild_data = {"guild_id": ctx.guild.id, "channels": []}
            settings.append(guild_data)
            
        if ctx.channel.id not in guild_data['channels']:
            guild_data['channels'].append(ctx.channel.id)
            self.bot.db.update_doc("anon_settings", "guild_id", ctx.guild.id, guild_data)
            await ctx.send(f"✅ `/anon` is now allowed in {ctx.channel.mention}.")
        else:
            await ctx.send(f"⚠️ This channel is already set for anon messages.")

    @commands.command(name="anonunset")
    @commands.has_permissions(administrator=True)
    async def anonunset(self, ctx):
        """Disallow /anon messages in this channel."""
        settings = self.bot.db.get_collection("anon_settings")
        if not isinstance(settings, list): settings = []
        
        guild_data = next((d for d in settings if d.get('guild_id') == ctx.guild.id), None)
        
        if guild_data and ctx.channel.id in guild_data['channels']:
            guild_data['channels'].remove(ctx.channel.id)
            self.bot.db.update_doc("anon_settings", "guild_id", ctx.guild.id, guild_data)
            await ctx.send(f"✅ `/anon` is now disabled in {ctx.channel.mention}.")
        else:
            await ctx.send(f"⚠️ This channel does not allow anon messages.")

async def setup(bot):
    await bot.add_cog(Anon(bot))
