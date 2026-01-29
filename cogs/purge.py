import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import asyncio
from typing import Literal, Optional

# Function/Class List:
# class Purge(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_purge_config()
# - save_purge_config(config)
# - get_pin_announcement_config(guild_id)
# - save_pin_announcement_config(guild_id, enabled)
# - do_purge(channel, limit, after, user_id, keep_media, keep_links)
# - nightly_purge_task()
# - before_nightly_purge()
# - on_message(message) [Listener]
# - purge(interaction, scope, since, user, keep_media, keep_links, message_id) [Slash]
# - pinpurge(interaction) [Slash]
# - autopurge(interaction, action, keep_media, keep_links) [Slash]
# setup(bot)

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Purge messages, nightly auto-purge, and pin cleanup."
        self.nightly_purge_task.start()

    def cog_unload(self):
        self.nightly_purge_task.cancel()

    # --- HELPERS ---

    def get_purge_config(self):
        """Fetches the list of channels set for nightly purge."""
        return self.bot.db.get_collection("purge_settings")

    def save_purge_config(self, config):
        """Saves the nightly purge configuration."""
        self.bot.db.save_collection("purge_settings", config)

    def get_pin_announcement_config(self, guild_id):
        """Fetches whether server-wide pin announcement purge is enabled."""
        collection = self.bot.db.get_collection("pin_announcement_purge_config")
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        return doc.get('enabled', False) if doc else False

    def save_pin_announcement_config(self, guild_id, enabled):
        """Saves the server-wide pin announcement purge setting."""
        collection = self.bot.db.get_collection("pin_announcement_purge_config")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append({"guild_id": guild_id, "enabled": enabled})
        self.bot.db.save_collection("pin_announcement_purge_config", collection)

    async def do_purge(self, channel, limit=None, after=None, user_id=None, keep_media=False, keep_links=False):
        """
        Purges messages from a channel with specific filters.
        ALWAYS protects pinned messages.
        """
        def check(m):
            # 1. Always protect pins
            if m.pinned: return False
            
            # 2. User Filter
            if user_id and m.author.id != user_id:
                return False
                
            # 3. Media Filter
            # (Checks for attachments or embeds which usually contain media)
            if keep_media and (m.attachments or m.embeds):
                return False
                
            # 4. Link Filter
            if keep_links and ("http://" in m.content or "https://" in m.content):
                return False
                
            return True

        # Perform the purge
        deleted = await channel.purge(limit=limit, after=after, check=check, oldest_first=False)
        return len(deleted)

    # --- TASKS ---

    # EST is UTC-5
    @tasks.loop(time=datetime.time(hour=4, minute=0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))) 
    async def nightly_purge_task(self):
        print("⏰ Starting Nightly Purge Task (4 AM EST)...")
        config = self.get_purge_config()
        
        if not config:
            print("Nightly Purge: No channels configured.")
            return

        for entry in config:
            channel_id = entry.get('channel_id')
            keep_media = entry.get('keep_media', False)
            keep_links = entry.get('keep_links', False)
            
            # Try to get from cache first
            channel = self.bot.get_channel(channel_id)
            
            # If not in cache, try fetching it
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception as e:
                    print(f"Nightly Purge Error: Could not fetch channel {channel_id}: {e}")
                    continue

            if channel:
                try:
                    # Perform purge with configured settings
                    count = await self.do_purge(channel, limit=None, keep_media=keep_media, keep_links=keep_links)
                    print(f"Nightly Purge: Deleted {count} messages in {channel.name} ({channel.id}).")
                    
                    # Post Announcement (Deletes after 30 seconds)
                    if count > 0:
                        await channel.send(f"🧹 **Nightly Purge Complete.** Deleted {count} messages.", delete_after=30)
                    
                except Exception as e:
                    print(f"Failed to auto-purge {channel.name}: {e}")

    @nightly_purge_task.before_loop
    async def before_nightly_purge(self):
        await self.bot.wait_until_ready()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listens for system 'pin' announcements and deletes them if configured."""
        if message.type == discord.MessageType.pins_add:
            is_enabled = self.get_pin_announcement_config(message.guild.id)
            
            if is_enabled:
                try:
                    await message.delete()
                except:
                    pass

    # --- COMMANDS ---

    @app_commands.command(name="purge", description="Delete messages with advanced filters.")
    @app_commands.describe(
        scope="Where to purge messages?",
        since="Time range to purge",
        user="Optional: Only purge this user's messages",
        keep_media="Optional: Keep messages with attachments/embeds? (Default: False)",
        keep_links="Optional: Keep messages with links? (Default: False)",
        message_id="Required if 'Until Message' is chosen. Deletes up to AND including this ID."
    )
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, 
                    scope: Literal["Channel", "Category", "Server"],
                    since: Literal["Past Hour", "Today", "Until Message"],
                    user: Optional[discord.User] = None,
                    keep_media: bool = False,
                    keep_links: bool = False,
                    message_id: str = None):
        """Delete messages with advanced filters."""
        await interaction.response.defer(ephemeral=True)
        
        # 1. Determine Time Cutoff (After)
        after_date = None
        
        if since == "Past Hour":
            after_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
            
        elif since == "Today":
            # Start of the current day (UTC)
            now = datetime.datetime.now(datetime.timezone.utc)
            after_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
        elif since == "Until Message":
            if not message_id:
                return await interaction.followup.send("❌ You selected 'Until Message' but did not provide a `message_id`!", ephemeral=True)
            try:
                # Try to fetch message from current channel to get timestamp
                # Note: If Scope is Server, we assume the ID is from the current channel for reference time
                ref_msg = await interaction.channel.fetch_message(int(message_id))
                # Subtract 1 microsecond to ensure the message itself is included in the "after" range check
                # (purge deletes messages NEWER than 'after')
                after_date = ref_msg.created_at - datetime.timedelta(microseconds=1)
            except:
                return await interaction.followup.send("❌ Could not find that message in this channel to calculate time.", ephemeral=True)

        # 2. Determine Target Channels
        target_channels = []
        if scope == "Channel":
            target_channels.append(interaction.channel)
        elif scope == "Category":
            if interaction.channel.category:
                target_channels = interaction.channel.category.text_channels
            else:
                return await interaction.followup.send("❌ This channel is not in a category!", ephemeral=True)
        elif scope == "Server":
             target_channels = interaction.guild.text_channels

        # 3. Safety Check
        if len(target_channels) > 5:
             await interaction.followup.send(f"⏳ Starting purge on {len(target_channels)} channels... This may take a while.", ephemeral=True)

        total_deleted = 0
        processed_channels = 0
        user_id_val = user.id if user else None

        # 4. Execute Purge
        for channel in target_channels:
            try:
                count = await self.do_purge(
                    channel, 
                    limit=None, # Iterate history based on date
                    after=after_date,
                    user_id=user_id_val,
                    keep_media=keep_media,
                    keep_links=keep_links
                )
                total_deleted += count
                processed_channels += 1
                
                if len(target_channels) > 1: await asyncio.sleep(1) 
            except Exception as e:
                print(f"Failed to purge {channel.name}: {e}")

        # 5. Report
        location_text = "this channel"
        if scope == "Category": location_text = f"the **{interaction.channel.category.name}** category"
        elif scope == "Server": location_text = "the **entire server**"

        await interaction.followup.send(f"✅ Purge Complete! Deleted **{total_deleted}** messages in {location_text}.", ephemeral=True)
        try:
            await interaction.channel.send(f"🧹 **Manual Purge Complete.** Deleted {total_deleted} messages in {location_text}.", delete_after=60)
        except: pass

    @app_commands.command(name="pinpurge", description="Toggle server-wide deletion of 'XY pinned a message' announcements.")
    @app_commands.default_permissions(manage_messages=True)
    async def pinpurge(self, interaction: discord.Interaction):
        """Toggle server-wide deletion of 'XY pinned a message' announcements."""
        current_state = self.get_pin_announcement_config(interaction.guild_id)
        new_state = not current_state
        self.save_pin_announcement_config(interaction.guild_id, new_state)
        
        status = "ENABLED" if new_state else "DISABLED"
        
        if new_state:
            desc = "✅ Auto-deletion of pin announcements is now **ENABLED**.\n*(Note: This never deletes the actual pinned messages, only the notification)*"
        else:
            desc = "✅ Auto-deletion of pin announcements is now **DISABLED**."

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="autopurge", description="Manage nightly auto-purge for this channel.")
    @app_commands.describe(
        action="Add, Remove, or List channels",
        keep_media="[Add Only] Keep attachments/embeds? (Default: False)",
        keep_links="[Add Only] Keep links? (Default: False)"
    )
    @app_commands.default_permissions(administrator=True)
    async def autopurge(self, interaction: discord.Interaction, 
                        action: Literal["Add", "Remove", "List"],
                        keep_media: bool = False,
                        keep_links: bool = False):
        """Manage nightly auto-purge for this channel."""
        config = self.get_purge_config()
        if not isinstance(config, list): config = []
        
        # Find existing config for this channel
        existing_idx = next((i for i, c in enumerate(config) if c['channel_id'] == interaction.channel_id), -1)
        
        if action == "Add":
            new_entry = {
                "guild_id": interaction.guild_id, 
                "channel_id": interaction.channel_id,
                "keep_media": keep_media,
                "keep_links": keep_links
            }
            
            if existing_idx != -1:
                config[existing_idx] = new_entry
                msg = "✅ Updated nightly auto-purge settings for this channel."
            else:
                config.append(new_entry)
                msg = "✅ Channel added to nightly auto-purge (4 AM EST)."
            
            self.save_purge_config(config)
            await interaction.response.send_message(msg, ephemeral=True)
                
        elif action == "Remove":
            if existing_idx == -1:
                await interaction.response.send_message("⚠️ This channel is not set for auto-purge.", ephemeral=True)
            else:
                config.pop(existing_idx)
                self.save_purge_config(config)
                await interaction.response.send_message("✅ Channel removed from nightly auto-purge.", ephemeral=True)

        elif action == "List":
            if not config:
                return await interaction.response.send_message("📝 No auto-purge channels configured.", ephemeral=True)

            # Map config by channel ID for lookup
            config_map = {c['channel_id']: c for c in config if c.get('guild_id') == interaction.guild_id}
            
            # Sort by server channel order
            sorted_entries = []
            for channel in interaction.guild.text_channels:
                if channel.id in config_map:
                    sorted_entries.append((channel, config_map[channel.id]))

            if not sorted_entries:
                return await interaction.response.send_message("📝 No auto-purge channels found in this server.", ephemeral=True)

            text = "**🌙 Nightly Auto-Purge Channels**\n"
            for channel, data in sorted_entries:
                flags = []
                if data.get('keep_media'): flags.append("🖼️ Keep Media")
                if data.get('keep_links'): flags.append("🔗 Keep Links")
                
                flag_str = f" ({', '.join(flags)})" if flags else " (Wipe All)"
                text += f"• {channel.mention}{flag_str}\n"

            await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Purge(bot))
