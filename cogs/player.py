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

    async def cog_load(self):
        """Called when the Cog is loaded. Attempts to connect to Lavalink automatically."""
        self.bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        """Connect to the Lavalink node if the server is running."""
        await self.bot.wait_until_ready()
        
        if wavelink.Pool.nodes:
            return

        node = wavelink.Node(
            identifier="AutoNode",
            uri=LAVALINK_URI,
            password=LAVALINK_PASS
        )
        
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
            print("[Music] ✅ Automatically connected to Lavalink node.")
        except Exception:
            print("[Music] ⚠️ Could not auto-connect to Lavalink. Run /checkplayer to start it.")

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
        # We check size to ensure it's not a corrupted 0kb file
        if not os.path.exists(JAR_NAME) or os.path.getsize(JAR_NAME) < 1000:
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
        """
        Creates or Overwrites application.yml with Lavalink v4 + YouTube Plugin config.
        We overwrite every time to ensure the plugin config is up to date.
        """
        print(f"[Bot] Updating {CONFIG_NAME} with YouTube Plugin config...")
        
        # This config includes the 'dev.lavalink.youtube' plugin 
        # which is REQUIRED for YouTube support in Lavalink v4+
        v4_config = """
server:
  port: 2333
  address: 0.0.0.0
lavalink:
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:1.11.1"
      repository: "https://maven.lavalink.dev/releases"
  server:
    password: "youshallnotpass"
    sources:
      # The youtube plugin handles youtube/youtube_music
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
plugins:
  youtube:
    enabled: true
    allowSearch: true
    allowDirectVideoIds: true
    allowDirectPlaylistIds: true
    clients:
      - MUSIC
      - ANDROID_TESTSUITE
      - WEB
      - TVHTML5EMBEDDED
"""
        with open(CONFIG_NAME, "w") as f:
            f.write(v4_config)

    async def launch_java_process(self):
        """Starts the Java process."""
        if not os.path.exists(JAR_NAME): return False
        
        cmd = ["java", "-jar", JAR_NAME]
        try:
            print("[Bot] Starting Java Process (This may take 30s to download plugins)...")
            if platform.system() == "Windows":
                 subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                 subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"[Bot] Failed to launch Java: {e}")
            return False

    @app_commands.command(name="checkplayer", description="Diagnostics: Always restarts Lavalink and reconnects.")
    async def checkplayer(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        
        # Check active connection just for logging, but don't stop
        nodes = wavelink.Pool.nodes.values()
        active = [n for n in nodes if n.status == wavelink.NodeStatus.CONNECTED]
        
        status_msg = f"✅ **Player was online** ({len(active)} node(s))." if active else "⚠️ **Player offline.**"
        await interaction.followup.send(f"{status_msg}\n🔄 **Initiating full restart sequence...**")

        # 1. Stop Old Server
        await self.stop_existing_process()
        
        # 2. Download Update (only if missing or tiny)
        if not await self.update_lavalink():
            await interaction.followup.send("❌ Update failed.")
            return
            
        # 3. Ensure Config is correct
        await self.check_config()
        
        # 4. Start Java
        if not await self.launch_java_process():
            await interaction.followup.send("❌ Java launch failed.")
            return

        # 5. Wait for port (Increased timeout for plugin download)
        connected_to_port = False
        await interaction.edit_original_response(content="⏳ **Server Starting...** (Downloading YouTube plugins, please wait ~45s)...")
        
        for i in range(60): # 60 seconds timeout
            if self.is_port_in_use(PORT):
                connected_to_port = True
                break
            await asyncio.sleep(1)

        if not connected_to_port:
            await interaction.followup.send("❌ **Timeout:** Server launched but port didn't open. Check the opened Java window for errors.")
            return

        # 6. Connect Wavelink
        node = wavelink.Node(identifier="AutoNode", uri=LAVALINK_URI, password=LAVALINK_PASS)
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
            await interaction.followup.send("✅ **System Restored!** YouTube Plugin enabled. Try `/play` now.")
        except Exception as e:
            await interaction.followup.send(f"❌ Connection Error: `{e}`")


    # =========================================================================
    #  SECTION 2: MUSIC PLAYING LOGIC
    # =========================================================================

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player = payload.player
        if player:
            print(f"[Music] Started playing: {payload.track.title}")

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        print(f"[Music] ❌ Track Exception: {payload.exception}")
        
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player: return
        if not player.queue.is_empty:
            next_track = player.queue.get()
            await player.play(next_track)

    @app_commands.command(name="play", description="Play a song from YouTube or other sources.")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.guild: return
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You must be in a voice channel!", ephemeral=True)
            return

        if not wavelink.Pool.nodes:
            await interaction.response.send_message("❌ Player server disconnected. Run `/checkplayer`.", ephemeral=True)
            return

        await interaction.response.defer()

        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=wavelink.Player, self_deaf=True)
                await player.set_volume(100)
            except Exception as e:
                await interaction.followup.send(f"❌ Connection Error: {e}")
                return

        # Auto-add ytsearch if not a URL
        if not urllib.parse.urlparse(query).scheme:
            query = f"ytsearch:{query}"

        try:
            tracks = await wavelink.Playable.search(query)
        except Exception as e:
            await interaction.followup.send(f"❌ Search Error: {e}")
            return

        if not tracks:
            await interaction.followup.send(f"❌ No tracks found for `{query}`. (YouTube plugin might still be loading)")
            return

        if isinstance(tracks, wavelink.Playlist):
            for track in tracks:
                if player.playing:
                    await player.queue.put_wait(track)
                else:
                    await player.play(track)
            await interaction.followup.send(f"✅ Added playlist **{tracks.name}** to queue.")
        else:
            track = tracks[0]
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
            await interaction.response.send_message("❌ Not connected.", ephemeral=True)

    @app_commands.command(name="volume", description="Set volume (0-100).")
    async def volume(self, interaction: discord.Interaction, value: int):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if player:
            vol = max(0, min(100, value))
            await player.set_volume(vol)
            await interaction.response.send_message(f"🔊 Volume set to {vol}%")

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue(self, interaction: discord.Interaction):
        player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or player.queue.is_empty:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)
            return
        
        embed = discord.Embed(title="Current Queue")
        desc = ""
        for i, track in enumerate(player.queue[:10], start=1):
            desc += f"{i}. {track.title}\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicPlayer(bot))
