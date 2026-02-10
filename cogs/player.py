import discord
from discord import app_commands
from discord.ext import commands
import wavelink
import logging
import subprocess
import os
import asyncio

# Function List:
# class Player(commands.Cog)
# - __init__(bot)
# - start_lavalink()
# - cog_load()
# - play(interaction, search)
# - skip(interaction)
# - stop(interaction)
# - volume(interaction, level)
# - queue(interaction)
# - nowplaying(interaction)
# - checkplayer(interaction)
# - on_wavelink_track_start(payload)
# - on_wavelink_track_end(payload)
# - on_wavelink_track_exception(payload) <--- IMPROVED
# def setup(bot)

class Player(commands.Cog):
    """Music commands using Wavelink (Lavalink)"""
    
    def __init__(self, bot):
        self.bot = bot
        self.java_path = "/usr/lib/jvm/java-17-openjdk-arm64/bin/java"
        self.lavalink_dir = "lavalink" 
        self.lavalink_jar = "Lavalink.jar"

    async def start_lavalink(self):
        """Starts the Lavalink server."""
        jar_full_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        
        if not os.path.exists(jar_full_path):
            print(f"❌ Player Cog: Could not find {self.lavalink_jar}. Skipping auto-start.")
            return

        try:
            if not os.path.exists(self.java_path):
                 print(f"❌ Player Cog: Java not found at {self.java_path}")
                 return

            subprocess.Popen(
                [self.java_path, "-jar", self.lavalink_jar],
                cwd=os.path.join(os.getcwd(), self.lavalink_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await asyncio.sleep(5) # Wait for Java to spin up
        except Exception as e:
            print(f"❌ Failed to start Lavalink: {e}")

    async def cog_load(self):
        """Connects to Lavalink."""
        await self.start_lavalink()

        nodes = [
            wavelink.Node(
                identifier="local-node",
                uri="http://localhost:2333",
                password="youshallnotpass"
            )
        ]
        
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self.bot, cache_capacity=100)
            print("✅ Player Cog: Connected to Lavalink Node!")
        except Exception as e:
            print(f"❌ Player Cog: Could not connect to Lavalink: {e}")

    # --- EVENT LISTENERS ---

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        """Called when a track starts playing."""
        player = payload.player
        if not player: return
        
        # Try to find a channel to announce in
        # We look for a 'home' channel attribute we set during /play
        channel = getattr(player, 'home', None)
        
        if channel:
            embed = discord.Embed(
                description=f"🎵 Now Playing: **{payload.track.title}** (`{payload.track.author}`)",
                color=discord.Color.from_str("#ff90aa")
            )
            try:
                await channel.send(embed=embed)
            except: pass
        
        print(f"🎵 Started playing: {payload.track.title}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """Called when a track finishes. Plays the next one."""
        player = payload.player
        if not player: return

        # If the queue isn't empty, play the next track
        if not player.queue.is_empty:
            next_track = player.queue.get()
            await player.play(next_track)
        else:
            # Queue finished, maybe disconnect after timeout?
            # For now we just stay connected.
            pass

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        """Called when a track crashes (e.g., 403 Forbidden)."""
        player = payload.player
        exception = payload.exception
        print(f"❌ Track Exception: {exception}")
        
        # Notify user if possible
        channel = getattr(player, 'home', None)
        if channel:
            try:
                await channel.send(f"⚠️ **Error Playing Track:** `{payload.track.title}`\nReason: `{exception}`")
            except: pass

        # Try to play next if one failed
        if player and not player.queue.is_empty:
             next_track = player.queue.get()
             await player.play(next_track)

    # --- COMMANDS ---

    @app_commands.command(name="play", description="Play a song from YouTube/Spotify")
    @app_commands.describe(search="The song name or URL")
    async def play(self, interaction: discord.Interaction, search: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You aren't in a voice channel!", ephemeral=True)

        await interaction.response.defer()
        
        # Join VC
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"❌ Connection error: {e}")
        else:
            vc: wavelink.Player = interaction.guild.voice_client

        # Store the text channel so we can send updates later
        vc.home = interaction.channel

        # Search
        try:
            tracks = await wavelink.Playable.search(search)
        except Exception as e:
            return await interaction.followup.send(f"❌ Search error: {e}")

        if not tracks:
             return await interaction.followup.send("❌ No tracks found.")
        
        if isinstance(tracks, wavelink.Playlist):
            added = 0
            for track in tracks:
                await vc.queue.put_wait(track)
                added += 1
            await interaction.followup.send(f"✅ Added playlist **{tracks.name}** ({added} songs).")
        else:
            track = tracks[0]
            await vc.queue.put_wait(track)
            await interaction.followup.send(f"✅ Added: **{track.title}**")

        # Play if idle
        if not vc.playing:
            try:
                next_track = vc.queue.get()
                await vc.play(next_track)
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to start playback: {e}")

    @app_commands.command(name="skip", description="Skips the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        await vc.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped!")

    @app_commands.command(name="stop", description="Stops music and leaves.")
    async def stop(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return await interaction.response.send_message("❌ Not connected.", ephemeral=True)
        await vc.disconnect()
        await interaction.response.send_message("👋 Bye!")

    @app_commands.command(name="volume", description="Sets volume (0-100).")
    async def volume(self, interaction: discord.Interaction, level: int):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return await interaction.response.send_message("❌ Not connected.", ephemeral=True)
        await vc.set_volume(max(0, min(100, level)))
        await interaction.response.send_message(f"🔊 Volume: {level}%")

    @app_commands.command(name="queue", description="Shows the current queue.")
    async def queue(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or (not vc.playing and vc.queue.is_empty):
             return await interaction.response.send_message("The queue is empty.", ephemeral=True)
        
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.from_str("#ff90aa"))
        if vc.current:
            embed.add_field(name="Now Playing", value=f"▶️ {vc.current.title} (`{vc.current.author}`)", inline=False)
        
        upcoming = ""
        for i, track in enumerate(vc.queue[:10], 1):
            upcoming += f"**{i}.** {track.title}\n"
        if upcoming: embed.add_field(name="Up Next", value=upcoming, inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Shows what is currently playing.")
    async def nowplaying(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.current:
            return await interaction.response.send_message("❌ Nothing is playing right now.", ephemeral=True)
            
        embed = discord.Embed(title="Now Playing", description=f"[{vc.current.title}]({vc.current.uri})", color=discord.Color.from_str("#ff90aa"))
        embed.add_field(name="Artist", value=vc.current.author, inline=True)
        position = int(vc.position / 1000)
        length = int(vc.current.length / 1000)
        embed.set_footer(text=f"{position // 60}:{position % 60:02d} / {length // 60}:{length % 60:02d}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="checkplayer", description="Diagnostics: Check Lavalink connection and search.")
    async def checkplayer(self, interaction: discord.Interaction):
        await interaction.response.defer()
        node = wavelink.Pool.get_node("local-node")
        
        embed = discord.Embed(title="🎧 Player Diagnostics", color=discord.Color.from_str("#ff90aa"))
        
        if not node:
            embed.description = "❌ **Lavalink Node is NOT connected.**"
            return await interaction.followup.send(embed=embed)

        embed.add_field(name="1. Lavalink Node", value=f"✅ Connected (`{node.identifier}`)\nStatus: {node.status}", inline=False)

        try:
            tracks = await wavelink.Playable.search("ytsearch:Rick Astley Never Gonna Give You Up")
            if tracks:
                track = tracks[0]
                embed.add_field(name="2. Search & Access", value=f"✅ **Success**\nFound: {track.title}", inline=False)
            else:
                embed.add_field(name="2. Search & Access", value="❌ **Failed** (No results)", inline=False)
        except Exception as e:
             embed.add_field(name="2. Search & Access", value=f"❌ **Error**: {e}", inline=False)

        if interaction.guild.voice_client:
             vc: wavelink.Player = interaction.guild.voice_client
             embed.add_field(name="3. Voice Client", value=f"Connected to {vc.channel.mention}\nPlaying: {vc.playing}\nPaused: {vc.paused}", inline=False)
        else:
             embed.add_field(name="3. Voice Client", value="Idle (Not in VC)", inline=False)

        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Player(bot))
