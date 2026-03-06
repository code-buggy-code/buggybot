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
# - on_wavelink_track_start(payload)
# - on_wavelink_track_exception(payload)
# - on_voice_state_update(member, before, after)
# - _connect_and_sync(interaction)
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
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        """Fired when a track starts playing."""
        print(f"🎵 Now playing: {payload.track.title}")

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
        """Fired when a track fails to play. Helps us debug leaving issues."""
        print(f"❌ Track Exception: {payload.exception}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Monitor why the bot is leaving."""
        if member.id == self.bot.user.id and before.channel is not None and after.channel is None:
            print(f"⚠️ Bot left voice channel: {before.channel.name}. Deleting player state.")

    async def _connect_and_sync(self, interaction: discord.Interaction) -> wavelink.Player:
        """Internal helper to force a stable connection for Lavalink v4."""
        # 1. Self-deafen often prevents Discord from killing the session prematurely
        # Using self_deaf=True is a standard stability practice
        vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player, timeout=60.0, self_deaf=True)
        
        # 2. Aggressive handshake wait
        # We wait for the node, channel, and a session ID to be populated.
        for _ in range(15): 
            await asyncio.sleep(1)
            if vc.channel and vc.node and vc.guild:
                # If the player already has a session ID, we're likely ready
                if getattr(vc, 'session_id', None):
                    break
        
        # 3. Final stabilization delay
        await asyncio.sleep(2)
        vc.autoplay = wavelink.AutoPlayMode.partial
        return vc

    # --- SLASH COMMANDS ---

    @app_commands.command(name="play", description="Play music from YouTube")
    @app_commands.describe(query="The URL or search query")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play music."""
        await interaction.response.defer()

        try:
            tracks: wavelink.Search = await wavelink.Playable.search(query)
        except Exception as e:
            return await interaction.followup.send(f"❌ Search error: `{e}`")

        if not tracks:
            return await interaction.followup.send("❌ No tracks found.")

        if not interaction.user.voice:
            return await interaction.followup.send("❌ Join a VC first!")

        vc: wavelink.Player = interaction.guild.voice_client
        
        if not vc:
            try:
                vc = await self._connect_and_sync(interaction)
            except Exception as e:
                return await interaction.followup.send(f"❌ Handshake failed: `{e}`")
        
        if not vc or not vc.channel:
             return await interaction.followup.send("❌ Player failed to sync. Try again, buggy!")

        # Queue
        if isinstance(tracks, wavelink.Playlist):
            for track in tracks.tracks:
                vc.queue.put(track)
            await interaction.followup.send(f"🎵 Added playlist **{tracks.name}**.")
        else:
            track = tracks[0]
            vc.queue.put(track)
            await interaction.followup.send(f"🎵 Added **{track.title}**.")

        # Play
        if not vc.playing:
            try:
                # One last sleep to make sure the DELETE command from Lavalink doesn't race us
                await asyncio.sleep(2)
                await vc.play(vc.queue.get(), add_history=True)
            except Exception as e:
                print(f"Play error: {e}")
                await interaction.followup.send("⚠️ Sync error. Re-running /play usually fixes it!")

    @app_commands.command(name="leave", description="Stop music and leave")
    async def leave(self, interaction: discord.Interaction):
        """Leave channel."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ Not in a VC.", ephemeral=True)
        await vc.disconnect()
        await interaction.response.send_message("👋 Bye!")

    @app_commands.command(name="pause", description="Pause music")
    async def pause(self, interaction: discord.Interaction):
        """Pause."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Not playing.", ephemeral=True)
        await vc.pause(True)
        await interaction.response.send_message("⏸️ Paused.")

    @app_commands.command(name="resume", description="Resume music")
    async def resume(self, interaction: discord.Interaction):
        """Resume."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.paused:
            return await interaction.response.send_message("❌ Not paused.", ephemeral=True)
        await vc.pause(False)
        await interaction.response.send_message("▶️ Resumed.")

    @app_commands.command(name="skip", description="Skip song")
    async def skip(self, interaction: discord.Interaction):
        """Skip."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            return await interaction.response.send_message("❌ Not playing.", ephemeral=True)
        await vc.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped.")

    @app_commands.command(name="go_back", description="Previous song")
    async def go_back(self, interaction: discord.Interaction):
        """Back."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ Not in VC.", ephemeral=True)
        history = list(vc.queue.history)
        if not history:
            return await interaction.response.send_message("❌ No history.", ephemeral=True)
        track = history[-1]
        if vc.current:
            vc.queue.put_at_front(vc.current)
        await vc.play(track)
        await interaction.response.send_message(f"⏮️ Back to **{track.title}**")

    @app_commands.command(name="queue", description="Show queue")
    async def queue(self, interaction: discord.Interaction):
        """Queue list."""
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or vc.queue.is_empty:
            return await interaction.response.send_message("📝 Queue empty.", ephemeral=True)
        q = list(vc.queue)[:10]
        desc = "\n".join([f"**{i+1}.** {t.title}" for i, t in enumerate(q)])
        if len(vc.queue) > 10:
            desc += f"\n*...and {len(vc.queue)-10} more*"
        await interaction.response.send_message(embed=discord.Embed(title="🎶 Queue", description=desc, color=discord.Color.blue()))

async def setup(bot):
    await bot.add_cog(Player(bot))
