import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from typing import cast
import logging
import re
import traceback

# Set up logging
logging.basicConfig(level=logging.INFO)

# Function/Class List:
# class Player(commands.Cog)
# - __init__(bot)
# - cog_load()
# - connect_nodes()
# - on_wavelink_node_ready(payload)
# - on_wavelink_track_start(payload)
# - play(interaction, query)
# - skip(interaction)
# - stop(interaction)
# - queue(interaction)
# - node_status(interaction)
# - debug_play(interaction)
# setup(bot)

class Player(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Start the connection process when the cog loads."""
        # We use create_task so we don't block the bot/cog loading if Lavalink is down/slow
        self.bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        """Connects to the Lavalink Server."""
        await self.bot.wait_until_ready()
        try:
            # Standard default configuration for Lavalink
            node: wavelink.Node = wavelink.Node(
                uri='http://localhost:2333', 
                password='youshallnotpass'
            )
            await wavelink.Pool.connect(client=self.bot, nodes=[node])
        except Exception as e:
            logging.error(f"Failed to connect to Lavalink: {e}")
            print(f"Failed to connect to Lavalink: {e}")

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        logging.info(f"Lavalink Node connected: {payload.node.identifier}")
        print(f"Lavalink Node connected: {payload.node.identifier}")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if not player:
            return
        
        original_requester = getattr(payload.track.extras, "requester", None)
        
        embed = discord.Embed(
            title="Now Playing",
            description=f"[{payload.track.title}]({payload.track.uri})",
            color=discord.Color.blurple()
        )
        
        if original_requester:
            embed.set_footer(text=f"Requested by {original_requester}")
        
        if hasattr(player, 'home') and player.home:
            channel = self.bot.get_channel(player.home)
            if channel:
                await channel.send(embed=embed)

    @app_commands.command(name="play", description="Play a song from YouTube/SoundCloud/Spotify")
    @app_commands.describe(query="The search query or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play a song from YouTube/SoundCloud/Spotify."""
        await interaction.response.defer()

        if not interaction.guild:
            return await interaction.followup.send("This command can only be used in a server.")

        user = cast(discord.Member, interaction.user)
        if not user.voice:
            return await interaction.followup.send("You need to be in a voice channel to play music!")

        if not interaction.guild.voice_client:
            try:
                player: wavelink.Player = await user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"I couldn't connect to the voice channel: {e}")
        else:
            player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)

        player.home = interaction.channel_id

        # Using ytsearch as requested
        if not re.match(r'https?://', query):
            query = f"ytsearch:{query}"

        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except Exception as e:
            # Enhanced error logging for diagnosis
            error_msg = f"Error: {str(e)}"
            print(f"DEBUG: Search failed for query '{query}': {e}")
            traceback.print_exc() 
            return await interaction.followup.send(f"An error occurred while searching. Check console for details.\n`{error_msg}`")

        if not tracks:
            return await interaction.followup.send("No tracks found with that query.")

        if isinstance(tracks, wavelink.Playlist):
            added: int = await player.queue.put_wait(tracks)
            await interaction.followup.send(f"Added playlist **{tracks.name}** ({added} songs) to the queue.")
        else:
            track: wavelink.Playable = tracks[0]
            track.extras = {"requester": user.display_name}
            await player.queue.put_wait(track)
            await interaction.followup.send(f"Added **{track.title}** to the queue.")

        if not player.playing:
            await player.play(player.queue.get())

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I am not currently connected.", ephemeral=True)
        
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        
        if not player.playing:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)

        await player.skip(force=True)
        await interaction.response.send_message("Skipped! ⏭️")

    @app_commands.command(name="stop", description="Stop music and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        """Stop music and clear the queue."""
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I am not currently connected.", ephemeral=True)
        
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        await player.disconnect()
        await interaction.response.send_message("Disconnected. 👋")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        """Show the current queue."""
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("I am not currently connected.", ephemeral=True)
        
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        
        if player.queue.is_empty and not player.playing:
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        embed = discord.Embed(title="Music Queue", color=discord.Color.green())
        
        if player.current:
            embed.add_field(name="Now Playing", value=player.current.title, inline=False)

        if not player.queue.is_empty:
            upcoming = ""
            for index, track in enumerate(player.queue):
                upcoming += f"{index + 1}. {track.title}\n"
                if index >= 9:
                    upcoming += "... and more"
                    break
            embed.add_field(name="Up Next", value=upcoming, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="node", description="Check Lavalink connection status")
    async def node_status(self, interaction: discord.Interaction):
        """Check the connection status of the Lavalink server."""
        nodes = wavelink.Pool.nodes.values()
        
        if not nodes:
            return await interaction.response.send_message("❌ No Lavalink nodes connected. Please check your Java terminal.", ephemeral=True)

        embed = discord.Embed(title="Lavalink Connection Status", color=discord.Color.green())
        
        for node in nodes:
            status_emoji = "🟢" if node.status == wavelink.NodeStatus.CONNECTED else "🔴"
            embed.add_field(
                name=f"{status_emoji} Node: {node.identifier}",
                value=f"**URI:** `{node.uri}`\n**Status:** {node.status}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="debug_play", description="Diagnose audio playback issues step-by-step")
    async def debug_play(self, interaction: discord.Interaction):
        """Run a diagnostic check for the audio system."""
        await interaction.response.defer()
        
        report = []
        user = cast(discord.Member, interaction.user)
        
        # Step 1: Check User Voice State
        if user.voice and user.voice.channel:
            report.append(f"✅ User is in voice channel: **{user.voice.channel.name}**")
        else:
            report.append("❌ User is NOT in a voice channel.")
            await interaction.followup.send("\n".join(report))
            return

        # Step 2: Check Bot Permissions
        permissions = user.voice.channel.permissions_for(interaction.guild.me)
        if permissions.connect and permissions.speak:
            report.append("✅ Bot has Connect/Speak permissions.")
        else:
            report.append("❌ Bot is missing Connect or Speak permissions in that channel.")
            await interaction.followup.send("\n".join(report))
            return

        # Step 3: Check Lavalink Node Connection
        nodes = wavelink.Pool.nodes.values()
        connected_nodes = [n for n in nodes if n.status == wavelink.NodeStatus.CONNECTED]
        if connected_nodes:
            report.append(f"✅ Lavalink Node connected ({len(connected_nodes)} active).")
        else:
            report.append("❌ No active Lavalink nodes found. Is `java -jar Lavalink.jar` running?")
            await interaction.followup.send("\n".join(report))
            return

        # Step 4: Test Search Query with verbose logging
        report.append("🔍 Attempting test search for 'ytsearch:rick roll'...")
        try:
            tracks: wavelink.Search = await wavelink.Playable.search("ytsearch:rick roll")
            if tracks:
                report.append(f"✅ Search successful. Found: {tracks[0].title}")
            else:
                report.append("⚠️ Search returned no results.")
        except Exception as e:
            report.append(f"❌ Search failed with error: {str(e)}")
            report.append(f"ℹ️ **Action Required:** Please check the Lavalink Java window for the full error trace.")

        embed = discord.Embed(title="Audio System Diagnostic", description="\n".join(report), color=discord.Color.orange())
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Player(bot))
