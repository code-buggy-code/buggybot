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
# - setschedule(self, interaction, start, end, repeat) [Slash - Public]
# - view_schedule(self, interaction) [Slash - Public]
# - clear_schedule(self, interaction) [Slash - Public]
# - timeout(self, ctx, member, minutes, cancel) [Prefix]
# - adminclear(self, ctx, member) [Prefix]
# - lockoutchannel(self, ctx, channel) [Prefix]
# - lockout (Group) [Prefix]
#   - config (Group)
#     - target_role(self, ctx, role)
#     - jail_channel(self, ctx, channel)
#   - zone (Group)
#     - add(self, ctx, role, offset)
#     - remove(self, ctx, role)
#     - list(self, ctx)
# - setup(bot)

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

    @tasks.loop(minutes=1)
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
            return await interaction.response.send_message("❌ Invalid time format. Please use HH:MM (24-hour).", ephemeral=True)
        
        repeat_val = repeat.value if repeat else "daily"
        self.save_user_schedule(interaction.guild_id, interaction.user.id, {"start": start, "end": end, "repeat": repeat_val, "locked_by_bot": False})
        await interaction.response.send_message(f"✅ Schedule set! Lockout from **{start}** to **{end}** ({repeat_val.capitalize()}).", ephemeral=True)

    view_group = app_commands.Group(name="view", description="View your settings")

    @view_group.command(name="schedule", description="View your current lockout schedule.", extras={'public': True})
    async def view_schedule(self, interaction: discord.Interaction):
        """View your current lockout schedule."""
        config = self.get_config(interaction.guild_id)
        if config and config.get('command_channel_id') and interaction.channel_id != config['command_channel_id']:
            return await interaction.response.send_message(f"Please use <#{config['command_channel_id']}> for lock commands!", ephemeral=True)

        data = self.get_user_schedule(interaction.guild_id, interaction.user.id)
        if not data: return await interaction.response.send_message("You don't have a schedule set.", ephemeral=True)
        await interaction.response.send_message(f"📅 **Your Schedule:** {data['start']} - {data['end']} ({data.get('repeat', 'daily').capitalize()})", ephemeral=True)

    clear_group = app_commands.Group(name="clear", description="Clear your settings")

    @clear_group.command(name="schedule", description="Delete your lockout schedule.", extras={'public': True})
    async def clear_schedule(self, interaction: discord.Interaction):
        """Delete your lockout schedule."""
        config = self.get_config(interaction.guild_id)
        if config and config.get('command_channel_id') and interaction.channel_id != config['command_channel_id']:
            return await interaction.response.send_message(f"Please use <#{config['command_channel_id']}> for lock commands!", ephemeral=True)

        data = self.get_user_schedule(interaction.guild_id, interaction.user.id)
        if not data: return await interaction.response.send_message("You don't have a schedule set.", ephemeral=True)
        
        user_tz_offset, found_tz = 0, False
        if config and config.get('time_zones'):
            for zone in config['time_zones']:
                role = interaction.guild.get_role(zone['role_id'])
                if role and role in interaction.user.roles:
                    user_tz_offset, found_tz = zone['offset'], True
                    break
        
        if found_tz:
            local_time = datetime.datetime.now(timezone.utc) + timedelta(hours=user_tz_offset)
            repeat_mode = data.get('repeat', 'daily')
            is_active_day = not ((repeat_mode == 'weekdays' and local_time.weekday() >= 5) or (repeat_mode == 'weekends' and local_time.weekday() < 5))
            if is_active_day and self.is_time_in_range(data['start'], data['end'], local_time):
                 return await interaction.response.send_message("❌ You cannot clear your schedule while it is active, buggy!", ephemeral=True)

        self.delete_user_schedule(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message("✅ Schedule cleared.", ephemeral=True)

    # --- PREFIX COMMANDS (ADMIN) ---

    @commands.command(name="timeout")
    @commands.has_permissions(administrator=True)
    async def timeout(self, ctx, member: discord.Member = None, minutes: int = None, cancel: bool = False):
        """Put a user in timeout or release them. Usage: ?timeout @User <minutes> OR ?timeout @User 0 True (to release)"""
        if member is None:
            return await ctx.send("❌ Who are we putting in timeout, buggy? Usage: `?timeout @User <minutes>`")

        config = self.get_config(ctx.guild.id)
        if cancel:
            if not self.get_jail_data(ctx.guild.id, member.id): return await ctx.send(f"⚠️ {member.mention} is not in timeout.")
            target_role = ctx.guild.get_role(config.get('target_role_id')) if config else None
            if target_role:
                try: await member.add_roles(target_role)
                except: pass
            self.delete_jail_data(ctx.guild.id, member.id)
            await self.update_jail_sticky(ctx.guild)
            return await ctx.send(f"✅ Released {member.mention} from timeout.")

        if minutes is None or minutes <= 0: return await ctx.send("❌ How many minutes, buggy? Must be a positive number.")
        if not config or not config.get('jail_channel_id') or not config.get('target_role_id'):
            return await ctx.send("❌ Please finish configuration first with `?lockout config`!")

        target_role = ctx.guild.get_role(config['target_role_id'])
        if target_role:
            try: await member.remove_roles(target_role)
            except: pass

        total_secs = minutes * 60
        data = {"total_seconds": total_secs, "remaining_seconds": total_secs, "last_check": None}
        if member.voice and member.voice.channel and member.voice.channel.id == config['jail_channel_id']:
            data['last_check'] = datetime.datetime.now().timestamp()
            
        self.save_jail_data(ctx.guild.id, member.id, data)
        await ctx.send(f"🔒 {member.mention} put in timeout for {minutes}m. Go to <#{config['jail_channel_id']}> to count down.")
        await self.update_jail_sticky(ctx.guild)

    @commands.command(name="adminclear")
    @commands.has_permissions(administrator=True)
    async def adminclear(self, ctx, member: discord.Member = None):
        """Force clear a user's schedule. Usage: ?adminclear @User"""
        if member is None: return await ctx.send("❌ Specify a member: `?adminclear @User`")
        self.delete_user_schedule(ctx.guild.id, member.id)
        await ctx.send(f"✅ Cleared schedule for {member.display_name}.")

    @commands.command(name="lockoutchannel")
    @commands.has_permissions(administrator=True)
    async def lockoutchannel(self, ctx, channel: discord.TextChannel = None):
        """Restrict user lock commands to a specific channel. Usage: ?lockoutchannel #channel"""
        if channel is None: return await ctx.send("❌ Specify a channel: `?lockoutchannel #channel`")
        config = self.get_config(ctx.guild.id) or {"guild_id": ctx.guild.id}
        config['command_channel_id'] = channel.id
        self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ User lock commands restricted to {channel.mention}.")

    # --- PREFIX CONFIG GROUPS ---

    @commands.group(name="lockout", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def lockout_group(self, ctx):
        """Main lockout management group."""
        await ctx.send("Commands: `config`, `zone`")

    @lockout_group.group(name="config", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def config_group(self, ctx):
        """Configure system settings like the timeout VC and target role."""
        await ctx.send("Commands: `target_role`, `jail_channel`")

    @config_group.command(name="target_role")
    @commands.has_permissions(administrator=True)
    async def config_target_role(self, ctx, role: discord.Role = None):
        """Set the role that is REMOVED during lockout/timeout. Usage: ?lockout config target_role @Role"""
        if role is None: return await ctx.send("❌ Specify a role: `?lockout config target_role @Role`")
        config = self.get_config(ctx.guild.id) or {"guild_id": ctx.guild.id}
        config['target_role_id'] = role.id
        self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Target role set to {role.mention}.")

    @config_group.command(name="jail_channel")
    @commands.has_permissions(administrator=True)
    async def config_jail_channel(self, ctx, channel: discord.VoiceChannel = None):
        """Set the VC where timeout timers count down. Usage: ?lockout config jail_channel #VC"""
        if channel is None: return await ctx.send("❌ Specify a voice channel: `?lockout config jail_channel #Channel`")
        config = self.get_config(ctx.guild.id) or {"guild_id": ctx.guild.id}
        config['jail_channel_id'] = channel.id
        self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Timeout channel set to {channel.mention}.")

    @lockout_group.group(name="zone", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def zone_group(self, ctx):
        """Manage server-wide timezone role mappings."""
        await ctx.send("Commands: `add`, `remove`, `list`")

    @zone_group.command(name="add")
    @commands.has_permissions(administrator=True)
    async def zone_add(self, ctx, role: discord.Role = None, offset: int = None):
        """Map a role to a UTC offset. Usage: ?lockout zone add @EST -5"""
        if role is None or offset is None: return await ctx.send("❌ Usage: `?lockout zone add @Role <offset>` (e.g. -5)")
        config = self.get_config(ctx.guild.id) or {"guild_id": ctx.guild.id}
        zones = [z for z in config.get('time_zones', []) if z['role_id'] != role.id]
        zones.append({'role_id': role.id, 'offset': offset})
        config['time_zones'] = zones
        self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Mapped {role.mention} to UTC{offset:+d}.")

    @zone_group.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def zone_remove(self, ctx, role: discord.Role = None):
        """Remove a timezone role mapping. Usage: ?lockout zone remove @Role"""
        if role is None: return await ctx.send("❌ Specify a role: `?lockout zone remove @Role`")
        config = self.get_config(ctx.guild.id)
        if not config or 'time_zones' not in config: return await ctx.send("No zones found.")
        initial = len(config['time_zones'])
        config['time_zones'] = [z for z in config['time_zones'] if z['role_id'] != role.id]
        if len(config['time_zones']) < initial:
            self.save_config(ctx.guild.id, config)
            await ctx.send(f"✅ Removed mapping for {role.mention}.")
        else: await ctx.send("That role is not mapped.")

    @zone_group.command(name="list")
    @commands.has_permissions(administrator=True)
    async def zone_list(self, ctx):
        """List all active timezone role mappings."""
        config = self.get_config(ctx.guild.id)
        if not config or not config.get('time_zones'): return await ctx.send("No zones found.")
        text = "**🌍 Timezone Mappings:**\n"
        for z in config['time_zones']:
            r = ctx.guild.get_role(z['role_id'])
            text += f"• {r.mention if r else f'ID:{z[role_id]}'}: UTC{z['offset']:+d}\n"
        await ctx.send(text)

async def setup(bot): await bot.add_cog(Lockout(bot))
