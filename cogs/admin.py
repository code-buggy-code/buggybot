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
# - load_config()
# - save_config()
# - cog_unload()
# - get_stickies()
# - save_stickies(stickies)
# - get_sticky_settings()
# - save_sticky_settings(settings)
# - get_log_settings()
# - save_log_settings(settings)
# - get_dm_settings(guild_id)
# - save_dm_settings(guild_id, data)
# - dm_message_index_autocomplete(interaction, current)
# - log_to_channel(guild, embed)
# - on_message(message)
# - on_raw_reaction_add(payload)
# - on_message_delete(message)
# - on_message_edit(before, after)
# - on_member_remove(member)
# - handle_sticky(message)
# - handle_dm_request(message)
# - stick(interaction, message)
# - unstick(interaction)
# - stickylist(interaction)
# - stickytime(interaction, timing, number, unit)
# - setlogchannel(interaction, channel)
# - dmset(interaction)
# - dmunset(interaction)
# - dmreq (Group)
#   - roles(interaction, role1, role2, role3)
#   - reacts(interaction, accept, deny)
#   - message(interaction, index, message)
#   - listmessages(interaction)
#   - list(interaction)
# - vote(interaction, member) [/vote]
# - vote_role(interaction, role) [/vote-role]
# - vote_remove(interaction, member) [/vote-remove]
# - vote_list(interaction) [/vote-list]
# - vcping_group (Group)
# - vcping_ignore_group (Group)
# - vcping_ignore_add(interaction, channel)
# - vcping_ignore_remove(interaction, channel)
# - vcping_ignore_list(interaction)
# - vcping_set(interaction, role, people, minutes)
# - check_vcs()
# - before_check_vcs()
# - on_voice_state_update(member, before, after)
# setup(bot)

