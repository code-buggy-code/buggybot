"""
================================================================================
FUNCTION AND METHOD MANIFEST:
This manifest lists every function, method, and UI component class in this file.
Referenced during compilation to ensure no functions are omitted or removed.
--------------------------------------------------------------------------------
1.  load_vc_data(filepath: str) -> dict
2.  save_vc_data(filepath: str, data: dict) -> None
3.  InviteUserSelect(discord.ui.UserSelect)
    - __init__(self, channel: discord.VoiceChannel)
    - callback(self, interaction: discord.Interaction)
4.  InviteView(discord.ui.View)
    - __init__(self, channel: discord.VoiceChannel, invite_url: str)
    - copy_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button)
5.  VC(commands.Cog)
    - __init__(self, bot: commands.Bot)
    - cog_load(self)
    - cog_unload(self)
    - vc(self, ctx: commands.Context)
    - on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState)
    - _create_private_vc(self, member: discord.Member, trigger_channel: discord.VoiceChannel)
    - _cleanup_temp_channel(self, channel: discord.VoiceChannel)
6.  setup(bot: commands.Bot)
================================================================================
"""

import asyncio
import json
import os
from typing import Dict, List

import discord
from discord import app_commands
from discord.ext import commands


# ------------------------------------------------------------------------------
# 1 & 2. Persistence Helpers
# ------------------------------------------------------------------------------

def load_vc_data(filepath: str) -> dict:
    """Loads persistent voice configuration data from a JSON file."""
    dir_name = os.path.dirname(filepath)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    if not os.path.exists(filepath):
        default_data = {"triggers": {}, "temp_channels": []}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        return default_data

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"triggers": {}, "temp_channels": []}


def save_vc_data(filepath: str, data: dict) -> None:
    """Writes persistent voice configuration data to a JSON file."""
    dir_name = os.path.dirname(filepath)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# ------------------------------------------------------------------------------
# 3 & 4. Interactive Invite UI Components
# ------------------------------------------------------------------------------

