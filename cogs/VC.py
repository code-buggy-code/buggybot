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
8.  VC.invite_command(self, interaction: discord.Interaction, target: discord.Member)
9.  setup(bot: commands.Bot)
================================================================================
"""

import asyncio
import json
import os
from typing import Dict, Any

import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = "vc_data.json"


class VC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Mapping of temporary voice channel IDs to their configuration
        # Format: { channel_id: {"creator": user_id, "invited": [user_id, ...]} }
        self.temp_vcs: Dict[int, Dict[str, Any]] = {}
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
                
                temp_vcs_raw = data.get("temp_vcs", {})
                self.temp_vcs = {}
                
                # Migrate older list format if it exists, otherwise load the dict format
                if isinstance(temp_vcs_raw, list):
                    for ch_id in temp_vcs_raw:
                        self.temp_vcs[int(ch_id)] = {"creator": None, "invited": []}
                elif isinstance(temp_vcs_raw, dict):
                    for k, v in temp_vcs_raw.items():
                        self.temp_vcs[int(k)] = {
                            "creator": v.get("creator"),
                            "invited": v.get("invited", [])
                        }
        except (json.JSONDecodeError, OSError) as e:
            print(f"[VC Cog] Failed to load {DATA_FILE}: {e}")

    def save_data(self) -> None:
        """Saves current trigger_channel_id and temp_vcs to persistent JSON storage."""
        try:
            data = {
                "trigger_channel_id": self.trigger_channel_id,
                "temp_vcs": self.temp_vcs,
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

        for ch_id in list(self.temp_vcs.keys()):
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
            for ch_id in stale_channels:
                self.temp_vcs.pop(ch_id, None)
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

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False
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
                
                # Track the new channel with the creator and an empty invited list
                self.temp_vcs[new_channel.id] = {
                    "creator": member.id,
                    "invited": []
                }
                self.save_data()
            except discord.HTTPException as e:
                print(f"Failed to create or move user to temp channel: {e}")

        # Check if the user left a tracked temporary VC
        if before.channel and before.channel.id in self.temp_vcs:
            human_members = [m for m in before.channel.members if not m.bot]
            vc_data = self.temp_vcs[before.channel.id]

            # Delete if no humans are left
            if len(human_members) == 0:
                try:
                    await before.channel.delete(reason="Temporary VC empty (no non-bot users left).")
                except (discord.NotFound, discord.HTTPException):
                    pass
                
                self.temp_vcs.pop(before.channel.id, None)
                self.save_data()
            else:
                # Remove explicit permissions ONLY IF they are NOT the creator AND NOT explicitly invited.
                # This preserves access for invited users, while hiding the room from uninvited link-joiners when they leave.
                if member.id != vc_data.get("creator") and member.id not in vc_data.get("invited", []):
                    try:
                        await before.channel.set_permissions(member, overwrite=None)
                    except discord.HTTPException:
                        pass

    @app_commands.command(name="vc", description="Set the current voice channel as the trigger channel.")
    @app_commands.default_permissions(manage_channels=True)
    async def vc_command(self, interaction: discord.Interaction):
        if interaction.channel.type != discord.ChannelType.voice:
            await interaction.response.send_message(
                "❌ You must use this command inside the text chat of a Voice Channel.",
                ephemeral=True,
            )
            return

        self.trigger_channel_id = interaction.channel.id
        self.save_data()

        await interaction.response.send_message(
            f"✅ Successfully set **{interaction.channel.name}** as the trigger channel. Anyone joining it will now get a private VC.",
            ephemeral=True,
        )

    @app_commands.command(name="invite", description="Toggle a server member's access to your private VC.")
    async def invite_command(self, interaction: discord.Interaction, target: discord.Member):
        """Grants or revokes a specific user's explicit access to the private voice channel."""
        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel or voice_state.channel.id not in self.temp_vcs:
            await interaction.response.send_message(
                "❌ You must be inside your private voice channel to manage invites.",
                ephemeral=True,
            )
            return

        channel = voice_state.channel
        vc_data = self.temp_vcs[channel.id]
        invited_list = vc_data.get("invited", [])

        if target.id in invited_list:
            # User is already invited -> Revoke access
            invited_list.remove(target.id)
            self.save_data()
            try:
                await channel.set_permissions(target, overwrite=None)
                await interaction.response.send_message(
                    f"🚫 Revoked {target.mention}'s invite. The room will disappear for them.",
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ Failed to revoke access: {e}", ephemeral=True)
        else:
            # User is not invited -> Grant access
            invited_list.append(target.id)
            self.save_data()
            try:
                await channel.set_permissions(
                    target,
                    view_channel=True,
                    connect=True,
                    speak=True,
                    send_messages=True,
                    read_message_history=True,
                    use_voice_activation=True,
                    stream=True
                )
                await interaction.response.send_message(
                    f"✅ Granted {target.mention} access! They are now on the invite list and can see the room.",
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ Failed to grant access: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VC(bot))
