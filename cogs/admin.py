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
# - get_dm_settings()
# - save_dm_settings(data)
# - delayed_repost(channel, delay)
# - repost_sticky(channel)
# - on_message(message) [Handles Sticky & DM Req validation]
# - on_reaction_add(reaction, user) [Handles DM Req interaction]
# - sticky(interaction, message, set) [Slash Command]
# - stickytime(interaction, timing, number, unit) [Slash Command]
# - stickylist(interaction) [Slash Command]
# - dmconfig(interaction, ...) [Slash Command - NEW]
# - dmchannel(interaction, action, channel) [Slash Command - NEW]
# - kick(interaction, user, reason) [Slash Command]
# - ban(interaction, user, reason) [Slash Command]
# - unban(interaction, user_id) [Slash Command]
# - dm(interaction, user, message) [Slash Command]
# setup(bot)

DM_MSG_0 = "⚠️ **Message 0:** You must include a message content along with your mention to make a DM request."

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

    def get_dm_settings(self):
        """Fetches DM Request configuration."""
        # We store this as a single document/dict in a collection, or just use a dedicated collection.
        # Assuming a 'dm_settings' collection with one main config doc.
        config = self.bot.db.get_collection("dm_settings")
        if not config: return {}
        return config[0] if isinstance(config, list) and config else config

    def save_dm_settings(self, data):
        """Saves DM Request configuration."""
        self.bot.db.save_collection("dm_settings", [data])

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
            # Updated color to #ff90aa
            embed = discord.Embed(description=data['content'], color=discord.Color(0xff90aa))
            
            new_msg = await channel.send(embed=embed)
            
            # Update DB
            data['last_message_id'] = new_msg.id
            data['last_posted_at'] = datetime.datetime.now().timestamp()
            self.save_sticky(data)
            
        except Exception as e:
            print(f"Failed to repost sticky in {channel.id}: {e}")

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles both Sticky Messages and DM Request validation."""
        if message.author.bot:
            return

        # ---------------------------------------------------------
        # 1. DM REQUEST LOGIC
        # ---------------------------------------------------------
        dm_config = self.get_dm_settings()
        dm_channels = dm_config.get('channels', [])
        
        if message.channel.id in dm_channels:
            # Rule: Must have mention AND text.
            has_mention = len(message.mentions) > 0
            has_text = len(message.content.strip()) > 0
            
            # If no mention, delete (as per "deletes any messages... that do not contain a mention and text")
            if not has_mention:
                try:
                    await message.delete()
                    # Optional: We could tell them why, but prompt only specified message 0 for mention-only case
                except: pass
                return # Stop processing
            
            # If mention but no text (effectively empty message besides mention), send Message 0
            # Discord mentions are part of content, so we check if content is JUST the mention
            clean_content = message.content
            for user in message.mentions:
                clean_content = clean_content.replace(user.mention, "").replace(f"<@!{user.id}>", "").replace(f"<@{user.id}>", "")
            
            if not clean_content.strip():
                try:
                    await message.delete()
                    await message.channel.send(f"{message.author.mention} {DM_MSG_0}", delete_after=10)
                except: pass
                return # Stop processing

            # Valid DM Request (Mention + Text) - Process Roles
            target = message.mentions[0] # Take the first mentioned user
            # We need the Member object to check roles
            if isinstance(target, discord.User):
                try:
                    target = await message.guild.fetch_member(target.id)
                except:
                    pass # Can't fetch member, maybe left?
            
            if isinstance(target, discord.Member):
                role_ids = [r.id for r in target.roles]
                
                r1_id = dm_config.get('role1_id')
                r2_id = dm_config.get('role2_id')
                r3_id = dm_config.get('role3_id')

                # Logic 2a-2f
                if r1_id and r1_id in role_ids:
                    # 2a: React with emoji 1 and 2
                    e1 = dm_config.get('emoji1')
                    e2 = dm_config.get('emoji2')
                    try:
                        if e1: await message.add_reaction(e1)
                        if e2: await message.add_reaction(e2)
                    except Exception as e:
                        print(f"Failed to react in DM req: {e}")

                elif r2_id and r2_id in role_ids:
                    # 2d: Send message 3
                    msg3 = dm_config.get('message3')
                    if msg3: await message.reply(msg3)

                elif r3_id and r3_id in role_ids:
                    # 2e: Send message 4
                    msg4 = dm_config.get('message4')
                    if msg4: await message.reply(msg4)

                else:
                    # 2f: No roles - Send fallback message (Message 5)
                    # "sorry they dont have dm roles yet :sob:, buggy's working on this"
                    msg5 = dm_config.get('message5', "Sorry they don't have dm roles yet 😭, buggy's working on this")
                    await message.reply(msg5)

        # ---------------------------------------------------------
        # 2. STICKY MESSAGE LOGIC
        # ---------------------------------------------------------
        sticky_data = self.get_sticky(message.channel.id)
        if sticky_data:
            # Timing Logic
            mode = sticky_data.get('mode', 'after')
            delay = sticky_data.get('delay', 0)
            now = datetime.datetime.now().timestamp()

            if mode == 'after':
                if delay > 0:
                    if message.channel.id in self.sticky_tasks:
                        self.sticky_tasks[message.channel.id].cancel()
                    self.sticky_tasks[message.channel.id] = asyncio.create_task(
                        self.delayed_repost(message.channel, delay)
                    )
                    return # Task started, exit
                # If delay 0, fall through

            elif mode == 'before':
                last_posted = sticky_data.get('last_posted_at', 0)
                if (now - last_posted) < delay:
                    return

            await self.repost_sticky(message.channel)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Handles interactions for DM Requests (Role 1 logic)."""
        if user.bot: return

        dm_config = self.get_dm_settings()
        if not dm_config: return
        
        # Check if channel is a DM Req channel
        if reaction.message.channel.id not in dm_config.get('channels', []):
            return

        # Check if the message has mentions
        if not reaction.message.mentions:
            return

        # Check if the reactor is the person mentioned (Logic 2b/2c)
        mentioned_user = reaction.message.mentions[0]
        if user.id != mentioned_user.id:
            return

        # Check Emojis
        emoji_str = str(reaction.emoji)
        
        if emoji_str == dm_config.get('emoji1'):
            # 2b: Send Message 1
            msg1 = dm_config.get('message1')
            if msg1: await reaction.message.channel.send(f"{user.mention} responded: {msg1}")
        
        elif emoji_str == dm_config.get('emoji2'):
            # 2c: Send Message 2
            msg2 = dm_config.get('message2')
            if msg2: await reaction.message.channel.send(f"{user.mention} responded: {msg2}")


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
            await self.repost_sticky(interaction.channel)
            await interaction.response.send_message("✅ Sticky message set!", ephemeral=True)
        else:
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

    @app_commands.command(name="stickylist", description="List all sticky messages in the server.")
    @app_commands.default_permissions(administrator=True)
    async def stickylist(self, interaction: discord.Interaction):
        collection = self.bot.db.get_collection("sticky_messages")
        sticky_map = {s['channel_id']: s for s in collection}
        
        sorted_stickies = []
        for channel in interaction.guild.text_channels:
            if channel.id in sticky_map:
                sorted_stickies.append((channel, sticky_map[channel.id]))
        
        if not sorted_stickies:
            return await interaction.response.send_message("📝 No active sticky messages found in this server.", ephemeral=True)
        
        description = ""
        for channel, data in sorted_stickies:
            mode = data.get('mode', 'after').title()
            delay = data.get('delay', 0)
            short_content = (data['content'][:60] + "...") if len(data['content']) > 60 else data['content']
            description += f"{channel.mention} • **{mode}** ({delay}s)\n`{short_content}`\n\n"
            
        embed = discord.Embed(title="📌 Sticky Messages", description=description, color=discord.Color(0xff90aa))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- DM REQUEST COMMANDS ---

    @app_commands.command(name="dmconfig", description="Configure DM Request settings (Roles, Emojis, Messages).")
    @app_commands.describe(
        role1="Role 1 (Triggers Reactions)",
        role2="Role 2 (Triggers Message 3)",
        role3="Role 3 (Triggers Message 4)",
        emoji1="Emoji 1 (For Role 1 reaction)",
        emoji2="Emoji 2 (For Role 1 reaction)",
        message1="Sent when user clicks Emoji 1",
        message2="Sent when user clicks Emoji 2",
        message3="Sent if user has Role 2",
        message4="Sent if user has Role 3",
        message5="Sent if user has NO roles (Fallback)"
    )
    @app_commands.default_permissions(administrator=True)
    async def dmconfig(self, interaction: discord.Interaction, 
                       role1: discord.Role, role2: discord.Role, role3: discord.Role,
                       emoji1: str, emoji2: str,
                       message1: str, message2: str, message3: str, message4: str, message5: str):
        
        config = self.get_dm_settings()
        
        # Update config
        config['role1_id'] = role1.id
        config['role2_id'] = role2.id
        config['role3_id'] = role3.id
        config['emoji1'] = emoji1
        config['emoji2'] = emoji2
        config['message1'] = message1
        config['message2'] = message2
        config['message3'] = message3
        config['message4'] = message4
        config['message5'] = message5
        
        self.save_dm_settings(config)
        
        embed = discord.Embed(title="✅ DM Request Config Updated", color=discord.Color(0xff90aa))
        embed.add_field(name="Roles", value=f"1: {role1.mention}\n2: {role2.mention}\n3: {role3.mention}", inline=False)
        embed.add_field(name="Reactions", value=f"1: {emoji1} -> {message1}\n2: {emoji2} -> {message2}", inline=False)
        embed.add_field(name="Auto Responses", value=f"Role 2: {message3}\nRole 3: {message4}\nNone: {message5}", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="dmchannel", description="Manage channels where DM Requests are active.")
    @app_commands.describe(action="Add, Remove, or List", channel="Channel to configure")
    @app_commands.default_permissions(administrator=True)
    async def dmchannel(self, interaction: discord.Interaction, action: Literal["Add", "Remove", "List"], channel: discord.TextChannel = None):
        config = self.get_dm_settings()
        channels = config.get('channels', [])
        
        if action == "List":
            if not channels:
                return await interaction.response.send_message("📝 No DM Request channels configured.", ephemeral=True)
            
            # Show list
            mentions = [f"<#{c_id}>" for c_id in channels]
            await interaction.response.send_message(f"**DM Request Channels:**\n" + ", ".join(mentions), ephemeral=True)
            return

        if not channel:
            return await interaction.response.send_message("❌ You must specify a channel to Add or Remove.", ephemeral=True)

        if action == "Add":
            if channel.id not in channels:
                channels.append(channel.id)
                config['channels'] = channels
                self.save_dm_settings(config)
                await interaction.response.send_message(f"✅ Added {channel.mention} to DM Request channels.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {channel.mention} is already in the list.", ephemeral=True)
        
        elif action == "Remove":
            if channel.id in channels:
                channels.remove(channel.id)
                config['channels'] = channels
                self.save_dm_settings(config)
                await interaction.response.send_message(f"✅ Removed {channel.mention} from DM Request channels.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {channel.mention} was not in the list.", ephemeral=True)

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

    @app_commands.command(name="dm", description="Send a direct message to a user.")
    @app_commands.describe(user="The user to DM", message="The message content")
    @app_commands.default_permissions(administrator=True)
    async def dm(self, interaction: discord.Interaction, user: discord.User, message: str):
        """Sends a direct message to a user."""
        try:
            await user.send(message)
            await interaction.response.send_message(f"✅ Sent DM to **{user.name}**: {message}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Could not DM **{user.name}**. They may have DMs closed or blocked the bot.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to send DM: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
