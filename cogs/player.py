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
import time

# --- CONFIGURATION ---
LAVALINK_URI = "http://127.0.0.1:2333"
LAVALINK_PASS = "youshallnotpass"
LAVALINK_JAR_URL = "https://github.com/lavalink-devs/Lavalink/releases/latest/download/Lavalink.jar"
JAR_NAME = "Lavalink.jar"
CONFIG_NAME = "application.yml"
PORT = 2333
# ---------------------

class MusicAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    async def stop_existing_process(self):
        """Kills any process listening on port 2333."""
        if not self.is_port_in_use(PORT):
            return "Port was clear."

        print(f"[Bot] Port {PORT} in use. Killing old process...")
        
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "java.exe"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["pkill", "-f", "Lavalink"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Non-blocking wait for port release
        await asyncio.sleep(3)
        return "Old process killed."

    async def update_lavalink(self):
        """Downloads the latest Lavalink.jar."""
        print(f"[Bot] Downloading {JAR_NAME}...")
        
        def download():
            try:
                opener = urllib.request.build_opener()
                opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
                urllib.request.install_opener(opener)
                urllib.request.urlretrieve(LAVALINK_JAR_URL, JAR_NAME)
                return True
            except Exception as e:
                print(f"[Bot] Update failed: {e}")
                return False

        # Run blocking download in a separate thread so bot doesn't freeze
        success = await asyncio.to_thread(download)
        
        if not success and not os.path.exists(JAR_NAME):
            return False # Failed and no backup
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
            # File I/O is fast enough to do here, or could use to_thread
            with open(CONFIG_NAME, "w") as f:
                f.write(default_config)

    async def launch_java_process(self):
        """Starts the Java process in the background."""
        if not os.path.exists(JAR_NAME):
            return False

        print("[Bot] Launching Java...")
        cmd = ["java", "-jar", JAR_NAME]

        # We launch it as a subprocess.
        # On Windows, using 'start' via shell=True keeps it independent-ish, 
        # but for simple bot hosting, a standard Popen is usually fine.
        try:
            if platform.system() == "Windows":
                 # Create_new_console flag prevents it from cluttering bot terminal
                 subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                 # Linux/Mac
                 subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"[Bot] Failed to launch Java: {e}")
            return False

    @app_commands.command(name="checkplayer", description="Fully restarts and connects the Lavalink audio player.")
    async def checkplayer(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        # 1. Check if already connected
        nodes = wavelink.Pool.nodes.values()
        active = [n for n in nodes if n.status == wavelink.NodeStatus.CONNECTED]
        if active:
            await interaction.followup.send(f"✅ **Player is already online.** ({len(active)} node(s))")
            return

        await interaction.followup.send("🔄 **Player offline.** Initiating full restart sequence...\n"
                                      "1️⃣ Stopping old processes...\n"
                                      "2️⃣ Checking for updates...\n"
                                      "3️⃣ Launching Server...")

        # 2. Stop Old Process
        await self.stop_existing_process()

        # 3. Update & Config
        if not await self.update_lavalink():
            await interaction.followup.send("❌ **Update Failed:** Could not download Lavalink.jar.")
            return
        await self.check_config()

        # 4. Launch Java
        launched = await self.launch_java_process()
        if not launched:
            await interaction.followup.send("❌ **Launch Failed:** Is Java installed?")
            return

        # 5. Wait for Port 2333 to be active (Max 30 seconds)
        await interaction.edit_original_response(content="⏳ **Server starting...** Waiting for connection (max 30s)...")
        
        connected_to_port = False
        for i in range(30):
            if self.is_port_in_use(PORT):
                connected_to_port = True
                break
            await asyncio.sleep(1)

        if not connected_to_port:
            await interaction.followup.send("❌ **Timeout:** Server launched but Port 2333 never opened. Check Java logs.")
            return

        # 6. Connect Wavelink
        node = wavelink.Node(
            identifier="AutoNode",
            uri=LAVALINK_URI,
            password=LAVALINK_PASS
        )
        
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
            await interaction.followup.send("✅ **Success!** Lavalink server updated, restarted, and connected.")
        except Exception as e:
            await interaction.followup.send(f"❌ **Connection Error:** Server is up, but bot couldn't handshake.\nError: `{e}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicAdmin(bot))
