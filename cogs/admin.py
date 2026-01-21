import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import datetime

# Function/Class List:
# class Admin(commands.Cog)
# - __init__(bot)
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - on_message(message)
# - handle_sticky(message)
# - stick(interaction, message)
# - unstick(interaction)
# - stickytime(interaction, timing, number, unit)
# setup(bot)

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Admin tools and Sticky Message management."

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

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles sticky message logic."""
        if message.author.bot:
            return

        # Check if this channel has a sticky message active
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
    await bot.add_cog(Admin(bot))
