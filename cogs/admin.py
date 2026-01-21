import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta

# List of functions:
# - Admin.__init__: Initializes the cog, loads config, starts background task.
# - Admin.load_config: Loads configuration from JSON file.
# - Admin.save_config: Saves configuration to JSON file.
# - Admin.cog_unload: Cancels the background task when cog is unloaded.
# - Admin.vcping_group: The main slash command group for vcping.
# - Admin.vcping_ignore_group: The subgroup for ignore commands.
# - Admin.vcping_ignore_add: Adds a VC to the ignore list.
# - Admin.vcping_ignore_remove: Removes a VC from the ignore list.
# - Admin.vcping_ignore_list: Lists ignored VCs.
# - Admin.vcping_set: Sets the ping role and thresholds.
# - Admin.check_vcs: Background task to check VC duration and send pings.
# - Admin.on_voice_state_update: Event listener to track VC occupancy and reset state.

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = 'vcping_config.json'
        self.config = self.load_config()
        # State structure: {guild_id_str: {channel_id_str: {'start_time': timestamp_iso, 'pinged': bool}}}
        self.vc_state = {}
        self.check_vcs.start()

    def load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f)
        return {}

    def save_config(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)

    def cog_unload(self):
        self.check_vcs.cancel()

    vcping_group = app_commands.Group(name="vcping", description="Manage VC Ping settings")
    vcping_ignore_group = app_commands.Group(name="ignore", parent=vcping_group, description="Manage ignored Voice Channels")

    @vcping_ignore_group.command(name="add", description="Add a Voice Channel to the ignore list")
    @app_commands.describe(channel="The Voice Channel to ignore")
    async def vcping_ignore_add(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild_id = str(interaction.guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {'ignored': [], 'role': None, 'people': 2, 'minutes': 5}
        
        if channel.id not in self.config[guild_id]['ignored']:
            self.config[guild_id]['ignored'].append(channel.id)
            self.save_config()
            await interaction.response.send_message(f"Added {channel.mention} to the ignore list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{channel.mention} is already ignored.", ephemeral=True)

    @vcping_ignore_group.command(name="remove", description="Remove a Voice Channel from the ignore list")
    @app_commands.describe(channel="The Voice Channel to un-ignore")
    async def vcping_ignore_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild_id = str(interaction.guild_id)
        if guild_id in self.config and channel.id in self.config[guild_id]['ignored']:
            self.config[guild_id]['ignored'].remove(channel.id)
            self.save_config()
            await interaction.response.send_message(f"Removed {channel.mention} from the ignore list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{channel.mention} is not in the ignore list.", ephemeral=True)

    @vcping_ignore_group.command(name="list", description="List ignored Voice Channels")
    async def vcping_ignore_list(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        if guild_id in self.config and self.config[guild_id]['ignored']:
            channels = [f"<#{cid}>" for cid in self.config[guild_id]['ignored']]
            await interaction.response.send_message(f"Ignored VCs: {', '.join(channels)}", ephemeral=True)
        else:
            await interaction.response.send_message("No VCs are currently ignored.", ephemeral=True)

    @vcping_group.command(name="set", description="Set the VC ping settings")
    @app_commands.describe(role="The role to ping", people="Number of people required", minutes="Minutes to wait before pinging")
    async def vcping_set(self, interaction: discord.Interaction, role: discord.Role, people: int, minutes: int):
        guild_id = str(interaction.guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {'ignored': []}
        
        self.config[guild_id].update({
            'role': role.id,
            'people': people,
            'minutes': minutes
        })
        self.save_config()
        await interaction.response.send_message(f"Settings updated: Ping {role.mention} when {people} people are in a VC for {minutes} minutes.", ephemeral=True)

    @tasks.loop(seconds=60)
    async def check_vcs(self):
        for guild_id, state_data in self.vc_state.items():
            if guild_id not in self.config:
                continue
            
            settings = self.config[guild_id]
            threshold_minutes = settings.get('minutes', 5)
            ping_role_id = settings.get('role')

            if not ping_role_id:
                continue

            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            role = guild.get_role(ping_role_id)
            if not role:
                continue

            for channel_id, data in state_data.items():
                if data.get('pinged'):
                    continue

                start_time_iso = data.get('start_time')
                if not start_time_iso:
                    continue

                start_time = datetime.fromisoformat(start_time_iso)
                if datetime.now() - start_time >= timedelta(minutes=threshold_minutes):
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        try:
                            await channel.send(f"{role.mention} The VC has been active for {threshold_minutes} minutes!")
                            self.vc_state[guild_id][channel_id]['pinged'] = True
                        except Exception as e:
                            print(f"Failed to send VC ping in {channel.name}: {e}")

    @check_vcs.before_loop
    async def before_check_vcs(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        guild_id = str(member.guild.id)
        if guild_id not in self.config:
            return

        settings = self.config[guild_id]
        threshold_people = settings.get('people', 2)
        ignored_vcs = settings.get('ignored', [])

        # Function to process a channel state
        def update_channel_state(channel):
            if not channel or channel.id in ignored_vcs:
                return

            cid = str(channel.id)
            if guild_id not in self.vc_state:
                self.vc_state[guild_id] = {}

            current_members = len(channel.members)

            # If empty, reset everything
            if current_members == 0:
                if cid in self.vc_state[guild_id]:
                    del self.vc_state[guild_id][cid]
                return

            # Check occupancy
            if current_members >= threshold_people:
                if cid not in self.vc_state[guild_id]:
                    # Start tracking
                    self.vc_state[guild_id][cid] = {
                        'start_time': datetime.now().isoformat(),
                        'pinged': False
                    }
            else:
                # Below threshold
                # If we were tracking, we stop tracking unless it was already pinged.
                # If it was ALREADY pinged, we stay in 'pinged' state (to prevent re-ping) until it goes to 0 (handled above).
                
                if cid in self.vc_state[guild_id]:
                    if not self.vc_state[guild_id][cid]['pinged']:
                        # Reset timer if not yet pinged and drops below threshold
                         del self.vc_state[guild_id][cid]

        # Check both before and after channels
        if before.channel:
            update_channel_state(before.channel)
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            update_channel_state(after.channel)

async def setup(bot):
    await bot.add_cog(Admin(bot))
