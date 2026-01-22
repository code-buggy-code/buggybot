import discord
from discord import app_commands
from discord.ext import commands

# Function/Class List:
# class Anon(commands.Cog)
# - __init__(bot)
# - get_anon_settings(guild_id)
# - save_anon_settings(guild_id, channels)
# - anon(interaction, message, name)
# - anonchannel (Group)
# - anon_add(interaction, channel)
# - anon_remove(interaction, channel)
# - anon_list(interaction)
# setup(bot)

class Anon(commands.Cog, name="anonymous messaging"):
    def __init__(self, bot):
        self.bot = bot
        self.description = "" # No description shown in help

    # --- HELPERS ---

    def get_anon_settings(self, guild_id):
        """Fetches the list of allowed anon channels for a guild."""
        collection = self.bot.db.get_collection("anon_settings")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                return doc.get('channels', [])
        return []

    def save_anon_settings(self, guild_id, channels):
        """Saves the list of allowed anon channels."""
        collection = self.bot.db.get_collection("anon_settings")
        # Remove old entry for this guild
        collection = [d for d in collection if d['guild_id'] != guild_id]
        # Add new entry
        collection.append({"guild_id": guild_id, "channels": channels})
        self.bot.db.save_collection("anon_settings", collection)

    # --- COMMANDS ---

    @app_commands.command(name="anon", description="Send an anonymous message in this channel.")
    @app_commands.describe(message="The message to send", name="Optional: A name to display")
    async def anon(self, interaction: discord.Interaction, message: str, name: str = None):
        allowed_channels = self.get_anon_settings(interaction.guild_id)
        
        if interaction.channel_id not in allowed_channels:
            return await interaction.response.send_message("❌ This channel is not set up for anonymous messaging.", ephemeral=True)

        content = message
        if name:
            content = f"**{name}:** {message}"

        # Send ephemeral confirmation so nobody sees who used the command
        await interaction.response.send_message("✅ Message sent anonymously.", ephemeral=True)
        
        # Send the actual message to the channel
        await interaction.channel.send(content)

    anonchannel_group = app_commands.Group(name="anonchannel", description="Manage anonymous messaging channels")

    @anonchannel_group.command(name="add", description="Allow /anon usage in a channel (defaults to current channel).")
    @app_commands.describe(channel="The channel to add (optional)")
    @app_commands.checks.has_permissions(administrator=True)
    async def anon_add(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        channels = self.get_anon_settings(interaction.guild_id)
        
        if target.id in channels:
            return await interaction.response.send_message(f"⚠️ {target.mention} is already an anon channel.", ephemeral=True)
        
        channels.append(target.id)
        self.save_anon_settings(interaction.guild_id, channels)
        await interaction.response.send_message(f"✅ /anon can now be used in {target.mention}.", ephemeral=True)

    @anonchannel_group.command(name="remove", description="Disallow /anon usage in a channel (defaults to current channel).")
    @app_commands.describe(channel="The channel to remove (optional)")
    @app_commands.checks.has_permissions(administrator=True)
    async def anon_remove(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        channels = self.get_anon_settings(interaction.guild_id)
        
        if target.id not in channels:
            return await interaction.response.send_message(f"⚠️ {target.mention} is not an anon channel.", ephemeral=True)
        
        channels.remove(target.id)
        self.save_anon_settings(interaction.guild_id, channels)
        await interaction.response.send_message(f"✅ Removed {target.mention} from anon channels.", ephemeral=True)

    @anonchannel_group.command(name="list", description="List all allowed anon channels in this server.")
    async def anon_list(self, interaction: discord.Interaction):
        channels = self.get_anon_settings(interaction.guild_id)
        
        if not channels:
            return await interaction.response.send_message("📝 No anon channels configured for this server.", ephemeral=True)

        text = "**🕵️ Allowed Anon Channels:**\n"
        for cid in channels:
            # Check if channel still exists
            channel = interaction.guild.get_channel(cid)
            if channel:
                text += f"• {channel.mention}\n"
            else:
                text += f"• ID: {cid} (Deleted)\n"
        
        await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Anon(bot))
