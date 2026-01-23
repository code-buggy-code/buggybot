import discord
from discord import app_commands
from discord.ext import commands

# Function/Class List:
# class Anon(commands.Cog)
# - anon(interaction, message, name)
# - anonset(interaction)
# - anonunset(interaction)
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
        
        if name:
            await interaction.channel.send(f"**{name}**: {message}")
        else:
            await interaction.channel.send(message)
        
        # Delete the hidden loading state so the command looks invisible
        await interaction.delete_original_response()

    @app_commands.command(name="anonset", description="Allow /anon messages in this channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def anonset(self, interaction: discord.Interaction):
        settings = self.bot.db.get_collection("anon_settings")
        if not isinstance(settings, list): settings = []
        
        guild_data = next((d for d in settings if d.get('guild_id') == interaction.guild_id), None)
        
        if not guild_data:
            guild_data = {"guild_id": interaction.guild_id, "channels": []}
            settings.append(guild_data)
            
        if interaction.channel_id not in guild_data['channels']:
            guild_data['channels'].append(interaction.channel_id)
            self.bot.db.update_doc("anon_settings", "guild_id", interaction.guild_id, guild_data)
            await interaction.response.send_message(f"✅ `/anon` is now allowed in {interaction.channel.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ This channel is already set for anon messages.", ephemeral=True)

    @app_commands.command(name="anonunset", description="Disallow /anon messages in this channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def anonunset(self, interaction: discord.Interaction):
        settings = self.bot.db.get_collection("anon_settings")
        if not isinstance(settings, list): settings = []
        
        guild_data = next((d for d in settings if d.get('guild_id') == interaction.guild_id), None)
        
        if guild_data and interaction.channel_id in guild_data['channels']:
            guild_data['channels'].remove(interaction.channel_id)
            self.bot.db.update_doc("anon_settings", "guild_id", interaction.guild_id, guild_data)
            await interaction.response.send_message(f"✅ `/anon` is now disabled in {interaction.channel.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ This channel does not allow anon messages.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Anon(bot))
