import discord
from discord.ext import commands
import asyncio

class VC(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # A set to keep track of the temporary voice channel IDs
        self.temp_vcs = set()
        
        # ID of the Master "Join to Create" channel. 
        # (Replace this with your actual channel ID or a database fetch in your real bot)
        self.hub_channel_id = 123456789012345678  

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Ignore if the user is just muting/deafening themselves in the same channel
        if before.channel == after.channel:
            return

        # Check if the user joined the "Join to Create" hub channel
        if after.channel and after.channel.id == self.hub_channel_id:
            # Create a new temporary channel for the user
            category = after.channel.category
            try:
                # Create the channel named after the user
                new_channel = await after.channel.guild.create_voice_channel(
                    name=f"{member.display_name}'s VC",
                    category=category,
                    reason="Temporary VC Creation"
                )
                
                # Move the user into their new temporary channel
                await member.move_to(new_channel)
                
                # Add the new channel ID to our tracking set
                self.temp_vcs.add(new_channel.id)
            except discord.HTTPException as e:
                print(f"Failed to create or move user to temp channel: {e}")

        # Check if the user left a channel, and if that channel was a temporary one
        if before.channel and before.channel.id in self.temp_vcs:
            
            # --- THE FIX ---
            # Create a list of members in the channel who are NOT bots.
            # If a human leaves and only bots are left, this list will be empty.
            human_members = [m for m in before.channel.members if not m.bot]
            
            # If there are no humans left in the channel (length is 0)
            if len(human_members) == 0:
                try:
                    # Delete the channel
                    await before.channel.delete(reason="Temporary VC empty (no non-bot users left).")
                    
                    # Remove the channel ID from our tracking set
                    self.temp_vcs.discard(before.channel.id)
                except discord.NotFound:
                    # Channel was already deleted somehow
                    self.temp_vcs.discard(before.channel.id)
                except discord.HTTPException as e:
                    print(f"Failed to delete temporary channel: {e}")

async def setup(bot):
    await bot.add_cog(VC(bot))
