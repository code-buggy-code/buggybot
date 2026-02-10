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
import aiohttp # We use this to download the file asynchronously

# Function List:
# class Player(commands.Cog)
# - __init__(bot)
# - is_port_in_use(port)
# - download_redbot_lavalink() <--- NEW: INTEGRATED UPDATER
# - start_lavalink()
# - cog_load()
# - play(interaction, search)
# - skip(interaction)
# - stop(interaction)
# - volume(interaction, level)
# - queue(interaction)
# - nowplaying(interaction)
# - checkplayer(interaction)
# - updatenode(interaction) <--- NEW: Force update command
# - on_wavelink_track_start(payload)
# - on_wavelink_track_end(payload)
# - on_wavelink_track_exception(payload)
# def setup(bot)

class Player(commands.Cog):
    """Music commands using Wavelink and RedBot's Lavalink build."""
    
    def __init__(self, bot):
        self.bot = bot
        # Config
        self.java_path = "/usr/lib/jvm/java-17-openjdk-arm64/bin/java"
        self.lavalink_dir = "lavalink" 
        self.lavalink_jar = "Lavalink.jar"
        self.host = "localhost"
        self.port = 2333
        self.password = "youshallnotpass"
        # The official Red-DiscordBot jar source (Patched for YouTube)
        self.download_url = "https://github.com/Cog-Creators/Lavalink-Jars/releases/latest/download/Lavalink.jar"

    def is_port_in_use(self, port: int) -> bool:
        """Checks if a port is already being used by another process."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((self.host, port)) == 0

    async def download_redbot_lavalink(self):
        """Downloads the latest Lavalink.jar from RedBot's repo."""
        jar_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        
        # Ensure directory exists
        if not os.path.exists(self.lavalink_dir):
            os.makedirs(self.lavalink_dir)

        print(f"⬇️  Player Cog: Downloading RedBot's Lavalink.jar...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.download_url) as response:
                    if response.status == 200:
                        with open(jar_path, 'wb') as f:
                            while True:
                                chunk = await response.content.read(1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                        print("✅ Player Cog: Download complete!")
                        return True
                    else:
                        print(f"❌ Download failed with status: {response.status}")
                        return False
        except Exception as e:
            print(f"❌ Download error: {e}")
            return False

    async def start_lavalink(self):
        """Starts the Lavalink server."""
        # 1. Check Port
        if self.is_port_in_use(self.port):
            print(f"⚡ Player Cog: Port {self.port} is busy. Connecting to existing Lavalink.")
            return

        # 2. Check existence / Download if missing
        jar_full_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        if not os.path.exists(jar_full_path):
            print("⚠️ Player Cog: Lavalink.jar not found. Downloading now...")
            success = await self.download_redbot_lavalink()
            if not success:
                print("❌ Player Cog: Startup Aborted due to download failure.")
                return

        # 3. Start Process
        print(f"☕ Player Cog: Starting RedBot's Lavalink...")
        try:
            subprocess.Popen(
                [self.java_path, "-jar", self.lavalink_jar],
                cwd=os.path.join(os.getcwd(), self.lavalink_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await asyncio.sleep(5) # Let Java initialize
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
            # If the RedBot jar fails, it usually means we need an update
            if "Must find action functions" in exception:
                 embed = discord.Embed(
                    title="⚠️ Youtube Error",
                    description="It looks like the music engine needs an update. Try running `/updatenode`!",
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

    @app_commands.command(name="updatenode", description="Force updates the Lavalink jar (Admin only).")
    async def updatenode(self, interaction: discord.Interaction):
        """Forces a re-download of the Lavalink jar."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You need Admin permissions.", ephemeral=True)
        
        await interaction.response.defer()
        success = await self.download_redbot_lavalink()
        
        if success:
            await interaction.followup.send("✅ **Update Complete!** Please restart the bot to apply the new music engine.")
        else:
            await interaction.followup.send("❌ Update failed. Check console logs.")

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