class InviteUserSelect(discord.ui.UserSelect):
    """Dropdown menu allowing users in the room to grant permissions to server members."""

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(
            placeholder="Select server members to invite...",
            min_values=1,
            max_values=10,
        )
        self.target_channel = channel

    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.target_channel.members and interaction.user.id != self.target_channel.guild.owner_id:
            await interaction.response.send_message(
                "⚠️ You must be inside this voice channel to invite other members!",
                ephemeral=True,
            )
            return

        invited_members = []
        for user in self.values:
            if isinstance(user, discord.Member):
                await self.target_channel.set_permissions(
                    user,
                    view_channel=True,
                    connect=True,
                    create_instant_invite=True,
                    speak=True,
                    stream=True,
                    reason=f"Invited by {interaction.user.display_name}",
                )
                invited_members.append(user.mention)

        if invited_members:
            await interaction.response.send_message(
                f"✅ Granted access to: {', '.join(invited_members)}. They can now view and join this channel!",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("⚠️ No valid guild members were selected.", ephemeral=True)


class InviteView(discord.ui.View):
    """View embedded in the private VC text chat with an invite button and member selector."""

    def __init__(self, channel: discord.VoiceChannel, invite_url: str):
        super().__init__(timeout=None)
        self.channel = channel
        self.invite_url = invite_url
        self.add_item(InviteUserSelect(channel=channel))

    @discord.ui.button(label="Show Invite Link", style=discord.ButtonStyle.secondary, emoji="🔗")
    async def copy_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Allows users to retrieve the direct instant invite URL."""
        await interaction.response.send_message(
            f"**Shareable Invite Link:**\n{self.invite_url}",
            ephemeral=True,
        )


# ------------------------------------------------------------------------------
# 5. Main Cog: VC
# ------------------------------------------------------------------------------

class VC(commands.Cog, name="VC"):
    """Manages trigger voice channels and automated private temporary voice channels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_path = os.path.join("data", "vc_data.json")
        self.data: dict = load_vc_data(self.data_path)

    async def cog_load(self):
        """Loads configuration and prunes stale or empty temporary channels from downtime."""
        self.data = load_vc_data(self.data_path)
        await self.bot.wait_until_ready()

        to_remove = []
        for channel_id in list(self.data.get("temp_channels", [])):
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None

            if channel is None:
                to_remove.append(channel_id)
            elif isinstance(channel, discord.VoiceChannel):
                if len(channel.members) == 0:
                    try:
                        await channel.delete(reason="Cleaning up empty private VC on startup.")
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    to_remove.append(channel_id)

        for channel_id in to_remove:
            if channel_id in self.data["temp_channels"]:
                self.data["temp_channels"].remove(channel_id)

        if to_remove:
            save_vc_data(self.data_path, self.data)

    async def cog_unload(self):
        """Persists current state when cog is unloaded."""
        save_vc_data(self.data_path, self.data)

    @commands.hybrid_command(name="vc", description="Set this voice channel as the private voice trigger.")
    @commands.guild_only()
    async def vc(self, ctx: commands.Context):
        """Sets the current voice channel chat as the trigger for generating private rooms."""
        if not isinstance(ctx.channel, discord.VoiceChannel):
            await ctx.reply(
                "⚠️ This command must be executed inside the text chat of a voice channel!",
                ephemeral=True,
            )
            return

        guild_id_str = str(ctx.guild.id)
        channel_id = ctx.channel.id

        self.data.setdefault("triggers", {})[guild_id_str] = channel_id
        save_vc_data(self.data_path, self.data)

        await ctx.reply(
            f"✅ **{ctx.channel.name}** has been marked as the trigger channel!\n"
            "Whenever someone joins this channel, a private voice room will be generated for them.",
            ephemeral=False,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Handles voice state transitions for creating private channels and deleting empty ones."""
        if member.bot:
            return

        guild = member.guild
        guild_id_str = str(guild.id)
        trigger_id = self.data.get("triggers", {}).get(guild_id_str)

        if after.channel and after.channel.id == trigger_id and (before.channel != after.channel):
            await self._create_private_vc(member, after.channel)

        if after.channel and after.channel.id in self.data.get("temp_channels", []):
            if member not in after.channel.overwrites:
                try:
                    await after.channel.set_permissions(
                        member,
                        view_channel=True,
                        connect=True,
                        create_instant_invite=True,
                        speak=True,
                        stream=True,
                        reason="Joined temporary private voice channel.",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if before.channel and before.channel.id in self.data.get("temp_channels", []):
            if len(before.channel.members) == 0:
                await self._cleanup_temp_channel(before.channel)

    async def _create_private_vc(self, member: discord.Member, trigger_channel: discord.VoiceChannel):
        """Creates a hidden temporary voice channel for the user and moves them in."""
        guild = member.guild
        category = trigger_channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
                connect=False,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                create_instant_invite=True,
                speak=True,
                stream=True,
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
                name=f"🔒 {member.display_name}'s Room",
                category=category,
                overwrites=overwrites,
                reason=f"Private VC requested by {member.display_name}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return

        self.data.setdefault("temp_channels", []).append(new_channel.id)
        save_vc_data(self.data_path, self.data)

        try:
            await member.move_to(new_channel, reason="Moved into temporary private VC.")
        except (discord.Forbidden, discord.HTTPException):
            pass

        invite_url = "Invite link unavailable"
        try:
            invite = await new_channel.create_invite(
                max_age=0,
                reason=f"Invite link for {new_channel.name}",
            )
            invite_url = invite.url
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="🔒 Private Voice Channel Active",
            description=(
                f"Welcome {member.mention}! This channel is currently visible only to you.\n\n"
                f"**Invite Link:** {invite_url}\n"
                "You and anyone who joins can share this link or use the selector below to grant access to other server members.\n\n"
                "*This channel will automatically be deleted once everyone leaves.*"
            ),
            color=discord.Color.brand_green(),
        )
        view = InviteView(channel=new_channel, invite_url=invite_url)
        try:
            await new_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _cleanup_temp_channel(self, channel: discord.VoiceChannel):
        """Deletes an empty temporary voice channel and removes it from storage."""
        channel_id = channel.id
        try:
            await channel.delete(reason="Temporary private VC is now empty.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        if channel_id in self.data.get("temp_channels", []):
            self.data["temp_channels"].remove(channel_id)
            save_vc_data(self.data_path, self.data)


# ------------------------------------------------------------------------------
# 6. Extension Setup Entrypoint
# ------------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    """Registers the VC cog with the bot."""
    await bot.add_cog(VC(bot))
