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
import aiohttp
import signal

# Function List:
# class Player(commands.Cog)
# - __init__(bot)
# - is_port_in_use(port)
# - kill_process_on_port(port)
# - download_redbot_lavalink()
# - start_lavalink()
# - connect_nodes()
# - cog_load()
# - cog_unload()
# - update_lavalink(interaction)
# - play(interaction, search)
# - skip(interaction)
# - stop(interaction)
# - volume(interaction, level)
# - queue(interaction)
# - nowplaying(interaction)
# - checkplayer(interaction) <--- UPDATED: Shows real status
# - on_wavelink_track_start(payload)
# - on_wavelink_track_end(payload)
# - on_wavelink_track_exception(payload)
# def setup(bot)

class Player(commands.Cog):
    """Music commands using Wavelink and RedBot's Lavalink build."""
    
    def __init__(self, bot):
        self.bot = bot
        # Configuration
        self.java_path = "/usr/lib/jvm/java-17-openjdk-arm64/bin/java"
        self.lavalink_dir = "lavalink" 
        self.lavalink_jar = "Lavalink.jar"
        self.host = "localhost"
        self.port = 2333
        self.password = "youshallnotpass"
        self.download_url = "https://github.com/Cog-Creators/Lavalink-Jars/releases/latest/download/Lavalink.jar"
        self.lavalink_process = None

    def is_port_in_use(self, port: int) -> bool:
        """Checks if a port is already being used."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((self.host, port)) == 0

    def kill_process_on_port(self, port: int):
        """Finds the process on the specific port and kills it."""
        print(f"🔪 Player Cog: Hunting for process on port {port}...")
        try:
            cmd_pid = f"lsof -t -i:{port}"
            pid_res = subprocess.run(cmd_pid, shell=True, capture_output=True, text=True)
            pid = pid_res.stdout.strip()

            if pid:
                print(f"💥 Player Cog: Found PID {pid}. Killing it...")
                subprocess.run(f"kill -9 {pid}", shell=True)
                return True
            return True
        except Exception as e:
            print(f"⚠️ Player Cog: Failed to force kill process: {e}")
            return False

    async def download_redbot_lavalink(self):
        """Downloads the latest Lavalink.jar from RedBot's repo."""
        jar_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        
        if not os.path.exists(self.lavalink_dir):
            os.makedirs(self.lavalink_dir)

        print(f"⬇️  Player Cog: Downloading latest RedBot Lavalink.jar...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.download_url) as response:
                    if response.status == 200:
                        with open(jar_path, 'wb') as f:
                            while True:
                                chunk = await response.content.read(1024)
                                if not chunk: break
                                f.write(chunk)
                        print("✅ Player Cog: Download complete!")
                        return True
                    else:
                        print(f"❌ Player Cog: Download failed (Status: {response.status})")
                        return False
        except Exception as e:
            print(f"❌ Player Cog: Download error: {e}")
            return False

    async def start_lavalink(self):
        """Starts the Lavalink server using nohup."""
        # 1. Check Port
        if self.is_port_in_use(self.port):
            print(f"⚡ Player Cog: Port {self.port} is busy. Assuming Lavalink is running.")
            return

        # 2. Download if missing
        jar_path = os.path.join(os.getcwd(), self.lavalink_dir, self.lavalink_jar)
        if not os.path.exists(jar_path):
            print("⚠️ Player Cog: Lavalink.jar not found. Initial download...")
            if not await self.download_redbot_lavalink():
                return

        # 3. Start Process using nohup
        print(f"☕ Player Cog: Launching Lavalink via nohup...")
        try:
            # We explicitly allow writing to lavalink.log for debugging
            cmd = f"nohup {self.java_path} -jar {self.lavalink_jar} > lavalink.log 2>&1 &"
            
            subprocess.Popen(
                cmd,
                cwd=os.path.join(os.getcwd(), self.lavalink_dir),
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            print("✅ Player Cog: Lavalink process launched. Waiting for startup...")
            await asyncio.sleep(10) # Give it 10s to ensure it binds to port
        except Exception as e:
            print(f"❌ Player Cog: Failed to launch Java: {e}")

    async def connect_nodes(self):
        """Connects Wavelink to the Lavalink node."""
        # Wait for port to be open before connecting
        retries = 0
        while not self.is_port_in_use(self.port) and retries < 5:
            print(f"⏳ Player Cog: Waiting for Lavalink port {self.port}...")
            await asyncio.sleep(2)
            retries += 1
            
        nodes = [
            wavelink.Node(
                identifier="local-node",
                uri=f"http://{self.host}:{self.port}",
                password=self.password
            )
        ]
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self.bot, cache_capacity=100)
            print("✅ Player Cog: Wavelink connected to nodes!")
        except Exception as e:
            print(f"❌ Player Cog: Wavelink connection error: {e}")

    async def cog_load(self):
        """Called when bot loads this cog."""
        await self.start_lavalink()
        await self.connect_nodes()

    async def cog_unload(self):
        """Called when bot unloads this cog."""
        await self.stop_lavalink()
        try: await wavelink.Pool.close()
        except: pass
    
    async def stop_lavalink(self):
        # Placeholder for strict stop logic if needed later
        pass

    # --- UPDATER COMMAND ---

    @app_commands.command(name="update_lavalink", description="[Admin] Kills, Updates, and Restarts Lavalink.")
    async def update_lavalink(self, interaction: discord.Interaction):
        """Force kills existing Lavalink, updates jar, and restarts."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You must be an administrator to use this.", ephemeral=True)

        await interaction.response.defer()
        
        embed = discord.Embed(title="🔄 Updating Music System", color=discord.Color.blue())
        embed.add_field(name="Step 1", value="Cleaning up old processes... ⏳")
        msg = await interaction.followup.send(embed=embed)

        self.kill_process_on_port(self.port)
        await asyncio.sleep(3) 
        
        embed.set_field_at(0, name="Step 1", value="Cleaning up old processes... ✅")
        embed.add_field(name="Step 2", value="Downloading latest core... ⏳", inline=False)
        await msg.edit(embed=embed)

        success = await self.download_redbot_lavalink()
        if not success:
            embed.color = discord.Color.red()
            embed.set_field_at(1, name="Step 2", value="Downloading latest core... ❌ Failed!")
            return await msg.edit(embed=embed)

        embed.set_field_at(1, name="Step 2", value="Downloading latest core... ✅")
        embed.add_field(name="Step 3", value="Restarting services... ⏳", inline=False)
        await msg.edit(embed=embed)

        await self.start_lavalink()

        # Force reconnect logic
        try:
            node = wavelink.Pool.get_node("local-node")
            if node:
                await node.close() # Close old connection if exists
            await self.connect_nodes()
        except:
            pass

        embed.color = discord.Color.green()
        embed.set_field_at(2, name="Step 3", value="Restarting services... ✅")
        embed.description = "**Success!** The music system has been fully updated and restarted."
        await msg.edit(embed=embed)

    # --- MUSIC COMMANDS ---

    @app_commands.command(name="play", description="Play a song from YouTube/Spotify")
    @app_commands.describe(search="The song name or URL")
    async def play(self, interaction: discord.Interaction, search: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ Join a voice channel first!", ephemeral=True)

        await interaction.response.defer()
        
        # Ensure voice connection
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"❌ I couldn't join: {e}")
        else:
            vc: wavelink.Player = interaction.guild.voice_client

        vc.home = interaction.channel

        # CRITICAL: Verify Node Status before searching
        node = wavelink.Pool.get_node("local-node")
        if not node or node.status != wavelink.NodeStatus.CONNECTED:
             return await interaction.followup.send(
                 f"❌ **Music System Offline.**\n"
                 f"Status: `{node.status if node else 'None'}`\n"
                 "Please run `/update_lavalink` to restart it."
             )

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
            embed.description = "❌ **Node Object Missing (Code Logic Error).**"
            return await interaction.followup.send(embed=embed)

        # Get the REAL status
        status_str = str(node.status)
        status_emoji = "✅" if node.status == wavelink.NodeStatus.CONNECTED else "🔴"
        
        # Show Version
        version_info = "Unknown"
        if hasattr(node, "server_version"): 
             version_info = node.server_version
        
        embed.add_field(name="1. Lavalink Node", value=f"{status_emoji} Status: `{status_str}`\nID: `{node.identifier}`\nVersion: `{version_info}`", inline=False)

        if node.status != wavelink.NodeStatus.CONNECTED:
             embed.add_field(name="2. Search & Access", value="🚫 **Skipped** (Node not connected)", inline=False)
             embed.set_footer(text="Try running /update_lavalink to restart the server.")
        else:
            try:
                tracks = await wavelink.Playable.search("ytsearch:Rick Astley Never Gonna Give You Up")
                if tracks:
                    embed.add_field(name="2. Search & Access", value=f"✅ **Success**\nFound: {tracks[0].title}", inline=False)
                else:
                    embed.add_field(name="2. Search & Access", value="❌ **Failed** (No results)", inline=False)
            except Exception as e:
                 embed.add_field(name="2. Search & Access", value=f"❌ **Error**: {e}", inline=False)

        await interaction.followup.send(embed=embed)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player = payload.player
        if not player: return
        channel = getattr(player, 'home', None)
        if channel:
            try: await channel.send(embed=discord.Embed(description=f"🎵 Now Playing: **{payload.track.title}**", color=discord.Color.from_str("#ff90aa")))
            except: pass

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if player and not player.queue.is_empty:
            await player.play(player.queue.get())

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        print(f"❌ Track Exception: {payload.exception}")
        channel = getattr(payload.player, 'home', None)
        if channel:
            if "Must find action functions" in str(payload.exception):
                await channel.send(embed=discord.Embed(title="⚠️ Update Needed", description="YouTube updated! Run `/update_lavalink` to fix.", color=discord.Color.red()))
            else:
                await channel.send(f"⚠️ Error: `{payload.track.title}`")
        if payload.player and not payload.player.queue.is_empty:
            await payload.player.play(payload.player.queue.get())

async def setup(bot):
    await bot.add_cog(Player(bot))
