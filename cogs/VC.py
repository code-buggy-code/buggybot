"""
================================================================================
FUNCTION AND METHOD MANIFEST:
This manifest lists every function, method, and entrypoint in this file.
Referenced during compilation to ensure no functions are omitted or removed.
--------------------------------------------------------------------------------
1.  VC.__init__(self, bot: commands.Bot)
2.  VC.load_data(self) -> None
3.  VC.save_data(self) -> None
4.  VC.cog_load(self) -> None
5.  VC._startup_cleanup(self) -> None
6.  VC.on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState)
7.  VC.vc_command(self, interaction: discord.Interaction)
8.  setup(bot: commands.Bot)
================================================================================
"""

import asyncio
import json
import os
import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = "vc_data.json"


class VC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # A set to keep track of active temporary voice channel IDs
        self.temp_vcs: set[int] = set()
        # The dynamic trigger voice channel ID set via /vc
        self.trigger_channel_id: int | None = None
        # Load stored configuration immediately upon instantiation
        self.load_data()

    def load_data(self) -> None:
        """Loads trigger_channel_id and temp_vcs from the persistent JSON storage."""
        if not os.path.exists(DATA_FILE):
            return

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.trigger_channel_id = data.get("trigger_channel_id")
                self.temp_vcs = set(data.get("temp_vcs", []))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[VC Cog] Failed to load {DATA_FILE}: {e}")

    def save_data(self) -> None:
        """Saves current trigger_channel_id and temp_vcs to persistent JSON storage."""
        try:
            data = {
                "trigger_channel_id": self.trigger_channel_id,
                "temp_vcs": list(self.temp_vcs),
            }
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            print(f"[VC Cog] Failed to save {DATA_FILE}: {e}")

    async def cog_load(self) -> None:
        """Starts the background cleanup task without blocking the setup process."""
        self.bot.loop.create_task(self._startup_cleanup())

    async def _startup_cleanup(self) -> None:
        """Background task to prune any empty temporary channels that were left over during downtime."""
        await self.bot.wait_until_ready()
        stale_channels: set[int] = set()

        for ch_id in list(self.temp_vcs):
            channel = self.bot.get_channel(ch_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(ch_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None

            if channel is None:
                stale_channels.add(ch_id)
            elif isinstance(channel, discord.VoiceChannel):
                human_members = [m for m in channel.members if not m.bot]
                if len(human_members) == 0:
                    try:
                        await channel.delete(reason="Cleaning up empty temporary VC on bot restart.")
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    stale_channels.add(ch_id)

        if stale_channels:
            self.temp_vcs -= stale_channels
            self.save_data()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # Ignore if the user is just muting/deafening themselves
        if before.channel == after.channel:
            return

        guild = member.guild

        # Check if the user joined the dynamic trigger channel
        if after.channel and self.trigger_channel_id and after.channel.id == self.trigger_channel_id:
            category = after.channel.category

            # Overwrite configuration:
            # - @everyone: view_channel=False hides it from the server channel sidebar.
            #   Explicitly allowing connect, speak, send_messages, and read_message_history gives them
            #   join and in-channel text access while connected.
            # - member: full viewing, channel management, status, and invite creation rights.
            # - bot (guild.me): permissions needed to manage, move members, and clean up.
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    connect=True,
                    speak=True,
                    send_messages=True,
                    read_message_history=True,
                    use_voice_activation=True,
                    stream=True,
                ),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    set_voice_channel_status=True,
                    create_instant_invite=True,
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    manage_channels=True,
                    move_members=True,
                    create_instant_invite=True,
                ),
            }

            try:
                new_channel = await guild.create_voice_channel(
                    name=f"{member.display_name}'s Private VC",
                    category=category,
                    overwrites=overwrites,
                    reason="Private Temp VC Creation",
                )

                # Move the user into their new temporary channel
                await member.move_to(new_channel)
                self.temp_vcs.add(new_channel.id)
                self.save_data()
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
                    self.save_data()
                except discord.NotFound:
                    self.temp_vcs.discard(before.channel.id)
                    self.save_data()
                except discord.HTTPException as e:
                    print(f"Failed to delete temporary channel: {e}")

    @app_commands.command(name="vc", description="Set the current voice channel as the trigger channel.")
    @app_commands.default_permissions(manage_channels=True)  # Ensures only you/admins can use this
    async def vc_command(self, interaction: discord.Interaction):
        # Check if the command was run in a voice channel's text chat
        if interaction.channel.type != discord.ChannelType.voice:
            await interaction.response.send_message(
                "❌ You must use this command inside the text chat of a Voice Channel.",
                ephemeral=True,
            )
            return

        # Set the trigger to this channel and persist the state
        self.trigger_channel_id = interaction.channel.id
        self.save_data()

        await interaction.response.send_message(
            f"✅ Successfully set **{interaction.channel.name}** as the trigger channel. Anyone joining it will now get a private VC.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VC(bot))
