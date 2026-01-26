import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import datetime
from datetime import timedelta
import re
from zoneinfo import ZoneInfo
from typing import Literal

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
# - on_message(message)
# - purge(interaction, time, user) [Slash]
# - purge_till_here(interaction, message) [Context Menu]
# - schedulepurge(interaction, action, keep_media, keep_links) [Slash]
# - pinpurge(interaction, enabled) [Slash]
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
        
        # Sticky Protection: Check if this message is the current sticky for the channel
        stickies = self.bot.db.get_collection("sticky_messages")
        # We check if this message's ID matches the 'last_message_id' of any sticky setup
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
                print(f"[purge] Failed to purge channel {channel_id}: {e}")

    @purge_scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles manual 'reply purge' and 'User pinned a message' deletion."""
        if message.author.bot:
            return
        
        # 1. Manual "Purge Till Here" via Reply
        # If user replies to a message and sends "/purge" or "purge" (and has permissions)
        if message.reference and message.content.lower().strip() in ["/purge", "purge", "/purge till reply"]:
            if message.channel.permissions_for(message.author).manage_messages:
                try:
                    # Fetch the message that was replied to
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    
                    # Delete the command message
                    await message.delete()
                    
                    # Purge everything after that reference
                    deleted = await message.channel.purge(limit=None, after=ref_msg)
                    
                    # Also try to delete the reference message itself (inclusive purge)
                    try:
                        await ref_msg.delete()
                        count = len(deleted) + 1
                    except:
                        count = len(deleted)
                        
                    # Feedback (self-delete after 5s)
                    await message.channel.send(f"✅ Purged **{count}** messages.", delete_after=5)
                except Exception as e:
                    print(f"Failed to execute reply purge: {e}")
            return

        # 2. Pin Announcement Deletion
        if message.type == discord.MessageType.pins_add:
            settings = self.get_pin_settings()
            guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
            
            if guild_setting and guild_setting.get('enabled', False):
                try:
                    await message.delete()
                except:
                    pass

    # --- SLASH COMMAND: PURGE (Time Based) ---
    
    @app_commands.command(name="purge", description="Purge messages based on time.")
    @app_commands.describe(
        time="How far back to purge?",
        user="Optional: Only purge messages from this user"
    )
    @app_commands.choices(time=[
        app_commands.Choice(name="Past Hour", value="hour"),
        app_commands.Choice(name="Today (Past 24h)", value="today")
    ])
    @app_commands.default_permissions(administrator=True)
    async def purge(self, interaction: discord.Interaction, 
                    time: app_commands.Choice[str],
                    user: discord.User = None):
        """Purge messages based on time."""
        
        # Calculate time limit
        now = datetime.datetime.now(datetime.timezone.utc)
        
        if time.value == "hour":
            cutoff = now - timedelta(hours=1)
        elif time.value == "today":
            cutoff = now - timedelta(hours=24)
        else:
            cutoff = now - timedelta(hours=1) # Default
            
        await interaction.response.defer(ephemeral=True)
        
        def check(m):
            # 1. Time Check (Too old? Keep it. We only delete NEWER than cutoff)
            # purge(after=cutoff) handles the main logic, but 'check' is for extra filters
            
            # 2. User Check
            if user and m.author.id != user.id:
                return False
                
            return True

        try:
            # Note: 'after' argument in purge() means "Delete messages that are NEWER than this date"
            deleted = await interaction.channel.purge(limit=None, after=cutoff, check=check)
            await interaction.followup.send(f"✅ Purged **{len(deleted)}** messages from the **{time.name}**.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to purge: {e}", ephemeral=True)

    # --- CONTEXT MENU: PURGE TILL HERE ---

    @app_commands.context_menu(name="Purge till here")
    @app_commands.default_permissions(administrator=True)
    async def purge_till_here(self, interaction: discord.Interaction, message: discord.Message):
        """Context menu to purge messages newer than the selected one."""
        if not interaction.channel.permissions_for(interaction.guild.me).manage_messages:
            return await interaction.response.send_message("❌ I need `Manage Messages` permission to do this.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            deleted = await interaction.channel.purge(limit=None, after=message)
            try:
                await message.delete()
                count = len(deleted) + 1
            except:
                count = len(deleted)
                
            await interaction.followup.send(f"✅ Purged **{count}** messages down to the selected message.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to purge: {e}", ephemeral=True)

    # --- SLASH COMMAND: SCHEDULE PURGE (4am Task) ---

    @app_commands.command(name="schedulepurge", description="Manage the 4am EST purge schedule.")
    @app_commands.describe(
        action="What do you want to do?",
        keep_media="Keep images/videos? (Add/Edit only)",
        keep_links="Keep links? (Add/Edit only)"
    )
    @app_commands.default_permissions(administrator=True)
    async def schedulepurge(self, interaction: discord.Interaction, 
                            action: Literal["Add", "Edit", "Remove", "List"],
                            keep_media: bool = False,
                            keep_links: bool = False):
        """Manage the 4am EST purge schedule."""
        
        # --- 1. ADD ---
        if action == "Add":
            schedules = self.get_schedules()
            if any(s['channel_id'] == interaction.channel_id for s in schedules):
                return await interaction.response.send_message("❌ This channel is already scheduled for purging. Use `Edit`.", ephemeral=True)

            schedules.append({
                "channel_id": interaction.channel_id,
                "guild_id": interaction.guild_id,
                "keep_media": keep_media,
                "keep_links": keep_links
            })
            self.save_schedules(schedules)
            await interaction.response.send_message(f"✅ Channel added to 4am EST purge schedule.\nOptions: Media={keep_media}, Links={keep_links}", ephemeral=True)

        # --- 2. EDIT ---
        elif action == "Edit":
            schedules = self.get_schedules()
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

        # --- 3. REMOVE ---
        elif action == "Remove":
            schedules = self.get_schedules()
            initial_len = len(schedules)
            schedules = [s for s in schedules if s['channel_id'] != interaction.channel_id]
            
            if len(schedules) < initial_len:
                self.save_schedules(schedules)
                await interaction.response.send_message("✅ Channel removed from purge schedule.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ This channel was not scheduled.", ephemeral=True)

        # --- 4. LIST ---
        elif action == "List":
            schedules = self.get_schedules()
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

            await interaction.response.send_message(text, ephemeral=True)

    # --- SLASH COMMAND: PIN PURGE ---
    
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

async def setup(bot):
    await bot.add_cog(Purge(bot))
