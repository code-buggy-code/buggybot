import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import datetime
import json
import os
import re

# Function/Class List:
# class Admin(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - get_log_settings()
# - save_log_settings(settings)
# - get_dm_settings(guild_id)
# - save_dm_settings(guild_id, data)
# - get_vote_data(guild_id)
# - save_vote_data(guild_id, data)
# - get_vcping_config(guild_id)
# - save_vcping_config(guild_id, config)
# - log_to_channel(guild, embed)
# - on_message(message)
# - on_raw_reaction_add(payload)
# - on_message_delete(message)
# - on_message_edit(before, after)
# - on_member_remove(member)
# - handle_sticky(message)
# - handle_dm_request(message)
# - stick(ctx, message) [Prefix]
# - unstick(ctx) [Prefix]
# - stickylist(ctx) [Prefix]
# - stickytime(ctx, timing, number, unit) [Prefix]
# - setlogchannel(ctx, channel) [Prefix]
# - dmset(ctx) [Prefix]
# - dmunset(ctx) [Prefix]
# - dmreq (Group) [Prefix]
#   - dmreq_roles(ctx, role1, role2, role3)
#   - dmreq_reacts(ctx, accept, deny)
#   - dmreq_message(ctx, index, message)
#   - dmreq_listmessages(ctx)
#   - dmreq_list(ctx)
# - vote(interaction, member) [Slash - Public]
# - vote_list(interaction) [Slash - Public]
# - voterole(ctx, role) [Prefix]
# - voteremove(ctx, member) [Prefix]
# - vcping (Group) [Prefix]
#   - vcping_ignore (Group)
#     - ignore_add(ctx, channel)
#     - ignore_remove(ctx, channel)
#     - ignore_list(ctx)
#   - vcping_set(ctx, role, people, minutes)
# - check_vcs()
# - before_check_vcs()
# - on_voice_state_update(member, before, after)
# setup(bot)

