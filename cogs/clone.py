import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import re

class Clone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- HELPERS ---
    def get_clone_setups(self):
        """Returns the list of all clone setups."""
        return self.bot.db.get_collection("clone_setups")

    def save_clone_setups(self, setups):
        """Saves the list of setups."""
        self.bot.db.save_collection("clone_setups", setups)

    def get_history(self):
        """Returns the mapping history (Source MSG -> Clone MSG)."""
        return self.bot.db.get_collection("clone_history")
    
    def save_history(self, history):
        self.bot.db.save_collection("clone_history", history)

    async def get_webhook(self, channel):
        """Finds or creates a webhook for the bot in the channel."""
        if not isinstance(channel, discord.TextChannel):
            return None
            
        webhooks = await channel.webhooks()
        for wh in webhooks:
            # We reuse our own webhook if found
            if wh.user == self.bot.user or wh.name == "BuggyClone":
                return wh
        return await channel.create_webhook(name="BuggyClone")

    async def resolve_mentions(self, content, guild):
        """
        Replaces user mentions with their display name (non-pinging text).
        If user is not in guild, fetches their name to display.
        """
        if not content: return content

        # Regex to find <@123456789> or <@!123456789>
        mention_pattern = re.compile(r'<@!?(\d+)>')
        
        # Helper to find name for a specific match
        async def get_name(match):
            user_id = int(match.group(1))
            member = guild.get_member(user_id)
            if member:
                return f"**@{member.display_name}**"
            else:
                # Try fetching user if not in cache
                try:
                    user = await self.bot.fetch_user(user_id)
                    return f"**@{user.display_name}**"
                except:
                    return "**@UnknownUser**"

        # We iterate matches and replace them
        # Note: We replace one by one. For a large message with many pings this is okay.
        new_content = content
        matches = list(mention_pattern.finditer(content))
        
        # Iterate backwards to replace without affecting indices
        for m in reversed(matches):
            replacement = await get_name(m)
            start, end = m.span()
            new_content = new_content[:start] + replacement + new_content[end:]
            
        return new_content

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # 1. Check if this is a Reply in a Receiving Channel (Return Replies)
        # This allows users in the receiving channel to talk back
        await self.handle_return_reply(message)

        # 2. Check if this message needs to be CLONED (Source -> Receiver)
        await self.handle_cloning(message)

    async def handle_cloning(self, message):
        setups = self.get_clone_setups()
        if not setups: return

        # We need to find setups where this message's channel (or category) is the source
        applicable_setups = []
        for s in setups:
            is_source = False
            
            # Direct Channel Match
            if s['source_id'] == message.channel.id:
                is_source = True
            
            # Category Match
            elif message.channel.category and s['source_id'] == message.channel.category.id:
                is_source = True
            
            if is_source:
                # Check Ignore List (Channels to skip within a category)
                if message.channel.id in s.get('ignore_channels', []):
                    continue
                
                # Check Attachments Only
                if s.get('attachments_only', False) and not message.attachments:
                    continue
                
                # Check Reaction Threshold 
                # If > 0, we skip cloning NOW. It will be handled in on_raw_reaction_add
                if s.get('min_reactions', 0) > 0:
                    continue

                applicable_setups.append(s)

        for s in applicable_setups:
            await self.execute_clone(message, s)

    async def execute_clone(self, message, setup):
        """Performs the actual webhook cloning."""
        receiver = self.bot.get_channel(setup['receive_id'])
        if not receiver: return

        # 1. Prepare Content & Resolve Mentions
        content = message.content
        content = await self.resolve_mentions(content, receiver.guild)
        
        # 2. Handle Attachments (Convert to Links)
        attachment_urls = []
        if message.attachments:
            attachment_urls = [a.url for a in message.attachments]
        
        # Append URLs to content. 
        # Discord auto-embeds URLs at the bottom if they are clean links.
        final_content = content
        if attachment_urls:
            if final_content:
                final_content += "\n" + "\n".join(attachment_urls)
            else:
                final_content = "\n".join(attachment_urls)

        # 3. Handle Embeds
        # Filter logic:
        # We only preserve 'rich' embeds (manually created embeds, e.g. from bots).
        # We intentionally DROP 'video', 'gifv', 'image', 'link' embeds.
        # Why? Because these are auto-generated by Discord from URLs. 
        # Since we are sending the URLs in the 'content' field, Discord will 
        # automatically re-generate the full, native preview (Large GIF, Video Player, etc.).
        # If we manually send the captured embed object, Discord often renders it 
        # as a small thumbnail or static preview instead of the interactive media.
        clean_embeds = []
        if message.embeds:
            clean_embeds = [e for e in message.embeds if e.type == 'rich']

        if not final_content and not clean_embeds:
            return # Nothing to send

        webhook = await self.get_webhook(receiver)
        if not webhook: return # Could not create webhook
        
        try:
            # Send via Webhook to impersonate
            # allowed_mentions=discord.AllowedMentions.none() prevents any lingering pings
            cloned_msg = await webhook.send(
                content=final_content,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                embeds=clean_embeds,
                wait=True,
                allowed_mentions=discord.AllowedMentions.none()
            )
            
            # Save History for deletions and replies
            history = self.get_history()
            history.append({
                "source_msg_id": message.id,
                "clone_msg_id": cloned_msg.id,
                "source_channel_id": message.channel.id,
                "receive_channel_id": receiver.id
            })
            self.save_history(history)
            
        except Exception as e:
            print(f"Failed to clone message: {e}")

    async def handle_return_reply(self, message):
        """Handles replies in the receiving channel sent back to source."""
        if not message.reference: return

        history = self.get_history()
        # Find the entry where clone_msg_id == reference.message_id
        entry = next((h for h in history if h['clone_msg_id'] == message.reference.message_id), None)
        
        if not entry: return

        # Found the link! Check if the setup allows replies
        setups = self.get_clone_setups()
        
        # We need to find the setup that links these two channels
        relevant_setup = None
        for s in setups:
            if s['receive_id'] == entry['receive_channel_id']:
                # Does this setup cover the source channel?
                source_chan = self.bot.get_channel(entry['source_channel_id'])
                if not source_chan: continue
                
                # Check if this setup matches the source channel ID or its category
                if s['source_id'] == source_chan.id or (source_chan.category and s['source_id'] == source_chan.category.id):
                     relevant_setup = s
                     break
        
        if relevant_setup and relevant_setup.get('return_replies', False):
            source_chan = self.bot.get_channel(entry['source_channel_id'])
            if source_chan:
                # Send the reply as the Bot (Webhooks can't reply to specific messages easily)
                nick = message.author.display_name
                content = f"**{nick}**: {message.content}"
                if message.attachments:
                     content += "\n" + "\n".join([a.url for a in message.attachments])

                try:
                    # Reply to the original source message if possible
                    try:
                        orig_msg = await source_chan.fetch_message(entry['source_msg_id'])
                        await orig_msg.reply(content, mention_author=False)
                    except discord.NotFound:
                        # Original message deleted, just send to channel
                        await source_chan.send(content)
                except Exception as e:
                    print(f"Failed to return reply: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles delayed cloning based on reaction thresholds."""
        if payload.member and payload.member.bot: return

        # Check if message is in a Source Channel that requires reactions
        setups = self.get_clone_setups()
        channel = self.bot.get_channel(payload.channel_id)
        if not channel: return

        msg_id = payload.message_id
        
        # Check history to ensure we haven't cloned it yet
        history = self.get_history()
        if any(h['source_msg_id'] == msg_id for h in history):
            return # Already cloned

        # Find applicable setup
        for s in setups:
            min_reacts = s.get('min_reactions', 0)
            if min_reacts <= 0: continue

            # Is this the source?
            is_source = (s['source_id'] == channel.id) or (channel.category and s['source_id'] == channel.category.id)
            if is_source:
                if channel.id in s.get('ignore_channels', []): continue
                if s.get('attachments_only', False):
                    # We'd need to fetch message to check attachments, which we do below
                    pass

                # Fetch message to count reactions
                try:
                    message = await channel.fetch_message(msg_id)
                    
                    # Double check attachments if required
                    if s.get('attachments_only', False) and not message.attachments:
                        continue

                    # Count total reactions
                    total = sum(r.count for r in message.reactions)
                    
                    if total >= min_reacts:
                        await self.execute_clone(message, s)
                        # We stop after one clone to prevent duplicate messages if multiple setups match
                        # (Though prompt said no channel should receive from multiple setups, so this is safe)
                        break
                except:
                    pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Deletes the cloned message if the source is deleted."""
        history = self.get_history()
        # Find entries where this message is the SOURCE
        entries = [h for h in history if h['source_msg_id'] == message.id]
        
        if entries:
            # Remove from history DB
            new_history = [h for h in history if h['source_msg_id'] != message.id]
            self.save_history(new_history)
            
            # Delete the clones
            for entry in entries:
                receiver = self.bot.get_channel(entry['receive_channel_id'])
                if receiver:
                    try:
                        clone = await receiver.fetch_message(entry['clone_msg_id'])
                        await clone.delete()
                    except: pass

    # --- COMMANDS ---

    clone_group = app_commands.Group(name="clone", description="Manage message cloning setups")

    @clone_group.command(name="add", description="Add a new clone setup")
    @app_commands.describe(
        receive_channel="Channel where messages will be cloned TO",
        source_id="Channel or Category ID to clone FROM",
        ignore_channel="Optional: Channel to ignore (if source is category)",
        attachments_only="Only clone messages with attachments?",
        return_replies="Allow replies in receiving channel to be sent back?",
        min_reactions="Minimum reactions required to clone (0 for instant)"
    )
    async def clone_add(self, interaction: discord.Interaction, 
                        receive_channel: discord.TextChannel, 
                        source_id: str, 
                        ignore_channel: discord.TextChannel = None,
                        attachments_only: bool = False,
                        return_replies: bool = False,
                        min_reactions: int = 0):
        
        # Clean ID (Source can be channel or category, so we take string and parse)
        try:
            s_id = int(source_id)
        except:
            return await interaction.response.send_message("❌ Source ID must be a valid number.", ephemeral=True)

        setups = self.get_clone_setups()
        
        # Create new setup object
        new_setup = {
            "receive_id": receive_channel.id,
            "source_id": s_id,
            "ignore_channels": [ignore_channel.id] if ignore_channel else [],
            "attachments_only": attachments_only,
            "return_replies": return_replies,
            "min_reactions": min_reactions
        }
        
        # Check duplicates (Receiver + Source combo)
        for s in setups:
            if s['receive_id'] == receive_channel.id and s['source_id'] == s_id:
                return await interaction.response.send_message("❌ A setup for this Receiver and Source already exists. Use `/clone edit`.", ephemeral=True)

        setups.append(new_setup)
        self.save_clone_setups(setups)
        
        await interaction.response.send_message(f"✅ Setup added! Cloning from `{s_id}` to {receive_channel.mention}.", ephemeral=True)

    @clone_group.command(name="edit", description="Edit ALL clone setups for a receiving channel")
    async def clone_edit(self, interaction: discord.Interaction,
                         receive_channel: discord.TextChannel,
                         ignore_channel: discord.TextChannel = None,
                         attachments_only: bool = None,
                         return_replies: bool = None,
                         min_reactions: int = None):
        
        setups = self.get_clone_setups()
        updated_count = 0
        
        for s in setups:
            if s['receive_id'] == receive_channel.id:
                # Update provided fields for ALL setups for this receiver
                if ignore_channel: 
                    current_ignores = s.get('ignore_channels', [])
                    if ignore_channel.id not in current_ignores:
                        current_ignores.append(ignore_channel.id)
                    s['ignore_channels'] = current_ignores
                
                if attachments_only is not None: s['attachments_only'] = attachments_only
                if return_replies is not None: s['return_replies'] = return_replies
                if min_reactions is not None: s['min_reactions'] = min_reactions
                updated_count += 1
        
        if updated_count > 0:
            self.save_clone_setups(setups)
            await interaction.response.send_message(f"✅ Updated {updated_count} setups for {receive_channel.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ No setups found for {receive_channel.mention}.", ephemeral=True)

    @clone_group.command(name="remove", description="Remove a clone setup")
    async def clone_remove(self, interaction: discord.Interaction, receive_channel: discord.TextChannel, source_id: str):
        try: s_id = int(source_id)
        except: return await interaction.response.send_message("❌ ID invalid.", ephemeral=True)

        setups = self.get_clone_setups()
        initial_len = len(setups)
        
        # Remove matching setup
        setups = [s for s in setups if not (s['receive_id'] == receive_channel.id and s['source_id'] == s_id)]
        
        if len(setups) < initial_len:
            self.save_clone_setups(setups)
            await interaction.response.send_message(f"✅ Removed setup for {receive_channel.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ No matching setup found.", ephemeral=True)

    @clone_group.command(name="list", description="List all clone setups for this server")
    async def clone_list(self, interaction: discord.Interaction):
        setups = self.get_clone_setups()
        if not setups:
            return await interaction.response.send_message("📝 No clone setups active.", ephemeral=True)

        # 1. Map current guild channels for fast local lookup
        current_guild_map = {c.id: c.name for c in interaction.guild.channels}

        # 2. Filter setups: Only show if receiver is in this guild
        filtered_setups = [s for s in setups if s['receive_id'] in current_guild_map]

        if not filtered_setups:
            return await interaction.response.send_message("📝 No clone setups found for this server.", ephemeral=True)

        # 3. Group by Receiver
        grouped = {}
        for s in filtered_setups:
            rid = s['receive_id']
            if rid not in grouped: grouped[rid] = []
            grouped[rid].append(s)

        text = "**🐏 Clone Setups (This Server):**\n"
        
        for rid, source_list in grouped.items():
            # Receiver is guaranteed to be in current_guild_map
            r_name = current_guild_map[rid]
            
            text += f"\n📂 **Receiver: {r_name}**\n"
            for s in source_list:
                # Resolve Source Name
                sid = s['source_id']
                if sid in current_guild_map:
                    # It's a local channel
                    s_name = current_guild_map[sid]
                else:
                    # It's an external/deleted channel. Check global cache.
                    global_chan = self.bot.get_channel(sid)
                    s_name = global_chan.name if global_chan else f"ID:{sid}"
                
                # Format flags
                flags = []
                if s.get('attachments_only'): flags.append("🖼️ MediaOnly")
                if s.get('return_replies'): flags.append("↩️ Replies")
                if s.get('min_reactions', 0) > 0: flags.append(f"⭐ {s['min_reactions']}+ Reacts")
                
                # Format Ignores
                ignores = s.get('ignore_channels', [])
                ignore_text = ""
                if ignores:
                    ign_names = []
                    for iid in ignores:
                        # Resolve ignore channel names (try local, then global)
                        if iid in current_guild_map:
                            i_name = current_guild_map[iid]
                        else:
                            gc = self.bot.get_channel(iid)
                            i_name = gc.name if gc else str(iid)
                        ign_names.append(i_name)
                    ignore_text = f" | 🚫 Excluding: {', '.join(ign_names)}"

                flag_text = f" ({', '.join(flags)})" if flags else ""
                text += f" - Source: **{s_name}**{flag_text}{ignore_text}\n"

        await interaction.response.send_message(text[:2000], ephemeral=True)

async def setup(bot):
    await bot.add_cog(Clone(bot))
