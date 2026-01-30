import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import random
import shutil

# Safe Import for yt_dlp (Required for YouTube, but optional for direct links)
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    print("⚠️ 'yt_dlp' not found. YouTube playback will be disabled. Direct URLs only.")

# Function/Class List:
# class MusicControls(discord.ui.View):
#   - __init__(self, cog, guild)
#   - back_button(self, interaction, button)
#   - pause_button(self, interaction, button)
#   - skip_button(self, interaction, button)
#   - queue_button(self, interaction, button)
#   - stop_button(self, interaction, button)
# class Music(commands.Cog):
#   - __init__(self, bot)
#   - load_config(self, guild_id)
#   - save_config(self, guild_id, config)
#   - get_ytdl_opts(self, flat=False)
#   - extract_stream_url(self, url)
#   - play_next_song(self, guild)
#   - stop_playback(self, interaction)
#   - play(self, interaction, query) [Slash]
#   - pause(self, interaction) [Slash]
#   - resume(self, interaction) [Slash]
#   - skip(self, interaction) [Slash]
#   - stop(self, interaction) [Slash]
#   - queue(self, interaction) [Slash]
#   - shuffle(self, interaction) [Slash]
#   - musicchannel(self, interaction, channel) [Slash]
#   - setup(bot)

# Check for FFMPEG
if not shutil.which("ffmpeg"):
    print("❌ Critical: 'ffmpeg' is missing from system PATH. Music will not work.")

# --- UI VIEW FOR CONTROLS ---

