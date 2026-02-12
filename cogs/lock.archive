import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from datetime import timedelta, timezone
import asyncio
from typing import Literal, Optional

# Function/Class List:
# class Lockout(commands.Cog)
# - __init__(self, bot)
# - cog_unload(self)
# - get_config(self, guild_id)
# - save_config(self, guild_id, data)
# - get_user_schedule(self, guild_id, user_id)
# - save_user_schedule(self, guild_id, user_id, data)
# - delete_user_schedule(self, guild_id, user_id)
# - get_jail_data(self, guild_id, user_id)
# - save_jail_data(self, guild_id, user_id, data)
# - delete_jail_data(self, guild_id, user_id)
# - is_time_in_range(self, start_str, end_str, current_dt)
# - create_progress_bar(self, total, remaining)
# - update_jail_sticky(self, guild)
# - check_lockout_loop(self)
# - on_voice_state_update(self, member, before, after)
# - on_message(self, message)
# - setschedule(self, interaction, start, end, repeat) [Slash - Public]
# - viewschedule(self, interaction) [Slash - Public]
# - clearschedule(self, interaction) [Slash - Public]
# - timeout(self, interaction, member, minutes) [Slash - Admin]
# - untimeout(self, interaction, member) [Slash - Admin]
# - adminclear(self, interaction, member) [Slash - Admin]
# - setlockout(self, interaction, command_channel, jail_channel, target_role) [Slash - Admin]
# - timezone(self, interaction, action, role, offset) [Slash - Admin]
# setup(bot)

