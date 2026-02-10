import discord
from discord import app_commands
from discord.ext import commands
import wavelink
import logging
import subprocess
import os
import asyncio
import sys
import socket

# Function List:
# class Player(commands.Cog)
# - __init__(bot)
# - is_port_in_use(port)
# - check_and_update_lavalink() <--- UPDATED PATH
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
# - on_wavelink_track_exception(payload)
# def setup(bot)

class Player(commands.Cog):
    """Music commands using Wavelink (Lavalink)"""
    
    def __init__(self, bot):
        self.bot = bot
        self.java_path = "/usr/lib/jvm/java-17-openjdk-arm64/bin/java"
        self.lavalink_dir = "lavalink" 
        self.lavalink_jar = "Lavalink.jar"
        self.updater_script = "update_lavalink.py" # The name of the file
        self.host = "localhost"
        self.port = 2333
        self.password = "youshallnotpass"

    def is_port_in_use(self, port: int) -> bool:
        """Checks if a port is already being used by another process."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((self.host, port)) == 0

    async def check_and_update_lavalink(self):
        """Checks for the updater script INSIDE the lavalink folder and runs it."""
        root_dir = os.getcwd()
        # Look for script inside the 'lavalink' folder
        updater_path = os.path.join(root_dir, self.lavalink_dir, self.updater_script)
        
        if os.path.exists(updater_path):
            print(f"🔄 Player Cog: Found updater at {updater_path}. Running update check...")
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, updater_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    print("✅ Player Cog: Update script completed successfully.")
                else:
                    print(f"⚠️ Player Cog: Update script failed with code {process.returncode}.")
                    print(f"Error: {stderr.decode()}")
            except Exception as e:
                print(f"❌ Player Cog: Failed to run updater script: {e}")
        else:
            print(f"ℹ️ Player Cog: Updater script not found at '{updater_path}'. Skipping update.")

    async def start_lavalink(self):
        """Starts the Lavalink server only if it's not already running."""
        
        # 1. Check if Lavalink is already running
        if self.is_port_in_use(self.port):
            print(f"⚡ Player Cog: Port {self.port} is busy. Lavalink is likely running. Skipping startup.")
            return

        # 2. If not running, Update and Start
        await self.check_and_update_lavalink()

        jar_full_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        
        if not os.path.exists(jar_full_path):
            print(f"❌ Player Cog: {self.lavalink_jar} not found! Please ensure '{self.updater_script}' ran successfully.")
            return

        print(f"☕ Starting Lavalink Process...")
        try:
            subprocess.Popen(
                [self.java_path, "-jar", self.lavalink_jar],
                cwd=os.path.join(os.getcwd(), self.lavalink_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            # Give Java a moment to warm up
            await asyncio.sleep(5)
        except Exception as e:
            print(f"❌ Failed to start Lavalink: {e}")

    async def cog_load(self):
        """Connects to Lavalink."""
        await self.start_lavalink()

        nodes = [
            wavelink.Node(
                identifier="local-node",
                uri=f"http://{self.host}:{self.port}",
                password=self.password
            )
        ]
        
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self.bot, cache_capacity=100)
            print("✅ Player Cog: Connected to Lavalink Node!")
        except Exception as e:
            print(f"❌ Player Cog: Could not connect to Lavalink. Error: {e}")

    # --- EVENT LISTENERS ---

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player = payload.player
        if not player: return
        
        channel = getattr(player, 'home', None)
        if channel:
            embed = discord.Embed(
                description=f"🎵 Now Playing: **{payload.track.title}**",
                color=discord.Color.from_str("#ff90aa")
            )
            try: await channel.send(embed=embed)
            except: pass
        print(f"🎵 Started: {payload.track.title}")

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player: return

        if not player.queue.is_empty:
            next_track = player.queue.get()
            await player.play(next_track)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        player = payload.player
        exception = str(payload.exception)
        print(f"❌ Track Exception: {exception}")
        
        channel = getattr(player, 'home', None)
        if channel:
            if "Must find action functions" in exception:
                 embed = discord.Embed(
                    title="⚠️ Auto-Update Required",
                    description="**Youtube updated their system!**\nPlease restart the bot to trigger the `update_lavalink.py` script.",
                    color=discord.Color.red()
                )
                 try: await channel.send(embed=embed)
                 except: pass
            else:
                 try: await channel.send(f"⚠️ Error playing track: `{payload.track.title}`")
                 except: pass

        if player and not player.queue.is_empty:
             await player.play(player.queue.get())

    # --- COMMANDS ---

    @app_commands.command(name="play", description="Play a song from YouTube/Spotify")
    @app_commands.describe(search="The song name or URL")
    async def play(self, interaction: discord.Interaction, search: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ Join a voice channel first!", ephemeral=True)

        await interaction.response.defer()
        
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"❌ I couldn't join: {e}")
        else:
            vc: wavelink.Player = interaction.guild.voice_client

        vc.home = interaction.channel

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

        if not vc.playing:
            try:
                await vc.play(vc.queue.get())
            except Exception as e:
                await interaction.followup.send(f"❌ Playback error: {e}")

    @app_commands.command(name="stop", description="Stops music and leaves.")
    async def stop(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return await interaction.response.send_message("❌ Not playing.", ephemeral=True)
        await vc.disconnect()
        await interaction.response.send_message("👋 Stopped.")

    @app_commands.command(name="skip", description="Skips the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if vc and vc.playing:
            await vc.skip(force=True)
            await interaction.response.send_message("⏭️ Skipped!")
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)
            
    @app_commands.command(name="volume", description="Sets the volume (0-100).")
    async def volume(self, interaction: discord.Interaction, level: int):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return await interaction.response.send_message("❌ Not connected.", ephemeral=True)
        await vc.set_volume(max(0, min(100, level)))
        await interaction.response.send_message(f"🔊 Volume: {level}%")

    @app_commands.command(name="nowplaying", description="Shows what is currently playing.")
    async def nowplaying(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.current:
            return await interaction.response.send_message("❌ Nothing is playing right now.", ephemeral=True)
            
        embed = discord.Embed(title="Now Playing", description=f"[{vc.current.title}]({vc.current.uri})", color=discord.Color.from_str("#ff90aa"))
        embed.add_field(name="Artist", value=vc.current.author, inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="checkplayer", description="Diagnostics: Check Lavalink connection and search.")
    async def checkplayer(self, interaction: discord.Interaction):
        await interaction.response.defer()
        node = wavelink.Pool.get_node("local-node")
        
        embed = discord.Embed(title="🎧 Player Diagnostics", color=discord.Color.from_str("#ff90aa"))
        
        if not node:
            embed.description = "❌ **Lavalink Node is NOT connected.**"
            return await interaction.followup.send(embed=embed)

        version_info = "Unknown"
        if hasattr(node, "server_version"): 
             version_info = node.server_version
        
        embed.add_field(name="1. Lavalink Node", value=f"✅ Connected\nID: `{node.identifier}`\nVersion: `{version_info}`", inline=False)

        try:
            tracks = await wavelink.Playable.search("ytsearch:Rick Astley Never Gonna Give You Up")
            if tracks:
                embed.add_field(name="2. Search & Access", value=f"✅ **Success**\nFound: {tracks[0].title}", inline=False)
            else:
                embed.add_field(name="2. Search & Access", value="❌ **Failed** (No results)", inline=False)
        except Exception as e:
             embed.add_field(name="2. Search & Access", value=f"❌ **Error**: {e}", inline=False)

        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Player(bot))