BUGGY_ID = 1433003746719170560

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "logging, dm requests, stickies, vc pings, and kick voting"
        
        self.vc_state = {}
        self.check_vcs.start()

        # Vote Kick Constants
        self.VOTE_THRESHOLD = 3 

        # DM Request Defaults
        self.DEFAULT_DM_MESSAGES = {
            "0": "{mention} Please include text with your mention to make a request.",
            "1": "Request Accepted!",
            "2": "Request Denied.",
            "3": "DM Request (Role 2) sent to {requested}.",
            "4": "DM Request (Role 3) sent to {requested}.",
            "5": "sorry they dont have dm roles yet :sob:, buggy's working on this"
        }
        self.DEFAULT_DM_REACTS = ["👍", "👎"]

    def cog_unload(self):
        self.check_vcs.cancel()

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

    def get_log_settings(self):
        """Returns server-specific logging settings."""
        return self.bot.db.get_collection("log_settings")

    def save_log_settings(self, settings):
        """Saves logging settings."""
        self.bot.db.save_collection("log_settings", settings)

    def get_dm_settings(self, guild_id):
        """Fetches DM settings for a specific guild."""
        collection = self.bot.db.get_collection("dm_settings")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                # Ensure defaults exist if partial data is found
                if "messages" not in doc: doc["messages"] = self.DEFAULT_DM_MESSAGES.copy()
                if "reacts" not in doc: doc["reacts"] = self.DEFAULT_DM_REACTS.copy()
                if "roles" not in doc: doc["roles"] = [0, 0, 0]
                if "channels" not in doc: doc["channels"] = []
                return doc
        
        # Return default structure if not found
        return {
            "guild_id": guild_id,
            "channels": [],
            "roles": [0, 0, 0],
            "reacts": self.DEFAULT_DM_REACTS.copy(),
            "messages": self.DEFAULT_DM_MESSAGES.copy()
        }

    def save_dm_settings(self, guild_id, data):
        """Saves DM settings for a guild."""
        collection = self.bot.db.get_collection("dm_settings")
        # Remove old entry
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append(data)
        self.bot.db.save_collection("dm_settings", collection)

    def get_vote_data(self, guild_id):
        """Fetches voting configuration and active votes for a guild."""
        collection = self.bot.db.get_collection("vote_data")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                # Ensure structure
                if 'active_votes' not in doc: doc['active_votes'] = {} # {target_id_str: [voters_list]}
                if 'voting_role_id' not in doc: doc['voting_role_id'] = None
                return doc
        
        return {
            "guild_id": guild_id,
            "voting_role_id": None,
            "active_votes": {}
        }

    def save_vote_data(self, guild_id, data):
        """Saves vote data for a guild."""
        collection = self.bot.db.get_collection("vote_data")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append(data)
        self.bot.db.save_collection("vote_data", collection)

    def get_vcping_config(self):
        """Fetches VC Ping config for all guilds."""
        # Stored as a dict {guild_id: settings}
        data = self.bot.db.get_collection("vcping_config")
        if isinstance(data, list): return {} # Migration safety
        return data

    def save_vcping_config(self, config):
        """Saves VC Ping config."""
        self.bot.db.save_collection("vcping_config", config)

    async def log_to_channel(self, guild, embed):
        """Helper to send logs to the configured channel."""
        settings = self.get_log_settings()
        guild_setting = next((s for s in settings if s['guild_id'] == guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles sticky message logic and DM Request logic."""
        if message.author.bot or not message.guild:
            return

        # 1. DM Request Logic
        await self.handle_dm_request(message)

        # 2. Sticky Logic
        stickies = self.get_stickies()
        if any(s['channel_id'] == message.channel.id for s in stickies):
            await self.handle_sticky(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles DM Request reactions."""
        if payload.user_id == self.bot.user.id: return
        if not payload.guild_id: return

        settings = self.get_dm_settings(payload.guild_id)

        # Check if channel is tracked
        if payload.channel_id not in settings['channels']:
            return

        # Check if emoji is a configured DM react
        if str(payload.emoji) not in settings['reacts']:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel: return

        try:
            message = await channel.fetch_message(payload.message_id)
            # Find the user mentioned in the message (The DM Receiver)
            if not message.mentions: return
            
            target_member = message.mentions[0] # The person who was asked
            
            # Verify the person reacting is the person who was asked
            if payload.user_id != target_member.id:
                return 

            # Determine Accepted (0) or Denied (1)
            msg_index = -1
            if str(payload.emoji) == settings['reacts'][0]: msg_index = "1"
            elif str(payload.emoji) == settings['reacts'][1]: msg_index = "2"
            
            if msg_index != -1:
                raw_msg = settings['messages'].get(msg_index, "")
                
                # Format message
                formatted_msg = raw_msg.replace("{mention}", message.author.mention)\
                                       .replace("{requester}", message.author.mention)\
                                       .replace("{requested}", f"**{target_member.display_name}**")\
                                       .replace("{requested_nickname}", target_member.display_name)
                
                await channel.send(formatted_msg)
                
                # Cleanup reactions
                try:
                    for e in settings['reacts']:
                        await message.remove_reaction(e, self.bot.user) # Remove bot's reacts
                except: pass

        except Exception as e:
            print(f"DM Req Reaction Error: {e}")

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Logs deleted messages."""
        # Ignored if bot OR if author is buggy
        if message.author.bot or message.author.id == BUGGY_ID or not message.guild:
            return

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

        await self.log_to_channel(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Logs edited messages."""
        # Ignored if bot OR if author is buggy
        if before.author.bot or before.author.id == BUGGY_ID or not before.guild:
            return
        
        # Ignore checks if content is the same (e.g. embed update)
        if before.content == after.content:
            return

        embed = discord.Embed(
            title="✏️ Message Edited",
            description=f"**Author:** {before.author.mention} ({before.author.id})\n**Channel:** {before.channel.mention}\n**Jump:** [Link]({before.jump_url})",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Before", value=before.content[:1024] or "[No Content]", inline=False)
        embed.add_field(name="After", value=after.content[:1024] or "[No Content]", inline=False)

        await self.log_to_channel(before.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Logs when a member leaves."""
        embed = discord.Embed(
            title="👋 Member Left",
            description=f"{member.mention} has left the server.",
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=member.id, inline=True)
        embed.add_field(name="Joined At", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", inline=True)

        await self.log_to_channel(member.guild, embed)

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
        if mode == "before" and delay > 0:
            last_posted = sticky_data.get('last_posted_at', 0)
            if (now - last_posted) < delay:
                return

        # LOGIC 2: AFTER (Delay)
        if mode == "after" and delay > 0:
            await asyncio.sleep(delay)
            # Re-fetch stickies to ensure it wasn't deleted during the sleep
            current_stickies = self.get_stickies()
            if not any(s['channel_id'] == message.channel.id for s in current_stickies):
                return
            sticky_data = next((s for s in current_stickies if s['channel_id'] == message.channel.id), None)
            if not sticky_data: return

        # Delete old sticky
        if sticky_data.get('last_message_id'):
            try:
                old_msg = await message.channel.fetch_message(sticky_data['last_message_id'])
                await old_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
        
        # Send new sticky
        try:
            new_msg = await message.channel.send(sticky_data['content'])
            stickies = self.get_stickies()
            for s in stickies:
                if s['channel_id'] == message.channel.id:
                    s['last_message_id'] = new_msg.id
                    s['last_posted_at'] = datetime.datetime.now().timestamp()
                    break
            self.save_stickies(stickies)
        except Exception as e:
            print(f"Failed to send sticky: {e}")

    async def handle_dm_request(self, message):
        settings = self.get_dm_settings(message.guild.id)
        
        # Check if channel is a DM Request channel
        if message.channel.id not in settings['channels']:
            return

        # Check for admin Privileges (Bypass deletion)
        is_admin = message.author.guild_permissions.administrator

        # 1. STRICT PARSING
        cleaned_content = message.content.strip()
        match = re.match(r'^<@!?(\d+)>\s+(.+)', cleaned_content, re.DOTALL)
        
        valid_request = False
        target_member = None
        
        if match:
            user_id = int(match.group(1))
            target_member = message.guild.get_member(user_id)
            if target_member and not target_member.bot:
                valid_request = True
        
        # 2. ENFORCE RESTRICTIONS (Delete if bad)
        if not is_admin:
            if not valid_request:
                try:
                    await message.delete()
                    raw_msg = settings['messages'].get("0", "Error: No text.")
                    formatted_msg = raw_msg.replace("{mention}", message.author.mention).replace("{requester}", message.author.mention)
                    await message.channel.send(formatted_msg, delete_after=5)
                except: pass
                return
        
        # 3. FEATURE LOGIC
        if valid_request and target_member:
            target = target_member
            roles = settings['roles'] # [Role 1 ID, Role 2 ID, Role 3 ID]
            
            has_role_1 = any(r.id == roles[0] for r in target.roles)
            has_role_2 = any(r.id == roles[1] for r in target.roles)
            has_role_3 = any(r.id == roles[2] for r in target.roles)
            
            raw_msg = ""
            if has_role_1:
                # Role 1: Add Reactions (DMs Open)
                try:
                    for e in settings['reacts']:
                        await message.add_reaction(e)
                except: pass
            
            elif has_role_2:
                # Role 2: Send Message 3
                raw_msg = settings['messages'].get("3", "")
            elif has_role_3:
                # Role 3: Send Message 4
                raw_msg = settings['messages'].get("4", "")
            else:
                # No Roles: Send Message 5
                raw_msg = settings['messages'].get("5", "")
            
            if raw_msg:
                formatted_msg = raw_msg.replace("{mention}", message.author.mention)\
                                       .replace("{requester}", message.author.mention)\
                                       .replace("{requested}", f"**{target.display_name}**")\
                                       .replace("{requested_nickname}", target.display_name)
                await message.channel.send(formatted_msg)

    # --- PREFIX COMMANDS ---

    @commands.command(name="stick")
    @commands.has_permissions(administrator=True)
    async def stick(self, ctx, *, message: str):
        """Stick a message to the bottom of this channel."""
        content = message.replace("\\n", "\n")
        stickies = self.get_stickies()
        stickies = [s for s in stickies if s['channel_id'] != ctx.channel.id]

        new_sticky = {
            "channel_id": ctx.channel.id,
            "guild_id": ctx.guild.id,
            "content": content,
            "last_message_id": None,
            "last_posted_at": datetime.datetime.now().timestamp()
        }

        try:
            sent_msg = await ctx.send(content)
            new_sticky['last_message_id'] = sent_msg.id
        except Exception as e:
            return await ctx.send(f"❌ Failed to send sticky message: {e}")

        stickies.append(new_sticky)
        self.save_stickies(stickies)
        await ctx.send("✅ Message stuck to this channel!", delete_after=5)

    @commands.command(name="unstick")
    @commands.has_permissions(administrator=True)
    async def unstick(self, ctx):
        """Remove the sticky message from this channel."""
        stickies = self.get_stickies()
        target = next((s for s in stickies if s['channel_id'] == ctx.channel.id), None)
        
        if not target:
            return await ctx.send("❌ No sticky message found in this channel.")

        if target.get('last_message_id'):
            try:
                msg = await ctx.channel.fetch_message(target['last_message_id'])
                await msg.delete()
            except: pass

        stickies = [s for s in stickies if s['channel_id'] != ctx.channel.id]
        self.save_stickies(stickies)
        await ctx.send("✅ Sticky message removed.", delete_after=5)

    @commands.command(name="stickylist")
    @commands.has_permissions(administrator=True)
    async def stickylist(self, ctx):
        """List all active sticky messages in this server."""
        stickies = self.get_stickies()
        current_guild_stickies = [s for s in stickies if s.get('guild_id') == ctx.guild.id]

        if not current_guild_stickies:
            return await ctx.send("📝 No sticky messages found for this server.")

        text = "**📌 Active Sticky Messages:**\n"
        for s in current_guild_stickies:
            channel = ctx.guild.get_channel(s['channel_id'])
            chan_mention = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
            content_preview = s['content'].replace("\n", " ")
            if len(content_preview) > 50: content_preview = content_preview[:47] + "..."
            text += f"• {chan_mention}: {content_preview}\n"
        
        await ctx.send(text)

    @commands.command(name="stickytime")
    @commands.has_permissions(administrator=True)
    async def stickytime(self, ctx, timing: str, number: int, unit: str):
        """Configure server-wide sticky message timing (e.g., ?stickytime before 10 seconds)."""
        timing = timing.lower()
        unit = unit.lower()
        
        if timing not in ['before', 'after']:
            return await ctx.send("❌ Mode must be 'before' or 'after'.")
        if unit not in ['second', 'seconds', 'minute', 'minutes']:
             return await ctx.send("❌ Unit must be 'seconds' or 'minutes'.")

        multiplier = 60 if 'minute' in unit else 1
        total_seconds = number * multiplier
        
        settings = self.get_sticky_settings()
        settings = [s for s in settings if s['guild_id'] != ctx.guild.id]
        settings.append({"guild_id": ctx.guild.id, "delay": total_seconds, "mode": timing})
        self.save_sticky_settings(settings)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing == "before" else "Delay (After)"
        await ctx.send(f"✅ Sticky settings updated.\nMode: **{mode_text}**\nTime: **{delay_text}**")

    @commands.command(name="setlogchannel")
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where server logs will be sent."""
        settings = self.get_log_settings()
        settings = [s for s in settings if s['guild_id'] != ctx.guild.id]
        
        settings.append({"guild_id": ctx.guild.id, "log_channel_id": channel.id})
        self.save_log_settings(settings)
        await ctx.send(f"✅ Logging channel set to {channel.mention}.")

    # --- DM REQUEST COMMANDS (Prefix) ---
    
    @commands.command(name="dmset")
    @commands.has_permissions(administrator=True)
    async def dmset(self, ctx):
        """Set THIS channel as a DM Request channel."""
        settings = self.get_dm_settings(ctx.guild.id)
        
        if ctx.channel.id in settings['channels']:
            return await ctx.send("⚠️ This channel is already set for DM Requests.")
        
        settings['channels'].append(ctx.channel.id)
        self.save_dm_settings(ctx.guild.id, settings)
        await ctx.send(f"✅ <#{ctx.channel.id}> is now a DM Request channel.")

    @commands.command(name="dmunset")
    @commands.has_permissions(administrator=True)
    async def dmunset(self, ctx):
        """Remove THIS channel from DM Request channels."""
        settings = self.get_dm_settings(ctx.guild.id)
        
        if ctx.channel.id not in settings['channels']:
            return await ctx.send("⚠️ This channel is not a DM Request channel.")
        
        settings['channels'].remove(ctx.channel.id)
        self.save_dm_settings(ctx.guild.id, settings)
        await ctx.send(f"✅ Removed <#{ctx.channel.id}> from DM Request channels.")

    @commands.group(name="dmreq", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def dmreq(self, ctx):
        """Manage DM Request settings."""
        await ctx.send("Commands: `roles`, `reacts`, `message`, `listmessages`, `list`")

    @dmreq.command(name="roles")
    @commands.has_permissions(administrator=True)
    async def dmreq_roles(self, ctx, role1: discord.Role, role2: discord.Role, role3: discord.Role):
        settings = self.get_dm_settings(ctx.guild.id)
        settings['roles'] = [role1.id, role2.id, role3.id]
        self.save_dm_settings(ctx.guild.id, settings)
        await ctx.send(f"✅ **DM Roles Set:**\n1. {role1.mention}\n2. {role2.mention}\n3. {role3.mention}")

    @dmreq.command(name="reacts")
    @commands.has_permissions(administrator=True)
    async def dmreq_reacts(self, ctx, accept: str, deny: str):
        settings = self.get_dm_settings(ctx.guild.id)
        settings['reacts'] = [accept, deny]
        self.save_dm_settings(ctx.guild.id, settings)
        await ctx.send(f"✅ **DM Reacts Set:** {accept} (Accept) and {deny} (Deny)")

    @dmreq.command(name="message")
    @commands.has_permissions(administrator=True)
    async def dmreq_message(self, ctx, index: str, *, message: str):
        if index not in ["0", "1", "2", "3", "4", "5"]:
            return await ctx.send("❌ Index must be 0-5.")
        
        settings = self.get_dm_settings(ctx.guild.id)
        settings['messages'][index] = message
        self.save_dm_settings(ctx.guild.id, settings)
        await ctx.send(f"✅ **Message {index} Updated.**\nPreview: `{message}`")

    @dmreq.command(name="listmessages")
    @commands.has_permissions(administrator=True)
    async def dmreq_listmessages(self, ctx):
        settings = self.get_dm_settings(ctx.guild.id)
        text = "**📨 Current DM Messages:**\n"
        for i in range(6):
            key = str(i)
            msg = settings['messages'].get(key, "Not set")
            text += f"**[{key}]:** {msg}\n"
        await ctx.send(text)

    @dmreq.command(name="list")
    @commands.has_permissions(administrator=True)
    async def dmreq_list(self, ctx):
        settings = self.get_dm_settings(ctx.guild.id)
        
        channels = settings.get('channels', [])
        chan_text = " ".join([f"<#{c}>" for c in channels]) if channels else "None"
        
        roles = settings.get('roles', [0, 0, 0])
        reacts = settings.get('reacts', [])
        
        text = "**📨 DM Request Settings**\n"
        text += f"**Active Channels:** {chan_text}\n"
        text += f"**Roles:** <@&{roles[0]}>, <@&{roles[1]}>, <@&{roles[2]}>\n"
        text += f"**Reacts:** {reacts[0]} {reacts[1]}\n"
        
        await ctx.send(text)

    # --- VOTE KICK COMMANDS ---
    
    @app_commands.command(name="vote", description="Vote to kick a user", extras={'public': True})
    @app_commands.describe(member="The member to vote kick")
    async def vote(self, interaction: discord.Interaction, member: discord.Member):
        data = self.get_vote_data(interaction.guild.id)
        voting_role_id = data['voting_role_id']
        active_votes = data['active_votes'] # {target_id_str: [list of voters]}

        if voting_role_id is None:
            return await interaction.response.send_message("❌ The voting role has not been set yet. An admin must use `?voterole` first.", ephemeral=True)

        user_role_ids = [r.id for r in interaction.user.roles]
        if voting_role_id not in user_role_ids and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You do not have the required role to vote.", ephemeral=True)

        if member == interaction.user: return await interaction.response.send_message("❌ You cannot vote to kick yourself.", ephemeral=True)
        if member.bot: return await interaction.response.send_message("❌ You cannot vote to kick a bot.", ephemeral=True)

        target_id_str = str(member.id)
        
        if target_id_str not in active_votes: 
            active_votes[target_id_str] = []
        
        if interaction.user.id in active_votes[target_id_str]:
            return await interaction.response.send_message(f"⚠️ You have already voted to kick {member.display_name}.", ephemeral=True)

        active_votes[target_id_str].append(interaction.user.id)
        data['active_votes'] = active_votes
        self.save_vote_data(interaction.guild.id, data)
        
        current_votes = len(active_votes[target_id_str])
        
        embed = discord.Embed(description=f"🗳️ **Vote Cast**\n{interaction.user.mention} voted to kick {member.mention}.\nCurrent Votes: **{current_votes}/{self.VOTE_THRESHOLD}**", color=discord.Color.yellow(), timestamp=datetime.datetime.now())
        await self.log_to_channel(interaction.guild, embed)

        if current_votes >= self.VOTE_THRESHOLD:
            try:
                await member.kick(reason=f"Votekicked by {current_votes} users.")
                embed = discord.Embed(description=f"✅ **VOTEKICK SUCCESS**\n{member.mention} was kicked.\nTotal Votes: {current_votes}", color=discord.Color.green(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
                await interaction.response.send_message(f"✅ {member.mention} has been kicked by vote.", ephemeral=False) 
                
                # Cleanup and Save
                if target_id_str in active_votes: del active_votes[target_id_str]
                data['active_votes'] = active_votes
                self.save_vote_data(interaction.guild.id, data)

            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Vote threshold reached, but I do not have permission to kick this user.", ephemeral=True)
                embed = discord.Embed(description=f"❌ **VOTEKICK FAILED**\nTried to kick {member.mention} but lacked permissions.", color=discord.Color.red(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"✅ Vote cast! {member.display_name} has {current_votes}/{self.VOTE_THRESHOLD} votes.", ephemeral=True)

    @commands.command(name="voterole")
    @commands.has_permissions(administrator=True)
    async def voterole(self, ctx, role: discord.Role):
        """Set the role allowed to vote."""
        data = self.get_vote_data(ctx.guild.id)
        data['voting_role_id'] = role.id
        self.save_vote_data(ctx.guild.id, data)

        embed = discord.Embed(description=f"**Vote Role Updated**\nNew Role: {role.mention}\nSet By: {ctx.author.mention}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
        await self.log_to_channel(ctx.guild, embed)
        await ctx.send(f"✅ Voting role set to {role.mention}.")

    @commands.command(name="voteremove")
    @commands.has_permissions(administrator=True)
    async def voteremove(self, ctx, member: discord.Member):
        """Remove an active vote against a user (buggy only)."""
        data = self.get_vote_data(ctx.guild.id)
        target_id_str = str(member.id)
        
        if target_id_str in data['active_votes']:
            del data['active_votes'][target_id_str]
            self.save_vote_data(ctx.guild.id, data)
            
            embed = discord.Embed(description=f"**Vote Cancelled**\nVotes against {member.mention} were cleared by {ctx.author.mention}.", color=discord.Color.orange(), timestamp=datetime.datetime.now())
            await self.log_to_channel(ctx.guild, embed)
            await ctx.send(f"✅ Cleared all votes against {member.display_name}.")
        else:
            await ctx.send(f"⚠️ There are no active votes against {member.display_name}.")

    @app_commands.command(name="vote-list", description="List active vote kicks", extras={'public': True})
    async def vote_list(self, interaction: discord.Interaction):
        data = self.get_vote_data(interaction.guild.id)
        active_votes = data.get('active_votes', {})

        if not active_votes: 
            return await interaction.response.send_message("No active votes.", ephemeral=True)

        description = ""
        for target_id_str, voters in active_votes.items():
            try: target_id = int(target_id_str)
            except: continue
            
            member = interaction.guild.get_member(target_id)
            name = member.display_name if member else f"ID: {target_id}"
            description += f"**{name}**: {len(voters)}/{self.VOTE_THRESHOLD} votes\n"

        embed = discord.Embed(title="🗳️ Active Vote Kicks", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- VC PING (New Prefix Group) ---

    @commands.group(name="vcping", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def vcping(self, ctx):
        """Manage VC Ping settings."""
        await ctx.send("Commands: `set`, `ignore`")

    @vcping.group(name="ignore", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def vcping_ignore(self, ctx):
        await ctx.send("Commands: `add`, `remove`, `list`")

    @vcping_ignore.command(name="add")
    @commands.has_permissions(administrator=True)
    async def vcping_ignore_add(self, ctx, channel: discord.VoiceChannel):
        guild_id = str(ctx.guild.id)
        config = self.get_vcping_config()
        if guild_id not in config: config[guild_id] = {'ignored': [], 'role': None, 'people': 2, 'minutes': 5}
        
        if channel.id not in config[guild_id]['ignored']:
            config[guild_id]['ignored'].append(channel.id)
            self.save_vcping_config(config)
            await ctx.send(f"✅ Added {channel.mention} to the ignore list.")
        else:
            await ctx.send(f"⚠️ {channel.mention} is already ignored.")

    @vcping_ignore.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def vcping_ignore_remove(self, ctx, channel: discord.VoiceChannel):
        guild_id = str(ctx.guild.id)
        config = self.get_vcping_config()
        if guild_id in config and channel.id in config[guild_id]['ignored']:
            config[guild_id]['ignored'].remove(channel.id)
            self.save_vcping_config(config)
            await ctx.send(f"✅ Removed {channel.mention} from the ignore list.")
        else:
            await ctx.send(f"⚠️ {channel.mention} is not in the ignore list.")

    @vcping_ignore.command(name="list")
    @commands.has_permissions(administrator=True)
    async def vcping_ignore_list(self, ctx):
        guild_id = str(ctx.guild.id)
        config = self.get_vcping_config()
        if guild_id in config and config[guild_id]['ignored']:
            channels = [f"<#{cid}>" for cid in config[guild_id]['ignored']]
            await ctx.send(f"Ignored VCs: {', '.join(channels)}")
        else:
            await ctx.send("No VCs are currently ignored.")

    @vcping.command(name="set")
    @commands.has_permissions(administrator=True)
    async def vcping_set(self, ctx, role: discord.Role, people: int, minutes: int):
        guild_id = str(ctx.guild.id)
        config = self.get_vcping_config()
        if guild_id not in config: config[guild_id] = {'ignored': []}
        config[guild_id].update({'role': role.id, 'people': people, 'minutes': minutes})
        self.save_vcping_config(config)
        await ctx.send(f"✅ Settings updated: Ping {role.mention} when {people} people are in a VC for {minutes} minutes.")

    @tasks.loop(seconds=60)
    async def check_vcs(self):
        config = self.get_vcping_config()
        for guild_id, state_data in self.vc_state.items():
            if guild_id not in config: continue
            
            settings = config[guild_id]
            threshold_minutes = settings.get('minutes', 5)
            ping_role_id = settings.get('role')

            if not ping_role_id: continue

            guild = self.bot.get_guild(int(guild_id))
            if not guild: continue

            role = guild.get_role(ping_role_id)
            if not role: continue

            for channel_id, data in state_data.items():
                if data.get('pinged'): continue

                start_time_iso = data.get('start_time')
                if not start_time_iso: continue

                start_time = datetime.datetime.fromisoformat(start_time_iso)
                if datetime.datetime.now() - start_time >= datetime.timedelta(minutes=threshold_minutes):
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        try:
                            await channel.send(f"{role.mention} The VC has been active for {threshold_minutes} minutes!")
                            self.vc_state[guild_id][channel_id]['pinged'] = True
                        except Exception as e: print(f"Failed to send VC ping in {channel.name}: {e}")

    @check_vcs.before_loop
    async def before_check_vcs(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return

        guild_id = str(member.guild.id)
        config = self.get_vcping_config()
        if guild_id not in config: return

        settings = config[guild_id]
        threshold_people = settings.get('people', 2)
        ignored_vcs = settings.get('ignored', [])

        def update_channel_state(channel):
            if not channel or channel.id in ignored_vcs: return

            cid = str(channel.id)
            if guild_id not in self.vc_state: self.vc_state[guild_id] = {}

            current_members = len(channel.members)

            if current_members == 0:
                if cid in self.vc_state[guild_id]: del self.vc_state[guild_id][cid]
                return

            if current_members >= threshold_people:
                if cid not in self.vc_state[guild_id]:
                    self.vc_state[guild_id][cid] = {'start_time': datetime.datetime.now().isoformat(), 'pinged': False}
            else:
                if cid in self.vc_state[guild_id]:
                    if not self.vc_state[guild_id][cid]['pinged']:
                         del self.vc_state[guild_id][cid]

        if before.channel: update_channel_state(before.channel)
        if after.channel and (not before.channel or before.channel.id != after.channel.id): update_channel_state(after.channel)

async def setup(bot):
    await bot.add_cog(Admin(bot))