class Lockout(commands.Cog):
    """Cog for managing user schedules, timezones, and the jail/timeout system."""
    def __init__(self, bot):
        self.bot = bot
        self.check_lockout_loop.start()

    def cog_unload(self):
        self.check_lockout_loop.cancel()

    # --- DB HELPERS ---
    def get_config(self, guild_id):
        configs = self.bot.db.get_collection("lockout_configs")
        if isinstance(configs, list):
            return next((c for c in configs if c.get('guild_id') == guild_id), None)
        return None

    def save_config(self, guild_id, data):
        data['guild_id'] = guild_id
        self.bot.db.update_doc("lockout_configs", "guild_id", guild_id, data)

    def get_user_schedule(self, guild_id, user_id):
        schedules = self.bot.db.get_collection("lockout_schedules")
        return next((s for s in schedules if s['guild_id'] == guild_id and s['user_id'] == user_id), None)

    def save_user_schedule(self, guild_id, user_id, data):
        data['guild_id'] = guild_id
        data['user_id'] = user_id
        schedules = self.bot.db.get_collection("lockout_schedules")
        schedules = [s for s in schedules if not (s['guild_id'] == guild_id and s['user_id'] == user_id)]
        schedules.append(data)
        self.bot.db.save_collection("lockout_schedules", schedules)

    def delete_user_schedule(self, guild_id, user_id):
        schedules = self.bot.db.get_collection("lockout_schedules")
        schedules = [s for s in schedules if not (s['guild_id'] == guild_id and s['user_id'] == user_id)]
        self.bot.db.save_collection("lockout_schedules", schedules)

    def get_jail_data(self, guild_id, user_id):
        jails = self.bot.db.get_collection("lockout_jail")
        return next((j for j in jails if j['guild_id'] == guild_id and j['user_id'] == user_id), None)

    def save_jail_data(self, guild_id, user_id, data):
        data['guild_id'] = guild_id
        data['user_id'] = user_id
        jails = self.bot.db.get_collection("lockout_jail")
        jails = [j for j in jails if not (j['guild_id'] == guild_id and j['user_id'] == user_id)]
        jails.append(data)
        self.bot.db.save_collection("lockout_jail", jails)

    def delete_jail_data(self, guild_id, user_id):
        jails = self.bot.db.get_collection("lockout_jail")
        jails = [j for j in jails if not (j['guild_id'] == guild_id and j['user_id'] == user_id)]
        self.bot.db.save_collection("lockout_jail", jails)

    # --- LOGIC ---
    def is_time_in_range(self, start_str, end_str, current_dt):
        current_time = current_dt.time()
        start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.datetime.strptime(end_str, "%H:%M").time()
        
        if start_time < end_time:
            return start_time <= current_time <= end_time
        else: 
            return current_time >= start_time or current_time <= end_time

    def create_progress_bar(self, total, remaining):
        """Creates a visual bar that matches the tasks.py style."""
        if total <= 0: return ""
        
        # Grid Size: 16 Columns x 2 Rows = 32 Squares total (Matching tasks.py)
        cols = 16
        rows = 2
        total_blocks = cols * rows
        
        # Calculate percent completion
        elapsed = max(0, total - remaining)
        percent = elapsed / total
        
        green_blocks = int(percent * total_blocks)
        visual_state = [1] * green_blocks + [0] * (total_blocks - green_blocks)
        
        if len(visual_state) > total_blocks: 
            visual_state = visual_state[:total_blocks]

        SYM_DONE = "🟩" 
        SYM_TODO = "⬜"
        
        row0 = "-# "
        row1 = "-# "
        
        for i in range(total_blocks):
            sym = SYM_DONE if visual_state[i] == 1 else SYM_TODO
            if i % 2 == 0: row0 += sym
            else: row1 += sym
            
        return f"{row0}\n{row1}"

    async def update_jail_sticky(self, guild):
        config = self.get_config(guild.id)
        if not config or not config.get('jail_channel_id'): return

        channel = guild.get_channel(config['jail_channel_id'])
        if not channel: return

        jails = self.bot.db.get_collection("lockout_jail")
        # Buggy's requested change: Show all active timeouts even if they haven't joined yet
        guild_jails = [j for j in jails if j['guild_id'] == guild.id and j.get('remaining_seconds', 0) > 0]

        if not guild_jails:
            if config.get('jail_sticky_id'):
                try:
                    msg = await channel.fetch_message(config['jail_sticky_id'])
                    await msg.delete()
                except: pass
                config['jail_sticky_id'] = None
                self.save_config(guild.id, config)
            return

        content = "**🔒 Active Timeouts**\n"
        for inmate in guild_jails:
            uid = inmate['user_id']
            remaining = int(inmate['remaining_seconds'])
            total = inmate.get('total_seconds', remaining)
            member = guild.get_member(uid)
            name = member.display_name if member else f"ID: {uid}"
            
            # Check if currently counting (in the timeout VC)
            in_vc = member and member.voice and member.voice.channel and member.voice.channel.id == config['jail_channel_id']
            status = " (Counting...)" if in_vc else " (Paused)"
            
            bar = self.create_progress_bar(total, remaining)
            mins_left = int(remaining // 60)
            
            content += f"\n**{name}**{status} — {mins_left}m remaining\n{bar}\n"

        existing_id = config.get('jail_sticky_id')
        msg = None
        if existing_id:
            try:
                msg = await channel.fetch_message(existing_id)
                # If someone talked, move sticky down
                if channel.last_message_id != msg.id:
                    await msg.delete()
                    msg = None
                else:
                    if msg.content != content: await msg.edit(content=content)
            except (discord.NotFound, discord.Forbidden): msg = None

        if not msg:
            try:
                msg = await channel.send(content)
                config['jail_sticky_id'] = msg.id
                self.save_config(guild.id, config)
            except: pass

    @tasks.loop(seconds=15)
    async def check_lockout_loop(self):
        jails = self.bot.db.get_collection("lockout_jail")
        active_jails = [j for j in jails if j.get('remaining_seconds', 0) > 0]
        jail_updates = []
        guilds_to_update = set()
        now_ts = datetime.datetime.now().timestamp()
        
        for jail_rec in active_jails:
            guild = self.bot.get_guild(jail_rec['guild_id'])
            if not guild: continue
            member = guild.get_member(jail_rec['user_id'])
            if not member: continue
            config = self.get_config(guild.id)
            if not config or not config.get('jail_channel_id'): continue
            
            if member.voice and member.voice.channel and member.voice.channel.id == config['jail_channel_id']:
                if jail_rec.get('last_check'):
                    # Calculate actual elapsed time since last check
                    diff = now_ts - jail_rec['last_check']
                    jail_rec['remaining_seconds'] = max(0, jail_rec['remaining_seconds'] - diff)
                    guilds_to_update.add(guild)
                
                jail_rec['last_check'] = now_ts
                
                if jail_rec['remaining_seconds'] <= 0:
                    # Release Inmate
                    target_role = guild.get_role(config.get('target_role_id'))
                    if target_role:
                        try:
                            await member.add_roles(target_role)
                            try: 
                                chan = guild.get_channel(config['jail_channel_id'])
                                await chan.send(f"🔓 {member.mention} has completed their timeout!")
                            except: pass
                        except: pass
                    self.delete_jail_data(guild.id, member.id)
                    continue 
            else: 
                # Not in VC, don't count down
                jail_rec['last_check'] = None
            
            jail_updates.append(jail_rec)
        
        # Save updates to DB
        if jail_updates:
            current_jails = self.bot.db.get_collection("lockout_jail")
            for update in jail_updates:
                for i, j in enumerate(current_jails):
                    if j['guild_id'] == update['guild_id'] and j['user_id'] == update['user_id']:
                        current_jails[i] = update
                        break
            self.bot.db.save_collection("lockout_jail", current_jails)
            
        for g in guilds_to_update: await self.update_jail_sticky(g)

        # Handle Schedules
        all_configs = self.bot.db.get_collection("lockout_configs")
        current_utc = datetime.datetime.now(timezone.utc)
        if not isinstance(all_configs, list): return

        for config in all_configs:
            guild_id = config.get('guild_id')
            if not guild_id: continue
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            target_role = guild.get_role(config.get('target_role_id'))
            if not target_role: continue
            time_zones = config.get('time_zones', [])
            if not time_zones: continue

            for zone in time_zones:
                tz_role = guild.get_role(zone['role_id'])
                if not tz_role: continue
                offset = zone['offset']
                local_time = current_utc + timedelta(hours=offset)

                for member in tz_role.members:
                    if self.get_jail_data(guild.id, member.id): continue
                    schedule = self.get_user_schedule(guild.id, member.id)
                    if not schedule or 'start' not in schedule: continue
                    
                    repeat_mode = schedule.get('repeat', 'daily')
                    current_weekday = local_time.weekday()
                    is_active_day = True
                    if repeat_mode == 'weekdays' and current_weekday >= 5: is_active_day = False
                    elif repeat_mode == 'weekends' and current_weekday < 5: is_active_day = False

                    should_be_locked = self.is_time_in_range(schedule['start'], schedule['end'], local_time)
                    if not is_active_day: should_be_locked = False

                    has_role = target_role in member.roles
                    was_locked_by_bot = schedule.get('locked_by_bot', False)

                    if should_be_locked and has_role:
                        try:
                            await member.remove_roles(target_role)
                            schedule['locked_by_bot'] = True
                            self.save_user_schedule(guild.id, member.id, schedule)
                        except: pass
                    elif not should_be_locked and not has_role and was_locked_by_bot:
                        try:
                            await member.add_roles(target_role)
                            schedule['locked_by_bot'] = False
                            self.save_user_schedule(guild.id, member.id, schedule)
                        except: pass

    @check_lockout_loop.before_loop
    async def before_check(self): await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        jail_data = self.get_jail_data(member.guild.id, member.id)
        if not jail_data: return
        config = self.get_config(member.guild.id)
        if not config or not config.get('jail_channel_id'): return
        
        jail_vc_id = config['jail_channel_id']
        now_ts = datetime.datetime.now().timestamp()
        should_update_sticky = False

        if after.channel and after.channel.id == jail_vc_id:
            jail_data['last_check'] = now_ts
            self.save_jail_data(member.guild.id, member.id, jail_data)
            should_update_sticky = True
        elif before.channel and before.channel.id == jail_vc_id:
            if jail_data.get('last_check'):
                diff = now_ts - jail_data['last_check']
                jail_data['remaining_seconds'] = max(0, jail_data['remaining_seconds'] - diff)
                jail_data['last_check'] = None
                self.save_jail_data(member.guild.id, member.id, jail_data)
                should_update_sticky = True
        if should_update_sticky: await self.update_jail_sticky(member.guild)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        config = self.get_config(message.guild.id)
        if config and config.get('jail_channel_id') == message.channel.id:
            await self.update_jail_sticky(message.guild)

    # --- SLASH COMMANDS (USER) ---
    
    @app_commands.command(name="setschedule", description="Set your personal lockout schedule (HH:MM format).", extras={'public': True})
    @app_commands.describe(start="Start time (e.g. 23:00)", end="End time (e.g. 07:00)", repeat="How often to repeat")
    @app_commands.choices(repeat=[
        app_commands.Choice(name="Daily (Every Day)", value="daily"),
        app_commands.Choice(name="Weekdays (Mon-Fri)", value="weekdays"),
        app_commands.Choice(name="Weekends (Sat-Sun)", value="weekends")
    ])
    async def setschedule(self, interaction: discord.Interaction, start: str, end: str, repeat: app_commands.Choice[str] = None):
        """Set your personal lockout schedule (HH:MM format)."""
        config = self.get_config(interaction.guild_id)
        if config and config.get('command_channel_id') and interaction.channel_id != config['command_channel_id']:
            return await interaction.response.send_message(f"Please use <#{config['command_channel_id']}> for lock commands!", ephemeral=True)

        try:
            datetime.datetime.strptime(start, "%H:%M")
            datetime.datetime.strptime(end, "%H:%M")
        except ValueError:
            return await interaction.response.send_message("❌ Invalid time format! Use HH:MM (24-hour).", ephemeral=True)

        repeat_val = repeat.value if repeat else "daily"
        
        data = {
            "start": start,
            "end": end,
            "repeat": repeat_val,
            "locked_by_bot": False
        }
        self.save_user_schedule(interaction.guild_id, interaction.user.id, data)
        await interaction.response.send_message(f"✅ Schedule set! Lock from **{start}** to **{end}** ({repeat_val}).", ephemeral=True)

    @app_commands.command(name="viewschedule", description="View your current lockout schedule.", extras={'public': True})
    async def viewschedule(self, interaction: discord.Interaction):
        """View your current lockout schedule."""
        config = self.get_config(interaction.guild_id)
        if config and config.get('command_channel_id') and interaction.channel_id != config['command_channel_id']:
            return await interaction.response.send_message(f"Please use <#{config['command_channel_id']}> for lock commands!", ephemeral=True)

        schedule = self.get_user_schedule(interaction.guild_id, interaction.user.id)
        if not schedule:
            return await interaction.response.send_message("You don't have a schedule set.", ephemeral=True)

        await interaction.response.send_message(f"📅 **Your Schedule**\nTime: {schedule['start']} - {schedule['end']}\nRepeat: {schedule.get('repeat', 'daily')}", ephemeral=True)

    @app_commands.command(name="clearschedule", description="Remove your lockout schedule.", extras={'public': True})
    async def clearschedule(self, interaction: discord.Interaction):
        """Remove your lockout schedule."""
        config = self.get_config(interaction.guild_id)
        if config and config.get('command_channel_id') and interaction.channel_id != config['command_channel_id']:
            return await interaction.response.send_message(f"Please use <#{config['command_channel_id']}> for lock commands!", ephemeral=True)

        self.delete_user_schedule(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("✅ Schedule deleted.", ephemeral=True)

    # --- SLASH COMMANDS (ADMIN) ---

    @app_commands.command(name="timeout", description="Send a user to the lockout jail.")
    @app_commands.describe(member="The user to timeout", minutes="Duration in minutes")
    @app_commands.default_permissions(administrator=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int):
        """Send a user to the lockout jail."""
        config = self.get_config(interaction.guild_id)
        if not config or not config.get('target_role_id') or not config.get('jail_channel_id'):
            return await interaction.response.send_message("❌ Lockout system not configured! Run `/setlockout` first.", ephemeral=True)

        target_role = interaction.guild.get_role(config['target_role_id'])
        if not target_role:
             return await interaction.response.send_message("❌ Target role not found!", ephemeral=True)

        # 1. Remove Target Role (Lock them out)
        try:
            await member.remove_roles(target_role)
        except:
            return await interaction.response.send_message("❌ Failed to remove role. Check my permissions.", ephemeral=True)

        # 2. Save Jail Data
        seconds = minutes * 60
        data = {
            "start_time": datetime.datetime.now().timestamp(),
            "total_seconds": seconds,
            "remaining_seconds": seconds,
            "last_check": None
        }
        self.save_jail_data(interaction.guild_id, member.id, data)
        await self.update_jail_sticky(interaction.guild)

        jail_channel = interaction.guild.get_channel(config['jail_channel_id'])
        await interaction.response.send_message(f"🚨 **{member.display_name}** has been sent to jail for {minutes} minutes!", ephemeral=False)
        if jail_channel:
             await jail_channel.send(f"{member.mention} You have been timed out for {minutes} minutes. Join this VC to start your timer.")

    @app_commands.command(name="untimeout", description="Release a user from jail early.")
    @app_commands.describe(member="The user to release")
    @app_commands.default_permissions(administrator=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        """Release a user from jail early."""
        self.delete_jail_data(interaction.guild_id, member.id)
        
        config = self.get_config(interaction.guild_id)
        if config and config.get('target_role_id'):
            role = interaction.guild.get_role(config['target_role_id'])
            if role:
                try: await member.add_roles(role)
                except: pass
        
        await self.update_jail_sticky(interaction.guild)
        await interaction.response.send_message(f"✅ Released {member.mention} from jail.", ephemeral=True)

    @app_commands.command(name="adminclear", description="Force clear a user's schedule.")
    @app_commands.describe(member="The user")
    @app_commands.default_permissions(administrator=True)
    async def adminclear(self, interaction: discord.Interaction, member: discord.Member):
        """Force clear a user's schedule."""
        self.delete_user_schedule(interaction.guild_id, member.id)
        await interaction.response.send_message(f"✅ Cleared schedule for {member.display_name}.", ephemeral=True)

    # --- ADMIN CONFIGURATION ---

    @app_commands.command(name="setlockout", description="Configure the main lockout settings.")
    @app_commands.describe(
        command_channel="Channel for user commands (/setschedule)",
        jail_channel="Voice Channel for timeouts",
        target_role="The role to remove (Lockout Role)"
    )
    @app_commands.default_permissions(administrator=True)
    async def setlockout(self, interaction: discord.Interaction, 
                         command_channel: discord.TextChannel, 
                         jail_channel: discord.VoiceChannel, 
                         target_role: discord.Role):
        """Configure the main lockout settings."""
        config = self.get_config(interaction.guild_id) or {"guild_id": interaction.guild_id}
        
        config['command_channel_id'] = command_channel.id
        config['jail_channel_id'] = jail_channel.id
        config['target_role_id'] = target_role.id
        
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(
            f"✅ **Lockout Configured!**\n"
            f"📜 Commands: {command_channel.mention}\n"
            f"🔊 Jail: {jail_channel.mention}\n"
            f"🎭 Target Role: {target_role.mention}",
            ephemeral=True
        )

    @app_commands.command(name="timezone", description="Manage timezone roles.")
    @app_commands.describe(
        action="Add, Remove, or List timezones",
        role="The role for this timezone (Add/Remove)",
        offset="Offset from UTC (e.g., -5 for EST) (Add only)"
    )
    @app_commands.default_permissions(administrator=True)
    async def timezone(self, interaction: discord.Interaction, 
                       action: Literal["Add", "Remove", "List"], 
                       role: Optional[discord.Role] = None, 
                       offset: Optional[int] = None):
        """Manage timezone roles."""
        config = self.get_config(interaction.guild_id) or {"guild_id": interaction.guild_id}
        zones = config.get('time_zones', [])

        # --- ADD ---
        if action == "Add":
            if not role or offset is None:
                return await interaction.response.send_message("❌ Error: `role` and `offset` are required to Add.", ephemeral=True)
            
            # Remove existing if role already present to overwrite
            zones = [z for z in zones if z['role_id'] != role.id]
            zones.append({'role_id': role.id, 'offset': offset})
            
            config['time_zones'] = zones
            self.save_config(interaction.guild_id, config)
            await interaction.response.send_message(f"✅ Added Timezone: {role.mention} = UTC{offset:+d}", ephemeral=True)

        # --- REMOVE ---
        elif action == "Remove":
            if not role:
                return await interaction.response.send_message("❌ Error: `role` is required to Remove.", ephemeral=True)
            
            initial_len = len(zones)
            zones = [z for z in zones if z['role_id'] != role.id]
            
            if len(zones) < initial_len:
                config['time_zones'] = zones
                self.save_config(interaction.guild_id, config)
                await interaction.response.send_message(f"✅ Removed Timezone for {role.mention}.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ That role is not configured as a timezone.", ephemeral=True)

        # --- LIST ---
        elif action == "List":
            if not zones:
                return await interaction.response.send_message("📝 No timezones configured.", ephemeral=True)
                
            text = "**🌍 Configured Timezones**\n"
            for z in zones:
                r = interaction.guild.get_role(z['role_id'])
                role_name = r.mention if r else f"ID:{z['role_id']}"
                text += f"• {role_name}: UTC{z['offset']:+d}\n"
                
            await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Lockout(bot))
