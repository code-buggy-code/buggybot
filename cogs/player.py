
import discord
import wavelink
from discord.ext import commands
from typing import cast

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def setup_hook(self):
        """
        Connects to the Lavalink Server when the cog is loaded.
        """
        # We need to connect to the Lavalink node. 
        # Standard default configuration for Lavalink is:
        # URI: http://localhost:2333
        # Password: youshallnotpass
        node: wavelink.Node = wavelink.Node(
            uri='http://localhost:2333', 
            password='youshallnotpass'
        )
        await wavelink.Pool.connect(client=self.bot, nodes=[node])

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        print(f"Lavalink Node connected: {payload.node.identifier}")

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        if not player:
            return
        
        original_requester = getattr(payload.track.extras, "requester", None)
        embed = discord.Embed(
            title="Now Playing",
            description=f"[{payload.track.title}]({payload.track.uri})",
            color=discord.Color.blurple()
        )
        if original_requester:
            embed.set_footer(text=f"Requested by {original_requester}")
        
        if player.home:
            channel = self.bot.get_channel(player.home)
            if channel:
                await channel.send(embed=embed)

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song from YouTube/SoundCloud/Spotify."""
        if not ctx.guild:
            return

        # 1. Check if user is in voice
        if not ctx.author.voice:
            return await ctx.send("You need to be in a voice channel to play music!")

        # 2. Get or Connect Player
        if not ctx.voice_client:
            try:
                player: wavelink.Player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
            except Exception as e:
                return await ctx.send("I couldn't connect to the voice channel.")
        else:
            player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)

        # 3. Set the "home" channel for notifications
        player.home = ctx.channel.id

        # 4. Search for the track
        # wavelink.Playable.search will automatically search YouTube by default if no URL is provided
        tracks: wavelink.Search = await wavelink.Playable.search(query)

        if not tracks:
            return await ctx.send("No tracks found with that query.")

        # 5. Add to queue
        if isinstance(tracks, wavelink.Playlist):
            # If it's a playlist, add all tracks
            added: int = await player.queue.put_wait(tracks)
            await ctx.send(f"Added playlist **{tracks.name}** ({added} songs) to the queue.")
        else:
            # If it's a single track, take the first result
            track: wavelink.Playable = tracks[0]
            # Store the requester ID in the track object for later reference
            track.extras = {"requester": ctx.author.display_name}
            
            await player.queue.put_wait(track)
            await ctx.send(f"Added **{track.title}** to the queue.")

        # 6. If not playing, start playing
        if not player.playing:
            await player.play(player.queue.get())

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        """Skip the current song."""
        if not ctx.voice_client:
            return
        
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        
        if not player.playing:
            return await ctx.send("Nothing is playing.")

        await player.skip(force=True)
        await ctx.send("Skipped! ⏭️")

    @commands.command(name="stop", aliases=["leave", "dc"])
    async def stop(self, ctx: commands.Context):
        """Stop music and clear the queue."""
        if not ctx.voice_client:
            return
        
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        await player.disconnect()
        await ctx.send("Disconnected. 👋")

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context):
        """Show the current queue."""
        if not ctx.voice_client:
            return
        
        player: wavelink.Player = cast(wavelink.Player, ctx.voice_client)
        
        if player.queue.is_empty and not player.playing:
            return await ctx.send("The queue is empty.")

        embed = discord.Embed(title="Music Queue", color=discord.Color.green())
        
        # Current track
        if player.current:
            embed.add_field(name="Now Playing", value=player.current.title, inline=False)

        # Upcoming tracks
        if not player.queue.is_empty:
            upcoming = ""
            for index, track in enumerate(player.queue):
                upcoming += f"{index + 1}. {track.title}\n"
                if index >= 9: # Only show next 10 songs
                    upcoming += "... and more"
                    break
            embed.add_field(name="Up Next", value=upcoming, inline=False)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Music(bot))
