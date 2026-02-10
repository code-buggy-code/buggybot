import discord
from discord import app_commands
from discord.ext import commands
import wavelink
import asyncio
import subprocess
import os
import sys
import socket
import urllib.request
import platform
from typing import cast

# --- CONFIGURATION ---
LAVALINK_URI = "http://127.0.0.1:2333"
LAVALINK_PASS = "youshallnotpass"
LAVALINK_JAR_URL = "https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar"
JAR_NAME = "Lavalink.jar"
CONFIG_NAME = "application.yml"
PORT = 2333
# ---------------------

class MusicPlayer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =========================================================================
    #  SECTION 1: SERVER MANAGEMENT & LAUNCHER LOGIC
    # =========================================================================

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    async def stop_existing_process(self):
        """Kills any process listening on port 2333."""
        if not self.is_port_in_use(PORT):
            return
        
        print(f"[Bot] Port {PORT} in use. Killing old process...")
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "java.exe"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["pkill", "-f", "Lavalink"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(3)

    async def update_lavalink(self):
        """Downloads the latest Lavalink.jar."""
        if not os.path.exists(JAR_NAME):
            print(f"[Bot] Downloading {JAR_NAME}...")
            try:
                opener = urllib.request.build_opener()
                opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
                urllib.request.install_opener(opener)
                await asyncio.to_thread(urllib.request.urlretrieve, LAVALINK_JAR_URL, JAR_NAME)
            except Exception as e:
                print(f"[Bot] Download failed: {e}")
                return False
        return True

    async def check_config(self):
        """Creates application.yml if missing."""
        if not os.path.exists(CONFIG_NAME):
            print(f"[Bot] Creating default {CONFIG_NAME}...")
            default_config = """
server:
  port: 2333
  address: 0.0.0.0
lavalink:
  server:
    password: "youshallnotpass"
    sources:
      youtube: true
      bandcamp: true
      soundcloud: true
      twitch: true
      vimeo: true
      http: true
      local: false
    bufferDurationMs: 400
    frameBufferDurationMs: 5000
    opusEncodingQuality: 10
    resamplingQuality: LOW
    trackStuckThresholdMs: 10000
"""
            with open(CONFIG_NAME, "w") as f:
                f.write(default_config)

    async def launch_java_process(self):
        """Starts the Java process."""
        if not os.path.exists(JAR_NAME): return False
        
        cmd = ["java", "-jar", JAR_NAME]
        try:
            if platform.system() == "Windows":
                 subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                 subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"[Bot] Failed to launch Java: {e}")
            return False

    @app_commands.command(name="checkplayer", description="Diagnostics: Restarts Lavalink and connects.")
    async def checkplayer(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        
        # Check active connection
        nodes = wavelink.Pool.nodes.values()
        active = [n for n in nodes if n.status == wavelink.NodeStatus.CONNECTED]
        if active:
            await interaction.followup.send(f"✅ **Player is online.** ({len(active)} node(s))")
            return

        await interaction.followup.send("🔄 **Player offline.** Restarting system...")

        await self.stop_existing_process()
        if not await self.update_lavalink():
            await interaction.followup.send("❌ Update failed.")
            return
        await self.check_config()
        
        if not await self.launch_java_process():
            await interaction.followup.send("❌ Java launch failed.")
            return

        # Wait for port
        connected_to_port = False
        for i in range(30):
            if self.is_port_in_use(PORT):
                connected_to_port = True
                break
            await asyncio.sleep(1)

        if not connected_to_port:
            await interaction.followup.send("❌ Server launched but port didn't open.")
            return

        # Connect Wavelink
        node = wavelink.Node(identifier="AutoNode", uri=LAVALINK_URI, password=LAVALINK_PASS)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
            await interaction.followup.send("✅ **System Restored!** You can now use `/play`.")
        except Exception as e:
            await interaction.followup.send(f"❌ Connection Error: `{e}`")


    # =========================================================================
    #  SECTION 2: MUSIC PLAYING LOGIC
    # =========================================================================

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """Callback when a track finishes. Plays the next track in queue."""
        player = payload.player
        if not player:
            return

        # If queue is not empty, play next
        if not player.queue.is_empty:
            next_track = player.queue.get()
            await player.play(next_track)
        else:
            # If queue is empty, disconnect after a delay (optional) or just wait
            # await player.disconnect()
            pass

    @app_commands.command(name="play", description="Play a song from YouTube or other sources.")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.guild: return

        # Check if user is in a voice channel
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You must be in a voice channel!", ephemeral=True)
            return

        await interaction.response.defer()

        # Get or Connect Player
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                await interaction.followup.send(f"❌ Could not connect. Run `/checkplayer` first. Error: {e}")
                return

        # Search for Track
        tracks = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.followup.send(f"❌ No tracks found for `{query}`.")
            return

        track = tracks[0] # Get the first result

        # Add to Queue or Play Immediately
        if player.playing:
            await player.queue.put_wait(track)
            await interaction.followup.send(f"📝 Added to queue: **{track.title}**")
        else:
            await player.play(track)
            await interaction.followup.send(f"▶️ Playing: **{track.title}**")

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or not player.playing:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        
        await player.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped.")

    @app_commands.command(name="stop", description="Stop music and disconnect.")
    async def stop(self, interaction: discord.Interaction):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if player:
            await player.disconnect()
            await interaction.response.send_message("⏹️ Disconnected.")
        else:
            await interaction.response.send_message("❌ I'm not connected.", ephemeral=True)

    @app_commands.command(name="volume", description="Set volume (0-100).")
    async def volume(self, interaction: discord.Interaction, value: int):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            await interaction.response.send_message("❌ Not connected.", ephemeral=True)
            return

        vol = max(0, min(100, value))
        await player.set_volume(vol)
        await interaction.response.send_message(f"🔊 Volume set to {vol}%")

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue(self, interaction: discord.Interaction):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or player.queue.is_empty:
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        embed = discord.Embed(title="Current Queue")
        
        # Show first 10 tracks
        track_list = ""
        for i, track in enumerate(player.queue[:10], start=1):
            track_list += f"{i}. {track.title}\n"
            
        if len(player.queue) > 10:
            track_list += f"... and {len(player.queue) - 10} more."
            
        embed.description = track_list
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicPlayer(bot))
