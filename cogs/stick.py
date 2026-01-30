import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
from typing import Literal

# Function/Class List:
# class Stickies(commands.Cog)
# - __init__(bot)
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - send_sticky(channel)
# - sticky_task(channel, delay)
# - handle_sticky(message)
# - on_message(message)
# - on_message_delete(message)
# - sticky(interaction, action, message) [Slash]
# - stickytime(interaction, timing, number, unit) [Slash]
# setup(bot)

class Stickies(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Manage sticky messages."
        self.pending_tasks = {} # {channel_id: asyncio.Task}
        self.reposting = set()  # {channel_id} - Safety lock to prevent race conditions

    # --- HELPERS ---

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

    async def send_sticky(self, channel):
        """Actually sends the sticky message."""
        # Safety Lock: Prevent concurrent sends in the same channel
        # This prevents the "triggers itself once" race condition where the bot sees its own new sticky
        if channel.id in self.reposting:
            return

        self.reposting.add(channel.id)
        try:
            stickies = self.get_stickies()
            sticky_data = next((s for s in stickies if s['channel_id'] == channel.id), None)
            
            if not sticky_data: return

            # Check if the last message is already the sticky to avoid double posting
            if channel.last_message_id == sticky_data.get('last_message_id'):
                 return

            # Delete old sticky
            if sticky_data.get('last_message_id'):
                try:
                    old_msg = await channel.fetch_message(sticky_data['last_message_id'])
                    await old_msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            
            # Send new sticky
            try:
                embed = discord.Embed(description=sticky_data['content'], color=discord.Color(0xff90aa))
                new_msg = await channel.send(embed=embed)
                
                sticky_data['last_message_id'] = new_msg.id
                sticky_data['last_posted_at'] = datetime.datetime.now().timestamp()
                sticky_data['active'] = True

                self.bot.db.update_doc("sticky_messages", "channel_id", channel.id, sticky_data)

            except Exception as e:
                print(f"Failed to send sticky: {e}")
        finally:
            # Always release the lock so the next message can trigger it
            self.reposting.discard(channel.id)

    async def sticky_task(self, channel, delay):
        """Waits for delay then sends sticky."""
        try:
            await asyncio.sleep(delay)
            # Re-fetch channel to ensure freshness
            if not channel: return
            
            await self.send_sticky(channel)
        except asyncio.CancelledError:
            pass
        finally:
            if channel.id in self.pending_tasks:
                del self.pending_tasks[channel.id]

    async def handle_sticky(self, message):
        """Resends the sticky message to the bottom."""
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
        if mode == "before" and delay > 0:
            last_posted = sticky_data.get('last_posted_at', 0)
            if (now - last_posted) < delay:
                return
            # If cooldown passed, send immediately
            await self.send_sticky(message.channel)

        # LOGIC 2: AFTER (Delay/Silence)
        elif mode == "after":
            if delay > 0:
                # Cancel existing task (reset timer)
                if message.channel.id in self.pending_tasks:
                    self.pending_tasks[message.channel.id].cancel()
                
                # Start new task
                self.pending_tasks[message.channel.id] = asyncio.create_task(
                    self.sticky_task(message.channel, delay)
                )
            else:
                # No delay, send immediately
                await self.send_sticky(message.channel)

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles sticky message triggering."""
        if not message.guild: return
        
        # Allow purge announcements to trigger stickies, but ignore other bot messages (especially self)
        if message.author.bot:
            if not message.content.startswith("🧹 **Nightly Purge Complete.**"):
                return

        stickies = self.get_stickies()
        sticky_data = next((s for s in stickies if s['channel_id'] == message.channel.id), None)
        
        if sticky_data:
            if not sticky_data.get('active', True): return
            if sticky_data.get('last_message_id') == message.id: return

            await self.handle_sticky(message)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Prevents log spam if a sticky is deleted by the bot logic."""
        pass

    # --- SLASH COMMANDS ---

    @app_commands.command(name="sticky", description="Manage sticky messages.")
    @app_commands.describe(action="Choose an action", message="The message content (Required for Add)")
    @app_commands.default_permissions(administrator=True)
    async def sticky(self, interaction: discord.Interaction, action: Literal["Add", "List", "Remove"], message: str = None):
        """Manage sticky messages."""
        
        if action == "List":
            stickies = self.get_stickies()
            current_guild_stickies = [s for s in stickies if s.get('guild_id') == interaction.guild_id]

            if not current_guild_stickies:
                return await interaction.response.send_message("📝 No sticky messages found for this server.", ephemeral=True)

            def get_sort_key(s):
                channel = interaction.guild.get_channel(s['channel_id'])
                return channel.position if channel else float('inf')

            current_guild_stickies.sort(key=get_sort_key)

            text = "**📌 Active Sticky Messages:**\n"
            for s in current_guild_stickies:
                channel = interaction.guild.get_channel(s['channel_id'])
                chan_mention = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
                content_preview = s['content'].replace("\n", " ")
                status = " (Paused)" if not s.get('active', True) else ""
                if len(content_preview) > 50: content_preview = content_preview[:47] + "..."
                text += f"• {chan_mention}{status}: {content_preview}\n"
            
            return await interaction.response.send_message(text, ephemeral=True)
        
        if action == "Remove":
            stickies = self.get_stickies()
            target = next((s for s in stickies if s['channel_id'] == interaction.channel_id), None)
            
            if target:
                if target.get('last_message_id'):
                    try:
                        old_msg = await interaction.channel.fetch_message(target['last_message_id'])
                        await old_msg.delete()
                    except: pass
                
                stickies = [s for s in stickies if s['channel_id'] != interaction.channel_id]
                self.save_stickies(stickies)
                await interaction.response.send_message("✅ Sticky message removed.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No sticky message found in this channel.", ephemeral=True)
            return

        if not message:
            return await interaction.response.send_message(f"❌ You must provide a message to {action} a sticky!", ephemeral=True)

        content = message.replace("\\n", "\n")
        existing = next((s for s in self.get_stickies() if s['channel_id'] == interaction.channel_id), None)

        if action == "Add":
            if existing:
                return await interaction.response.send_message("⚠️ A sticky message already exists in this channel. Remove it first to set a new one.", ephemeral=True)
            
            new_sticky = {
                "channel_id": interaction.channel_id,
                "guild_id": interaction.guild_id,
                "content": content,
                "last_message_id": None,
                "last_posted_at": datetime.datetime.now().timestamp(),
                "active": True
            }
            
            stickies = self.get_stickies()
            stickies.append(new_sticky)
            self.save_stickies(stickies)

            try:
                embed = discord.Embed(description=content, color=discord.Color(0xff90aa))
                msg = await interaction.channel.send(embed=embed)
                new_sticky['last_message_id'] = msg.id
                self.bot.db.update_doc("sticky_messages", "channel_id", interaction.channel_id, new_sticky)
                await interaction.response.send_message("✅ Sticky message added!", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ Failed to send sticky: {e}", ephemeral=True)

    @app_commands.command(name="stickytime", description="Configure server-wide sticky message timing.")
    @app_commands.describe(timing="Mode: 'before' (Cooldown) or 'after' (Delay)", number="Time amount", unit="Time unit")
    @app_commands.choices(
        timing=[app_commands.Choice(name="Before (Cooldown)", value="before"), app_commands.Choice(name="After (Delay)", value="after")],
        unit=[app_commands.Choice(name="Seconds", value="seconds"), app_commands.Choice(name="Minutes", value="minutes")]
    )
    @app_commands.default_permissions(administrator=True)
    async def stickytime(self, interaction: discord.Interaction, timing: app_commands.Choice[str], number: int, unit: app_commands.Choice[str]):
        """Configure server-wide sticky message timing."""
        multiplier = 60 if unit.value == 'minutes' else 1
        total_seconds = number * multiplier
        
        settings = self.get_sticky_settings()
        settings = [s for s in settings if s['guild_id'] != interaction.guild_id]
        settings.append({"guild_id": interaction.guild_id, "delay": total_seconds, "mode": timing.value})
        self.save_sticky_settings(settings)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing.value == "before" else "Delay (After)"
        await interaction.response.send_message(f"✅ Sticky settings updated.\nMode: **{mode_text}**\nTime: **{delay_text}**", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Stickies(bot))
