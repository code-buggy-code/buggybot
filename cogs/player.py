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
# def setup(bot)

class Player(commands.Cog):
    """Music commands using Wavelink (Lavalink)"""
    
    def __init__(self, bot):
        self.bot = bot
        # Configuration for the Java path and Lavalink location
        self.java_path = "/usr/lib/jvm/java-17-openjdk-arm64/bin/java"
        self.lavalink_dir = "lavalink" # Assuming it is in a subfolder named 'lavalink'
        self.lavalink_jar = "Lavalink.jar"

    async def start_lavalink(self):
        """Starts the Lavalink server using the specific Java path."""
        jar_full_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        
        if not os.path.exists(jar_full_path):
            print(f"❌ Player Cog: Could not find {self.lavalink_jar} in {self.lavalink_dir}. Skipping auto-start.")
            return

        print(f"☕ Attempting to start Lavalink with: {self.java_path}")
        try:
            # We verify the java path exists first
            if not os.path.exists(self.java_path):
                 print(f"❌ Player Cog: Java executable not found at {self.java_path}")
                 return

            # Start the process. 
            subprocess.Popen(
                [self.java_path, "-jar", self.lavalink_jar],
                cwd=os.path.join(os.getcwd(), self.lavalink_dir),
                stdout=subprocess.DEVNULL, # Hide output to keep console clean
                stderr=subprocess.DEVNULL
            )
            # Give it a few seconds to initialize before we try to connect
            # MUST use asyncio.sleep here to avoid freezing the bot
            await asyncio.sleep(5)
        except Exception as e:
            print(f"❌ Failed to start Lavalink process: {e}")

    async def cog_load(self):
        """Connects to the local Lavalink server when the cog loads."""
        # 1. Attempt to start the server
        await self.start_lavalink()

        # 2. Connect
        # NOTE: This requires a Lavalink server running on localhost:2333
        # Password default is usually 'youshallnotpass'
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
            print(f"❌ Player Cog: Could not connect to Lavalink. Error: {e}")

    @app_commands.command(name="play", description="Play a song from YouTube/Spotify")
    @app_commands.describe(search="The song name or URL")
    async def play(self, interaction: discord.Interaction, search: str):
        """Plays a song."""
        # 1. Check if user is in VC
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You aren't in a voice channel!", ephemeral=True)

        await interaction.response.defer()
        
        # 2. Connect to VC if not already connected
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"❌ I couldn't join that channel: {e}")
        else:
            vc: wavelink.Player = interaction.guild.voice_client

        # 3. Search and Add to Queue
        try:
            tracks = await wavelink.Playable.search(search)
        except Exception as e:
            return await interaction.followup.send(f"❌ Search error: {e}")

        if not tracks:
             return await interaction.followup.send("❌ No tracks found.")
        
        # Handle Playlists vs Single Tracks
        if isinstance(tracks, wavelink.Playlist):
            added = 0
            for track in tracks:
                await vc.queue.put_wait(track)
                added += 1
            await interaction.followup.send(f"✅ Added playlist **{tracks.name}** ({added} songs) to queue.")
        else:
            track = tracks[0]
            await vc.queue.put_wait(track)
            await interaction.followup.send(f"✅ Added to queue: **{track.title}**")

        # 4. Start playing if idle
        if not vc.playing:
            try:
                next_track = vc.queue.get()
                await vc.play(next_track)
                # await interaction.channel.send(f"🎵 Now Playing: **{next_track.title}**")
            except Exception as e:
                print(f"Error starting playback: {e}")

    @app_commands.command(name="skip", description="Skips the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        
        await vc.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=False)

    @app_commands.command(name="stop", description="Stops music and leaves.")
    async def stop(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ I'm not playing anything.", ephemeral=True)
        
        await vc.disconnect()
        await interaction.response.send_message("Xx_Stopped_xX", ephemeral=False)

    @app_commands.command(name="volume", description="Sets the volume (0-100).")
    async def volume(self, interaction: discord.Interaction, level: int):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ Not connected.", ephemeral=True)
        
        vol = max(0, min(100, level))
        await vc.set_volume(vol)
        await interaction.response.send_message(f"🔊 Volume set to {vol}%")

    @app_commands.command(name="queue", description="Shows the current queue.")
    async def queue(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or (not vc.playing and vc.queue.is_empty):
             return await interaction.response.send_message("The queue is empty.", ephemeral=True)
        
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.from_str("#ff90aa"))
        
        # Current
        if vc.current:
            embed.add_field(name="Now Playing", value=f"▶️ {vc.current.title} (`{vc.current.author}`)", inline=False)
        
        # Up Next
        upcoming = ""
        for i, track in enumerate(vc.queue[:10], 1):
            upcoming += f"**{i}.** {track.title}\n"
        
        if upcoming:
            embed.add_field(name="Up Next", value=upcoming, inline=False)
        else:
            embed.set_footer(text="Queue is empty.")

        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="nowplaying", description="Shows what is currently playing.")
    async def nowplaying(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.current:
            return await interaction.response.send_message("❌ Nothing is playing right now.", ephemeral=True)
            
        embed = discord.Embed(title="Now Playing", description=f"[{vc.current.title}]({vc.current.uri})", color=discord.Color.from_str("#ff90aa"))
        embed.add_field(name="Artist", value=vc.current.author, inline=True)
        # Position formatting
        position = int(vc.position / 1000)
        length = int(vc.current.length / 1000)
        embed.set_footer(text=f"{position // 60}:{position % 60:02d} / {length // 60}:{length % 60:02d}")
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Player(bot))
