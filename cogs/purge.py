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
# - purge(interaction, amount) [Slash]
# - autopurge(interaction, action) [Slash]
# setup(bot)

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Purge messages and nightly auto-purge."
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
        Note: We explicitly DO NOT exclude sticky messages here. 
        They will be deleted, and the Admin cog will repost them when the announcement fires.
        """
        # We only avoid deleting pinned messages
        deleted = await channel.purge(limit=limit, check=lambda m: not m.pinned)
        return len(deleted)

    # --- TASKS ---

    @tasks.loop(time=datetime.time(hour=4, minute=0, tzinfo=datetime.timezone.utc)) # Runs at 4 AM UTC
    async def nightly_purge_task(self):
        config = self.get_purge_config()
        # config structure: [{"guild_id": 123, "channel_id": 456}]
        
        for entry in config:
            channel = self.bot.get_channel(entry['channel_id'])
            if channel:
                try:
                    # 1. Perform purge (Sticky messages included!)
                    count = await self.do_purge(channel)
                    
                    # 2. Post Announcement
                    # This message triggers 'on_message' in Admin cog, which reposts the sticky!
                    if count > 0:
                        msg = await channel.send(f"🧹 **Nightly Purge Complete.** Deleted {count} messages.", delete_after=300)
                    
                except Exception as e:
                    print(f"Failed to auto-purge {channel.name}: {e}")

    @nightly_purge_task.before_loop
    async def before_nightly_purge(self):
        await self.bot.wait_until_ready()

    # --- COMMANDS ---

    @app_commands.command(name="purge", description="Delete a number of messages.")
    @app_commands.describe(amount="Number of messages to delete")
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int):
        """Delete a number of messages."""
        await interaction.response.defer(ephemeral=True)
        # We don't verify sticky IDs here anymore, just purge 'em all!
        count = await self.do_purge(interaction.channel, limit=amount)
        await interaction.followup.send(f"✅ Deleted {count} messages.", ephemeral=True)

    @app_commands.command(name="autopurge", description="Manage nightly auto-purge for this channel.")
    @app_commands.describe(action="Add or Remove this channel from nightly purge")
    @app_commands.default_permissions(administrator=True)
    async def autopurge(self, interaction: discord.Interaction, action: Literal["Add", "Remove"]):
        """Manage nightly auto-purge for this channel."""
        config = self.get_purge_config()
        
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
