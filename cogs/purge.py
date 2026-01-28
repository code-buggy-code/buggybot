import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import asyncio
from typing import Literal

# Function/Class List:
# class Purge(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_purge_config()
# - save_purge_config(config)
# - do_purge(channel, limit)
# - nightly_purge_task()
# - before_nightly_purge()
# - on_message(message) [Listener]
# - purge(interaction, amount) [Slash]
# - pinpurge(interaction) [Slash]
# - purgepins(interaction, action) [Slash - Renamed from togglepins]
# - autopurge(interaction, action) [Slash]
# setup(bot)

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Purge messages, nightly auto-purge, and pin cleanup."
        self.nightly_purge_task.start()

    def cog_unload(self):
        self.nightly_purge_task.cancel()

    # --- HELPERS ---

    def get_purge_config(self):
        """Fetches the list of channels set for nightly purge."""
        return self.bot.db.get_collection("purge_settings")

    def save_purge_config(self, config):
        """Saves the nightly purge configuration."""
        self.bot.db.save_collection("purge_settings", config)

    async def do_purge(self, channel, limit=None):
        """
        Purges messages from a channel.
        ALWAYS protects pinned messages.
        """
        # Protect pins by default
        deleted = await channel.purge(limit=limit, check=lambda m: not m.pinned)
        return len(deleted)

    # --- TASKS ---

    @tasks.loop(time=datetime.time(hour=4, minute=0, tzinfo=datetime.timezone.utc)) # Runs at 4 AM UTC
    async def nightly_purge_task(self):
        print("⏰ Starting Nightly Purge Task...")
        config = self.get_purge_config()
        # config structure: [{"guild_id": 123, "channel_id": 456}]
        
        if not config:
            print("Nightly Purge: No channels configured.")
            return

        for entry in config:
            channel_id = entry.get('channel_id')
            # Try to get from cache first
            channel = self.bot.get_channel(channel_id)
            
            # If not in cache, try fetching it (more robust)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception as e:
                    print(f"Nightly Purge Error: Could not fetch channel {channel_id}: {e}")
                    continue

            if channel:
                try:
                    # 1. Perform purge (Sticky messages included! Pins protected!)
                    count = await self.do_purge(channel, limit=None)
                    print(f"Nightly Purge: Deleted {count} messages in {channel.name} ({channel.id}).")
                    
                    # 2. Post Announcement
                    if count > 0:
                        await channel.send(f"🧹 **Nightly Purge Complete.** Deleted {count} messages.", delete_after=300)
                    
                except Exception as e:
                    print(f"Failed to auto-purge {channel.name}: {e}")

    @nightly_purge_task.before_loop
    async def before_nightly_purge(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listens for system 'pin' announcements and deletes them if enabled."""
        if message.type == discord.MessageType.pins_add:
            # Check if this channel is configured for auto-deletion
            cleaner_config = self.bot.db.get_collection("pin_cleaner_config") # List of channel IDs
            if not isinstance(cleaner_config, list): cleaner_config = []
            
            if message.channel.id in cleaner_config:
                try:
                    await message.delete()
                except:
                    pass

    # --- COMMANDS ---

    @app_commands.command(name="purge", description="Delete a number of messages (always keeps pins).")
    @app_commands.describe(amount="Number of messages to delete")
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int):
        """Delete a number of messages (keeps pins)."""
        await interaction.response.defer(ephemeral=True)
        count = await self.do_purge(interaction.channel, limit=amount)
        await interaction.followup.send(f"✅ Deleted {count} messages (pins preserved).", ephemeral=True)

    @app_commands.command(name="pinpurge", description="Purge ALL messages in this channel (always keeps pins).")
    @app_commands.default_permissions(administrator=True)
    async def pinpurge(self, interaction: discord.Interaction):
        """Purge ALL messages in this channel (keeps pins)."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # limit=None means it will go through the entire channel history
            count = await self.do_purge(interaction.channel, limit=None)
            await interaction.followup.send(f"✅ Channel wiped! Deleted {count} messages (Pins preserved).", ephemeral=True)
            
            # Optional: Post the announcement so sticky comes back if configured
            await interaction.channel.send(f"🧹 **Manual Purge Complete.** Deleted {count} messages.", delete_after=60)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error purging channel: {e}", ephemeral=True)

    @app_commands.command(name="purgepins", description="Toggle auto-deletion of 'XY pinned a message' announcements.")
    @app_commands.describe(action="Enable or Disable for this channel")
    @app_commands.default_permissions(manage_messages=True)
    async def purgepins(self, interaction: discord.Interaction, action: Literal["Enable", "Disable"]):
        """Toggle auto-deletion of 'XY pinned a message' announcements."""
        config = self.bot.db.get_collection("pin_cleaner_config")
        if not isinstance(config, list): config = []
        
        if action == "Enable":
            if interaction.channel_id not in config:
                config.append(interaction.channel_id)
                self.bot.db.save_collection("pin_cleaner_config", config)
                await interaction.response.send_message("✅ I will now delete pin announcements in this channel.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Already enabled here.", ephemeral=True)
        else:
            if interaction.channel_id in config:
                config.remove(interaction.channel_id)
                self.bot.db.save_collection("pin_cleaner_config", config)
                await interaction.response.send_message("✅ Stopped deleting pin announcements.", ephemeral=True)
            else:
                 await interaction.response.send_message("⚠️ Not enabled here.", ephemeral=True)

    @app_commands.command(name="autopurge", description="Manage nightly auto-purge for this channel.")
    @app_commands.describe(action="Add or Remove this channel from nightly purge")
    @app_commands.default_permissions(administrator=True)
    async def autopurge(self, interaction: discord.Interaction, action: Literal["Add", "Remove"]):
        """Manage nightly auto-purge for this channel."""
        config = self.get_purge_config()
        if not isinstance(config, list): config = []
        
        # Check if channel is already configured
        exists = next((c for c in config if c['channel_id'] == interaction.channel_id), None)
        
        if action == "Add":
            if exists:
                await interaction.response.send_message("⚠️ This channel is already set for auto-purge.", ephemeral=True)
            else:
                config.append({"guild_id": interaction.guild_id, "channel_id": interaction.channel_id})
                self.save_purge_config(config)
                await interaction.response.send_message("✅ Channel added to nightly auto-purge (4 AM UTC).", ephemeral=True)
                
        elif action == "Remove":
            if not exists:
                await interaction.response.send_message("⚠️ This channel is not set for auto-purge.", ephemeral=True)
            else:
                config = [c for c in config if c['channel_id'] != interaction.channel_id]
                self.save_purge_config(config)
                await interaction.response.send_message("✅ Channel removed from nightly auto-purge.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Purge(bot))
