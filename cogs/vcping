import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from typing import Literal, Optional

# Function/Class List:
# class VCPing(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_vcping_config()
# - save_vcping_config(config)
# - check_vcs()
# - before_check_vcs()
# - on_voice_state_update(member, before, after)
# - vcping(interaction, role, people, minutes) [Slash]
# - vcignore(interaction, action, channel) [Slash]
# setup(bot)

class VCPing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "VC Ping system."
        self.vc_state = {}
        self.check_vcs.start()

    def cog_unload(self):
        self.check_vcs.cancel()

    # --- HELPERS ---

    def get_vcping_config(self):
        """Fetches VC Ping config for all guilds."""
        data = self.bot.db.get_collection("vcping_config")
        if isinstance(data, list): return {} 
        return data

    def save_vcping_config(self, config):
        """Saves VC Ping config."""
        self.bot.db.save_collection("vcping_config", config)

    # --- TASKS ---

    @tasks.loop(seconds=60)
    async def check_vcs(self):
        config = self.get_vcping_config()
        for guild_id, state_data in self.vc_state.items():
            if guild_id not in config: continue
            
            settings = config[guild_id]
            threshold_minutes = settings.get('minutes', 5)
            ping_role_id = settings.get('role')

            if not ping_role_id: continue

            guild = self.bot.get_guild(int(guild_id))
            if not guild: continue

            role = guild.get_role(ping_role_id)
            if not role: continue

            for channel_id, data in state_data.items():
                if data.get('pinged'): continue

                start_time_iso = data.get('start_time')
                if not start_time_iso: continue

                start_time = datetime.datetime.fromisoformat(start_time_iso)
                if datetime.datetime.now() - start_time >= datetime.timedelta(minutes=threshold_minutes):
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        try:
                            await channel.send(f"{role.mention} The VC has been active for {threshold_minutes} minutes!")
                            self.vc_state[guild_id][channel_id]['pinged'] = True
                        except Exception as e: print(f"Failed to send VC ping in {channel.name}: {e}")

    @check_vcs.before_loop
    async def before_check_vcs(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return

        guild_id = str(member.guild.id)
        config = self.get_vcping_config()
        if guild_id not in config: return

        settings = config[guild_id]
        threshold_people = settings.get('people', 2)
        ignored_vcs = settings.get('ignored', [])

        def update_channel_state(channel):
            if not channel or channel.id in ignored_vcs: return

            cid = str(channel.id)
            if guild_id not in self.vc_state: self.vc_state[guild_id] = {}

            current_members = len(channel.members)

            if current_members == 0:
                if cid in self.vc_state[guild_id]: del self.vc_state[guild_id][cid]
                return

            if current_members >= threshold_people:
                if cid not in self.vc_state[guild_id]:
                    self.vc_state[guild_id][cid] = {'start_time': datetime.datetime.now().isoformat(), 'pinged': False}
            else:
                if cid in self.vc_state[guild_id]:
                    if not self.vc_state[guild_id][cid]['pinged']:
                         del self.vc_state[guild_id][cid]

        if before.channel: update_channel_state(before.channel)
        if after.channel and (not before.channel or before.channel.id != after.channel.id): update_channel_state(after.channel)

    # --- SLASH COMMANDS ---

    @app_commands.command(name="vcignore", description="Manage ignored VCs for ping system.")
    @app_commands.describe(
        action="Add, Remove, or List",
        channel="The voice channel (required for Add/Remove)"
    )
    @app_commands.default_permissions(administrator=True)
    async def vcignore(self, interaction: discord.Interaction, 
                       action: Literal["Add", "Remove", "List"], 
                       channel: Optional[discord.VoiceChannel] = None):
        """Manage ignored VCs for ping system."""
        guild_id = str(interaction.guild_id)
        config = self.get_vcping_config()
        if guild_id not in config: config[guild_id] = {'ignored': [], 'role': None, 'people': 2, 'minutes': 5}
        
        if action == "Add":
            if not channel: return await interaction.response.send_message("❌ Error: `channel` is required to Add.", ephemeral=True)
            if channel.id in config[guild_id]['ignored']:
                return await interaction.response.send_message(f"⚠️ {channel.mention} is already ignored.", ephemeral=True)
            config[guild_id]['ignored'].append(channel.id)
            self.save_vcping_config(config)
            await interaction.response.send_message(f"✅ Added {channel.mention} to the ignore list.", ephemeral=True)

        elif action == "Remove":
            if not channel: return await interaction.response.send_message("❌ Error: `channel` is required to Remove.", ephemeral=True)
            if channel.id not in config[guild_id]['ignored']:
                return await interaction.response.send_message(f"⚠️ {channel.mention} is not in the ignore list.", ephemeral=True)
            config[guild_id]['ignored'].remove(channel.id)
            self.save_vcping_config(config)
            await interaction.response.send_message(f"✅ Removed {channel.mention} from the ignore list.", ephemeral=True)

        elif action == "List":
            if not config[guild_id]['ignored']:
                return await interaction.response.send_message("No VCs are currently ignored.", ephemeral=True)
            channels = [f"<#{cid}>" for cid in config[guild_id]['ignored']]
            await interaction.response.send_message(f"Ignored VCs: {', '.join(channels)}", ephemeral=True)

    @app_commands.command(name="vcping", description="Configure VC Ping settings.")
    @app_commands.describe(role="The role to ping", people="Minimum people required", minutes="Minutes active before ping")
    @app_commands.default_permissions(administrator=True)
    async def vcping_set(self, interaction: discord.Interaction, role: discord.Role, people: int, minutes: int):
        """Configure VC Ping settings."""
        guild_id = str(interaction.guild_id)
        config = self.get_vcping_config()
        if guild_id not in config: config[guild_id] = {'ignored': []}
        config[guild_id].update({'role': role.id, 'people': people, 'minutes': minutes})
        self.save_vcping_config(config)
        await interaction.response.send_message(f"✅ Settings updated: Ping {role.mention} when {people} people are in a VC for {minutes} minutes.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(VCPing(bot))
