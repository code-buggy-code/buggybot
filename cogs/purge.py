import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import datetime
from datetime import timedelta
import re
from zoneinfo import ZoneInfo
from typing import Literal, Optional, Union

# Function/Class List:
# class Purge(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - purge_scheduler()
# - perform_scheduled_purge()
# - check_should_keep(message, keep_media, keep_links)
# - is_media_message(message)
# - get_schedules()
# - save_schedules(schedules)
# - get_pin_settings()
# - save_pin_settings(settings)
# - on_message(message)
# - purge(interaction, time, user, category, entire_server) [Slash]
# - replypurge(interaction, filter, target_message) [Slash]
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
        
        # Sticky Protection
        stickies = self.bot.db.get_collection("sticky_messages")
        if any(s.get('last_message_id') == message.id for s in stickies):
            return True

        if keep_media:
            if message.attachments: return True
            if message.embeds and any(e.type in ('image', 'video', 'gifv') for e in message.embeds): return True
        
        if keep_links:
            url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
            if url_pattern.search(message.content): return True
                
        return False

    def is_media_message(self, message):
        """Checks if a message counts as 'media' (attachments or image embeds)."""
        if message.attachments: return True
        if message.embeds and any(e.type in ('image', 'video', 'gifv') for e in message.embeds): return True
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
            
            def check(m): return not self.check_should_keep(m, keep_media, keep_links)

            try: await channel.purge(limit=None, check=check)
            except Exception as e: print(f"[purge] Failed to purge channel {channel_id}: {e}")

    @purge_scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles manual 'reply purge' logic and 'User pinned a message' deletion."""
        if message.author.bot: return
        
        # 1. Manual "Reply Purge" Logic (Text Trigger)
        # Usage: Reply to a message -> Type "/replypurge [attachments/text]"
        msg_content = message.content.lower().strip()
        if msg_content.startswith("/replypurge") or msg_content.startswith("!replypurge"):
            # Permission check
            if not message.channel.permissions_for(message.author).manage_messages:
                return

            # Must be a reply
            if not message.reference:
                # If they just typed the command without replying, we ignore it here
                # so they can use the actual slash command if they want.
                # But if they meant to reply, we can give a hint.
                return 

            try:
                # Fetch the message that was replied to
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                await message.delete() # Delete the command trigger immediately

                # Determine Mode
                mode = "all"
                if "attachment" in msg_content or "media" in msg_content:
                    mode = "attachments"
                elif "text" in msg_content:
                    mode = "text"

                # Validation
                ref_is_media = self.is_media_message(ref_msg)
                
                if mode == "attachments" and not ref_is_media:
                    return await message.channel.send("❌ **Error:** You selected 'Attachments Only' but replied to a text message.", delete_after=5)
                
                if mode == "text" and ref_is_media:
                    return await message.channel.send("❌ **Error:** You selected 'Text Only' but replied to a media message.", delete_after=5)

                # Define Check
                def purge_check(m):
                    if mode == "attachments": return self.is_media_message(m)
                    elif mode == "text": return not self.is_media_message(m)
                    return True

                # Execute
                deleted = await message.channel.purge(limit=None, after=ref_msg, check=purge_check)
                
                # Inclusive Delete
                ref_deleted = False
                if purge_check(ref_msg):
                    try:
                        await ref_msg.delete()
                        ref_deleted = True
                    except: pass
                
                count = len(deleted) + (1 if ref_deleted else 0)
                
                mode_text = f" ({mode})" if mode != "all" else ""
                await message.channel.send(f"✅ Purged **{count}** messages{mode_text} down to the reply.", delete_after=5)

            except Exception as e:
                print(f"Reply purge error: {e}")
            return

        # 2. Pin Announcement Deletion
        if message.type == discord.MessageType.pins_add:
            settings = self.get_pin_settings()
            guild_setting = next((s for s in settings if s['guild_id'] == message.guild.id), None)
            if guild_setting and guild_setting.get('enabled', False):
                try: await message.delete()
                except: pass

    # --- SLASH COMMAND: PURGE (Main) ---
    
    @app_commands.command(name="purge", description="Purge messages with time and scope options.")
    @app_commands.describe(
        time="Time range to purge",
        user="Optional: Only purge messages from this user",
        category="Optional: Purge an entire category",
        entire_server="Optional: Purge ALL channels in the server (Dangerous!)"
    )
    @app_commands.choices(time=[
        app_commands.Choice(name="All Time", value="all"),
        app_commands.Choice(name="Today (24h)", value="today"),
        app_commands.Choice(name="Past Hour", value="hour")
    ])
    @app_commands.default_permissions(administrator=True)
    async def purge(self, interaction: discord.Interaction, 
                    time: app_commands.Choice[str],
                    user: Optional[discord.User] = None,
                    category: Optional[discord.CategoryChannel] = None,
                    entire_server: bool = False):
        """Purge messages with time and scope options."""
        
        target_channels = []
        scope_name = "Current Channel"

        if entire_server:
            target_channels = interaction.guild.text_channels
            scope_name = "Entire Server"
        elif category:
            target_channels = category.text_channels
            scope_name = f"Category: {category.name}"
        else:
            target_channels = [interaction.channel]
            scope_name = "Current Channel"

        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = None
        
        if time.value == "hour":
            cutoff = now - timedelta(hours=1)
        elif time.value == "today":
            cutoff = now - timedelta(hours=24)

        if entire_server or (category and len(target_channels) > 1):
             await interaction.response.send_message(f"⚠️ **WARNING:** You are about to purge **{scope_name}** ({len(target_channels)} channels) for **{time.name}**. This may take a while.", ephemeral=True)
        else:
             await interaction.response.defer(ephemeral=True)

        total_deleted = 0
        
        for channel in target_channels:
            try:
                def check(m):
                    if user and m.author.id != user.id: return False
                    return True

                deleted = await channel.purge(limit=None, after=cutoff, check=check)
                total_deleted += len(deleted)
            except Exception as e:
                print(f"Failed to purge {channel.name}: {e}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"✅ **Purge Complete!**\nDeleted **{total_deleted}** messages in **{scope_name}** ({time.name}).", ephemeral=True)
            else:
                 await interaction.followup.send(f"✅ **Purge Complete!**\nDeleted **{total_deleted}** messages in **{scope_name}** ({time.name}).", ephemeral=True)
        except: pass

    # --- SLASH COMMAND: REPLYPURGE (New Slash Version) ---

    @app_commands.command(name="replypurge", description="Purge messages until a specific message (using ID/Link).")
    @app_commands.describe(
        filter="Keep Media or Text?",
        target_message="Paste the Message ID or Message Link to purge until."
    )
    @app_commands.choices(filter=[
        app_commands.Choice(name="Attachments Only", value="attachments"),
        app_commands.Choice(name="Non-Media Only (Text)", value="text")
    ])
    @app_commands.default_permissions(administrator=True)
    async def slash_replypurge(self, interaction: discord.Interaction, 
                               filter: app_commands.Choice[str], 
                               target_message: str):
        """Purge messages until a specific message using ID or Link."""
        await interaction.response.defer(ephemeral=True)

        # 1. Parse Message ID from input (handle Link or ID)
        msg_id = None
        try:
            if "discord.com/channels" in target_message:
                # Extract ID from link
                msg_id = int(target_message.split("/")[-1])
            else:
                msg_id = int(target_message)
        except ValueError:
            return await interaction.followup.send("❌ Invalid Message ID or Link.", ephemeral=True)

        # 2. Fetch the target message
        try:
            ref_msg = await interaction.channel.fetch_message(msg_id)
        except discord.NotFound:
            return await interaction.followup.send("❌ Could not find that message in this channel.", ephemeral=True)

        # 3. Validation Logic (Same as text version)
        ref_is_media = self.is_media_message(ref_msg)
        mode = filter.value

        if mode == "attachments" and not ref_is_media:
            return await interaction.followup.send("❌ **Error:** You selected 'Attachments Only' but the target message is text.", ephemeral=True)
        
        if mode == "text" and ref_is_media:
            return await interaction.followup.send("❌ **Error:** You selected 'Text Only' but the target message is media.", ephemeral=True)

        # 4. Define Check
        def purge_check(m):
            if mode == "attachments": return self.is_media_message(m)
            elif mode == "text": return not self.is_media_message(m)
            return True

        # 5. Execute
        try:
            deleted = await interaction.channel.purge(limit=None, after=ref_msg, check=purge_check)
            
            # Inclusive Delete
            ref_deleted = False
            if purge_check(ref_msg):
                try:
                    await ref_msg.delete()
                    ref_deleted = True
                except: pass
            
            count = len(deleted) + (1 if ref_deleted else 0)
            await interaction.followup.send(f"✅ Purged **{count}** messages matching **{filter.name}** down to the target.", ephemeral=True)
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
