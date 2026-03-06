import discord
from discord.ext import commands
from discord import app_commands
import wavelink
import asyncio

# Function/Class List:
# class Player(commands.Cog)
# - __init__(bot)
# - cog_load()
# - setup_nodes()
# - on_wavelink_node_ready(payload)
# - on_wavelink_track_exception(payload)
# - play(interaction, query) [Slash]
# - leave(interaction) [Slash]
# - pause(interaction) [Slash]
# - resume(interaction) [Slash]
# - skip(interaction) [Slash]
# - go_back(interaction) [Slash]
# - queue(interaction) [Slash]
# setup(bot)

class Player(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.bot.loop.create_task(self.setup_nodes())

    async def setup_nodes(self):
        """Connects to the local Lavalink node running on the Oracle device."""
        await self.bot.wait_until_ready()
        
        # Connects to the local Lavalink server
        node = wavelink.Node(
            uri="http://127.0.0.1:2333",
            password="youshallnotpass" 
        )
        
        try:
            await wavelink.Pool.connect(client=self.bot, nodes=[node])
            print("⏳ Requested Lavalink connection to local server (127.0.0.1:2333)...")
        except Exception as e:
            print(f"❌ Failed to connect to local Lavalink: {e}")

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        """Fired when the Lavalink node connects successfully."""
        print(f"✅ Lavalink Node connected successfully: {payload.node.identifier}")

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        """Fired when a track fails to play. Helps us debug leaving issues."""
        print(f"❌ Track Exception: {payload.exception}")

    # --- SLASH COMMANDS ---

    @app_commands.command(name="play", description="Play a YouTube playlist or song")
    @app_commands.describe(query="The YouTube URL or playlist link")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play a YouTube playlist or song."""
        await interaction.response.defer()

        # 1. Search for the track or playlist FIRST
        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except Exception as e:
            error_message = str(e)
            if "FriendlyException" in error_message or "Something went wrong" in error_message:
                return await interaction.followup.send(
                    "❌ **YouTube Blocked the Request!**\nUpdate your `application.yml` with the poToken, buggy!"
                )
            return await interaction.followup.send(f"❌ Error searching for the song: `{e}`")

        if not tracks:
            return await interaction.followup.send("❌ Could not find any songs with that query.")

        # 2. Check connections
        if not wavelink.Pool.nodes:
            return await interaction.followup.send("❌ **Lavalink Offline!**")

        if not interaction.user.voice:
            return await interaction.followup.send("❌ You need to be in a voice channel first!")

        vc: wavelink.Player = interaction.guild.voice_client
        
        if not vc:
            try:
                # 3. Connect to the voice channel
                vc = await interaction.user.voice.channel.connect(cls=wavelink.Player, timeout=60.0)
                
                # 4. Wait for session to be fully established
                await asyncio.sleep(2)
                vc.autoplay = wavelink.AutoPlayMode.partial
                
            except Exception as e:
                return await interaction.followup.send(f"❌ Failed to connect to voice channel: `{e}`")

        # 5. Handle Queueing
        if isinstance(tracks, wavelink.Playlist):
            for track in tracks.tracks:
                vc.queue.put(track)
            await interaction.followup.send(f"🎵 Added playlist **{tracks.name}** ({len(tracks.tracks)} songs) to the queue.")
        else:
            track = tracks[0]
            vc.queue.put(track)
            await interaction.followup.send(f"🎵 Added **{track.title}** to the queue.")

        # 6. Play the music
        if not vc.playing:
            try:
                # We need to make sure the player knows its channel ID for Lavalink v4
                # This helps fix the 'channelId is required' error in the logs
                track = vc.queue.get()
                await vc.play(track, add_history=True)
            except Exception as e:
                print(f"Play Error: {e}")

    @app_commands.command(name="leave", description="Disconnect the bot from the voice channel")
    async def leave(self, interaction: discord.Interaction):
        """Disconnect the bot from the voice channel."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ I'm not currently in a voice channel.", ephemeral=True)
        
        await vc.disconnect()
        await interaction.response.send_message("👋 Disconnected and cleared the queue.")

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        """Pause the current song."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
        
        await vc.pause(True)
        await interaction.response.send_message("⏸️ Paused the music.")

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        """Resume the paused song."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.paused:
            return await interaction.response.send_message("❌ The player is not paused.", ephemeral=True)
        
        await vc.pause(False)
        await interaction.response.send_message("▶️ Resumed the music.")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)

        await vc.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped the current song.")

    @app_commands.command(name="go_back", description="Play the previous song")
    async def go_back(self, interaction: discord.Interaction):
        """Play the previous song."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ I'm not in a voice channel.", ephemeral=True)

        history_list = list(vc.queue.history)
        if not history_list:
            return await interaction.response.send_message("❌ There is no previous song in the history.", ephemeral=True)

        previous_track = history_list[-1] 
        current = vc.current
        if current:
            vc.queue.put_at_front(current)
            
        await vc.play(previous_track)
        await interaction.response.send_message(f"⏮️ Going back to: **{previous_track.title}**")

    @app_commands.command(name="queue", description="View the upcoming songs in the queue")
    async def queue(self, interaction: discord.Interaction):
        """View the upcoming songs in the queue."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or vc.queue.is_empty:
            return await interaction.response.send_message("📝 The queue is currently empty.", ephemeral=True)
        
        queue_list = list(vc.queue)
        upcoming = queue_list[:10]
        
        desc = ""
        for i, track in enumerate(upcoming, 1):
            desc += f"**{i}.** {track.title}\n"
        
        if len(queue_list) > 10:
            desc += f"\n*...and {len(queue_list) - 10} more*"
            
        embed = discord.Embed(title="🎶 Current Queue", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Player(bot))
