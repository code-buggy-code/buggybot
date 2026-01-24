import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# Function/Class List:
# class Clone(commands.Cog)
# - __init__(bot)
# - get_clone_mapping()
# - save_clone_mapping(mapping)
# - migrate_data()
# - on_message(message)
# - clone_channel(interaction, source, destination)
# - unclone_channel(interaction, destination)
# - list_clones(interaction)
# setup(bot)

class Clone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Clone messages from one channel to another."
        self.migrate_data() # Ensure data structure is correct

    # --- HELPERS ---

    def get_clone_mapping(self):
        """Returns the list of clones."""
        # New structure: List of dicts {source_id, dest_ids, guild_id}
        data = self.bot.db.get_collection("clone_mappings")
        if isinstance(data, dict): return [] # Should be list now
        return data

    def save_clone_mapping(self, mapping):
        """Saves the clone mapping."""
        self.bot.db.save_collection("clone_mappings", mapping)

    def migrate_data(self):
        """Migrates old Dict structure to new List structure with guild_id."""
        data = self.bot.db.get_collection("clone_mappings")
        
        # If it's a dict, it's the old format: {source_id: [dest_ids]}
        if isinstance(data, dict) and data:
            print("🔄 Migrating clone_mappings to new format...")
            new_list = []
            for src, dests in data.items():
                # We can't know the guild ID easily without an object, 
                # but we can try to find it via the bot cache or just store 0 for now and fix on load.
                # Ideally, we find a channel object.
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
        
        # Find entry for this source
        entry = next((m for m in mappings if m['source_id'] == message.channel.id), None)
        
        if entry:
            destinations = entry['dest_ids']
            
            for dest_id in destinations:
                dest_channel = self.bot.get_channel(dest_id)
                if dest_channel:
                    try:
                        # Prepare content
                        content = message.content
                        files = []
                        if message.attachments:
                            for attachment in message.attachments:
                                files.append(await attachment.to_file())
                        
                        # Send as a webhook-like message (using Embed for cleaner look)
                        embed = discord.Embed(description=content, color=message.author.color, timestamp=message.created_at)
                        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                        
                        if message.reference:
                             embed.set_footer(text=f"Replying to a message")

                        await dest_channel.send(embed=embed, files=files)
                        
                    except Exception as e:
                        print(f"Failed to clone message to {dest_id}: {e}")

    # --- COMMANDS ---

    clone_group = app_commands.Group(name="clone", description="Manage channel cloning")

    @clone_group.command(name="add", description="Clone messages from Source to Destination.")
    @app_commands.describe(source="Where messages come FROM", destination="Where messages go TO")
    @app_commands.checks.has_permissions(administrator=True)
    async def clone_channel(self, interaction: discord.Interaction, source: discord.TextChannel, destination: discord.TextChannel):
        if source.id == destination.id:
            return await interaction.response.send_message("❌ Source and Destination cannot be the same.", ephemeral=True)

        mappings = self.get_clone_mapping()
        
        # Find or Create Entry
        entry = next((m for m in mappings if m['source_id'] == source.id), None)
        
        if not entry:
            entry = {"source_id": source.id, "dest_ids": [], "guild_id": interaction.guild.id}
            mappings.append(entry)
        
        # Update logic
        if destination.id in entry['dest_ids']:
             return await interaction.response.send_message("⚠️ This clone link already exists.", ephemeral=True)

        # Since we modified 'entry' (reference), we just need to ensure the list is saved
        entry['dest_ids'].append(destination.id)
        
        # Re-save the whole list (DatabaseHandler handles replacement)
        self.bot.db.update_doc("clone_mappings", "source_id", source.id, entry)
        
        await interaction.response.send_message(f"✅ Messages from {source.mention} will now be cloned to {destination.mention}.", ephemeral=True)

    @clone_group.command(name="remove", description="Stop cloning messages to this destination.")
    @app_commands.describe(destination="The channel to stop receiving messages")
    @app_commands.checks.has_permissions(administrator=True)
    async def unclone_channel(self, interaction: discord.Interaction, destination: discord.TextChannel):
        mappings = self.get_clone_mapping()
        found = False

        # Search all sources for this destination
        for entry in mappings:
            if destination.id in entry['dest_ids']:
                entry['dest_ids'].remove(destination.id)
                found = True
                
                # If empty, we could remove the entry, but keeping it empty is fine too
                if not entry['dest_ids']:
                    self.bot.db.delete_doc("clone_mappings", "source_id", entry['source_id'])
                else:
                    self.bot.db.update_doc("clone_mappings", "source_id", entry['source_id'], entry)

        if found:
            await interaction.response.send_message(f"✅ Stopped cloning messages to {destination.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {destination.mention} is not receiving any cloned messages.", ephemeral=True)

    @clone_group.command(name="list", description="List all active clones.")
    async def list_clones(self, interaction: discord.Interaction):
        mappings = self.get_clone_mapping()
        
        # Filter by Guild ID
        guild_mappings = [m for m in mappings if m.get('guild_id') == interaction.guild.id]
        
        if not guild_mappings:
            return await interaction.response.send_message("📝 No active clones.", ephemeral=True)

        text = "**Active Channel Clones:**\n"
        for entry in guild_mappings:
            source = interaction.guild.get_channel(entry['source_id'])
            source_name = source.mention if source else f"ID:{entry['source_id']}"
            
            dest_names = []
            for d in entry['dest_ids']:
                chan = interaction.guild.get_channel(d)
                dest_names.append(chan.mention if chan else f"ID:{d}")
            
            if dest_names:
                text += f"• {source_name} ➡️ {', '.join(dest_names)}\n"
            
        await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Clone(bot))
