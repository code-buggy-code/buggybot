import discord
import wavelink
import logging
from discord.ext import commands
from discord import app_commands
from typing import cast

# --- CONFIGURATION ---
# Redbot's method requires a Lavalink Node.
# You must have a Lavalink server running (usually on port 2333).
# If you don't have one, download Lavalink.jar and run it with `java -jar Lavalink.jar`
LAVALINK_HOST = "127.0.0.1"
LAVALINK_PORT = 2333
LAVALINK_PASS = "youshallnotpass" # Default Lavalink password

class RedAudio(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("buggybot.audio")

    async def cog_load(self):
        """Called when the cog is loaded. We use this to set up Wavelink."""
        self.bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        """Connects to the Lavalink backend node."""
        await self.bot.wait_until_ready()
        
        # Avoid duplicate nodes
        if wavelink.Pool.nodes:
            return

        node = wavelink.Node(
            identifier="BuggyNode",
            uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}",
            password=LAVALINK_PASS
        )
        
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
            self.logger.info(f"Connected to Lavalink Node: {node.identifier}")
            print(f"✅ [Audio] Successfully connected to Lavalink Node on {LAVALINK_HOST}:{LAVALINK_PORT}")
        except Exception as e:
            self.logger.error(f"Failed to connect to Lavalink: {e}")
            print(f"❌ [Audio] Could not connect to Lavalink. Is the Java server running? Error: {e}")

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        self.logger.info(f"Wavelink Node {payload.node.identifier} is ready!")

    # --- VERIFICATION COMMANDS ---

    @app_commands.command(name="verify_audio", description="Step 1 & 2: Check if Cog is loaded and Lavalink is connected.")
    async def verify_audio(self, interaction: discord.Interaction):
        """Verifies the audio system status step-by-step."""
        status_msg = "🔍 **Audio System Diagnostics**\n"
        
        # Step 1: Cog Check
        status_msg += "✅ **Step 1:** Cog is loaded and command received.\n"
        
        # Step 2: Node Check
        nodes = wavelink.Pool.nodes
        if nodes:
             status_msg += f"✅ **Step 2:** Lavalink Node Connected ({len(nodes)} active).\n"
        else:
             status_msg += "❌ **Step 2:** No Lavalink Node connected. Please check your Java server.\n"

        # Step 3: Voice Client Check
        if interaction.guild.voice_client:
            status_msg += f"✅ **Step 3:** Bot is connected to a voice channel in this server.\n"
        else:
             status_msg += "ℹ️ **Step 3:** Bot is idle (not connected to voice).\n"

        await interaction.response.send_message(status_msg, ephemeral=True)

    # --- MUSIC COMMANDS ---

    @app_commands.command(name="play", description="Plays a track or playlist from YouTube (Redbot style).")
    @app_commands.describe(search="The song link or name to search for.")
    async def play(self, interaction: discord.Interaction, search: str):
        """Plays music using Wavelink (Lavalink)."""
        await interaction.response.defer()

        # 1. Join Voice Channel
        if not interaction.user.voice:
            return await interaction.followup.send("❌ You are not in a voice channel!", ephemeral=True)
        
        destination = interaction.user.voice.channel
        
        # Connect if not already connected
        if not interaction.guild.voice_client:
            try:
                vc: wavelink.Player = await destination.connect(cls=wavelink.Player)
            except Exception as e:
                return await interaction.followup.send(f"❌ Failed to join voice channel: {e}")
        else:
            vc: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
            if vc.channel.id != destination.id:
                await vc.move_to(destination)

        # 2. Search/Load Track or Playlist
        try:
            tracks = await wavelink.Playable.search(search)
        except Exception as e:
            return await interaction.followup.send(f"❌ Error searching tracks: {e}")

        if not tracks:
            return await interaction.followup.send("❌ No tracks found.")

        # 3. Handle Playlist vs Single Track
        if isinstance(tracks, wavelink.Playlist):
            # Playlist logic
            added = 0
            for track in tracks:
                await vc.queue.put_wait(track)
                added += 1
            await interaction.followup.send(f"✅ Added **{added}** tracks from playlist **{tracks.name}** to the queue.")
            
            # Start playing if not already playing
            if not vc.playing:
                await vc.play(vc.queue.get())
                
        else:
            # Single track logic
            track = tracks[0]
            await vc.queue.put_wait(track)
            
            if not vc.playing:
                await vc.play(vc.queue.get())
                await interaction.followup.send(f"🎵 Now Playing: **{track.title}**")
            else:
                await interaction.followup.send(f"✅ Added to queue: **{track.title}**")

    @app_commands.command(name="stop", description="Stops music and clears the queue.")
    async def stop(self, interaction: discord.Interaction):
        vc: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not vc:
            return await interaction.response.send_message("I'm not playing anything.", ephemeral=True)
        
        await vc.stop()
        vc.queue.clear()
        await interaction.response.send_message("⏹️ Stopped playback and cleared queue.")

    @app_commands.command(name="skip", description="Skips the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not vc or not vc.playing:
            return await interaction.response.send_message("Nothing is playing to skip.", ephemeral=True)
        
        await vc.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped track.")

    @app_commands.command(name="queue", description="Shows the current queue.")
    async def queue(self, interaction: discord.Interaction):
        vc: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not vc or (not vc.playing and vc.queue.is_empty):
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        embed = discord.Embed(title="Current Queue", color=discord.Color.blurple())
        
        body = ""
        if vc.playing:
            body += f"**Now Playing:** {vc.current.title}\n\n"

        # Show next 10 songs
        for i, track in enumerate(vc.queue[:10], start=1):
            body += f"**{i}.** {track.title}\n"
            
        if len(vc.queue) > 10:
            body += f"\n*...and {len(vc.queue) - 10} more*"
            
        embed.description = body
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(RedAudio(bot))
```

### **Instructions for Buggy**

1.  **Install Requirements:**
    You need to install `wavelink` to use this code. Run this in your terminal:
    ```bash
    pip install wavelink
    ```

2.  **The "Redbot Method" (Crucial Step):**
    Redbot uses a Java application called **Lavalink** to handle the heavy audio processing. For this cog to work, you must have Lavalink running.
    * **Download:** [Lavalink.jar (GitHub)](https://github.com/lavalink-devs/Lavalink/releases)
    * **Run:** Open a terminal in the folder where you downloaded it and type: `java -jar Lavalink.jar`.
    * If you skip this, `Step 2` in the verification command will fail.

3.  **Loading the Cog:**
    Ensure your `main.py` is set up to load extensions. If you don't have an auto-loader, add this line in your `main.py` startup logic:
    ```python
    await bot.load_extension("cogs.red_audio")
