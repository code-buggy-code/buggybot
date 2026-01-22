import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# Function/Class List:
# class Clone(commands.Cog)
# - __init__(bot)
# - get_clone_mapping()
# - save_clone_mapping(mapping)
# - on_message(message)
# - clone_channel(interaction, source, destination)
# - unclone_channel(interaction, destination)
# - list_clones(interaction)
# setup(bot)

class Clone(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- HELPERS ---

    def get_clone_mapping(self):
        """Returns the mapping of Source -> [Destinations]."""
        return self.bot.db.get_collection("clone_mappings")

    def save_clone_mapping(self, mapping):
        """Saves the clone mapping."""
        self.bot.db.save_collection("clone_mappings", mapping)

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Relays messages from source channels to destination channels."""
        if message.author.bot: # Prevent infinite loops
            return

        mappings = self.get_clone_mapping()
        
        # Check if current channel is a Source
        source_id = str(message.channel.id)
        if source_id in mappings:
            destinations = mappings[source_id]
            
            for dest_id in destinations:
                dest_channel = self.bot.get_channel(int(dest_id))
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
        source_id = str(source.id)
        dest_id = str(destination.id)

        if source_id not in mappings:
            mappings[source_id] = []

        if dest_id in mappings[source_id]:
             return await interaction.response.send_message("⚠️ This clone link already exists.", ephemeral=True)

        mappings[source_id].append(dest_id)
        self.save_clone_mapping(mappings)
        
        await interaction.response.send_message(f"✅ Messages from {source.mention} will now be cloned to {destination.mention}.", ephemeral=True)

    @clone_group.command(name="remove", description="Stop cloning messages to this destination.")
    @app_commands.describe(destination="The channel to stop receiving messages")
    @app_commands.checks.has_permissions(administrator=True)
    async def unclone_channel(self, interaction: discord.Interaction, destination: discord.TextChannel):
        mappings = self.get_clone_mapping()
        dest_id = str(destination.id)
        found = False

        # Search all sources for this destination
        for source_id, dests in list(mappings.items()):
            if dest_id in dests:
                dests.remove(dest_id)
                found = True
                if not dests: # Cleanup empty sources
                    del mappings[source_id]
        
        if found:
            self.save_clone_mapping(mappings)
            await interaction.response.send_message(f"✅ Stopped cloning messages to {destination.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {destination.mention} is not receiving any cloned messages.", ephemeral=True)

    @clone_group.command(name="list", description="List all active clones.")
    async def list_clones(self, interaction: discord.Interaction):
        mappings = self.get_clone_mapping()
        
        if not mappings:
            return await interaction.response.send_message("📝 No active clones.", ephemeral=True)

        text = "**Active Channel Clones:**\n"
        for source_id, dests in mappings.items():
            source = interaction.guild.get_channel(int(source_id))
            source_name = source.mention if source else f"ID:{source_id}"
            
            dest_names = []
            for d in dests:
                chan = interaction.guild.get_channel(int(d))
                dest_names.append(chan.mention if chan else f"ID:{d}")
            
            text += f"• {source_name} ➡️ {', '.join(dest_names)}\n"
            
        await interaction.response.send_message(text, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Clone(bot))
