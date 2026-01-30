import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import re
import datetime
import sys
import time
import random
import shutil

# Safe Imports for External Libraries
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    print("❌ Critical: 'yt_dlp' is not installed. Music will not work. Run: pip install yt-dlp")

try:
    import nacl
except ImportError:
    print("❌ Critical: 'PyNaCl' is not installed. Voice will crash. Run: pip install PyNaCl")

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import Flow
except ImportError:
    print("⚠️ Warning: Google Auth libraries not found. Run: pip install google-api-python-client google-auth-oauthlib")

try:
    from spotipy import Spotify
    from spotipy.oauth2 import SpotifyClientCredentials
except ImportError:
    Spotify = None
    SpotifyClientCredentials = None
    print("⚠️ Warning: 'spotipy' not found. Run: pip install spotipy")

# --- UI VIEW FOR CONTROLS ---

class MusicControls(discord.ui.View):
    def __init__(self, cog, guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # With streaming, we just push the current song back to the front of the queue
        # Logic: If > 10s, Restart. If < 10s, Go Previous.
        current = self.cog.current_song.get(self.guild.id)
        if not current: return

        elapsed = time.time() - current.get('start_time', 0)
        
        if elapsed > 10:
            # Restart
            if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
            self.cog.music_queues[self.guild.id].insert(0, current)
            if self.guild.voice_client: self.guild.voice_client.stop()
        else:
            # Previous
            history = self.cog.history.get(self.guild.id, [])
            if history:
                prev_song = history.pop()
                if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
                self.cog.music_queues[self.guild.id].insert(0, prev_song)
                if self.guild.voice_client: self.guild.voice_client.stop()
            else:
                # No history, restart
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

# Function/Class List:
# class Music(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - load_config(guild_id)
# - save_config(guild_id, config)
# - load_youtube_service()
# - load_music_services()
# - search_youtube_official(query)
# - process_spotify_link(url, guild_id)
# - check_and_convert_cookies()
# - get_ytdl_opts(format_mode='best') [Updated to accept format mode]
# - play_next_song(guild, interaction=None) [Updated with fallback logic]
# - stop_playback(interaction)
# - check_token_validity_task()
# - play(interaction, query)
# - pause(interaction)
# - resume(interaction)
# - skip(interaction)
# - stop(interaction)
# - queue(interaction)
# - shuffle(interaction)
# - checkmusic(interaction)
# - ytauth(interaction)
# - ytcode(interaction, code)
# - playlist(interaction, playlist)
# - musicchannel(interaction, channel)
# - on_message(message)
# setup(bot)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.youtube = None
        self.spotify = None
        self.auth_flow = None
        self.ytdl = None
        
        # Check for FFMPEG
        if not shutil.which("ffmpeg"):
            print("❌ Critical: 'ffmpeg' is missing from system PATH. Music will not play.")

        # Initialize Music Services
        self.load_music_services()
        if hasattr(self.bot, 'db'):
            self.bot.loop.create_task(self.load_youtube_service())
        
        # Initialize YTDL options for streaming (NO DOWNLOADING)
        if yt_dlp:
            yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''

        # Playback State
        self.music_queues = {} # {guild_id: [track_data, ...]}
        self.current_song = {} # {guild_id: track_data}
        self.history = {} # {guild_id: [track_data, ...]}
        
        self.check_token_validity_task.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()

    # --- HELPERS ---

    def check_and_convert_cookies(self):
        """Checks for cookies.txt, converts JSON if needed, fixes headers."""
        if not os.path.exists('cookies.txt'):
            return None

        try:
            with open('cookies.txt', 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Convert JSON to Netscape if needed
            if content.strip().startswith('[') or content.strip().startswith('{'):
                try:
                    data = json.loads(content)
                    with open('cookies.txt', 'w', encoding='utf-8') as f:
                        f.write("# Netscape HTTP Cookie File\n\n")
                        for c in data:
                            domain = c.get('Host raw', c.get('domain', '')).replace('https://', '').replace('http://', '').rstrip('/')
                            flag = "TRUE" if domain.startswith('.') else "FALSE"
                            path = c.get('Path raw', c.get('path', '/'))
                            secure = "TRUE" if str(c.get('secure', False)).lower() == 'true' else "FALSE"
                            expiry = int(float(c.get('expirationDate', 0)))
                            name = c.get('name', '')
                            value = c.get('value', '')
                            if domain and name:
                                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")
                except: pass
            
            elif not content.strip().startswith("# Netscape"):
                 with open('cookies.txt', 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n\n" + content)

        except: pass
        return os.path.abspath('cookies.txt')

    def get_ytdl_opts(self, format_mode='best'):
        """Options strictly for extracting streaming URLs."""
        cookie_path = self.check_and_convert_cookies()
        
        # Determine format string based on mode
        # 'best' = Try to get high quality audio
        # 'fallback' = Just get ANYTHING that works
        fmt = 'bestaudio/best'
        if format_mode == 'fallback':
            fmt = 'best' # Allow video if audio-only fails
        
        opts = {
            'format': fmt,
            'noplaylist': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': False,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
            # Important: We are NOT downloading files
        }

        if cookie_path:
            opts['cookiefile'] = cookie_path
        
        return opts

    def load_config(self, guild_id):
        if not hasattr(self.bot, 'db'): return {}
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {} 
        return data.get(str(guild_id), {"playlist_id": "", "music_channel_id": 0, "shuffle": False})

    def save_config(self, guild_id, config):
        if not hasattr(self.bot, 'db'): return
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {}
        data[str(guild_id)] = config
        self.bot.db.save_collection("music_config", data)

    async def load_youtube_service(self):
        self.youtube = None
        if not hasattr(self.bot, 'db'): return False
        
        global_config = self.bot.db.get_collection("global_music_settings")
        if isinstance(global_config, list): 
            if global_config: global_config = global_config[0]
            else: global_config = {}

        token_json = global_config.get('youtube_token_json')
        if not token_json and os.path.exists('token.json'):
            try:
                with open('token.json', 'r') as f: token_json = f.read()
            except: pass

        if token_json:
            try:
                info = json.loads(token_json)
                creds = Credentials.from_authorized_user_info(info, ['https://www.googleapis.com/auth/youtube'])
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    global_config['youtube_token_json'] = creds.to_json()
                    self.bot.db.save_collection("global_music_settings", global_config)
                
                if creds.valid:
                    self.youtube = build('youtube', 'v3', credentials=creds)
                    return True
            except: return False
        return False

    def load_music_services(self):
        if not Spotify or not SpotifyClientCredentials: return
        spotify_id, spotify_secret = None, None
        
        if os.path.exists('spotify.json'):
            try:
                with open('spotify.json', 'r') as f:
                    secrets = json.load(f)
                    spotify_id = secrets.get('spotify_client_id')
                    spotify_secret = secrets.get('spotify_client_secret')
            except: pass

        if spotify_id and spotify_secret:
            try:
                self.spotify = Spotify(auth_manager=SpotifyClientCredentials(client_id=spotify_id, client_secret=spotify_secret))
                print("✅ Spotify Service Loaded.")
            except: pass

    async def search_youtube_official(self, query):
        if not self.youtube: return None
        try:
            loop = asyncio.get_running_loop()
            request = self.youtube.search().list(part="snippet", maxResults=1, q=query, type="video")
            response = await loop.run_in_executor(None, request.execute)
            if response.get('items'): return response['items'][0]['id']['videoId']
        except: return None
        return None

    async def process_spotify_link(self, url, guild_id):
        if not self.spotify or not self.youtube: return "Services not ready."
        config = self.load_config(guild_id)
        if not config['playlist_id']: return "No playlist ID set."

        clean_url = url.split("?")[0]
        loop = asyncio.get_running_loop()
        try:
            track = await loop.run_in_executor(None, self.spotify.track, clean_url)
            search_query = f"{track['artists'][0]['name']} - {track['name']}"
            video_id = await self.search_youtube_official(search_query)
            if not video_id: return "Song not found on YouTube."
            
            self.youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": config['playlist_id'], "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
            ).execute()
            return True
        except Exception as e: return f"Error: {e}"

    # --- STREAMING PLAYBACK LOGIC ---

    def play_next_song(self, guild, interaction=None):
        if guild.voice_client and guild.voice_client.is_playing(): return
        if guild.id not in self.music_queues or not self.music_queues[guild.id]:
            self.current_song.pop(guild.id, None)
            return

        # Pop song
        track_data = self.music_queues[guild.id].pop(0)
        track_data['start_time'] = time.time()
        self.current_song[guild.id] = track_data

        async def stream_audio():
            loop = asyncio.get_running_loop()
            
            # --- TRY 1: High Quality Audio ---
            opts = self.get_ytdl_opts(format_mode='best')
            stream_url = None
            data = None
            
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    data = await loop.run_in_executor(None, lambda: ydl.extract_info(track_data['url'], download=False))
                
                if 'entries' in data: data = data['entries'][0]
                stream_url = data.get('url')
            
            except Exception as e:
                # --- TRY 2: Fallback (Any Format) ---
                print(f"⚠️ First attempt failed ({e}). Retrying with fallback format...")
                try:
                    opts = self.get_ytdl_opts(format_mode='fallback')
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        data = await loop.run_in_executor(None, lambda: ydl.extract_info(track_data['url'], download=False))
                    if 'entries' in data: data = data['entries'][0]
                    stream_url = data.get('url')
                except Exception as e2:
                    print(f"❌ Fallback also failed: {e2}")
            
            if not stream_url:
                if interaction: 
                        try: await interaction.followup.send(f"⚠️ Failed to stream **{track_data['title']}** (Format unavailable)", ephemeral=True)
                        except: pass
                self.play_next_song(guild)
                return

            try:
                # --- CRITICAL FIX: PASS HEADERS TO FFMPEG ---
                # FFmpeg needs the same User-Agent and Cookies that yt-dlp used to access the URL
                ffmpeg_before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                
                if data and 'http_headers' in data:
                    headers = ""
                    for key, value in data['http_headers'].items():
                        headers += f"{key}: {value}\r\n"
                    # Add headers argument to ffmpeg
                    if headers:
                        ffmpeg_before_options += f' -headers "{headers}"'

                # Create Audio Source directly from URL with HEADERS
                source = discord.FFmpegPCMAudio(stream_url, before_options=ffmpeg_before_options, options='-vn')
                
                def after_playing(error):
                    if error: 
                        print(f"❌ Player Error: {error}")
                        if interaction:
                            try:
                                # We can't await here easily, but we can print
                                pass 
                            except: pass

                    if guild.id not in self.history: self.history[guild.id] = []
                    self.history[guild.id].append(track_data)
                    
                    # Next
                    fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.5), self.bot.loop)
                    try: fut.result()
                    except: pass
                    self.play_next_song(guild)

                if guild.voice_client:
                    try:
                        guild.voice_client.play(source, after=after_playing)
                        print(f"▶️ Streaming: {track_data['title']}")
                        
                        # Send Embed
                        config = self.load_config(guild.id)
                        if config.get('music_channel_id'):
                            chan = guild.get_channel(config['music_channel_id'])
                            if chan:
                                embed = discord.Embed(title="🎵 Now Streaming", description=f"[{track_data['title']}]({track_data['url']})", color=discord.Color(0xff90aa))
                                embed.add_field(name="Requested By", value=track_data.get('user', 'Unknown'))
                                await chan.send(embed=embed, view=MusicControls(self, guild))
                    except Exception as e:
                        print(f"❌ Play Error: {e}")
                        if interaction:
                             try: await interaction.followup.send(f"❌ Playback Error: {e}", ephemeral=True)
                             except: pass
                        self.play_next_song(guild)

            except Exception as e:
                print(f"❌ Streaming Error: {e}")
                if interaction:
                     try: await interaction.followup.send(f"❌ Streaming Error: {e}", ephemeral=True)
                     except: pass
                self.play_next_song(guild)

        asyncio.run_coroutine_threadsafe(stream_audio(), self.bot.loop)

    async def stop_playback(self, interaction):
        self.music_queues[interaction.guild.id] = []
        self.history[interaction.guild.id] = []
        self.current_song.pop(interaction.guild.id, None)
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
            try: await interaction.guild.voice_client.disconnect()
            except: pass
        await interaction.response.send_message("🛑 Stopped playback and cleared queue.", ephemeral=False)

    # --- TASKS ---
    @tasks.loop(hours=24)
    async def check_token_validity_task(self):
        await self.load_youtube_service()

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    # --- SLASH COMMANDS ---

    @app_commands.command(name="play", description="Stream music or the server playlist.")
    @app_commands.describe(query="Search query or URL (Leave empty for Server Playlist)")
    async def play(self, interaction: discord.Interaction, query: str = None):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You are not in a voice channel!", ephemeral=True)

        await interaction.response.defer()

        # Join VC
        if not interaction.guild.voice_client:
            try: await interaction.user.voice.channel.connect()
            except Exception as e: return await interaction.followup.send(f"❌ Failed to join VC: {e}")
        elif interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
            await interaction.guild.voice_client.move_to(interaction.user.voice.channel)

        guild_id = interaction.guild_id
        if guild_id not in self.music_queues: self.music_queues[guild_id] = []
        
        songs_added = 0
        is_playing = interaction.guild.voice_client.is_playing()
        
        # Mode 1: Server Playlist (No Query)
        if not query:
            config = self.load_config(guild_id)
            pid = config.get('playlist_id')
            if not pid or not self.youtube: return await interaction.followup.send("❌ No Playlist ID set or YouTube API not loaded.")

            try:
                next_tok = None
                items = []
                while True:
                    req = self.youtube.playlistItems().list(part="snippet", playlistId=pid, maxResults=50, pageToken=next_tok)
                    res = await self.bot.loop.run_in_executor(None, req.execute)
                    items.extend(res.get('items', []))
                    next_tok = res.get('nextPageToken')
                    if not next_tok: break

                new_songs = []
                for item in items:
                    vid = item['snippet']['resourceId']['videoId']
                    title = item['snippet']['title']
                    if title not in ["Private video", "Deleted video"]:
                        new_songs.append({'title': title, 'url': f"https://www.youtube.com/watch?v={vid}", 'user': "Server"})
                
                if config.get('shuffle', False): random.shuffle(new_songs)
                
                # Append to queue
                self.music_queues[guild_id].extend(new_songs)
                songs_added = len(new_songs)
                await interaction.followup.send(f"✅ Queued **{songs_added}** tracks from Server Playlist.")
            except Exception as e: return await interaction.followup.send(f"❌ API Error: {e}")

        # Mode 2: Search/URL (FIXED LOGIC)
        else:
            try:
                # Ensure query is handled as a search if not a URL
                search_query = query
                if not query.startswith("http"): 
                    search_query = f"ytsearch:{query}"
                
                # Just get metadata, no download
                opts = self.get_ytdl_opts()
                with yt_dlp.YoutubeDL(opts) as ydl:
                    data = await self.bot.loop.run_in_executor(None, lambda: ydl.extract_info(search_query, download=False))
                
                if 'entries' in data: 
                    data = data['entries'][0]
                
                song = {
                    'title': data.get('title', 'Unknown'), 
                    'url': data.get('webpage_url', data.get('url')), 
                    'user': interaction.user.display_name
                }
                
                # If nothing is playing, insert at 0. If playing, add to end (or after current?)
                # Standard queue behavior: Add to end.
                self.music_queues[guild_id].append(song)
                songs_added = 1
                
                if is_playing:
                    await interaction.followup.send(f"✅ Added to queue: **{song['title']}**")
                else:
                    await interaction.followup.send(f"✅ Queued: **{song['title']}**")

            except Exception as e: 
                return await interaction.followup.send(f"❌ Error finding song: {e}")

        # Start if idle and songs were added
        if not is_playing and songs_added > 0:
            self.play_next_song(interaction.guild, interaction)

    @app_commands.command(name="pause", description="Pause song.")
    async def pause(self, interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.response.send_message("⏸️ Paused")
        else: await interaction.response.send_message("Nothing playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume song.")
    async def resume(self, interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.response.send_message("▶️ Resumed")
        else: await interaction.response.send_message("Nothing paused.", ephemeral=True)

    @app_commands.command(name="skip", description="Skip song.")
    async def skip(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped")
        else: await interaction.response.send_message("Nothing playing.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback.")
    async def stop(self, interaction: discord.Interaction):
        await self.stop_playback(interaction)

    @app_commands.command(name="queue", description="Show queue.")
    async def queue(self, interaction: discord.Interaction):
        q = self.music_queues.get(interaction.guild_id, [])
        np = self.current_song.get(interaction.guild_id, {}).get('title', "Nothing")
        desc = f"**Now Playing:** {np}\n\n**Up Next:**\n"
        for i, s in enumerate(q[:10], 1): desc += f"`{i}.` {s['title']} ({s['user']})\n"
        if len(q) > 10: desc += f"\n*...and {len(q)-10} more.*"
        await interaction.response.send_message(embed=discord.Embed(title="Queue", description=desc, color=discord.Color(0xff90aa)), ephemeral=True)

    @app_commands.command(name="shuffle", description="Toggle shuffle.")
    async def shuffle(self, interaction: discord.Interaction):
        config = self.load_config(interaction.guild_id)
        config['shuffle'] = not config['shuffle']
        self.save_config(interaction.guild_id, config)
        if config['shuffle'] and interaction.guild_id in self.music_queues:
            random.shuffle(self.music_queues[interaction.guild_id])
        await interaction.response.send_message(f"🔀 Shuffle is now **{'ON' if config['shuffle'] else 'OFF'}**.")

    # --- CONFIG COMMANDS ---
    
    @app_commands.command(name="checkmusic", description="Check status.")
    @app_commands.default_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        yt = "✅ YouTube" if await self.load_youtube_service() else "❌ YouTube"
        sp = "✅ Spotify" if self.spotify else "❌ Spotify"
        ck = "✅ Cookies" if self.check_and_convert_cookies() else "❌ Cookies"
        await interaction.response.send_message(f"{yt}\n{sp}\n{ck}", ephemeral=True)

    @app_commands.command(name="ytauth", description="Auth YouTube.")
    @app_commands.default_permissions(administrator=True)
    async def ytauth(self, interaction: discord.Interaction):
        if not os.path.exists('client_secret.json'): return await interaction.response.send_message("❌ Missing client_secret.json", ephemeral=True)
        self.auth_flow = Flow.from_client_secrets_file('client_secret.json', scopes=['https://www.googleapis.com/auth/youtube'], redirect_uri='urn:ietf:wg:oauth:2.0:oob')
        url, _ = self.auth_flow.authorization_url(prompt='consent')
        await interaction.response.send_message(f"Auth Link: [Click Here]({url})\nUse `/ytcode` to finish.", ephemeral=True)

    @app_commands.command(name="ytcode", description="Finish Auth.")
    @app_commands.default_permissions(administrator=True)
    async def ytcode(self, interaction: discord.Interaction, code: str):
        if not self.auth_flow: return await interaction.response.send_message("Run `/ytauth` first.", ephemeral=True)
        try:
            self.auth_flow.fetch_token(code=code)
            gconf = self.bot.db.get_collection("global_music_settings")
            if isinstance(gconf, list): gconf = {}
            gconf['youtube_token_json'] = self.auth_flow.credentials.to_json()
            self.bot.db.save_collection("global_music_settings", gconf)
            await self.load_youtube_service()
            await interaction.response.send_message("✅ Success!", ephemeral=True)
        except Exception as e: await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @app_commands.command(name="playlist", description="Set Playlist ID.")
    @app_commands.default_permissions(administrator=True)
    async def playlist(self, interaction: discord.Interaction, playlist: str):
        match = re.search(r'list=([a-zA-Z0-9_-]+)', playlist)
        pid = match.group(1) if match else playlist
        conf = self.load_config(interaction.guild_id)
        conf['playlist_id'] = pid
        self.save_config(interaction.guild_id, conf)
        await interaction.response.send_message(f"✅ Playlist set: `{pid}`", ephemeral=True)

    @app_commands.command(name="musicchannel", description="Set Music Channel.")
    @app_commands.default_permissions(administrator=True)
    async def musicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        conf = self.load_config(interaction.guild_id)
        conf['music_channel_id'] = channel.id
        self.save_config(interaction.guild_id, conf)
        await interaction.response.send_message(f"✅ Channel set: {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        conf = self.load_config(message.guild.id)
        if conf['music_channel_id'] and message.channel.id != conf['music_channel_id']: return
        
        # Simple link detector for auto-adding to playlist
        if "spotify.com" in message.content:
            res = await self.process_spotify_link(re.search(r'(https?://[^\s]+)', message.content).group(1), message.guild.id)
            if res is True: await message.add_reaction("🎵")
        elif "youtube.com/watch" in message.content or "youtu.be/" in message.content:
            # Basic logic for adding YT link to playlist manually if needed
            # For brevity in this fix, we assume the user just wants Spotify auto-add or commands
            pass

async def setup(bot):
    await bot.add_cog(Music(bot))
