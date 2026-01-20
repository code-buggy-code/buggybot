import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import datetime
import re
from zoneinfo import ZoneInfo

# Function/Class List:
# class Purge(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - purge_scheduler()
# - perform_scheduled_purge()
# - check_should_keep(message, keep_media, keep_links)
# - get_schedules()
# - save_schedules(schedules)
# - get_pin_settings()
# - save_pin_settings(settings)
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - on_message(message)
# - handle_sticky(message)
# - schedule_add(interaction, keep_media, keep_links)
# - schedule_edit(interaction, keep_media, keep_links)
# - schedule_remove(interaction)
# - schedule_list(interaction)
# - manage_pins(interaction, enabled)
# - purge_user(interaction, target, amount, scope_id)
# - purge_messages(interaction, amount_or_till, message_id, keep_media, keep_links)
# - stick(interaction, message)
# - unstick(interaction)
# - stickytime(interaction, timing, number, unit)
# setup(bot)

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Advanced message purging, scheduling, and sticky system."
        self.timezone = ZoneInfo("America/New_York")
        self.purge_scheduler.start()

    def cog_unload(self):
        self.purge_scheduler.cancel()

    # --- HELPERS ---

    def get_schedules(self):
        """Returns the list of scheduled purge channels."""
        return self.bot.db.get_collection("purge_schedules")

    def save_schedules(self, schedules):
        """Saves the list of schedules."""
        self.bot.db.save_collection("purge_schedules", schedules)

    def get_pin_settings(self):
        """Returns the pin purge settings for servers."""
        return self.bot.db.get_collection("purge_pin_settings")

    def save_pin_settings(self, settings):
        """Saves the pin settings."""
        self.bot.db.save_collection("purge_pin_settings", settings)

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

    def check_should_keep(self, message, keep_media, keep_links):
        """Determines if a message should be kept based on flags."""
        if message.pinned:
            return True
        
        if keep_media:
            if message.attachments:
                return True
            if message.embeds:
                for e in message.embeds:
                    if e.type in ('image', 'video', 'gifv'):
                        return True
        
        if keep_links:
            url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
            if url_pattern.search(message.content):
                return True
                
        return False

    # --- SCHEDULER ---

    @tasks.loop(minutes=1)
    async def purge_scheduler(self):
        """Checks every minute if it is 4am EST."""
        now = datetime.datetime.now(self.timezone)
        
        if now.hour == 4 and now.minute == 0:
            await self.perform_scheduled_purge()

    async def perform_scheduled_purge(self):
        schedules = self.get_schedules()
        if not schedules: return

        print(f"[Purge] Starting scheduled purge for {len(schedules)} channels.")

        for sch in schedules:
            channel_id = sch['channel_id']
            channel = self.bot.get_channel(channel_id)
            
            if not channel: continue

            keep_media = sch.get('keep_media', False)
            keep_links = sch.get('keep_links', False)

            def check(m):
                return not self.check_should_keep(m, keep_media, keep_links)

            try:
                await channel.purge(limit=None, check=check)
            except Exception as e:
                print(f"[Purge] Failed to purge channel {channel_id}: {e}")

    @purge_scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles auto-deletion of pins and sticky message logic."""
        if message.author.bot:
            return

        # 1. Pin Logic
        if message.type == discord.MessageType.pins_add:
            settings = self.get_pin_settings()
            guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
            
            if guild_setting and guild_setting.get('enabled', False):
                try:
                    await message.delete()
                except:
                    pass

        # 2. Sticky Logic
        # We check if this channel has a sticky message active
        stickies = self.get_stickies()
        if any(s['channel_id'] == message.channel.id for s in stickies):
            await self.handle_sticky(message)

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

    # --- COMMANDS ---

    purge_group = app_commands.Group(name="purge", description="Manage purging and auto-deletion")

    # 1. Scheduled Purge Commands (Existing)
    @purge_group.command(name="add", description="Add this channel to the 4am EST purge schedule.")
    @app_commands.describe(keep_media="Keep images/videos?", keep_links="Keep messages with links?")
    async def schedule_add(self, interaction: discord.Interaction, keep_media: bool = False, keep_links: bool = False):
        schedules = self.get_schedules()
        if any(s['channel_id'] == interaction.channel.id for s in schedules):
            return await interaction.response.send_message("❌ This channel is already scheduled for purging. Use `/purge edit`.", ephemeral=True)

        schedules.append({
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild.id,
            "keep_media": keep_media,
            "keep_links": keep_links
        })
        self.save_schedules(schedules)
        await interaction.response.send_message(f"✅ Channel added to 4am EST purge schedule.\nOptions: Media={keep_media}, Links={keep_links}", ephemeral=True)

    @purge_group.command(name="edit", description="Edit purge settings for this channel.")
    async def schedule_edit(self, interaction: discord.Interaction, keep_media: bool, keep_links: bool):
        schedules = self.get_schedules()
        found = False
        for s in schedules:
            if s['channel_id'] == interaction.channel.id:
                s['keep_media'] = keep_media
                s['keep_links'] = keep_links
                found = True
                break
        
        if found:
            self.save_schedules(schedules)
            await interaction.response.send_message(f"✅ Updated schedule settings.\nOptions: Media={keep_media}, Links={keep_links}", ephemeral=True)
        else:
            await interaction.response.send_message("❌ This channel is not in the schedule.", ephemeral=True)

    @purge_group.command(name="remove", description="Remove this channel from the purge schedule.")
    async def schedule_remove(self, interaction: discord.Interaction):
        schedules = self.get_schedules()
        initial_len = len(schedules)
        schedules = [s for s in schedules if s['channel_id'] != interaction.channel.id]
        
        if len(schedules) < initial_len:
            self.save_schedules(schedules)
            await interaction.response.send_message("✅ Channel removed from purge schedule.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ This channel was not scheduled.", ephemeral=True)

    @purge_group.command(name="list", description="List all scheduled purge channels in this server.")
    async def schedule_list(self, interaction: discord.Interaction):
        schedules = self.get_schedules()
        guild_schedules = [s for s in schedules if s['guild_id'] == interaction.guild.id]
        
        if not guild_schedules:
            return await interaction.response.send_message("📝 No channels scheduled for purging in this server.", ephemeral=True)

        text = "**🗑️ Scheduled 4am EST Purges:**\n"
        for s in guild_schedules:
            channel = interaction.guild.get_channel(s['channel_id'])
            name = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
            opts = []
            if s.get('keep_media'): opts.append("KeepMedia")
            if s.get('keep_links'): opts.append("KeepLinks")
            opts_str = f" ({', '.join(opts)})" if opts else ""
            text += f"- {name}{opts_str}\n"

        await interaction.response.send_message(text, ephemeral=True)

    # 2. Pins Purge Command (Existing)
    @purge_group.command(name="pins", description="Toggle auto-deletion of 'user pinned a message' announcements.")
    @app_commands.describe(enabled="True to delete pin messages, False to keep them.")
    async def manage_pins(self, interaction: discord.Interaction, enabled: bool):
        settings = self.get_pin_settings()
        settings = [s for s in settings if s['guild_id'] != interaction.guild.id]
        
        settings.append({
            "guild_id": interaction.guild.id,
            "enabled": enabled
        })
        self.save_pin_settings(settings)
        status = "enabled (messages will be deleted)" if enabled else "disabled"
        await interaction.response.send_message(f"📌 Pin announcement cleaner is now **{status}** for this server.", ephemeral=True)

    # 3. User Purge Command (Existing)
    @purge_group.command(name="user", description="Purge messages from a specific user.")
    @app_commands.describe(target="The user to purge", amount="Number of messages or 'all'", scope_id="Optional: Scope ID")
    async def purge_user(self, interaction: discord.Interaction, target: discord.User, amount: str, scope_id: str = None):
        await interaction.response.defer(thinking=True, ephemeral=True)
        limit = None
        if amount.lower() != "all":
            try: limit = int(amount)
            except ValueError: return await interaction.followup.send("❌ Amount must be a number or 'all'.")

        channels_to_purge = []
        if not scope_id:
            channels_to_purge.append(interaction.channel)
        else:
            try:
                s_id = int(scope_id)
                if s_id == interaction.guild.id:
                    channels_to_purge = interaction.guild.text_channels
                else:
                    chan = interaction.guild.get_channel(s_id)
                    if isinstance(chan, discord.CategoryChannel):
                        channels_to_purge = chan.text_channels
                    elif isinstance(chan, discord.TextChannel):
                        channels_to_purge.append(chan)
                    else:
                        return await interaction.followup.send("❌ Invalid Scope ID.")
            except ValueError: return await interaction.followup.send("❌ Scope ID must be a number.")

        count_deleted = 0
        def check(m): return m.author.id == target.id

        for channel in channels_to_purge:
            try:
                deleted = await channel.purge(limit=limit, check=check)
                count_deleted += len(deleted)
                if limit is not None:
                    limit -= len(deleted)
                    if limit <= 0: break
            except Exception as e: print(f"Failed to purge in {channel.name}: {e}")

        await interaction.followup.send(f"✅ Purged **{count_deleted}** messages from {target.mention}.")

    # 4. Message/Till Purge Command (Existing)
    @purge_group.command(name="messages", description="Purge a number of messages or until a specific message.")
    @app_commands.describe(
        amount_or_till="Number of messages (e.g. 50) OR type 'till' to use message_id",
        message_id="The message ID to stop at (required if 'till' is used)",
        keep_media="Keep images/videos?", keep_links="Keep messages with links?"
    )
    async def purge_messages(self, interaction: discord.Interaction, amount_or_till: str, message_id: str = None, keep_media: bool = False, keep_links: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=True)
        limit = None
        after_msg = None

        if amount_or_till.lower() == "till":
            if not message_id: return await interaction.followup.send("❌ You must provide `message_id` when using 'till'.")
            try: after_msg = discord.Object(id=int(message_id))
            except ValueError: return await interaction.followup.send("❌ Invalid Message ID.")
        else:
            try: limit = int(amount_or_till)
            except ValueError: return await interaction.followup.send("❌ First argument must be a number or 'till'.")

        def check(m): return not self.check_should_keep(m, keep_media, keep_links)

        try:
            deleted = await interaction.channel.purge(limit=limit, after=after_msg, check=check)
            await interaction.followup.send(f"✅ Purged **{len(deleted)}** messages.")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to purge: {e}")

    # 5. Sticky Commands (New)

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

async def setup(bot):
    await bot.add_cog(Purge(bot))