class MusicControls(discord.ui.View):
    def __init__(self, cog, guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        current = self.cog.current_song.get(self.guild.id)
        if not current: return

        elapsed = time.time() - current.get('start_time', 0)
        
        # Logic: If > 10s, Restart. If < 10s, Go Previous.
        if elapsed > 10:
            if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
            self.cog.music_queues[self.guild.id].insert(0, current)
            if self.guild.voice_client: self.guild.voice_client.stop()
        else:
            history = self.cog.history.get(self.guild.id, [])
            if history:
                prev_song = history.pop() 
                if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
                self.cog.music_queues[self.guild.id].insert(0, prev_song)
                if self.guild.voice_client: self.guild.voice_client.stop()
            else:
                if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
                self.cog.music_queues[self.guild.id].insert(0, current)
                if self.guild.voice_client: self.guild.voice_client.stop()

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            if vc.is_playing():
                vc.pause()
                await interaction.response.send_message("⏸️ Paused", ephemeral=True)
            elif vc.is_paused():
                vc.resume()
                await interaction.response.send_message("▶️ Resumed", ephemeral=True)
        else:
             await interaction.response.defer()

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped", ephemeral=True)
        else:
            await interaction.response.defer()

    @discord.ui.button(emoji="🍔", style=discord.ButtonStyle.secondary)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        q = self.cog.music_queues.get(self.guild.id, [])
        now_playing = self.cog.current_song.get(self.guild.id, {}).get('title', "Nothing")

        desc = f"**Now Playing:** {now_playing}\n\n**Up Next:**\n"
        for i, song in enumerate(q[:10], 1):
            desc += f"`{i}.` {song['title']} ({song['user']})\n"
        if len(q) > 10: desc += f"\n*...and {len(q)-10} more.*"

        embed = discord.Embed(title="🎵 Music Queue", description=desc, color=discord.Color(0xff90aa))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.stop_playback(interaction)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # FFmpeg options for streaming (Crucial for network streams)
        self.ffmpeg_options = {
            'options': '-vn',
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        }

        # Playback State
        self.music_queues = {} # {guild_id: [track_data, ...]}
        self.current_song = {} # {guild_id: track_data}
        self.history = {} # {guild_id: [track_data, ...]}

    # --- HELPERS ---

    def load_config(self, guild_id):
        if not hasattr(self.bot, 'db'): return {}
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {} 
        return data.get(str(guild_id), {"music_channel_id": 0, "shuffle": False})

    def save_config(self, guild_id, config):
        if not hasattr(self.bot, 'db'): return
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {}
        data[str(guild_id)] = config
        self.bot.db.save_collection("music_config", data)

    def get_ytdl_opts(self, flat=False):
        """Streaming optimized options. No downloads."""
        opts = {
            'format': 'bestaudio/best',
            'noplaylist': True, # We handle playlists manually
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
            'extract_flat': flat, # If True, don't fetch stream yet, just metadata (Fast)
        }
        return opts

    async def extract_stream_url(self, url):
        """Uses yt-dlp to find the real audio stream URL just in time."""
        if not yt_dlp: return url # Fallback for direct links
        
        loop = asyncio.get_running_loop()
        try:
            # We fetch the stream URL right before playing because they expire!
            with yt_dlp.YoutubeDL(self.get_ytdl_opts(flat=False)) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                return info.get('url') # The direct stream URL
        except Exception as e:
            print(f"Extraction Error: {e}")
            return None

    def play_next_song(self, guild):
        """Plays next song using direct FFmpeg streaming."""
        if guild.voice_client and guild.voice_client.is_playing(): return
        if guild.id not in self.music_queues or not self.music_queues[guild.id]:
            self.current_song.pop(guild.id, None)
            return

        # Pop next song
        track_data = self.music_queues[guild.id].pop(0)
        track_data['start_time'] = time.time()
        self.current_song[guild.id] = track_data

        # Async Wrapper to fetch stream URL then play
        async def start_streaming():
            stream_url = track_data['url']
            
            # If it's NOT a direct link (no extension), try to extract
            # This allows direct MP3s to work without yt-dlp
            if not any(x in stream_url for x in ['.mp3', '.ogg', '.wav', '.flac']):
                extracted = await self.extract_stream_url(stream_url)
                if extracted:
                    stream_url = extracted
                else:
                    print(f"❌ Could not extract stream for {track_data['title']}")
                    self.play_next_song(guild)
                    return

            try:
                # Direct Stream: Pass the URL directly to FFmpeg
                source = discord.FFmpegPCMAudio(stream_url, **self.ffmpeg_options)
            except Exception as e:
                print(f"❌ Source Error (FFmpeg): {e}")
                self.play_next_song(guild)
                return

            def after_playing(error):
                if error: print(f"❌ Player Error: {error}")
                
                # History
                if guild.id not in self.history: self.history[guild.id] = []
                self.history[guild.id].append(track_data)
                if len(self.history[guild.id]) > 20: self.history[guild.id].pop(0)

                # Next
                fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.5), self.bot.loop)
                try: fut.result()
                except: pass
                self.play_next_song(guild)

            if guild.voice_client:
                try:
                    guild.voice_client.play(source, after=after_playing)
                    # Embed
                    try:
                        config = self.load_config(guild.id)
                        if config.get('music_channel_id'):
                            channel = guild.get_channel(config['music_channel_id'])
                            if channel:
                                embed = discord.Embed(title="🎵 Now Playing", description=f"[{track_data['title']}]({track_data['url']})", color=discord.Color(0xff90aa))
                                embed.add_field(name="Requested By", value=track_data.get('user', 'Unknown'), inline=True)
                                view = MusicControls(self, guild)
                                await channel.send(embed=embed, view=view)
                    except: pass
                except Exception as e:
                    print(f"❌ Play Error: {e}")
                    self.play_next_song(guild)

        asyncio.run_coroutine_threadsafe(start_streaming(), self.bot.loop)

    async def stop_playback(self, interaction):
        self.music_queues[interaction.guild.id] = []
        self.history[interaction.guild.id] = []
        self.current_song.pop(interaction.guild.id, None)

        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            try: await vc.disconnect()
            except: pass
        
        msg = "🛑 Stopped playback, cleared queue, and left the channel."
        if not interaction.response.is_done(): await interaction.response.send_message(msg)
        else: await interaction.followup.send(msg)

    # --- SLASH COMMANDS (Playback) ---

    @app_commands.command(name="play", description="Play a song or playlist (YouTube/Direct URL).")
    @app_commands.describe(query="URL or Search Query")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play a song or playlist (YouTube/Direct URL)."""
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You are not in a voice channel!", ephemeral=True)

        await interaction.response.defer()

        # Join VC
        if not interaction.guild.voice_client:
            try: await interaction.user.voice.channel.connect()
            except Exception as e: return await interaction.followup.send(f"❌ Failed to join VC: {e}")
        else:
            if interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
                await interaction.guild.voice_client.move_to(interaction.user.voice.channel)

        guild_id = interaction.guild_id
        if guild_id not in self.music_queues: self.music_queues[guild_id] = []
        
        songs_added = 0
        loop = asyncio.get_running_loop()

        # LOGIC: Check if it's a Playlist
        if yt_dlp and ("list=" in query or "playlist" in query):
            try:
                # Extract Flat (Metadata only, FAST)
                opts = self.get_ytdl_opts(flat=True)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    data = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                
                if 'entries' in data:
                    entries = list(data['entries'])
                    for entry in entries:
                        # Construct URL from ID if possible
                        url = entry.get('url')
                        if not url and entry.get('id'): url = f"https://www.youtube.com/watch?v={entry['id']}"
                        
                        self.music_queues[guild_id].append({
                            'title': entry.get('title', 'Unknown'),
                            'url': url,
                            'user': interaction.user.display_name
                        })
                    songs_added = len(entries)
                    await interaction.followup.send(f"✅ Added **{songs_added}** songs from playlist to queue!")
                else:
                    await interaction.followup.send("⚠️ Found a playlist but it was empty.")
            except Exception as e:
                await interaction.followup.send(f"❌ Playlist Error: {e}")

        # LOGIC: Single Song (YouTube or Direct)
        else:
            title = "Unknown Track"
            url = query
            
            # If yt-dlp exists, fetch metadata properly
            if yt_dlp and not query.startswith("http"):
                query = f"ytsearch:{query}" # Search mode
            
            if yt_dlp:
                try:
                    opts = self.get_ytdl_opts(flat=True) # Fast metadata
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                    
                    if 'entries' in info: info = info['entries'][0]
                    
                    title = info.get('title', title)
                    if info.get('webpage_url'): url = info['webpage_url']
                    elif info.get('url'): url = info['url']
                except: pass

            self.music_queues[guild_id].append({'title': title, 'url': url, 'user': interaction.user.display_name})
            songs_added = 1
            await interaction.followup.send(f"✅ Added to queue: **{title}**")

        # Start Playback if Idle
        is_playing = interaction.guild.voice_client and interaction.guild.voice_client.is_playing()
        if not is_playing and songs_added > 0:
            self.play_next_song(interaction.guild)

    @app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing(): return await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        vc.pause()
        await interaction.response.send_message("⏸️ Paused!", ephemeral=False)

    @app_commands.command(name="resume", description="Resume the current song.")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused(): return await interaction.response.send_message("❌ Nothing is paused!", ephemeral=True)
        vc.resume()
        await interaction.response.send_message("▶️ Resumed!", ephemeral=False)

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            return await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
        interaction.guild.voice_client.stop() 
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=False)

    @app_commands.command(name="stop", description="Stop music and clear queue.")
    async def stop(self, interaction: discord.Interaction):
        await self.stop_playback(interaction)

    @app_commands.command(name="queue", description="Show the current music queue.")
    async def queue(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        q = self.music_queues.get(guild_id, [])
        now_playing = self.current_song.get(guild_id, {}).get('title', "Nothing")
        desc = f"**Now Playing:** {now_playing}\n\n**Up Next:**\n"
        for i, song in enumerate(q[:10], 1):
            desc += f"`{i}.` {song['title']} ({song['user']})\n"
        if len(q) > 10: desc += f"\n*...and {len(q)-10} more.*"
        embed = discord.Embed(title="🎵 Music Queue", description=desc, color=discord.Color(0xff90aa))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shuffle", description="Toggle shuffle mode.")
    async def shuffle(self, interaction: discord.Interaction):
        config = self.load_config(interaction.guild_id)
        current = config.get('shuffle', False)
        config['shuffle'] = not current
        self.save_config(interaction.guild_id, config)
        msg = f"🔀 Shuffle is now **{'ON' if not current else 'OFF'}**."
        if not current and interaction.guild_id in self.music_queues:
            random.shuffle(self.music_queues[interaction.guild_id])
            msg += " Queue shuffled!"
        await interaction.response.send_message(msg, ephemeral=False)

    @app_commands.command(name="musicchannel", description="Set the music sharing channel.")
    async def musicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        config = self.load_config(interaction.guild_id)
        config['music_channel_id'] = channel.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ music channel set to {channel.mention}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Music(bot))
