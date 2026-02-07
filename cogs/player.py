import discord
from discord.ext import commands
import asyncio
import yt_dlp
import functools
import math
import random
import traceback

# List of functions in this code:
# - YTDLSource.__init__
# - YTDLSource.create_source
# - YTDLSource.regather_stream
# - VoiceState.__init__
# - VoiceState.__del__
# - VoiceState.audio_player_task
# - VoiceState.play_next_song
# - VoiceState.skip
# - VoiceState.stop
# - Player.__init__
# - Player.get_voice_state
# - Player.cog_unload
# - Player.cog_before_invoke
# - Player.cog_command_error
# - Player.join
# - Player.leave
# - Player.play
# - Player._play_playlist
# - Player.now
# - Player.pause
# - Player.resume
# - Player.stop
# - Player.skip
# - Player.queue
# - Player.ensure_voice
# - setup (NEW: Required to load the cog)

# Silence useless bug reports messages
yt_dlp.utils.bug_reports_message = lambda: ''

class VoiceError(Exception):
    pass

class YTDLError(Exception):
    pass

class YTDLSource(discord.PCMVolumeTransformer):
    """
    Standard YTDLSource class, adapted to mirror Redbot's extraction logic.
    """
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'geo_bypass': True,
        # 'cookiefile': 'cookies.txt', # Uncomment if you have a cookies.txt file
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)
        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data
        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4] if date else "Unknown"
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration', 0)))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @staticmethod
    def parse_duration(duration: int):
        if duration > 0:
            minutes, seconds = divmod(duration, 60)
            hours, minutes = divmod(minutes, 60)
            days, hours = divmod(hours, 24)

            duration_str = []
            if days > 0:
                duration_str.append('{}'.format(days))
            if hours > 0:
                duration_str.append('{}'.format(hours))
            if minutes > 0:
                duration_str.append('{}'.format(minutes))
            duration_str.append('{}'.format(seconds))
            
            return ':'.join(duration_str)
        return "LIVE"

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @classmethod
    async def regather_stream(cls, source, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()
        requester = source.requester
        
        partial = functools.partial(cls.ytdl.extract_info, source.url, download=False)
        data = await loop.run_in_executor(None, partial)
        
        return cls(source.ctx, discord.FFmpegPCMAudio(data['url'], **cls.FFMPEG_OPTIONS), data=data)


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = asyncio.Queue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            try:
                # Wait for the next song. If we time out, stop the player.
                async with asyncio.timeout(300): # 5 minutes
                    item = await self.songs.get()
            except asyncio.TimeoutError:
                self.bot.loop.create_task(self.stop())
                return

            # Ensure we have a valid voice connection before trying to play
            if not self.voice:
                # Try to recover if ctx.voice_client exists
                if self._ctx.voice_client:
                    self.voice = self._ctx.voice_client
                else:
                    # If we really aren't connected, we can't play.
                    # Re-queue the item so we don't lose it while we wait/fail
                    await self.songs.put(item)
                    await asyncio.sleep(1)
                    continue

            if isinstance(item, str):
                try:
                    source = await YTDLSource.create_source(self._ctx, item, loop=self.bot.loop)
                    self.current = source
                except Exception as e:
                    await self._ctx.send(f"Error processing track: `{str(e)}`. Skipping to next...")
                    traceback.print_exc()
                    self.next.set()
                    continue
            else:
                self.current = item

            self.current.volume = self._volume
            try:
                self.voice.play(self.current, after=self.play_next_song)
                await self.current.channel.send(embed=self.current.create_embed())
            except Exception as e:
                await self._ctx.send(f"Playback Error: `{str(e)}`")
                traceback.print_exc()
                self.next.set()
            
            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            print(f"Player error: {error}")
        self.next.set()

    def skip(self):
        self.skip_votes.clear()
        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs._queue.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

class Player(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state
        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')
        return True

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send('An error occurred: {}'.format(str(error)))

    @commands.command(name='join', invoke_without_subcommand=True)
    async def _join(self, ctx: commands.Context):
        """Joins a voice channel."""
        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon')
    @commands.has_permissions(manage_guild=True)
    async def _summon(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):
        """Summons the bot to a voice channel. (Admin only)"""
        if not channel and not ctx.author.voice:
            raise VoiceError('You are neither connected to a voice channel nor specified a channel to join.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    @commands.has_permissions(manage_guild=True)
    async def _leave(self, ctx: commands.Context):
        """Clears the queue and leaves the voice channel."""
        if not ctx.voice_state.voice:
            return await ctx.send('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.command(name='play')
    async def _play(self, ctx: commands.Context, *, search: str):
        """Plays a song. Redbot-style playlist support."""
        
        # 1. Handle Voice Connection explicitly
        if not ctx.voice_state.voice:
            if ctx.author.voice:
                try:
                    ctx.voice_state.voice = await ctx.author.voice.channel.connect()
                except discord.ClientException:
                    # It might be connected but the state is desynced
                    ctx.voice_state.voice = ctx.guild.voice_client
                except Exception as e:
                    return await ctx.send(f"❌ Failed to join voice channel: {e}")
            else:
                return await ctx.send("❌ You need to be in a voice channel first!")
        
        # 2. Immediate feedback
        msg = await ctx.send(f"🔎 **Searching** for `{search}`...")

        async with ctx.typing():
            # Tweak: music.youtube.com handling
            if "music.youtube.com" in search:
                search = search.replace("music.youtube.com", "www.youtube.com")

            ydl_opts_flat = {
                'extract_flat': True, 
                'skip_download': True,
                'quiet': True,
                'ignoreerrors': True,
                'default_search': 'auto',
                'source_address': '0.0.0.0',
            }
            
            partial = functools.partial(yt_dlp.YoutubeDL(ydl_opts_flat).extract_info, search, download=False)
            try:
                info = await self.bot.loop.run_in_executor(None, partial)
            except Exception as e:
                return await msg.edit(content=f"❌ Error during search: {e}")

            if info is None:
                return await msg.edit(content="❌ Could not find any matches.")

            # Check if it's a playlist
            if 'entries' in info and (info.get('_type') == 'playlist' or info.get('_type') == 'multi_video'):
                await self._play_playlist(ctx, info, msg)
            else:
                # Single video logic
                try:
                    if 'entries' in info:
                        info = info['entries'][0]
                        
                    url = info.get('webpage_url') or info.get('url')
                    if not url:
                        # Fallback for search results that might be bare
                        url = info.get('id')
                        
                    source = await YTDLSource.create_source(ctx, url, loop=self.bot.loop)
                except YTDLError as e:
                    await msg.edit(content=f'❌ An error occurred: {str(e)}')
                else:
                    song = source
                    await ctx.voice_state.songs.put(song)
                    await msg.edit(content=f'✅ Enqueued **{source.title}**')

    async def _play_playlist(self, ctx, info, msg):
        """
        Redbot-style lazy queueing.
        """
        entries = info['entries']
        count = 0
        
        for entry in entries:
            if not entry: 
                continue
            
            # Extract URL safely from lazy entry
            url = entry.get('url') or entry.get('webpage_url')
            if not url and 'id' in entry:
                url = f"https://www.youtube.com/watch?v={entry['id']}"
            
            if url:
                await ctx.voice_state.songs.put(url)
                count += 1

        title = info.get('title', 'Unknown Playlist')
        await msg.edit(content=f"✅ Enqueued **{count}** songs from playlist **{title}**.")

    @commands.command(name='now', aliases=['np', 'current', 'playing'])
    async def _now(self, ctx: commands.Context):
        """Displays the currently playing song."""
        if not ctx.voice_state.is_playing:
            return await ctx.send('Not playing any music right now.')
        
        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.command(name='pause')
    async def _pause(self, ctx: commands.Context):
        """Pauses the currently playing song."""
        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='resume')
    async def _resume(self, ctx: commands.Context):
        """Resumes a currently paused song."""
        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='stop')
    async def _stop(self, ctx: commands.Context):
        """Stops playing song and clears the queue."""
        ctx.voice_state.songs._queue.clear()

        if ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.message.add_reaction('⏹')

    @commands.command(name='skip')
    async def _skip(self, ctx: commands.Context):
        """Vote to skip a song. The requester can automatically skip."""
        if not ctx.voice_state.is_playing:
            return await ctx.send('Not playing any music right now.')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 3:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send(f'Skip vote added, currently at **{total_votes}/3**')
        else:
            await ctx.send('You have already voted to skip this song.')

    @commands.command(name='queue')
    async def _queue(self, ctx: commands.Context, page: int = 1):
        """Shows the player's queue."""
        if len(ctx.voice_state.songs._queue) == 0:
            return await ctx.send('Empty queue.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs._queue) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(list(ctx.voice_state.songs._queue)[start:end], start=start):
            if isinstance(song, str):
                queue += f'`{i+1}.` {song} (Pending processing)\n'
            else:
                queue += f'`{i+1}.` [**{song.source.title}**]({song.source.url})\n'

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs._queue), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    async def ensure_voice(self, ctx):
        """Legacy helper, kept for compatibility if referenced elsewhere."""
        if ctx.voice_client is None:
            if ctx.author.voice:
                ctx.voice_state.voice = await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        else:
            ctx.voice_state.voice = ctx.voice_client

async def setup(bot):
    await bot.add_cog(Player(bot))
