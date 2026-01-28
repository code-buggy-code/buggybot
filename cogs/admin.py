import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
from typing import Literal

# Function/Class List:
# class Admin(commands.Cog)
# - __init__(bot)
# - get_sticky(channel_id)
# - save_sticky(data)
# - delete_sticky(channel_id)
# - delayed_repost(channel, delay)
# - repost_sticky(channel)
# - on_message(message)
# - sticky(interaction, message, set) [Slash Command]
# - stickytime(interaction, timing, number, unit) [Slash Command]
# - stickylist(interaction) [Slash Command]
# - kick(interaction, user, reason) [Slash Command]
# - ban(interaction, user, reason) [Slash Command]
# - unban(interaction, user_id) [Slash Command]
# setup(bot)

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Task manager for debounce logic (cancelling timers on new messages)
        self.sticky_tasks = {} # {channel_id: asyncio.Task}

    # --- DB HELPERS ---

    def get_sticky(self, channel_id):
        """Fetches sticky config for a channel."""
        collection = self.bot.db.get_collection("sticky_messages")
        return next((s for s in collection if s['channel_id'] == channel_id), None)

    def save_sticky(self, data):
        """Saves/Updates sticky config."""
        updated = self.bot.db.update_doc("sticky_messages", "channel_id", data['channel_id'], data)
        if not updated:
            collection = self.bot.db.get_collection("sticky_messages")
            if not any(s['channel_id'] == data['channel_id'] for s in collection):
                collection.append(data)
                self.bot.db.save_collection("sticky_messages", collection)

    def delete_sticky(self, channel_id):
        """Removes sticky config."""
        collection = self.bot.db.get_collection("sticky_messages")
        collection = [s for s in collection if s['channel_id'] != channel_id]
        self.bot.db.save_collection("sticky_messages", collection)

    # --- STICKY LOGIC ---

    async def delayed_repost(self, channel, delay):
        """Waits for the delay to pass. If not cancelled, reposts the sticky."""
        try:
            await asyncio.sleep(delay)
            await self.repost_sticky(channel)
        except asyncio.CancelledError:
            # Task was cancelled because a new message appeared
            pass
        finally:
            # Cleanup task reference
            if channel.id in self.sticky_tasks:
                if self.sticky_tasks[channel.id] == asyncio.current_task():
                    del self.sticky_tasks[channel.id]

    async def repost_sticky(self, channel):
        """Deletes old sticky and sends new one."""
        data = self.get_sticky(channel.id)
        if not data: return

        # Delete old message
        try:
            if data.get('last_message_id'):
                old_msg = await channel.fetch_message(data['last_message_id'])
                await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        # Send new message
        try:
            embed = discord.Embed(description=data['content'], color=discord.Color.gold())
            # Optional: Add a footer or title if you want it to look distinct
            
            new_msg = await channel.send(embed=embed)
            
            # Update DB
            data['last_message_id'] = new_msg.id
            data['last_posted_at'] = datetime.datetime.now().timestamp()
            self.save_sticky(data)
            
        except Exception as e:
            print(f"Failed to repost sticky in {channel.id}: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles sticky message reposting with debounce logic."""
        if message.author.bot:
            return

        data = self.get_sticky(message.channel.id)
        if not data:
            return

        # Timing Logic
        mode = data.get('mode', 'after')
        delay = data.get('delay', 0)
        now = datetime.datetime.now().timestamp()

        # Mode: After (Delay/Silence) - The Fix!
        if mode == 'after':
            if delay > 0:
                # Cancel existing timer (reset silence clock)
                if message.channel.id in self.sticky_tasks:
                    self.sticky_tasks[message.channel.id].cancel()
                
                # Start new timer
                self.sticky_tasks[message.channel.id] = asyncio.create_task(
                    self.delayed_repost(message.channel, delay)
                )
                return
            
            # If delay is 0, fall through to immediate

        # Mode: Before (Cooldown)
        elif mode == 'before':
            last_posted = data.get('last_posted_at', 0)
            if (now - last_posted) < delay:
                return

        # Immediate Repost
        await self.repost_sticky(message.channel)

    # --- COMMANDS ---

    @app_commands.command(name="sticky", description="Set or remove a sticky message in this channel.")
    @app_commands.rename(should_set="set")
    @app_commands.describe(
        message="The message to stick (Required if set is True)",
        should_set="True to set/update, False to remove"
    )
    @app_commands.default_permissions(administrator=True)
    async def sticky(self, interaction: discord.Interaction, should_set: bool, message: str = None):
        if should_set:
            if not message:
                return await interaction.response.send_message("❌ You must provide a message to set a sticky!", ephemeral=True)
            
            # Save new config (preserving existing timing if any)
            existing = self.get_sticky(interaction.channel_id)
            new_data = {
                "channel_id": interaction.channel_id,
                "content": message,
                "mode": existing.get('mode', 'after') if existing else 'after',
                "delay": existing.get('delay', 0) if existing else 0,
                "last_message_id": None,
                "last_posted_at": 0
            }
            self.save_sticky(new_data)
            
            # Trigger initial post
            await self.repost_sticky(interaction.channel)
            await interaction.response.send_message("✅ Sticky message set!", ephemeral=True)
            
        else:
            # Remove
            existing = self.get_sticky(interaction.channel_id)
            if existing:
                try:
                    if existing.get('last_message_id'):
                        msg = await interaction.channel.fetch_message(existing['last_message_id'])
                        await msg.delete()
                except: pass
                self.delete_sticky(interaction.channel_id)
                await interaction.response.send_message("✅ Sticky message removed.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No sticky message found in this channel.", ephemeral=True)

    @app_commands.command(name="stickytime", description="Configure sticky message timing.")
    @app_commands.describe(timing="Mode: 'before' (Cooldown) or 'after' (Delay)", number="Time amount", unit="Time unit")
    @app_commands.choices(
        timing=[app_commands.Choice(name="Before (Cooldown)", value="before"), app_commands.Choice(name="After (Delay)", value="after")],
        unit=[app_commands.Choice(name="Seconds", value="seconds"), app_commands.Choice(name="Minutes", value="minutes")]
    )
    @app_commands.default_permissions(administrator=True)
    async def stickytime(self, interaction: discord.Interaction, timing: app_commands.Choice[str], number: int, unit: app_commands.Choice[str]):
        data = self.get_sticky(interaction.channel_id)
        if not data:
            return await interaction.response.send_message("❌ You need to set a sticky message first with `/sticky`!", ephemeral=True)

        multiplier = 60 if unit.value == 'minutes' else 1
        total_seconds = number * multiplier
        
        data['mode'] = timing.value
        data['delay'] = total_seconds
        self.save_sticky(data)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing.value == "before" else "Delay (After)"
        
        await interaction.response.send_message(f"✅ Sticky timing updated.\nMode: **{mode_text}**\nTime: **{delay_text}**", ephemeral=True)

    @app_commands.command(name="stickylist", description="List all sticky messages in the server, sorted by channel order.")
    @app_commands.default_permissions(administrator=True)
    async def stickylist(self, interaction: discord.Interaction):
        # 1. Get all sticky configs
        collection = self.bot.db.get_collection("sticky_messages")
        
        # 2. Map channel IDs to their sticky data for easy lookup
        sticky_map = {s['channel_id']: s for s in collection}
        
        # 3. Iterate through guild channels in their natural order (Discord returns them sorted)
        # This ensures the list matches the visual order in the sidebar
        sorted_stickies = []
        for channel in interaction.guild.text_channels:
            if channel.id in sticky_map:
                sorted_stickies.append((channel, sticky_map[channel.id]))
        
        if not sorted_stickies:
            return await interaction.response.send_message("📝 No active sticky messages found in this server.", ephemeral=True)
        
        # 4. Build the embed
        description = ""
        for channel, data in sorted_stickies:
            mode = data.get('mode', 'after').title()
            delay = data.get('delay', 0)
            short_content = (data['content'][:60] + "...") if len(data['content']) > 60 else data['content']
            
            description += f"{channel.mention} • **{mode}** ({delay}s)\n`{short_content}`\n\n"
            
        embed = discord.Embed(
            title="📌 Sticky Messages",
            description=description,
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- GENERAL ADMIN COMMANDS ---

    @app_commands.command(name="kick", description="Kick a user from the server.")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        if user.top_role >= interaction.user.top_role:
            return await interaction.response.send_message("❌ You cannot kick this user.", ephemeral=True)
        
        try:
            await user.kick(reason=reason)
            await interaction.response.send_message(f"👞 **{user}** has been kicked. Reason: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to kick that user.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a user from the server.")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        if user.top_role >= interaction.user.top_role:
            return await interaction.response.send_message("❌ You cannot ban this user.", ephemeral=True)
            
        try:
            await user.ban(reason=reason)
            await interaction.response.send_message(f"🔨 **{user}** has been banned. Reason: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to ban that user.", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user ID.")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user)
            await interaction.response.send_message(f"✅ **{user}** has been unbanned.")
        except:
            await interaction.response.send_message("❌ Could not unban user. Are they banned? Is the ID correct?", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
