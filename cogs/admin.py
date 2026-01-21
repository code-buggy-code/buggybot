import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import datetime
import json
import os

# Function/Class List:
# class Admin(commands.Cog)
# - __init__(bot)
# - load_config()
# - save_config()
# - cog_unload()
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - get_log_settings()
# - save_log_settings(settings)
# - on_message(message)
# - on_message_delete(message)
# - on_message_edit(before, after)
# - on_member_remove(member)
# - handle_sticky(message)
# - stick(interaction, message)
# - unstick(interaction)
# - stickylist(interaction)
# - stickytime(interaction, timing, number, unit)
# - setlogchannel(interaction, channel)
# - vcping_group (Group)
# - vcping_ignore_group (Group)
# - vcping_ignore_add(interaction, channel)
# - vcping_ignore_remove(interaction, channel)
# - vcping_ignore_list(interaction)
# - vcping_set(interaction, role, people, minutes)
# - check_vcs()
# - before_check_vcs()
# - on_voice_state_update(member, before, after)
# setup(bot)

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Admin tools, Logging, Sticky Message management, and VC Pings."
        
        # VC Ping Config
        self.config_file = 'vcping_config.json'
        self.config = self.load_config()
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

    # --- HELPERS (Sticky/Log) ---

    def get_stickies(self):
        """Returns active sticky messages."""
        return self.bot.db.get_collection("sticky_messages")

    def save_stickies(self, stickies):
        """Saves sticky messages."""
        self.bot.db.save_collection("sticky_messages", stickies)

    def get_sticky_settings(self):
        """Returns server-specific sticky settings (timings)."""
        return self.bot.db.get_collection("sticky_settings")

    def save_sticky_settings(self, settings):
        """Saves sticky settings."""
        self.bot.db.save_collection("sticky_settings", settings)

    def get_log_settings(self):
        """Returns server-specific logging settings."""
        return self.bot.db.get_collection("log_settings")

    def save_log_settings(self, settings):
        """Saves logging settings."""
        self.bot.db.save_collection("log_settings", settings)

    # --- EVENTS (Sticky/Log) ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles sticky message logic."""
        if message.author.bot:
            return

        # Check if this channel has a sticky message active
        stickies = self.get_stickies()
        if any(s['channel_id'] == message.channel.id for s in stickies):
            await self.handle_sticky(message)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Logs deleted messages."""
        if message.author.bot or not message.guild:
            return

        settings = self.get_log_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        embed = discord.Embed(
            title="🗑️ Message Deleted",
            description=f"**Author:** {message.author.mention} ({message.author.id})\n**Channel:** {message.channel.mention}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        if message.content:
            embed.add_field(name="Content", value=message.content[:1024], inline=False)
        
        if message.attachments:
            embed.add_field(name="Attachments", value=f"{len(message.attachments)} file(s)", inline=False)

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Logs edited messages."""
        if before.author.bot or not before.guild:
            return
        
        # Ignore checks if content is the same (e.g. embed update)
        if before.content == after.content:
            return

        settings = self.get_log_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == before.guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        embed = discord.Embed(
            title="✏️ Message Edited",
            description=f"**Author:** {before.author.mention} ({before.author.id})\n**Channel:** {before.channel.mention}\n**Jump:** [Link]({before.jump_url})",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Before", value=before.content[:1024] or "[No Content]", inline=False)
        embed.add_field(name="After", value=after.content[:1024] or "[No Content]", inline=False)

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Logs when a member leaves."""
        settings = self.get_log_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == member.guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        embed = discord.Embed(
            title="👋 Member Left",
            description=f"{member.mention} has left the server.",
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=member.id, inline=True)
        embed.add_field(name="Joined At", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", inline=True)

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    async def handle_sticky(self, message):
        """Resends the sticky message to the bottom."""
        # Refresh stickies from DB to ensure we have the latest last_posted_at
        stickies = self.get_stickies()
        sticky_data = next((s for s in stickies if s['channel_id'] == message.channel.id), None)
        
        if not sticky_data: return

        # Get Settings for delay
        settings = self.get_sticky_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
        
        delay = 0
        mode = "after" # Default behavior
        if guild_setting:
            delay = guild_setting.get('delay', 0)
            mode = guild_setting.get('mode', 'after')

        now = datetime.datetime.now().timestamp()

        # LOGIC 1: BEFORE (Cooldown)
        # "Wait that long until having to be triggered again"
        if mode == "before" and delay > 0:
            last_posted = sticky_data.get('last_posted_at', 0)
            if (now - last_posted) < delay:
                # We are still in the cooldown period. Do NOT repost sticky.
                return

        # LOGIC 2: AFTER (Delay)
        # "Wait that long after being triggered"
        if mode == "after" and delay > 0:
            await asyncio.sleep(delay)
            # Re-fetch stickies to ensure it wasn't deleted during the sleep
            current_stickies = self.get_stickies()
            if not any(s['channel_id'] == message.channel.id for s in current_stickies):
                return
            # Refresh sticky_data (mainly for ID)
            sticky_data = next((s for s in current_stickies if s['channel_id'] == message.channel.id), None)
            if not sticky_data: return

        # Delete old sticky
        if sticky_data.get('last_message_id'):
            try:
                # We try to fetch the specific message. 
                # If it's already deleted or not found, we pass.
                old_msg = await message.channel.fetch_message(sticky_data['last_message_id'])
                await old_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
        
        # Send new sticky
        try:
            # Send
            new_msg = await message.channel.send(sticky_data['content'])
            
            # Update DB
            stickies = self.get_stickies()
            for s in stickies:
                if s['channel_id'] == message.channel.id:
                    s['last_message_id'] = new_msg.id
                    s['last_posted_at'] = datetime.datetime.now().timestamp()
                    break
            self.save_stickies(stickies)
        except Exception as e:
            print(f"Failed to send sticky: {e}")

    # --- COMMANDS (Sticky/Log) ---

    @app_commands.command(name="stick", description="Stick a message to the bottom of this channel.")
    @app_commands.describe(message="The message to sticky")
    async def stick(self, interaction: discord.Interaction, message: str):
        # Process newlines so \n creates a real line break
        content = message.replace("\\n", "\n")

        stickies = self.get_stickies()
        
        # Remove existing sticky for this channel if present (to overwrite)
        stickies = [s for s in stickies if s['channel_id'] != interaction.channel.id]

        # Create new sticky entry
        new_sticky = {
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild.id,
            "content": content,
            "last_message_id": None,
            "last_posted_at": datetime.datetime.now().timestamp()
        }

        # Send the first message immediately
        try:
            sent_msg = await interaction.channel.send(content)
            new_sticky['last_message_id'] = sent_msg.id
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed to send sticky message: {e}", ephemeral=True)

        stickies.append(new_sticky)
        self.save_stickies(stickies)

        await interaction.response.send_message("✅ Message stuck to this channel!", ephemeral=True)

    @app_commands.command(name="unstick", description="Remove the sticky message from this channel.")
    async def unstick(self, interaction: discord.Interaction):
        stickies = self.get_stickies()
        target = next((s for s in stickies if s['channel_id'] == interaction.channel.id), None)
        
        if not target:
            return await interaction.response.send_message("❌ No sticky message found in this channel.", ephemeral=True)

        # Try to delete the last sticky message
        if target.get('last_message_id'):
            try:
                msg = await interaction.channel.fetch_message(target['last_message_id'])
                await msg.delete()
            except:
                pass

        # Remove from DB
        stickies = [s for s in stickies if s['channel_id'] != interaction.channel.id]
        self.save_stickies(stickies)
        
        await interaction.response.send_message("✅ Sticky message removed.", ephemeral=True)

    @app_commands.command(name="stickylist", description="List all active sticky messages in this server.")
    async def stickylist(self, interaction: discord.Interaction):
        stickies = self.get_stickies()
        # Filter for current guild
        current_guild_stickies = [s for s in stickies if s.get('guild_id') == interaction.guild.id]

        if not current_guild_stickies:
            return await interaction.response.send_message("📝 No sticky messages found for this server.", ephemeral=True)

        text = "**📌 Active Sticky Messages:**\n"
        for s in current_guild_stickies:
            channel = interaction.guild.get_channel(s['channel_id'])
            chan_mention = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
            
            # Truncate content for display
            content_preview = s['content'].replace("\n", " ")
            if len(content_preview) > 50:
                content_preview = content_preview[:47] + "..."
            
            text += f"• {chan_mention}: {content_preview}\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="stickytime", description="Configure server-wide sticky message timing.")
    @app_commands.describe(
        timing="Mode: 'Before' (Cooldown) or 'After' (Delay)",
        number="Number of seconds/minutes",
        unit="Time unit"
    )
    @app_commands.choices(timing=[
        app_commands.Choice(name="Before (Cooldown)", value="before"),
        app_commands.Choice(name="After (Delay)", value="after")
    ])
    @app_commands.choices(unit=[
        app_commands.Choice(name="Seconds", value="seconds"),
        app_commands.Choice(name="Minutes", value="minutes")
    ])
    async def stickytime(self, interaction: discord.Interaction, timing: app_commands.Choice[str], number: int, unit: app_commands.Choice[str]):
        settings = self.get_sticky_settings()
        
        # Calculate total seconds
        multiplier = 60 if unit.value == "minutes" else 1
        total_seconds = number * multiplier
        
        # Remove existing guild setting
        settings = [s for s in settings if s['guild_id'] != interaction.guild.id]
        
        settings.append({
            "guild_id": interaction.guild.id,
            "delay": total_seconds,
            "mode": timing.value
        })
        self.save_sticky_settings(settings)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing.value == "before" else "Delay (After)"
        await interaction.response.send_message(f"✅ Sticky settings updated.\nMode: **{mode_text}**\nTime: **{delay_text}**", ephemeral=True)

    @app_commands.command(name="setlogchannel", description="Set the channel where server logs (Deletes, Edits, Leaves) will be sent.")
    @app_commands.describe(channel="The channel to send logs to")
    async def setlogchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        settings = self.get_log_settings()
        
        # Remove existing setting for this guild
        settings = [s for s in settings if s['guild_id'] != interaction.guild.id]
        
        settings.append({
            "guild_id": interaction.guild.id,
            "log_channel_id": channel.id
        })
        self.save_log_settings(settings)
        
        await interaction.response.send_message(f"✅ Logging channel set to {channel.mention}.\nI will now log:\n- Message Deletions\n- Message Edits\n- Member Leaves", ephemeral=True)

    # --- VC PING (New) ---

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

                start_time = datetime.datetime.fromisoformat(start_time_iso)
                if datetime.datetime.now() - start_time >= datetime.timedelta(minutes=threshold_minutes):
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
                        'start_time': datetime.datetime.now().isoformat(),
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
