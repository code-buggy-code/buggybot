import discord
from discord.ext import commands
from discord import app_commands
import asyncio

class VC(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # A set to keep track of the temporary voice channel IDs
        self.temp_vcs = set()
        
        # We will store the trigger channel ID dynamically when you run /vc
        self.trigger_channel_id = None  

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Ignore if the user is just muting/deafening themselves
        if before.channel == after.channel:
            return

        # Check if the user joined the dynamic trigger channel
        if after.channel and self.trigger_channel_id and after.channel.id == self.trigger_channel_id:
            category = after.channel.category
            
            # Make the channel invisible to everyone by default, but visible to the creator
            # Grant the creator permissions to rename the channel and set the VC status
            overwrites = {
                after.channel.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    manage_channels=True,
                    set_voice_channel_status=True
                )
            }
            
            try:
                new_channel = await after.channel.guild.create_voice_channel(
                    name=f"{member.display_name}'s Private VC",
                    category=category,
                    overwrites=overwrites,
                    reason="Private Temp VC Creation"
                )
                
                # Move the user into their new temporary channel
                await member.move_to(new_channel)
                self.temp_vcs.add(new_channel.id)
            except discord.HTTPException as e:
                print(f"Failed to create or move user to temp channel: {e}")

        # Check if the user left a tracked temporary VC
        if before.channel and before.channel.id in self.temp_vcs:
            
            human_members = [m for m in before.channel.members if not m.bot]
            
            # Delete if no humans are left
            if len(human_members) == 0:
                try:
                    await before.channel.delete(reason="Temporary VC empty (no non-bot users left).")
                    self.temp_vcs.discard(before.channel.id)
                except discord.NotFound:
                    self.temp_vcs.discard(before.channel.id)
                except discord.HTTPException as e:
                    print(f"Failed to delete temporary channel: {e}")

    @app_commands.command(name="vc", description="Set the current voice channel as the trigger channel.")
    @app_commands.default_permissions(manage_channels=True) # Ensures only you/admins can use this
    async def vc_command(self, interaction: discord.Interaction):
        
        # Check if the command was run in a voice channel's text chat
        if interaction.channel.type != discord.ChannelType.voice:
            await interaction.response.send_message("❌ You must use this command inside the text chat of a Voice Channel.", ephemeral=True)
            return
            
        # Set the trigger to this channel
        self.trigger_channel_id = interaction.channel.id
        await interaction.response.send_message(f"✅ Successfully set **{interaction.channel.name}** as the trigger channel. Anyone joining it will now get a private VC.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(VC(bot))