BUGGY_ID = 1433003746719170560

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "logging, dm requests, stickies, vc pings, and kick voting"
        
        # VC Ping Config
        self.config_file = 'vcping_config.json'
        self.config = self.load_config()
        self.vc_state = {}
        self.check_vcs.start()

        # Vote Kick Config
        self.voting_role_id = None
        self.active_votes = {} # {target_id: set(voter_id)}
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

    def load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f)
        return {}

    def save_config(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)

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

    async def dm_message_index_autocomplete(self, interaction: discord.Interaction, current: str):
        indices = ["0", "1", "2", "3", "4", "5"]
        return [
            app_commands.Choice(name=index, value=index)
            for index in indices if current in index
        ]
    
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

    # --- COMMANDS ---

    @app_commands.command(name="stick", description="Stick a message to the bottom of this channel.")
    @app_commands.describe(message="The message to sticky")
    async def stick(self, interaction: discord.Interaction, message: str):
        content = message.replace("\\n", "\n")
        stickies = self.get_stickies()
        stickies = [s for s in stickies if s['channel_id'] != interaction.channel.id]

        new_sticky = {
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild.id,
            "content": content,
            "last_message_id": None,
            "last_posted_at": datetime.datetime.now().timestamp()
        }

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

        if target.get('last_message_id'):
            try:
                msg = await interaction.channel.fetch_message(target['last_message_id'])
                await msg.delete()
            except: pass

        stickies = [s for s in stickies if s['channel_id'] != interaction.channel.id]
        self.save_stickies(stickies)
        await interaction.response.send_message("✅ Sticky message removed.", ephemeral=True)

    @app_commands.command(name="stickylist", description="List all active sticky messages in this server.")
    async def stickylist(self, interaction: discord.Interaction):
        stickies = self.get_stickies()
        current_guild_stickies = [s for s in stickies if s.get('guild_id') == interaction.guild.id]

        if not current_guild_stickies:
            return await interaction.response.send_message("📝 No sticky messages found for this server.", ephemeral=True)

        text = "**📌 Active Sticky Messages:**\n"
        for s in current_guild_stickies:
            channel = interaction.guild.get_channel(s['channel_id'])
            chan_mention = channel.mention if channel else f"ID:{s['channel_id']} (Deleted)"
            content_preview = s['content'].replace("\n", " ")
            if len(content_preview) > 50: content_preview = content_preview[:47] + "..."
            text += f"• {chan_mention}: {content_preview}\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="stickytime", description="Configure server-wide sticky message timing.")
    @app_commands.describe(timing="Mode: 'Before' (Cooldown) or 'After' (Delay)", number="Number of seconds/minutes", unit="Time unit")
    @app_commands.choices(timing=[app_commands.Choice(name="Before (Cooldown)", value="before"), app_commands.Choice(name="After (Delay)", value="after")])
    @app_commands.choices(unit=[app_commands.Choice(name="Seconds", value="seconds"), app_commands.Choice(name="Minutes", value="minutes")])
    async def stickytime(self, interaction: discord.Interaction, timing: app_commands.Choice[str], number: int, unit: app_commands.Choice[str]):
        settings = self.get_sticky_settings()
        multiplier = 60 if unit.value == "minutes" else 1
        total_seconds = number * multiplier
        
        settings = [s for s in settings if s['guild_id'] != interaction.guild.id]
        settings.append({"guild_id": interaction.guild.id, "delay": total_seconds, "mode": timing.value})
        self.save_sticky_settings(settings)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing.value == "before" else "Delay (After)"
        await interaction.response.send_message(f"✅ Sticky settings updated.\nMode: **{mode_text}**\nTime: **{delay_text}**", ephemeral=True)

    @app_commands.command(name="setlogchannel", description="Set the channel where server logs (Deletes, Edits, Leaves) will be sent.")
    @app_commands.describe(channel="The channel to send logs to")
    async def setlogchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        settings = self.get_log_settings()
        settings = [s for s in settings if s['guild_id'] != interaction.guild.id]
        
        settings.append({"guild_id": interaction.guild.id, "log_channel_id": channel.id})
        self.save_log_settings(settings)
        await interaction.response.send_message(f"✅ Logging channel set to {channel.mention}.\nI will now log:\n- Message Deletions\n- Message Edits\n- Member Leaves\n- Votekick Results", ephemeral=True)

    # --- DM REQUEST COMMANDS ---
    dm_group = app_commands.Group(name="dmreq", description="Manage DM Request settings")

    @app_commands.command(name="dmset", description="Set THIS channel as a DM Request channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def dmset(self, interaction: discord.Interaction):
        settings = self.get_dm_settings(interaction.guild_id)
        
        if interaction.channel_id in settings['channels']:
            return await interaction.response.send_message("⚠️ This channel is already set for DM Requests.", ephemeral=True)
        
        settings['channels'].append(interaction.channel_id)
        self.save_dm_settings(interaction.guild_id, settings)
        await interaction.response.send_message(f"✅ <#{interaction.channel_id}> is now a DM Request channel.", ephemeral=True)

    @app_commands.command(name="dmunset", description="Remove THIS channel from DM Request channels.")
    @app_commands.checks.has_permissions(administrator=True)
    async def dmunset(self, interaction: discord.Interaction):
        settings = self.get_dm_settings(interaction.guild_id)
        
        if interaction.channel_id not in settings['channels']:
            return await interaction.response.send_message("⚠️ This channel is not a DM Request channel.", ephemeral=True)
        
        settings['channels'].remove(interaction.channel_id)
        self.save_dm_settings(interaction.guild_id, settings)
        await interaction.response.send_message(f"✅ Removed <#{interaction.channel_id}> from DM Request channels.", ephemeral=True)

    @dm_group.command(name="roles", description="Set the 3 DM roles (Role 1: Open, Role 2: Ask, Role 3: Closed).")
    @app_commands.describe(role1="Role 1 (Adds Reactions)", role2="Role 2 (Custom Msg 3)", role3="Role 3 (Custom Msg 4)")
    @app_commands.checks.has_permissions(administrator=True)
    async def dmroles(self, interaction: discord.Interaction, role1: discord.Role, role2: discord.Role, role3: discord.Role):
        settings = self.get_dm_settings(interaction.guild_id)
        settings['roles'] = [role1.id, role2.id, role3.id]
        self.save_dm_settings(interaction.guild_id, settings)
        
        await interaction.response.send_message(f"✅ **DM Roles Set:**\n1. {role1.mention} (Triggers Reactions)\n2. {role2.mention} (Triggers Msg 3)\n3. {role3.mention} (Triggers Msg 4)", ephemeral=True)

    @dm_group.command(name="reacts", description="Set the Accept/Deny emojis.")
    @app_commands.checks.has_permissions(administrator=True)
    async def dmreacts(self, interaction: discord.Interaction, accept: str, deny: str):
        settings = self.get_dm_settings(interaction.guild_id)
        settings['reacts'] = [accept, deny]
        self.save_dm_settings(interaction.guild_id, settings)
        
        await interaction.response.send_message(f"✅ **DM Reacts Set:** {accept} (Accept) and {deny} (Deny)", ephemeral=True)

    @dm_group.command(name="message", description="Set a custom DM system message.")
    @app_commands.autocomplete(index=dm_message_index_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def setdmmessage(self, interaction: discord.Interaction, index: str, message: str):
        if index not in ["0", "1", "2", "3", "4", "5"]:
            return await interaction.response.send_message("❌ Index must be 0-5.", ephemeral=True)
        
        settings = self.get_dm_settings(interaction.guild_id)
        settings['messages'][index] = message
        self.save_dm_settings(interaction.guild_id, settings)
        
        await interaction.response.send_message(f"✅ **Message {index} Updated.**\nPreview: `{message}`", ephemeral=True)

    @dm_group.command(name="listmessages", description="List all configured messages.")
    async def listdmmessages(self, interaction: discord.Interaction):
        settings = self.get_dm_settings(interaction.guild_id)
        text = "**📨 Current DM Messages:**\n"
        for i in range(6):
            key = str(i)
            msg = settings['messages'].get(key, "Not set")
            text += f"**[{key}]:** {msg}\n"
        await interaction.response.send_message(text, ephemeral=True)

    @dm_group.command(name="list", description="List active DM Request channels and settings.")
    async def listdmreq(self, interaction: discord.Interaction):
        settings = self.get_dm_settings(interaction.guild_id)
        
        channels = settings.get('channels', [])
        chan_text = " ".join([f"<#{c}>" for c in channels]) if channels else "None"
        
        roles = settings.get('roles', [0, 0, 0])
        reacts = settings.get('reacts', [])
        
        text = "**📨 DM Request Settings**\n"
        text += f"**Active Channels:** {chan_text}\n"
        text += f"**Roles:** <@&{roles[0]}>, <@&{roles[1]}>, <@&{roles[2]}>\n"
        text += f"**Reacts:** {reacts[0]} {reacts[1]}\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    # --- VOTE KICK COMMANDS ---
    
    @app_commands.command(name="vote", description="Vote to kick a user", extras={'public': True})
    @app_commands.describe(member="The member to vote kick")
    async def vote(self, interaction: discord.Interaction, member: discord.Member):
        if self.voting_role_id is None:
            return await interaction.response.send_message("❌ The voting role has not been set yet. An admin must use `/vote-role` first.", ephemeral=True)

        user_role_ids = [r.id for r in interaction.user.roles]
        if self.voting_role_id not in user_role_ids and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You do not have the required role to vote.", ephemeral=True)

        if member == interaction.user: return await interaction.response.send_message("❌ You cannot vote to kick yourself.", ephemeral=True)
        if member.bot: return await interaction.response.send_message("❌ You cannot vote to kick a bot.", ephemeral=True)

        if member.id not in self.active_votes: self.active_votes[member.id] = set()
        if interaction.user.id in self.active_votes[member.id]:
            return await interaction.response.send_message(f"⚠️ You have already voted to kick {member.display_name}.", ephemeral=True)

        self.active_votes[member.id].add(interaction.user.id)
        current_votes = len(self.active_votes[member.id])
        
        embed = discord.Embed(description=f"🗳️ **Vote Cast**\n{interaction.user.mention} voted to kick {member.mention}.\nCurrent Votes: **{current_votes}/{self.VOTE_THRESHOLD}**", color=discord.Color.yellow(), timestamp=datetime.datetime.now())
        await self.log_to_channel(interaction.guild, embed)

        if current_votes >= self.VOTE_THRESHOLD:
            try:
                await member.kick(reason=f"Votekicked by {current_votes} users.")
                embed = discord.Embed(description=f"✅ **VOTEKICK SUCCESS**\n{member.mention} was kicked.\nTotal Votes: {current_votes}", color=discord.Color.green(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
                await interaction.response.send_message(f"✅ {member.mention} has been kicked by vote.", ephemeral=False) 
                if member.id in self.active_votes: del self.active_votes[member.id]
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Vote threshold reached, but I do not have permission to kick this user.", ephemeral=True)
                embed = discord.Embed(description=f"❌ **VOTEKICK FAILED**\nTried to kick {member.mention} but lacked permissions.", color=discord.Color.red(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"✅ Vote cast! {member.display_name} has {current_votes}/{self.VOTE_THRESHOLD} votes.", ephemeral=True)

    @app_commands.command(name="vote-role", description="Set the role allowed to vote")
    @app_commands.describe(role="The role that can use the votekick command")
    async def vote_role(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You need administrator permissions to set the voting role.", ephemeral=True)

        self.voting_role_id = role.id
        embed = discord.Embed(description=f"**Vote Role Updated**\nNew Role: {role.mention}\nSet By: {interaction.user.mention}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
        await self.log_to_channel(interaction.guild, embed)
        await interaction.response.send_message(f"✅ Voting role set to {role.mention}.", ephemeral=True)

    @app_commands.command(name="vote-remove", description="Remove an active vote against a user (buggy only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def vote_remove(self, interaction: discord.Interaction, member: discord.Member):
        if member.id in self.active_votes:
            del self.active_votes[member.id]
            embed = discord.Embed(description=f"**Vote Cancelled**\nVotes against {member.mention} were cleared by {interaction.user.mention}.", color=discord.Color.orange(), timestamp=datetime.datetime.now())
            await self.log_to_channel(interaction.guild, embed)
            await interaction.response.send_message(f"✅ Cleared all votes against {member.display_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ There are no active votes against {member.display_name}.", ephemeral=True)

    @app_commands.command(name="vote-list", description="List active vote kicks")
    async def vote_list(self, interaction: discord.Interaction):
        if not self.active_votes: return await interaction.response.send_message("No active votes.", ephemeral=True)

        description = ""
        for target_id, voters in self.active_votes.items():
            member = interaction.guild.get_member(target_id)
            name = member.display_name if member else f"ID: {target_id}"
            description += f"**{name}**: {len(voters)}/{self.VOTE_THRESHOLD} votes\n"

        embed = discord.Embed(title="🗳️ Active Vote Kicks", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- VC PING (New) ---

    vcping_group = app_commands.Group(name="vcping", description="Manage VC Ping settings")
    vcping_ignore_group = app_commands.Group(name="ignore", parent=vcping_group, description="Manage ignored Voice Channels")

    @vcping_ignore_group.command(name="add", description="Add a Voice Channel to the ignore list")
    @app_commands.describe(channel="The Voice Channel to ignore")
    async def vcping_ignore_add(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild_id = str(interaction.guild_id)
        if guild_id not in self.config: self.config[guild_id] = {'ignored': [], 'role': None, 'people': 2, 'minutes': 5}
        
        if channel.id not in self.config[guild_id]['ignored']:
            self.config[guild_id]['ignored'].append(channel.id)
            self.save_config()
            await interaction.response.send_message(f"Added {channel.mention} to the ignore list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{channel.mention} is already ignored.", ephemeral=True)

    @vcping_ignore_group.command(name="remove", description="Remove a Voice Channel from the ignore list")
    @app_commands.describe(channel="The Voice Channel to un-ignore")
    async def vcping_ignore_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild_id = str(interaction.guild_id)
        if guild_id in self.config and channel.id in self.config[guild_id]['ignored']:
            self.config[guild_id]['ignored'].remove(channel.id)
            self.save_config()
            await interaction.response.send_message(f"Removed {channel.mention} from the ignore list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{channel.mention} is not in the ignore list.", ephemeral=True)

    @vcping_ignore_group.command(name="list", description="List ignored Voice Channels")
    async def vcping_ignore_list(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        if guild_id in self.config and self.config[guild_id]['ignored']:
            channels = [f"<#{cid}>" for cid in self.config[guild_id]['ignored']]
            await interaction.response.send_message(f"Ignored VCs: {', '.join(channels)}", ephemeral=True)
        else:
            await interaction.response.send_message("No VCs are currently ignored.", ephemeral=True)

    @vcping_group.command(name="set", description="Set the VC ping settings")
    @app_commands.describe(role="The role to ping", people="Number of people required", minutes="Minutes to wait before pinging")
    async def vcping_set(self, interaction: discord.Interaction, role: discord.Role, people: int, minutes: int):
        guild_id = str(interaction.guild_id)
        if guild_id not in self.config: self.config[guild_id] = {'ignored': []}
        self.config[guild_id].update({'role': role.id, 'people': people, 'minutes': minutes})
        self.save_config()
        await interaction.response.send_message(f"Settings updated: Ping {role.mention} when {people} people are in a VC for {minutes} minutes.", ephemeral=True)

    @tasks.loop(seconds=60)
    async def check_vcs(self):
        for guild_id, state_data in self.vc_state.items():
            if guild_id not in self.config: continue
            
            settings = self.config[guild_id]
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
        if guild_id not in self.config: return

        settings = self.config[guild_id]
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
