import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from datetime import timedelta, timezone
import asyncio

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
# - lockout_group (Group)
# - config_group (Group parent=lockout_group)
# - zone_group (Group parent=lockout_group)
# - myset(self, interaction, start, end, repeat)
# - myview(self, interaction)
# - myclear(self, interaction)
# - adminclear(self, interaction, member)
# - timeout(self, interaction, member, minutes, cancel)
# - set_target_role(self, interaction, role)
# - set_jail_channel(self, interaction, channel)
# - add_zone(self, interaction, role, offset)
# - remove_zone(self, interaction, role)
# - list_zones(self, interaction)
# setup(bot)

class Lockout(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_lockout_loop.start()

    def cog_unload(self):
        self.check_lockout_loop.cancel()

    # --- DB HELPERS ---
    def get_config(self, guild_id):
        # Collection: lockout_configs (List of dicts)
        configs = self.bot.db.get_collection("lockout_configs")
        if isinstance(configs, list):
            return next((c for c in configs if c.get('guild_id') == guild_id), None)
        return None

    def save_config(self, guild_id, data):
        data['guild_id'] = guild_id
        # Wrapper handles update/insert
        self.bot.db.update_doc("lockout_configs", "guild_id", guild_id, data)

    def get_user_schedule(self, guild_id, user_id):
        schedules = self.bot.db.get_collection("lockout_schedules")
        return next((s for s in schedules if s['guild_id'] == guild_id and s['user_id'] == user_id), None)

    def save_user_schedule(self, guild_id, user_id, data):
        data['guild_id'] = guild_id
        data['user_id'] = user_id
        schedules = self.bot.db.get_collection("lockout_schedules")
        # Remove old entry for this user/guild combo
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
        # Grid Size: 16 Columns x 2 Rows = 32 Squares total
        cols = 16
        rows = 2
        total_blocks = cols * rows
        
        elapsed = max(0, total - remaining)
        percent = elapsed / total if total > 0 else 1.0
        
        green_blocks = int(percent * total_blocks)
        
        # 1 = Green, 0 = White
        visual_state = [1] * green_blocks + [0] * (total_blocks - green_blocks)
        
        # Ensure exact length
        if len(visual_state) > total_blocks: visual_state = visual_state[:total_blocks]

        SYM_DONE = "🟩" 
        SYM_TODO = "⬜"

        row0 = "-# "
        row1 = "-# "
        
        for i in range(total_blocks):
            sym = SYM_DONE if visual_state[i] == 1 else SYM_TODO
            if i % 2 == 0:
                row0 += sym
            else:
                row1 += sym
                
        return f"{row0}\n{row1}"

    async def update_jail_sticky(self, guild):
        config = self.get_config(guild.id)
        if not config or not config.get('jail_channel_id'): return

        channel = guild.get_channel(config['jail_channel_id'])
        if not channel: return

        # Gather all active jail records for this guild
        jails = self.bot.db.get_collection("lockout_jail")
        guild_jails = [j for j in jails if j['guild_id'] == guild.id and j.get('remaining_seconds', 0) > 0]

        # Only care about people currently IN the VC
        active_inmates = []
        for j in guild_jails:
            member = guild.get_member(j['user_id'])
            if member and member.voice and member.voice.channel and member.voice.channel.id == channel.id:
                active_inmates.append(j)

        if not active_inmates:
            # If a sticky exists, delete it
            if config.get('jail_sticky_id'):
                try:
                    msg = await channel.fetch_message(config['jail_sticky_id'])
                    await msg.delete()
                except: pass
                config['jail_sticky_id'] = None
                self.save_config(guild.id, config)
            return

        # Build Message
        content = "**🔒 Active Timeouts**\n"
        for inmate in active_inmates:
            uid = inmate['user_id']
            remaining = int(inmate['remaining_seconds'])
            # We need the original total. If we didn't save it, estimate or default.
            # Assuming 'total_seconds' was saved (I'll add this to the timeout command)
            # If not present, default to remaining + 60
            total = inmate.get('total_seconds', remaining)
            
            member = guild.get_member(uid)
            name = member.display_name if member else f"ID: {uid}"
            
            bar = self.create_progress_bar(total, remaining)
            mins_left = int(remaining // 60)
            
            content += f"\n**{name}** ({mins_left}m remaining)\n{bar}\n"

        # Check existing
        existing_id = config.get('jail_sticky_id')
        msg = None
        
        if existing_id:
            try:
                msg = await channel.fetch_message(existing_id)
                # Check if it's the last message
                if channel.last_message_id != msg.id:
                    await msg.delete()
                    msg = None
                else:
                    if msg.content != content:
                        await msg.edit(content=content)
            except (discord.NotFound, discord.Forbidden):
                msg = None

        if not msg:
            try:
                msg = await channel.send(content)
                config['jail_sticky_id'] = msg.id
                self.save_config(guild.id, config)
            except: pass

    @tasks.loop(minutes=1)
    async def check_lockout_loop(self):
        # 1. JAIL CHECK
        jails = self.bot.db.get_collection("lockout_jail")
        active_jails = [j for j in jails if j.get('remaining_seconds', 0) > 0]
        
        # We need to process updates for active jails
        jail_updates = []
        guilds_to_update = set()
        
        now_ts = datetime.datetime.now().timestamp()
        
        for jail_rec in active_jails:
            guild = self.bot.get_guild(jail_rec['guild_id'])
            if not guild: continue
            
            # Use fetch_member to ensure cache, fallback to get_member
            member = guild.get_member(jail_rec['user_id'])
            if not member: continue

            config = self.get_config(guild.id)
            if not config or not config.get('jail_channel_id'): continue
            
            # Logic: If they are in VC, tick down.
            if member.voice and member.voice.channel and member.voice.channel.id == config['jail_channel_id']:
                if jail_rec.get('last_check'):
                    diff = now_ts - jail_rec['last_check']
                    jail_rec['remaining_seconds'] = max(0, jail_rec['remaining_seconds'] - diff)
                    guilds_to_update.add(guild)
                
                jail_rec['last_check'] = now_ts
                
                # Check if finished
                if jail_rec['remaining_seconds'] <= 0:
                    # Free them
                    target_role = guild.get_role(config.get('target_role_id'))
                    if target_role:
                        try:
                            await member.add_roles(target_role)
                            # Notify in jail channel
                            try: 
                                chan = guild.get_channel(config['jail_channel_id'])
                                await chan.send(f"🔓 {member.mention} has completed their timeout!")
                            except: pass
                        except: pass
                    self.delete_jail_data(guild.id, member.id)
                    continue # Skip adding to update list since deleted
            else:
                # Not in VC, pause timer
                jail_rec['last_check'] = None
            
            jail_updates.append(jail_rec)
        
        # Save Jail Updates
        if jail_updates:
            current_jails = self.bot.db.get_collection("lockout_jail")
            # Update modified records in the list
            for update in jail_updates:
                for i, j in enumerate(current_jails):
                    if j['guild_id'] == update['guild_id'] and j['user_id'] == update['user_id']:
                        current_jails[i] = update
                        break
            self.bot.db.save_collection("lockout_jail", current_jails)
            
        # Trigger sticky updates for relevant guilds
        for g in guilds_to_update:
            await self.update_jail_sticky(g)


        # 2. SCHEDULE CHECK
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

                # Check members with this timezone role
                for member in tz_role.members:
                    # Don't touch if they are in jail
                    jail_data = self.get_jail_data(guild.id, member.id)
                    if jail_data: continue

                    schedule = self.get_user_schedule(guild.id, member.id)
                    if not schedule or 'start' not in schedule: continue

                    # Check Repeat Logic
                    repeat_mode = schedule.get('repeat', 'daily')
                    current_weekday = local_time.weekday() # 0 = Monday, 6 = Sunday
                    
                    is_active_day = True
                    if repeat_mode == 'weekdays':
                        # Mon(0) to Fri(4) are weekdays. Sat(5), Sun(6) are weekends.
                        if current_weekday >= 5: 
                            is_active_day = False
                    elif repeat_mode == 'weekends':
                        if current_weekday < 5:
                            is_active_day = False

                    should_be_locked = self.is_time_in_range(schedule['start'], schedule['end'], local_time)
                    
                    # Force unlock if today is not an active day
                    if not is_active_day:
                        should_be_locked = False

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
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Update jail timer instantly on join/leave
        if member.bot: return
        
        jail_data = self.get_jail_data(member.guild.id, member.id)
        if not jail_data: return
        
        config = self.get_config(member.guild.id)
        if not config or not config.get('jail_channel_id'): return
        
        jail_vc_id = config['jail_channel_id']
        now_ts = datetime.datetime.now().timestamp()
        
        should_update_sticky = False

        # JOINING JAIL
        if after.channel and after.channel.id == jail_vc_id:
            jail_data['last_check'] = now_ts
            self.save_jail_data(member.guild.id, member.id, jail_data)
            should_update_sticky = True
        
        # LEAVING JAIL
        elif before.channel and before.channel.id == jail_vc_id:
            if jail_data.get('last_check'):
                diff = now_ts - jail_data['last_check']
                jail_data['remaining_seconds'] = max(0, jail_data['remaining_seconds'] - diff)
                jail_data['last_check'] = None
                self.save_jail_data(member.guild.id, member.id, jail_data)
                should_update_sticky = True
                
        if should_update_sticky:
            await self.update_jail_sticky(member.guild)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Ensures the jail sticky message stays at the bottom."""
        if message.author.bot or not message.guild: return
        
        config = self.get_config(message.guild.id)
        if not config or not config.get('jail_channel_id'): return
        
        if message.channel.id == config['jail_channel_id']:
            await self.update_jail_sticky(message.guild)


    # --- COMMANDS ---

    lockout_group = app_commands.Group(name="lockout", description="Manage lockout settings")
    config_group = app_commands.Group(name="config", parent=lockout_group, description="Configure server lockout settings")
    zone_group = app_commands.Group(name="zone", parent=lockout_group, description="Manage timezone roles")

    # USER COMMANDS
    @app_commands.command(name="myschedule", description="Set your personal lockout schedule (HH:MM format).")
    @app_commands.describe(start="Start time (e.g. 23:00)", end="End time (e.g. 07:00)", repeat="How often to repeat")
    @app_commands.choices(repeat=[
        app_commands.Choice(name="Daily (Every Day)", value="daily"),
        app_commands.Choice(name="Weekdays (Mon-Fri)", value="weekdays"),
        app_commands.Choice(name="Weekends (Sat-Sun)", value="weekends")
    ])
    async def myset(self, interaction: discord.Interaction, start: str, end: str, repeat: app_commands.Choice[str] = None):
        try:
            datetime.datetime.strptime(start, "%H:%M")
            datetime.datetime.strptime(end, "%H:%M")
        except ValueError:
            return await interaction.response.send_message("❌ Invalid time format. Please use HH:MM (24-hour).", ephemeral=True)
        
        repeat_val = repeat.value if repeat else "daily"

        data = {
            "start": start,
            "end": end,
            "repeat": repeat_val,
            "locked_by_bot": False
        }
        self.save_user_schedule(interaction.guild_id, interaction.user.id, data)
        await interaction.response.send_message(f"✅ Schedule set! Lockout from **{start}** to **{end}** ({repeat_val.capitalize()}).", ephemeral=True)

    @app_commands.command(name="myview", description="View your current lockout schedule.")
    async def myview(self, interaction: discord.Interaction):
        data = self.get_user_schedule(interaction.guild_id, interaction.user.id)
        if not data:
            return await interaction.response.send_message("You don't have a schedule set.", ephemeral=True)
        
        repeat_val = data.get('repeat', 'daily').capitalize()
        await interaction.response.send_message(f"📅 **Your Schedule:** {data['start']} - {data['end']} ({repeat_val})", ephemeral=True)

    @app_commands.command(name="myclear", description="Delete your lockout schedule.")
    async def myclear(self, interaction: discord.Interaction):
        data = self.get_user_schedule(interaction.guild_id, interaction.user.id)
        if not data:
            return await interaction.response.send_message("You don't have a schedule set.", ephemeral=True)
        
        # Check if currently locked (prevents escape)
        config = self.get_config(interaction.guild_id)
        user_tz_offset = 0
        found_tz = False
        
        if config and config.get('time_zones'):
            for zone in config['time_zones']:
                role = interaction.guild.get_role(zone['role_id'])
                if role and role in interaction.user.roles:
                    user_tz_offset = zone['offset']
                    found_tz = True
                    break
        
        if found_tz:
            current_utc = datetime.datetime.now(timezone.utc)
            local_time = current_utc + timedelta(hours=user_tz_offset)
            
            # Check if active day + active time
            repeat_mode = data.get('repeat', 'daily')
            current_weekday = local_time.weekday()
            is_active_day = True
            if repeat_mode == 'weekdays' and current_weekday >= 5: is_active_day = False
            elif repeat_mode == 'weekends' and current_weekday < 5: is_active_day = False
            
            if is_active_day and self.is_time_in_range(data['start'], data['end'], local_time):
                 return await interaction.response.send_message("❌ You cannot clear your schedule while it is active, buggy!", ephemeral=True)

        self.delete_user_schedule(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("✅ Schedule cleared.", ephemeral=True)

    # ADMIN COMMANDS
    @app_commands.command(name="timeout", description="Timeout (Jail) a user for a set time. Use cancel:True to release.")
    @app_commands.describe(member="The user to timeout", minutes="How long (minutes)", cancel="Set True to release user")
    @app_commands.checks.has_permissions(administrator=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int = None, cancel: bool = False):
        config = self.get_config(interaction.guild_id)
        
        if cancel:
            jail_data = self.get_jail_data(interaction.guild_id, member.id)
            if not jail_data:
                return await interaction.response.send_message(f"⚠️ {member.mention} is not currently in timeout.", ephemeral=True)
            
            # Release Logic
            target_role = interaction.guild.get_role(config.get('target_role_id')) if config else None
            if target_role:
                try: await member.add_roles(target_role)
                except: pass
            
            self.delete_jail_data(interaction.guild_id, member.id)
            await self.update_jail_sticky(interaction.guild)
            return await interaction.response.send_message(f"✅ Released {member.mention} from timeout.", ephemeral=False)

        # Start Timeout Logic
        if not minutes:
            return await interaction.response.send_message("❌ You must specify minutes unless canceling.", ephemeral=True)

        if not config or not config.get('jail_channel_id'):
            return await interaction.response.send_message("❌ Jail channel not configured.", ephemeral=True)
        
        if not config.get('target_role_id'):
             return await interaction.response.send_message("❌ Target role not configured.", ephemeral=True)

        target_role = interaction.guild.get_role(config['target_role_id'])
        if target_role:
            try: await member.remove_roles(target_role)
            except: pass

        total_secs = minutes * 60
        data = {
            "total_seconds": total_secs,
            "remaining_seconds": total_secs,
            "last_check": None
        }
        
        # Check if already in jail VC
        jail_vc = interaction.guild.get_channel(config['jail_channel_id'])
        if member.voice and member.voice.channel and member.voice.channel.id == config['jail_channel_id']:
            data['last_check'] = datetime.datetime.now().timestamp()
            
        self.save_jail_data(interaction.guild_id, member.id, data)
        await interaction.response.send_message(f"🔒 {member.mention} has been jailed for {minutes} minutes. Go to {jail_vc.mention} to count down.", ephemeral=False)
        await self.update_jail_sticky(interaction.guild)

    @app_commands.command(name="adminclear", description="Force clear a user's schedule.")
    @app_commands.checks.has_permissions(administrator=True)
    async def adminclear(self, interaction: discord.Interaction, member: discord.Member):
        self.delete_user_schedule(interaction.guild_id, member.id)
        await interaction.response.send_message(f"✅ Cleared schedule for {member.display_name}.", ephemeral=True)

    # CONFIG COMMANDS
    @config_group.command(name="target_role", description="Set the role to remove/add (e.g. NSFW role).")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_target_role(self, interaction: discord.Interaction, role: discord.Role):
        config = self.get_config(interaction.guild_id) or {"guild_id": interaction.guild_id}
        config['target_role_id'] = role.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Target role set to {role.mention}.", ephemeral=True)

    @config_group.command(name="jail_channel", description="Set the voice channel for timeouts.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_jail_channel(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        config = self.get_config(interaction.guild_id) or {"guild_id": interaction.guild_id}
        config['jail_channel_id'] = channel.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Jail channel set to {channel.mention}.", ephemeral=True)

    @zone_group.command(name="add", description="Add a timezone role configuration.")
    @app_commands.describe(offset="Hours offset from UTC (e.g. -5 for EST)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_zone(self, interaction: discord.Interaction, role: discord.Role, offset: int):
        config = self.get_config(interaction.guild_id) or {"guild_id": interaction.guild_id}
        zones = config.get('time_zones', [])
        
        # Remove existing if role already used
        zones = [z for z in zones if z['role_id'] != role.id]
        zones.append({'role_id': role.id, 'offset': offset})
        
        config['time_zones'] = zones
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Added timezone: {role.mention} = UTC{offset:+d}", ephemeral=True)

    @zone_group.command(name="remove", description="Remove a timezone role.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_zone(self, interaction: discord.Interaction, role: discord.Role):
        config = self.get_config(interaction.guild_id)
        if not config or 'time_zones' not in config:
            return await interaction.response.send_message("No zones configured.", ephemeral=True)
            
        initial_len = len(config['time_zones'])
        config['time_zones'] = [z for z in config['time_zones'] if z['role_id'] != role.id]
        
        if len(config['time_zones']) < initial_len:
            self.save_config(interaction.guild_id, config)
            await interaction.response.send_message(f"✅ Removed timezone role {role.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message("That role is not a configured timezone.", ephemeral=True)

    @zone_group.command(name="list", description="List configured timezone roles.")
    async def list_zones(self, interaction: discord.Interaction):
        config = self.get_config(interaction.guild_id)
        if not config or not config.get('time_zones'):
            return await interaction.response.send_message("No zones configured.", ephemeral=True)
            
        text = "**🌍 Timezone Roles:**\n"
        for z in config['time_zones']:
            role = interaction.guild.get_role(z['role_id'])
            name = role.mention if role else f"ID:{z['role_id']}"
            text += f"• {name}: UTC{z['offset']:+d}\n"
            
        await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Lockout(bot))
