import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from typing import Union, Optional

# Function/Class List:
# class Clone(commands.Cog)
# - __init__(bot)
# - get_clone_mapping()
# - save_clone_mapping(mapping)
# - migrate_data()
# - on_message(message)
# - on_raw_reaction_add(payload)
# - perform_clone(message, dest_ids)
# - clone_channel(interaction, source, destination, ignore_channel, attachments_only, return_replies, min_reactions)
# - unclone_channel(interaction, destination)
# - list_clones(interaction)
# setup(bot)

class Clone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Clone messages from one channel to another with advanced filters."
        self.migrate_data() # Ensure data structure is correct

    # --- HELPERS ---

    def get_clone_mapping(self):
        """Returns the list of clones."""
        data = self.bot.db.get_collection("clone_mappings")
        if not isinstance(data, list): return []
        return data

    def save_clone_mapping(self, mapping):
        """Saves the clone mapping."""
        self.bot.db.save_collection("clone_mappings", mapping)

    def migrate_data(self):
        """Migrates old Dict structure to new List structure with guild_id."""
        data = self.bot.db.get_collection("clone_mappings")
        
        if isinstance(data, dict) and data:
            print("🔄 Migrating clone_mappings to new format...")
            new_list = []
            for src, dests in data.items():
                channel = self.bot.get_channel(int(src))
                gid = channel.guild.id if channel else 0
                
                new_list.append({
                    "source_id": int(src),
                    "dest_ids": [int(d) for d in dests],
                    "guild_id": gid
                })
            self.save_clone_mapping(new_list)
            print("✅ Clone mappings migrated.")

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Relays messages from source channels to destination channels."""
        if message.author.bot or not message.guild:
            return

        mappings = self.get_clone_mapping()
        
        # 1. Handle Forward Cloning (Source -> Dests)
        # We search for the current channel ID or its category ID across all global mappings
        source_entry = next((m for m in mappings if m['source_id'] == message.channel.id or m['source_id'] == (message.channel.category.id if message.channel.category else None)), None)
        
        if source_entry:
            # Check ignore list
            if message.channel.id in source_entry.get('ignore_ids', []):
                return

            # Check attachments filter
            if source_entry.get('attachments_only') and not message.attachments:
                return

            # Check reaction requirement (if 0, clone instantly)
            if source_entry.get('min_reactions', 0) > 0:
                return

            await self.perform_clone(message, source_entry['dest_ids'])

        # 2. Handle Return Replies (Dest -> Source)
        for entry in mappings:
            if entry.get('return_replies') and message.channel.id in entry['dest_ids']:
                await self.perform_clone(message, [entry['source_id']])

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles cloning when a minimum reaction threshold is met."""
        if payload.user_id == self.bot.user.id:
            return

        mappings = self.get_clone_mapping()
        entry = next((m for m in mappings if m['source_id'] == payload.channel_id and m.get('min_reactions', 0) > 0), None)
        
        if not entry:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel: return
        
        try:
            message = await channel.fetch_message(payload.message_id)
            total_reacts = sum(count.count for count in message.reactions)
            
            if total_reacts >= entry['min_reactions']:
                if not any(r.emoji == "✅" and r.me for r in message.reactions):
                    await self.perform_clone(message, entry['dest_ids'])
                    await message.add_reaction("✅")
        except:
            pass

    async def perform_clone(self, message, dest_ids):
        """Internal helper to send the formatted embed to multiple destinations across servers."""
        for dest_id in dest_ids:
            # bot.get_channel searches across all guilds the bot is in
            dest_channel = self.bot.get_channel(dest_id)
            if dest_channel:
                try:
                    content = message.content
                    files = []
                    if message.attachments:
                        for attachment in message.attachments:
                            try: files.append(await attachment.to_file())
                            except: pass
                    
                    embed = discord.Embed(description=content, color=message.author.color, timestamp=message.created_at)
                    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                    
                    if message.reference:
                         embed.set_footer(text=f"Replying to a message in {message.guild.name} -> #{message.channel.name}")
                    else:
                         embed.set_footer(text=f"Sent from {message.guild.name} -> #{message.channel.name}")

                    await dest_channel.send(embed=embed, files=files)
                except Exception as e:
                    print(f"Failed to clone message to {dest_id}: {e}")

    # --- COMMANDS ---

    clone_group = app_commands.Group(name="clone", description="Manage channel cloning")

    @clone_group.command(name="add", description="Clone messages from Source to Destination.")
    @app_commands.describe(
        source="Where messages come FROM (Channel or Category)", 
        destination="Where messages go TO",
        ignore_channel="Optional: Channel to ignore (if source is category)",
        attachments_only="Only clone messages with attachments?",
        return_replies="Allow replies in receiving channel to be sent back?",
        min_reactions="Minimum reactions required to clone (0 for instant)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def clone_channel(self, interaction: discord.Interaction, 
                            source: Union[discord.TextChannel, discord.CategoryChannel], 
                            destination: discord.TextChannel,
                            ignore_channel: Optional[discord.TextChannel] = None,
                            attachments_only: bool = False,
                            return_replies: bool = False,
                            min_reactions: int = 0):
        await interaction.response.defer(ephemeral=True)
        
        if source.id == destination.id:
            return await interaction.followup.send("❌ Source and Destination cannot be the same.")

        mappings = self.get_clone_mapping()
        
        entry = next((m for m in mappings if m['source_id'] == source.id), None)
        
        if not entry:
            entry = {
                "source_id": source.id, 
                "dest_ids": [], 
                "guild_id": interaction.guild.id,
                "ignore_ids": [],
                "attachments_only": attachments_only,
                "return_replies": return_replies,
                "min_reactions": min_reactions
            }
            mappings.append(entry)
        else:
            entry['attachments_only'] = attachments_only
            entry['return_replies'] = return_replies
            entry['min_reactions'] = min_reactions
        
        if destination.id not in entry['dest_ids']:
            entry['dest_ids'].append(destination.id)
        
        if ignore_channel and ignore_channel.id not in entry.get('ignore_ids', []):
            if 'ignore_ids' not in entry: entry['ignore_ids'] = []
            entry['ignore_ids'].append(ignore_channel.id)

        self.save_clone_mapping(mappings)
        
        opts = []
        if attachments_only: opts.append("Attachments Only")
        if return_replies: opts.append("Two-way Replies")
        if min_reactions > 0: opts.append(f"{min_reactions}+ Reactions Required")
        opt_str = f" ({', '.join(opts)})" if opts else ""
        
        await interaction.followup.send(f"✅ Messages from **{source.name}** ({source.guild.name}) will now be cloned to {destination.mention}{opt_str}.")

    @clone_group.command(name="remove", description="Stop cloning messages to this destination.")
    @app_commands.describe(destination="The channel to stop receiving messages")
    @app_commands.checks.has_permissions(administrator=True)
    async def unclone_channel(self, interaction: discord.Interaction, destination: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        
        mappings = self.get_clone_mapping()
        found = False

        new_mappings = []
        for entry in mappings:
            if destination.id in entry['dest_ids']:
                entry['dest_ids'].remove(destination.id)
                found = True
            
            if entry['dest_ids']:
                new_mappings.append(entry)

        if found:
            self.save_clone_mapping(new_mappings)
            await interaction.followup.send(f"✅ Stopped cloning messages to {destination.mention}.")
        else:
            await interaction.followup.send(f"⚠️ {destination.mention} is not receiving any cloned messages.")

    @clone_group.command(name="list", description="List all active clones.")
    async def list_clones(self, interaction: discord.Interaction):
        mappings = self.get_clone_mapping()
        
        # Only show clones that originate from the current guild to keep the list relevant
        guild_mappings = [m for m in mappings if m.get('guild_id') == interaction.guild.id]
        
        if not guild_mappings:
            return await interaction.response.send_message("📝 No active clones originating from this server.", ephemeral=True)

        text = "**Active Channel Clones:**\n"
        for entry in guild_mappings:
            source = self.bot.get_channel(entry['source_id'])
            source_name = f"**{source.name}**" if source else f"ID:{entry['source_id']}"
            
            dest_names = []
            for d in entry['dest_ids']:
                chan = self.bot.get_channel(d)
                if chan:
                    # Show the server name if it's cross-server
                    if chan.guild.id != interaction.guild.id:
                        dest_names.append(f"{chan.mention} ({chan.guild.name})")
                    else:
                        dest_names.append(chan.mention)
                else:
                    dest_names.append(f"ID:{d}")
            
            opts = []
            if entry.get('attachments_only'): opts.append("MediaOnly")
            if entry.get('return_replies'): opts.append("2-Way")
            if entry.get('min_reactions', 0) > 0: opts.append(f"{entry['min_reactions']}rd")
            opt_str = f" `[{', '.join(opts)}]`" if opts else ""

            if dest_names:
                text += f"• {source_name} ➡️ {', '.join(dest_names)}{opt_str}\n"
            
        await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Clone(bot))
