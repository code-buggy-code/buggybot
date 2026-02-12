import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import re
from typing import Literal, Optional, Union, List, Tuple

# Function/Class List:
# class Clone(commands.Cog)
# - __init__(bot)
# - get_clone_setups()
# - save_clone_setups(setups)
# - get_history()
# - save_history(history)
# - get_webhook(channel)
# - resolve_mentions(content, guild)
# - _process_message_for_clone(message, guild_context)
# - on_message(message)
# - handle_cloning(message)
# - execute_clone(message, setup)
# - handle_return_reply(message)
# - on_raw_reaction_add(payload)
# - on_message_delete(message)
# - clone(interaction, action, receive_channel, source_id, min_reactions, attachments_only, return_replies)
# - postclone(interaction, source_id, destination_id)
# setup(bot)

class Clone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Channel mirroring and cloning system."

    # --- HELPERS ---
    def get_clone_setups(self):
        """Returns the list of all clone setups from the database."""
        return self.bot.db.get_collection("clone_setups")

    def save_clone_setups(self, setups):
        """Saves the list of setups to the database."""
        self.bot.db.save_collection("clone_setups", setups)

    def get_history(self):
        """Returns the mapping history (Source MSG -> Clone MSG) from the database."""
        return self.bot.db.get_collection("clone_history")
    
    def save_history(self, history):
        """Saves the mapping history to the database."""
        self.bot.db.save_collection("clone_history", history)

    async def get_webhook(self, channel):
        """Finds or creates a webhook for the bot in the channel (or parent if thread)."""
        target_channel = channel
        # Webhooks belong to the parent channel, not the thread itself
        if isinstance(channel, discord.Thread):
            target_channel = channel.parent
        
        # Ensure we are looking at a valid text-capable channel (Text, Voice, Stage, Forum)
        valid_types = (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)
        if not isinstance(target_channel, valid_types):
            return None
            
        webhooks = await target_channel.webhooks()
        for wh in webhooks:
            # We reuse our own webhook if found
            if wh.user == self.bot.user or wh.name == "BuggyClone":
                return wh
        return await target_channel.create_webhook(name="BuggyClone")

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

    async def _process_message_for_clone(self, message: discord.Message, guild_context: discord.Guild) -> List[dict]:
        """
        Takes a message and processes it into a list of payloads ready for webhook sending.
        Handles standard messages, attachments, and forwarded messages (snapshots).
        """
        payloads = []
        
        # Prepare a list of "sources" to process from this message
        # This handles standard messages AND forwarded messages (Snapshots)
        sources_to_process = []
        
        # Check for Forwarded Messages (Snapshots - discord.py 2.4+)
        snapshots = getattr(message, 'message_snapshots', [])
        if snapshots:
            sources_to_process.extend(snapshots)
        else:
            # No snapshots, just use the message itself
            sources_to_process.append(message)

        for src in sources_to_process:
            # Extract content
            content = src.content
            
            # Check for attachments/stickers
            src_attachments = getattr(src, 'attachments', [])
            media_links = [a.url for a in src_attachments]

            src_stickers = getattr(src, 'stickers', [])
            media_links.extend([s.url for s in src_stickers])

            if not content and not media_links:
                continue

            # Resolve mentions
            content = await self.resolve_mentions(content, guild_context)

            # Append links to content so they embed naturally
            if media_links:
                if content:
                    content += "\n" + "\n".join(media_links)
                else:
                    content = "\n".join(media_links)
            
            if not content: continue

            # Determine Author
            author_name = "Unknown"
            author_avatar = None
            
            if hasattr(src, 'author'):
                author_name = src.author.display_name
                author_avatar = src.author.display_avatar.url
            else:
                author_name = message.author.display_name
                author_avatar = message.author.display_avatar.url

            # Filter embeds (Rich only)
            embeds = []
            if hasattr(src, 'embeds'):
                embeds = [e for e in src.embeds if e.type == 'rich']

            payloads.append({
                "content": content,
                "username": author_name,
                "avatar_url": author_avatar,
                "embeds": embeds,
                "allowed_mentions": discord.AllowedMentions.none()
            })
            
        return payloads

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

            # Server Match
            elif s['source_id'] == message.guild.id:
                is_source = True
            
            if is_source:
                # Check Ignore List (Channels to skip within a category/server)
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

        payloads = await self._process_message_for_clone(message, receiver.guild)
        if not payloads: return

        webhook = await self.get_webhook(receiver)
        if not webhook: return 
        
        # We typically only expect one payload for a live message event, 
        # unless it's a multi-snapshot forward, but execute_clone handles live events.
        for payload in payloads:
            try:
                # If receiver is a thread, we must specify it in the webhook send
                if isinstance(receiver, discord.Thread):
                    payload["thread"] = receiver
                
                payload["wait"] = True

                # Send via Webhook
                cloned_msg = await webhook.send(**payload)
                
                # Save History
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
                
                # Check if this setup matches the source channel ID, its category, or the server
                if s['source_id'] == source_chan.id or \
                   (source_chan.category and s['source_id'] == source_chan.category.id) or \
                   (s['source_id'] == source_chan.guild.id):
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

            # Is this the source? (Channel, Category, or Server)
            is_source = (s['source_id'] == channel.id) or \
                        (channel.category and s['source_id'] == channel.category.id) or \
                        (s['source_id'] == channel.guild.id)
            
            if is_source:
                if channel.id in s.get('ignore_channels', []): continue

                try:
                    message = await channel.fetch_message(msg_id)
                    
                    if s.get('attachments_only', False) and not message.attachments:
                        continue

                    # This explicitly sums the count of ALL reactions on the message
                    total = sum(r.count for r in message.reactions)
                    
                    if total >= min_reacts:
                        await self.execute_clone(message, s)
                        break
                except:
                    pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Deletes the cloned message if the source is deleted."""
        history = self.get_history()
        entries = [h for h in history if h['source_msg_id'] == message.id]
        
        if entries:
            new_history = [h for h in history if h['source_msg_id'] != message.id]
            self.save_history(new_history)
            
            for entry in entries:
                receiver = self.bot.get_channel(entry['receive_channel_id'])
                if receiver:
                    try:
                        clone = await receiver.fetch_message(entry['clone_msg_id'])
                        await clone.delete()
                    except: pass

    # --- SLASH COMMANDS ---

    @app_commands.command(name="clone", description="Manage message cloning setups.")
    @app_commands.describe(
        action="What would you like to do?",
        receive_channel="[Add/Remove] Where to send cloned messages",
        source_id="[Add/Remove] ID of Source Channel, Category, or Server",
        min_reactions="[Add] Reactions needed to clone (0=Instant)",
        attachments_only="[Add] Only clone messages with files?",
        return_replies="[Add] Allow replies from receiver back to source?"
    )
    @app_commands.default_permissions(administrator=True)
    async def clone(self, interaction: discord.Interaction, 
                    action: Literal["Add", "Remove", "List"],
                    receive_channel: Optional[discord.TextChannel] = None,
                    source_id: Optional[str] = None,
                    min_reactions: int = 0,
                    attachments_only: bool = False,
                    return_replies: bool = False):
        """Manage message cloning setups."""
        
        # --- ADD ---
        if action == "Add":
            if not receive_channel or not source_id:
                return await interaction.response.send_message("❌ Error: `receive_channel` and `source_id` are required to Add.", ephemeral=True)
            
            try:
                s_id = int(source_id)
            except:
                return await interaction.response.send_message("❌ Source ID must be a valid number.", ephemeral=True)

            setups = self.get_clone_setups()
            
            # Check duplicates (Receiver + Source combo)
            for s in setups:
                if s['receive_id'] == receive_channel.id and s['source_id'] == s_id:
                    return await interaction.response.send_message("❌ A setup for this Receiver and Source already exists. Remove it first.", ephemeral=True)

            # Create new setup object
            new_setup = {
                "receive_id": receive_channel.id,
                "guild_id": interaction.guild_id, 
                "source_id": s_id,
                "ignore_channels": [],
                "attachments_only": attachments_only,
                "return_replies": return_replies,
                "min_reactions": min_reactions
            }

            setups.append(new_setup)
            self.save_clone_setups(setups)
            
            flags = []
            if attachments_only: flags.append("MediaOnly")
            if return_replies: flags.append("Replies")
            if min_reactions > 0: flags.append(f"{min_reactions}+ Reacts")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            
            await interaction.response.send_message(f"✅ Setup added! Cloning from `{s_id}` to {receive_channel.mention}{flag_str}.", ephemeral=True)

        # --- REMOVE ---
        elif action == "Remove":
            if not receive_channel or not source_id:
                return await interaction.response.send_message("❌ Error: `receive_channel` and `source_id` are required to Remove.", ephemeral=True)

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

        # --- LIST ---
        elif action == "List":
            setups = self.get_clone_setups()
            if not setups:
                return await interaction.response.send_message("📝 No clone setups active.", ephemeral=True)

            # 1. Map current guild channels for fast local lookup
            current_guild_map = {c.id: c.name for c in interaction.guild.channels}

            # 2. Filter setups
            filtered_setups = []
            for s in setups:
                if s.get('guild_id') == interaction.guild_id:
                    filtered_setups.append(s)
                elif s['receive_id'] in current_guild_map:
                    filtered_setups.append(s)

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
                if rid in current_guild_map:
                    r_name = current_guild_map[rid]
                else:
                    r_channel = self.bot.get_channel(rid)
                    r_name = r_channel.name if r_channel else f"ID:{rid}"
                
                text += f"\n📂 **Receiver: {r_name}**\n"
                for s in source_list:
                    sid = s['source_id']
                    if sid in current_guild_map:
                        s_name = current_guild_map[sid]
                    elif sid == interaction.guild_id:
                        s_name = f"Server: {interaction.guild.name}"
                    else:
                        global_chan = self.bot.get_channel(sid)
                        s_name = global_chan.name if global_chan else f"ID:{sid}"
                    
                    flags = []
                    if s.get('attachments_only'): flags.append("🖼️ MediaOnly")
                    if s.get('return_replies'): flags.append("↩️ Replies")
                    if s.get('min_reactions', 0) > 0: flags.append(f"⭐ {s['min_reactions']}+ Reacts")
                    
                    flag_text = f" ({', '.join(flags)})" if flags else ""
                    text += f" - Source: **{s_name}**{flag_text}\n"

            await interaction.response.send_message(text[:2000], ephemeral=True)

    @app_commands.command(name="postclone", description="Clone the last 100 messages/threads from a source ID to a destination ID.")
    @app_commands.describe(
        source_id="The Channel/Thread/Forum ID to copy FROM",
        destination_id="The Channel/Thread/Forum ID to send TO (Defaults to current channel)"
    )
    @app_commands.default_permissions(administrator=True)
    async def postclone(self, interaction: discord.Interaction, source_id: str, destination_id: Optional[str] = None):
        """Copies content from source to destination. Handles Text, Thread, and Forum cloning."""
        await interaction.response.defer(ephemeral=True)

        # Parse IDs
        try:
            s_id = int(source_id)
        except ValueError:
            return await interaction.followup.send("❌ Source ID must be a number.", ephemeral=True)

        source = self.bot.get_channel(s_id)
        if not source:
            try: source = await self.bot.fetch_channel(s_id)
            except: pass
        
        if destination_id:
            try:
                d_id = int(destination_id)
                destination = self.bot.get_channel(d_id)
                if not destination:
                    try: destination = await self.bot.fetch_channel(d_id)
                    except: pass
            except ValueError:
                 return await interaction.followup.send("❌ Destination ID must be a number.", ephemeral=True)
        else:
            destination = interaction.channel

        # Valid Types
        allowed_source_types = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)
        allowed_dest_types = (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)
        
        if not source or not isinstance(source, allowed_source_types):
             return await interaction.followup.send(f"❌ Could not find valid source channel (ID: {s_id}). Ensure I am in that server.", ephemeral=True)
        
        if not destination or not isinstance(destination, allowed_dest_types):
            return await interaction.followup.send(f"❌ Destination must be a text-capable channel, thread, or forum.", ephemeral=True)

        # Get Webhook
        webhook = await self.get_webhook(destination)
        if not webhook:
            return await interaction.followup.send(f"❌ Could not create a webhook for {destination.mention}. Check my permissions there.", ephemeral=True)

        count = 0

        # --- LOGIC BRANCH A: FORUM -> FORUM CLONING ---
        # "Copy the forum exactly" - Creates threads in dest for threads in source.
        if isinstance(source, discord.ForumChannel) and isinstance(destination, discord.ForumChannel):
            
            # 1. Gather threads (Active + Recent Archived)
            threads_to_clone = []
            threads_to_clone.extend(source.threads)
            try:
                async for t in source.archived_threads(limit=25):
                    threads_to_clone.append(t)
            except: pass
            
            # Sort chronological
            threads_to_clone.sort(key=lambda x: x.id)
            # Limit to last 25 threads to prevent timeout (Interaction is 15 mins max)
            threads_to_clone = threads_to_clone[-25:]
            
            for t in threads_to_clone:
                # Fetch messages for this thread
                try:
                    t_msgs = [m async for m in t.history(limit=100, oldest_first=True)]
                except: continue
                if not t_msgs: continue
                
                # Split starter vs rest
                starter = t_msgs[0]
                rest = t_msgs[1:]
                
                # Prepare starter payload
                starter_payloads = await self._process_message_for_clone(starter, source.guild)
                if not starter_payloads: continue
                payload = starter_payloads[0] # First payload starts thread
                
                # Match Tags
                applied_tags = []
                for stag in t.applied_tags:
                    dtag = discord.utils.get(destination.available_tags, name=stag.name)
                    if dtag: applied_tags.append(dtag)
                
                try:
                    # Send starter to create NEW thread in dest forum
                    payload["thread_name"] = t.name
                    payload["applied_tags"] = applied_tags
                    payload["wait"] = True
                    
                    res_msg = await webhook.send(**payload)
                    count += 1
                    
                    new_thread = res_msg.thread
                    if not new_thread: continue
                    
                    # Send remaining messages to the new thread
                    for m in rest:
                        m_payloads = await self._process_message_for_clone(m, source.guild)
                        for mp in m_payloads:
                            mp["thread"] = new_thread
                            mp["wait"] = True
                            await webhook.send(**mp)
                            count += 1
                            await asyncio.sleep(0.5) # Anti-abuse
                            
                except Exception as e:
                    print(f"Failed to clone thread {t.name}: {e}")
                    continue

            await interaction.followup.send(f"✅ Successfully cloned {len(threads_to_clone)} threads ({count} messages) from {source.mention} to {destination.mention}!", ephemeral=True)
            return

        # --- LOGIC BRANCH B: TEXT/THREAD -> ANY ---
        # Standard history copying
        
        # Check permissions for source
        if not source.permissions_for(source.guild.me).read_message_history:
             return await interaction.followup.send(f"❌ I cannot read message history in {source.mention}.", ephemeral=True)

        # Handle writing to Forum root (Create ONE thread for the batch)
        target_thread = None
        if isinstance(destination, discord.ForumChannel):
             try:
                start_content = f"📂 **Cloning Session**\nFrom: {source.mention}\nRunning..."
                thread_with_msg = await destination.create_thread(name=f"Clone: {source.name}", content=start_content)
                target_thread = thread_with_msg.thread
             except Exception as e:
                return await interaction.followup.send(f"❌ Failed to create a new post in the destination forum: {e}", ephemeral=True)

        # Fetch messages
        messages = [msg async for msg in source.history(limit=100)]
        messages = list(reversed(messages))
        
        for msg in messages:
            payloads = await self._process_message_for_clone(msg, source.guild)
            
            for payload in payloads:
                # If we created a specific thread in a Forum, target it
                if target_thread:
                    payload["thread"] = target_thread
                # If destination is natively a Thread, target it
                elif isinstance(destination, discord.Thread):
                    payload["thread"] = destination
                
                payload["wait"] = True
                
                try:
                    await webhook.send(**payload)
                    count += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Postclone error on msg {msg.id}: {e}")

        await interaction.followup.send(f"✅ Successfully cloned {count} messages from {source.mention} to {destination.mention}!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Clone(bot))
