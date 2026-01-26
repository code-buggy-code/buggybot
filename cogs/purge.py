import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import datetime
import re
from zoneinfo import ZoneInfo
from typing import Literal, Optional

# Function/Class List:
# class Purge(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_schedules()
# - save_schedules(schedules)
# - get_pin_settings()
# - save_pin_settings(settings)
# - check_should_keep(message, keep_media, keep_links)
# - purge_scheduler()
# - perform_scheduled_purge()
# - on_message(message)
# - autopurge(interaction, action, keep_media, keep_links) [Slash]
# - pinpurge(interaction, enabled) [Slash]
# - purge(interaction, limit_type, message_id, time_frame, user, attachments_only, non_attachments_only) [Slash]
# setup(bot)

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
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

    def check_should_keep(self, message, keep_media, keep_links):
        """Determines if a message should be kept based on flags."""
        if message.pinned:
            return True
        
        # Sticky Protection
        stickies = self.bot.db.get_collection("sticky_messages")
        if any(s.get('last_message_id') == message.id for s in stickies):
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

        print(f"[purge] Starting scheduled purge for {len(schedules)} channels.")

        admin_cog = self.bot.get_cog("Admin")

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
                if admin_cog:
                    await admin_cog.revive_sticky(channel_id)
            except Exception as e:
                print(f"[purge] Failed to purge channel {channel_id}: {e}")

    @purge_scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles auto-deletion of 'User pinned a message' system messages."""
        if message.author.bot:
            return
        
        if message.type == discord.MessageType.pins_add:
            settings = self.get_pin_settings()
            guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
            
            if guild_setting and guild_setting.get('enabled', False):
                try:
                    await message.delete()
                except:
                    pass

    # --- SLASH COMMANDS ---

    @app_commands.command(name="autopurge", description="Manage the 4am EST scheduled purge.")
    @app_commands.describe(
        action="Add, Edit, Remove, or List schedule",
        keep_media="[Add/Edit] Keep images/videos?",
        keep_links="[Add/Edit] Keep messages with links?"
    )
    @app_commands.default_permissions(administrator=True)
    async def autopurge(self, interaction: discord.Interaction, 
                        action: Literal["Add", "Edit", "Remove", "List"], 
                        keep_media: bool = False, 
                        keep_links: bool = False):
        """Manage the 4am EST scheduled purge."""
        schedules = self.get_schedules()
        
        # --- LIST ---
        if action == "List":
            guild_schedules = [s for s in schedules if s['guild_id'] == interaction.guild_id]
            
            if not guild_schedules:
                return await interaction.response.send_message("📝 No channels scheduled for purging in this server.", ephemeral=True)

            text = "**🗑️ Scheduled 4am EST purges:**\n"
            for s in guild_schedules:
                channel = interaction.guild.get_channel(s['channel_id'])
                name = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
                opts = []
                if s.get('keep_media'): opts.append("KeepMedia")
                if s.get('keep_links'): opts.append("KeepLinks")
                opts_str = f" ({', '.join(opts)})" if opts else ""
                text += f"- {name}{opts_str}\n"

            return await interaction.response.send_message(text, ephemeral=True)

        # --- ADD ---
        elif action == "Add":
            if any(s['channel_id'] == interaction.channel_id for s in schedules):
                return await interaction.response.send_message("❌ This channel is already scheduled. Use 'Edit' instead.", ephemeral=True)

            schedules.append({
                "channel_id": interaction.channel_id,
                "guild_id": interaction.guild_id,
                "keep_media": keep_media,
                "keep_links": keep_links
            })
            self.save_schedules(schedules)
            await interaction.response.send_message(f"✅ Channel added to 4am EST purge schedule.\nOptions: Media={keep_media}, Links={keep_links}", ephemeral=True)

        # --- EDIT ---
        elif action == "Edit":
            found = False
            for s in schedules:
                if s['channel_id'] == interaction.channel_id:
                    s['keep_media'] = keep_media
                    s['keep_links'] = keep_links
                    found = True
                    break
            
            if found:
                self.save_schedules(schedules)
                await interaction.response.send_message(f"✅ Updated schedule settings.\nOptions: Media={keep_media}, Links={keep_links}", ephemeral=True)
            else:
                await interaction.response.send_message("❌ This channel is not in the schedule.", ephemeral=True)

        # --- REMOVE ---
        elif action == "Remove":
            initial_len = len(schedules)
            schedules = [s for s in schedules if s['channel_id'] != interaction.channel_id]
            
            if len(schedules) < initial_len:
                self.save_schedules(schedules)
                await interaction.response.send_message("✅ Channel removed from purge schedule.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ This channel was not scheduled.", ephemeral=True)

    @app_commands.command(name="pinpurge", description="Toggle auto-deletion of 'user pinned a message'.")
    @app_commands.describe(enabled="Enable auto-deletion?")
    @app_commands.default_permissions(administrator=True)
    async def pinpurge(self, interaction: discord.Interaction, enabled: bool):
        """Toggle auto-deletion of 'user pinned a message' announcements."""
        settings = self.get_pin_settings()
        settings = [s for s in settings if s['guild_id'] != interaction.guild_id]
        
        settings.append({
            "guild_id": interaction.guild_id,
            "enabled": enabled
        })
        self.save_pin_settings(settings)
        status = "enabled (messages will be deleted)" if enabled else "disabled"
        await interaction.response.send_message(f"📌 Pin announcement cleaner is now **{status}** for this server.", ephemeral=True)

    @app_commands.command(name="purge", description="Delete messages based on conditions.")
    @app_commands.describe(
        limit_type="How to decide when to stop purging",
        message_id="The Message ID to stop AT (exclusive) (Required if limit_type is Message ID)",
        time_frame="The time range to delete (Required if limit_type is Time)",
        user="Only delete messages from this user",
        attachments_only="Only delete messages WITH attachments",
        non_attachments_only="Only delete messages WITHOUT attachments"
    )
    @app_commands.choices(limit_type=[
        app_commands.Choice(name="Until Message ID", value="msg_id"),
        app_commands.Choice(name="Time Frame", value="time")
    ], time_frame=[
        app_commands.Choice(name="Past Hour", value="hour"),
        app_commands.Choice(name="Today (Since Midnight)", value="today"),
        app_commands.Choice(name="All History", value="all")
    ])
    @app_commands.default_permissions(administrator=True)
    async def purge(self, interaction: discord.Interaction, 
                    limit_type: app_commands.Choice[str],
                    message_id: Optional[str] = None,
                    time_frame: Optional[app_commands.Choice[str]] = None,
                    user: Optional[discord.User] = None,
                    attachments_only: bool = False,
                    non_attachments_only: bool = False):
        """Delete messages based on conditions."""
        
        # --- VALIDATION ---
        if attachments_only and non_attachments_only:
             return await interaction.response.send_message("❌ You cannot select both 'Attachments Only' and 'Non-Attachments Only'.", ephemeral=True)

        if limit_type.value == "msg_id" and not message_id:
             return await interaction.response.send_message("❌ You selected 'Until Message ID' but didn't provide a `message_id`.", ephemeral=True)
        
        if limit_type.value == "time" and not time_frame:
             return await interaction.response.send_message("❌ You selected 'Time Frame' but didn't provide a `time_frame`.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # --- SETUP ---
        after_date = None
        
        if limit_type.value == "msg_id":
            try:
                # Create a snowflake object to purge *after* this ID
                after_date = discord.Object(id=int(message_id))
            except ValueError:
                return await interaction.followup.send("❌ Invalid Message ID provided.", ephemeral=True)
        
        elif limit_type.value == "time":
            now = datetime.datetime.now(datetime.timezone.utc)
            if time_frame.value == "hour":
                after_date = now - datetime.timedelta(hours=1)
            elif time_frame.value == "today":
                # UTC Midnight
                today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                after_date = today_midnight
            elif time_frame.value == "all":
                after_date = None # No limit on time

        # --- FILTER LOGIC ---
        def check(m):
            # 1. Pinned/Sticky safety
            if m.pinned: return False
            
            # Sticky check
            stickies = self.bot.db.get_collection("sticky_messages")
            if any(s.get('last_message_id') == m.id for s in stickies):
                return False

            # 2. User Filter
            if user and m.author.id != user.id:
                return False
            
            # 3. Content Filters
            if attachments_only:
                if not m.attachments: return False
            
            if non_attachments_only:
                if m.attachments: return False
            
            return True

        # --- EXECUTE ---
        try:
            deleted = await interaction.channel.purge(limit=None, after=after_date, check=check)
            await interaction.followup.send(f"✅ Purged **{len(deleted)}** messages.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Purge failed: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Purge(bot))
