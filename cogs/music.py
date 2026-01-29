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
        current = self.cog.current_song.get(self.guild.id)
        if not current: return

        elapsed = time.time() - current.get('start_time', 0)
        
        # Logic: If > 10s, Restart. If < 10s, Go Previous.
        if elapsed > 10:
            # Restart: Add current song to front of queue
            if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
            self.cog.music_queues[self.guild.id].insert(0, current)
            if self.guild.voice_client: self.guild.voice_client.stop()
        else:
            # Back: Get previous from history
            history = self.cog.history.get(self.guild.id, [])
            if history:
                prev_song = history.pop() # Remove from history to play it
                if self.guild.id not in self.cog.music_queues: self.cog.music_queues[self.guild.id] = []
                self.cog.music_queues[self.guild.id].insert(0, prev_song)
                if self.guild.voice_client: self.guild.voice_client.stop()
            else:
                # No history, just restart
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
        # Call the existing queue logic
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
        # Stop command logic duplicated here for the button
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
# - download_track(track_data, interaction=None) [Helper]
# - manage_downloads(guild_id) [Helper]
# - cleanup_files(guild_id) [Helper]
# - cleanup_all_files(guild_id) [Helper]
# - play_next_song(guild, interaction=None)
# - stop_playback(interaction) [Helper]
# - check_token_validity_task()
# - play(interaction, query) [Slash]
# - pause(interaction) [Slash]
# - resume(interaction) [Slash]
# - skip(interaction) [Slash]
# - stop(interaction) [Slash]
# - queue(interaction) [Slash]
# - shuffle(interaction) [Slash]
# - checkmusic(interaction) [Slash]
# - ytauth(interaction) [Slash]
# - ytcode(interaction, code) [Slash]
# - playlist(interaction, playlist) [Slash]
# - musicchannel(interaction, channel) [Slash]
# - on_message(message)
# setup(bot)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.youtube = None
        self.spotify = None
        self.auth_flow = None
        
        # Check for FFMPEG
        if not shutil.which("ffmpeg"):
            print("❌ Critical: 'ffmpeg' is missing from system PATH. Music will not play.")

        # --- COOKIE CONVERSION (JSON to Netscape) ---
        # If cookies.txt exists but contains JSON (starts with [ or {), convert it!
        if os.path.exists('cookies.txt'):
            try:
                # Read first char to detect JSON
                with open('cookies.txt', 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if content.strip().startswith('[') or content.strip().startswith('{'):
                    print("🔄 Detected JSON content in cookies.txt. Converting to Netscape format...")
                    try:
                        data = json.loads(content)
                        # Overwrite with Netscape format
                        with open('cookies.txt', 'w', encoding='utf-8') as f:
                            f.write("# Netscape HTTP Cookie File\n")
                            f.write("# This file was generated by BuggyBot from cookies.json\n\n")
                            
                            for c in data:
                                # Extract fields with fallbacks based on typical JSON exports
                                domain = c.get('Host raw', c.get('domain', ''))
                                # Clean domain
                                domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
                                
                                flag = "TRUE" if domain.startswith('.') else "FALSE"
                                path = c.get('Path raw', c.get('path', '/'))
                                
                                secure_raw = c.get('Send for raw', c.get('secure', 'false'))
                                if isinstance(secure_raw, bool):
                                    secure = "TRUE" if secure_raw else "FALSE"
                                else:
                                    secure = "TRUE" if str(secure_raw).lower() == 'true' else "FALSE"
                                
                                expiry = c.get('Expires raw', c.get('expirationDate', '0'))
                                name = c.get('Name raw', c.get('name', ''))
                                value = c.get('Content raw', c.get('value', ''))
                                
                                if domain and name:
                                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")
                        print("✅ Successfully converted cookies.txt to Netscape format.")
                    except json.JSONDecodeError:
                        print("⚠️ cookies.txt looked like JSON but failed to parse. Using as is.")
            except Exception as e:
                print(f"⚠️ Error checking/converting cookies.txt: {e}")

        # Initialize YTDL Options
        self.ytdl = None
        if yt_dlp:
            # Fix: Accept args/kwargs to prevent "unexpected keyword argument" errors
            yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''
            
            self.ytdl_format_options = {
                'format': 'bestaudio/best',
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
                # NEW: Disable cache to prevent persisting bad tokens
                'cachedir': False,
                # NEW: Add sleep to prevent rate limiting
                'sleep_interval': 3,
                'max_sleep_interval': 10,
                # NEW: Force Android client to bypass sign-in checks on cloud IPs
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web']
                    }
                }
            }
            
            # --- COOKIE LOADING (Crucial for Age-Restricted Content) ---
            if os.path.exists('cookies.txt'):
                # Use absolute path to be safe
                cookie_path = os.path.abspath('cookies.txt')
                self.ytdl_format_options['cookiefile'] = cookie_path
                print(f"✅ Found cookies.txt at {cookie_path}! Using it for authentication.")
            else:
                print("⚠️ No cookies.txt found. Age-restricted content will fail.")
            
            try:
                self.ytdl = yt_dlp.YoutubeDL(self.ytdl_format_options)
            except Exception as e:
                print(f"❌ Failed to initialize yt_dlp: {e}")

        # Add reconnect options to handle network blips
        self.ffmpeg_options = {
            'options': '-vn',
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        }

        # Playback State
        self.music_queues = {} # {guild_id: [track_data, ...]}
        self.current_song = {} # {guild_id: track_data}
        self.history = {} # {guild_id: [track_data, ...]}
        
        # Start services
        self.load_music_services()
        if hasattr(self.bot, 'db'):
            self.bot.loop.create_task(self.load_youtube_service())
        
        self.check_token_validity_task.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()

    def load_config(self, guild_id):
        """Loads music config for a specific guild from DB."""
        if not hasattr(self.bot, 'db'): return {}
        
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {} 
        
        return data.get(str(guild_id), {
            "playlist_id": "",
            "music_channel_id": 0,
            "shuffle": False
        })

    def save_config(self, guild_id, config):
        """Saves guild config to DB."""
        if not hasattr(self.bot, 'db'): return

        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {}
        
        data[str(guild_id)] = config
        self.bot.db.save_collection("music_config", data)

    async def load_youtube_service(self):
        """Loads the YouTube API service from stored token."""
        self.youtube = None
        if not hasattr(self.bot, 'db'): return False
        
        global_config = self.bot.db.get_collection("global_music_settings")
        if isinstance(global_config, list): 
            if global_config: global_config = global_config[0]
            else: global_config = {}

        token_json = global_config.get('youtube_token_json')
        
        if not token_json and os.path.exists('token.json'):
            try:
                with open('token.json', 'r') as f:
                    token_json = f.read()
            except: pass

        if token_json:
            try:
                info = json.loads(token_json)
                creds = Credentials.from_authorized_user_info(info, ['https://www.googleapis.com/auth/youtube'])
                
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        global_config['youtube_token_json'] = creds.to_json()
                        self.bot.db.save_collection("global_music_settings", global_config)
                    except: 
                        return False
                
                if creds.valid:
                    self.youtube = build('youtube', 'v3', credentials=creds)
                    return True
            except: 
                return False
        return False

    def load_music_services(self):
        """Loads Spotify service."""
        if not Spotify or not SpotifyClientCredentials: return

        spotify_id = None
        spotify_secret = None
        
        if os.path.exists('spotify.json'):
            try:
                with open('spotify.json', 'r') as f:
                    secrets = json.load(f)
                    spotify_id = secrets.get('spotify_client_id')
                    spotify_secret = secrets.get('spotify_client_secret')
            except Exception as e:
                print(f"❌ Failed to load spotify.json: {e}")

        if spotify_id and spotify_secret:
            try:
                sp_auth = SpotifyClientCredentials(client_id=spotify_id, client_secret=spotify_secret)
                self.spotify = Spotify(auth_manager=sp_auth)
                print("✅ Spotify Service Loaded.")
            except Exception as e:
                print(f"❌ Failed to load Spotify: {e}")
        else:
             print("⚠️ Spotify credentials not found in spotify.json.")

    async def search_youtube_official(self, query):
        """Uses the Official YouTube Data API to find a video ID."""
        if not self.youtube: return None

        try:
            loop = asyncio.get_running_loop()
            request = self.youtube.search().list(
                part="snippet",
                maxResults=1,
                q=query,
                type="video"
            )
            response = await loop.run_in_executor(None, request.execute)
            
            if response.get('items'):
                return response['items'][0]['id']['videoId']
        except Exception as e:
            print(f"YouTube Search Error: {e}")
            return None
        return None

    async def process_spotify_link(self, url, guild_id):
        """Converts Spotify link to YouTube video and adds to playlist."""
        errors = []
        if not self.spotify: errors.append("Spotify service not loaded.")
        if not self.youtube: errors.append("YouTube API not loaded.")
        
        config = self.load_config(guild_id)
        if not config['playlist_id']: errors.append("Playlist ID not set.")

        if errors:
            return "Setup Errors:\n" + "\n".join([f"- {e}" for e in errors])

        clean_url = url.split("?")[0]
        loop = asyncio.get_running_loop()

        try:
            try:
                track = await loop.run_in_executor(None, self.spotify.track, clean_url)
            except Exception as e:
                return f"Spotify Error: {e}"

            search_query = f"{track['artists'][0]['name']} - {track['name']}"
            video_id = await self.search_youtube_official(search_query)

            if not video_id: 
                return f"Could not find '{search_query}' on YouTube."

            try:
                req = self.youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": config['playlist_id'],
                            "resourceId": {
                                "kind": "youtube#video", 
                                "videoId": video_id
                            }
                        }
                    }
                )
                await loop.run_in_executor(None, req.execute)
                return True
            except Exception as e:
                return f"YouTube API Error: {e}"

        except Exception as e:
            return f"Unknown Error: {e}"

    # --- ADVANCED PLAYBACK LOGIC ---

    async def download_track(self, track_data, interaction=None):
        """Helper: Downloads a track and returns filename. Blocks until ready."""
        if not self.ytdl: return None
        if track_data.get('filename') and os.path.exists(track_data['filename']):
            return track_data['filename']

        loop = asyncio.get_running_loop()
        
        # We need a reference to the status message to update it
        status_message = None
        last_update_time = 0

        if interaction:
            try:
                # Send initial message and store it
                status_message = await interaction.followup.send(f"⬇️ Downloading: **{track_data['title']}**... [Starting]", ephemeral=True, wait=True)
            except: pass

        def progress_hook(d):
            nonlocal last_update_time
            if d['status'] == 'downloading':
                # Throttle updates to every 2 seconds to avoid rate limits
                current_time = time.time()
                if current_time - last_update_time > 2:
                    last_update_time = current_time
                    percent = d.get('_percent_str', '0%').replace('\x1b[0;94m', '').replace('\x1b[0m', '') # Strip colors if present
                    
                    # Create simple progress bar
                    try:
                        p = float(percent.strip('%'))
                        bar_len = 10
                        filled = int(bar_len * p / 100)
                        bar = '▓' * filled + '░' * (bar_len - filled)
                        msg = f"⬇️ Downloading: **{track_data['title']}**\n`[{bar}] {percent}`"
                        
                        if status_message:
                            asyncio.run_coroutine_threadsafe(status_message.edit(content=msg), loop)
                    except: pass

        try:
            print(f"⬇️ Downloading: {track_data['title']}...")
            
            # Create a copy of options to add the hook without messing up global options
            opts = self.ytdl_format_options.copy()
            opts['progress_hooks'] = [progress_hook]
            
            # Use a new YTDL instance for this specific download to use the hook
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(track_data['url'], download=True))
            
            if 'entries' in data:
                data = data['entries'][0]

            filename = self.ytdl.prepare_filename(data)
            track_data['filename'] = filename 
            
            # Final update
            if status_message:
                try:
                    await status_message.edit(content=f"✅ Ready to Play: **{track_data['title']}**")
                except: pass

            if os.path.exists(filename):
                print(f"✅ Downloaded: {filename}")
                return filename
            else:
                if status_message:
                    await status_message.edit(content=f"❌ Failed: File not found for **{track_data['title']}**")
                print(f"❌ Error: File not found after download: {filename}")
                return None
        except Exception as e:
            # Fallback logic: retry without cookies if they were the cause, OR if rate-limited
            print(f"❌ First attempt failed: {e}")
            err_str = str(e).lower()
            if "sign in" in err_str or "cookie" in err_str or "rate-limit" in err_str or "unavailable" in err_str:
                print("🔄 Retrying with fallback settings (iOS/No Cookies)...")
                try:
                    retry_opts = self.ytdl_format_options.copy()
                    # Remove cookies for fallback as they might be flagged
                    if 'cookiefile' in retry_opts:
                        del retry_opts['cookiefile']
                    
                    # Switch to iOS client which often bypasses rate limits/blocks
                    retry_opts['extractor_args'] = {
                        'youtube': {
                            'player_client': ['ios']
                        }
                    }
                    
                    with yt_dlp.YoutubeDL(retry_opts) as ydl:
                        data = await loop.run_in_executor(None, lambda: ydl.extract_info(track_data['url'], download=True))
                    
                    if 'entries' in data:
                        data = data['entries'][0]
                    
                    filename = self.ytdl.prepare_filename(data)
                    track_data['filename'] = filename
                    
                    if os.path.exists(filename):
                        print(f"✅ Downloaded (Fallback): {filename}")
                        if status_message:
                            try:
                                await status_message.edit(content=f"✅ Ready to Play (Fallback): **{track_data['title']}**")
                            except: pass
                        return filename
                except Exception as e2:
                    print(f"❌ Fallback failed too: {e2}")

            if status_message:
                try:
                    await status_message.edit(content=f"❌ Download Error: {e}")
                except: pass
            return None

    async def manage_downloads(self, guild_id):
        """Helper: Ensures current + next 3 are downloaded."""
        if guild_id not in self.music_queues: return

        queue = self.music_queues[guild_id]
        
        # Download next 3
        for i in range(min(3, len(queue))):
            track = queue[i]
            if not track.get('filename'):
                # print(f"Preloading: {track['title']}")
                await self.download_track(track)

    def cleanup_files(self, guild_id):
        """Deletes old files but keeps the last played song (for back button)."""
        if guild_id not in self.history: return
        
        # Keep ONLY the last item in history (previous song)
        # Delete everything older than that
        while len(self.history[guild_id]) > 1:
            old_track = self.history[guild_id].pop(0) # Remove oldest
            fname = old_track.get('filename')
            if fname and os.path.exists(fname):
                try:
                    os.remove(fname)
                    print(f"Deleted old track: {fname}")
                except Exception as e:
                    print(f"Cleanup Error: {e}")

    def cleanup_all_files(self, guild_id):
        """Force delete all files related to this guild's session."""
        # 1. Clear Queue Files
        if guild_id in self.music_queues:
            for track in self.music_queues[guild_id]:
                fname = track.get('filename')
                if fname and os.path.exists(fname):
                    try: os.remove(fname)
                    except: pass
        
        # 2. Clear History Files
        if guild_id in self.history:
            for track in self.history[guild_id]:
                fname = track.get('filename')
                if fname and os.path.exists(fname):
                    try: os.remove(fname)
                    except: pass
        
        # 3. Clear Current File
        current = self.current_song.get(guild_id)
        if current:
            fname = current.get('filename')
            if fname and os.path.exists(fname):
                try: os.remove(fname)
                except: pass

    def play_next_song(self, guild, interaction=None):
        """Plays next song, triggers preload, and handles cleanup."""
        # Check if already playing to avoid double-call race conditions
        if guild.voice_client and guild.voice_client.is_playing():
            return

        if guild.id not in self.music_queues or not self.music_queues[guild.id]:
            self.current_song.pop(guild.id, None)
            return

        # Pop next song
        track_data = self.music_queues[guild.id].pop(0)
        track_data['start_time'] = time.time() # Mark start time
        self.current_song[guild.id] = track_data

        async def start_playback():
            filename = track_data.get('filename')

            # If not preloaded, download now (Blocking)
            if not filename or not os.path.exists(filename):
                # Pass interaction here to send the "Downloading..." update!
                filename = await self.download_track(track_data, interaction)
            
            if not filename:
                print(f"⚠️ Skip: Could not download {track_data['title']}")
                await asyncio.sleep(1)
                self.play_next_song(guild)
                return

            try:
                source = discord.FFmpegPCMAudio(filename, **self.ffmpeg_options)
            except Exception as e:
                print(f"❌ Source Error (FFmpeg): {e}")
                self.play_next_song(guild)
                return

            def after_playing(error):
                if error: print(f"❌ Player Error: {error}")
                
                # Move finished song to History
                if guild.id not in self.history: self.history[guild.id] = []
                self.history[guild.id].append(track_data)
                
                # Run Cleanup (Keep only last 1 in history + current)
                self.cleanup_files(guild.id)
                
                # Next
                # We need to run this on the main loop safely
                fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.5), self.bot.loop)
                try: fut.result()
                except: pass
                self.play_next_song(guild)

            if guild.voice_client:
                try:
                    guild.voice_client.play(source, after=after_playing)
                    print(f"▶️ Now Playing: {track_data['title']}")

                    # --- SEND NOW PLAYING EMBED ---
                    try:
                        config = self.load_config(guild.id)
                        music_channel_id = config.get('music_channel_id')
                        
                        if music_channel_id:
                            channel = guild.get_channel(music_channel_id)
                            if channel:
                                embed = discord.Embed(
                                    title="🎵 Now Playing", 
                                    description=f"[{track_data['title']}]({track_data['url']})", 
                                    color=discord.Color(0xff90aa)
                                )
                                embed.add_field(name="Requested By", value=track_data.get('user', 'Unknown'), inline=True)
                                
                                view = MusicControls(self, guild)
                                await channel.send(embed=embed, view=view)
                    except Exception as e:
                        print(f"Failed to send NP embed: {e}")
                        
                except Exception as e:
                    print(f"❌ Failed to start voice_client.play: {e}")
                    # If play failed, maybe still playing? Try next.
                    self.play_next_song(guild)

            # --- TRIGGER DOWNLOADS FOR NEXT 3 SONGS ---
            self.bot.loop.create_task(self.manage_downloads(guild.id))

        asyncio.run_coroutine_threadsafe(start_playback(), self.bot.loop)

    async def stop_playback(self, interaction):
        """Helper to stop music and clear everything."""
        self.music_queues[interaction.guild.id] = []
        self.cleanup_all_files(interaction.guild.id) # Wipe all files
        self.history[interaction.guild.id] = [] # Wipe history
        self.current_song.pop(interaction.guild.id, None)

        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            try:
                await vc.disconnect()
            except:
                pass
        
        await interaction.response.send_message("🛑 Stopped playback, cleared queue, and left the channel.", ephemeral=False)

    # --- TASKS ---

    @tasks.loop(hours=24)
    async def check_token_validity_task(self):
        """Daily check for token validity."""
        valid = await self.load_youtube_service()
        status = "Valid" if valid else "Expired/Broken"
        print(f"[music] Daily Token Check: {status}")

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    # --- SLASH COMMANDS (Playback) ---

    @app_commands.command(name="play", description="Play music or the server playlist.")
    @app_commands.describe(query="Search query or URL (Leave empty for Server Playlist)")
    async def play(self, interaction: discord.Interaction, query: str = None):
        """Play music or the server playlist."""
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You are not in a voice channel, buggy!", ephemeral=True)

        if not self.ytdl:
            return await interaction.response.send_message("❌ Music is disabled because 'yt_dlp' is missing.", ephemeral=True)

        await interaction.response.defer()

        # Join VC if needed
        if not interaction.guild.voice_client:
            try:
                await interaction.user.voice.channel.connect()
            except Exception as e:
                return await interaction.followup.send(f"❌ Failed to join VC: {e}")
        else:
            if interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
                await interaction.guild.voice_client.move_to(interaction.user.voice.channel)

        guild_id = interaction.guild_id
        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = []

        songs_added = 0
        
        # Determine insertion point (Next in queue vs End of queue)
        is_playing = interaction.guild.voice_client and interaction.guild.voice_client.is_playing()
        insert_index = 0 if is_playing else len(self.music_queues[guild_id])

        # --- MODE 1: DEFAULT SERVER PLAYLIST (No Query) ---
        if not query:
            config = self.load_config(guild_id)
            pid = config.get('playlist_id')
            
            if not pid:
                return await interaction.followup.send("❌ No Server Playlist ID is set! Use `/playlist` to set one.")

            if self.youtube:
                try:
                    # Removed progress messages
                    
                    next_page_token = None
                    total_items = []
                    
                    # Fetch ALL pages of metadata first (Fast)
                    # We just store the URLs and titles, not download yet
                    while True:
                        request = self.youtube.playlistItems().list(
                            part="snippet", 
                            playlistId=pid, 
                            maxResults=50, 
                            pageToken=next_page_token
                        )
                        response = await self.bot.loop.run_in_executor(None, request.execute)
                        
                        items = response.get('items', [])
                        if not items: break
                        
                        total_items.extend(items)
                        next_page_token = response.get('nextPageToken')
                        if not next_page_token: break

                    if not total_items:
                        return await interaction.followup.send("⚠️ Server playlist seems empty.")

                    new_songs = []
                    for item in total_items:
                        vid = item['snippet']['resourceId']['videoId']
                        title = item['snippet']['title']
                        if title in ["Private video", "Deleted video"]: continue
                        
                        url = f"https://www.youtube.com/watch?v={vid}"
                        # filename=None means it's not downloaded yet
                        new_songs.append({'title': title, 'url': url, 'user': "Server", 'filename': None})

                    # Insert all metadata into queue
                    # If shuffling is on, we might want to shuffle this batch before inserting
                    if config.get('shuffle', False):
                        random.shuffle(new_songs)

                    self.music_queues[guild_id][insert_index:insert_index] = new_songs
                    songs_added = len(new_songs)

                    # Only show final success message now
                    await interaction.followup.send(f"✅ Queued **{songs_added}** tracks from the Server Playlist, mrow!")

                except Exception as e:
                    return await interaction.followup.send(f"❌ Failed to fetch playlist via API: {e}")
            else:
                return await interaction.followup.send("❌ YouTube API not loaded. Cannot fetch server playlist efficiently.")

        # --- MODE 2: SEARCH / URL ---
        else:
            try:
                # Removed progress message
                
                if not query.startswith("http"):
                    query = f"ytsearch:{query}"

                data = await self.bot.loop.run_in_executor(None, lambda: self.ytdl.extract_info(query, download=False))
                
                if 'entries' in data:
                    data = data['entries'][0]

                title = data.get('title', 'Unknown')
                url = data.get('webpage_url', data.get('url'))
                
                song_data = {'title': title, 'url': url, 'user': interaction.user.display_name, 'filename': None}
                self.music_queues[guild_id].insert(insert_index, song_data)
                songs_added = 1
                
                status_msg = f"✅ Added to **top of queue**: **{title}**" if is_playing else f"✅ Added to queue: **{title}**"
                await interaction.followup.send(status_msg)

            except Exception as e:
                return await interaction.followup.send(f"❌ Error fetching song: {e}")

        # Start Playback if Idle (this will trigger the download of the first song)
        if interaction.guild.voice_client and not interaction.guild.voice_client.is_playing() and songs_added > 0:
            self.play_next_song(interaction.guild, interaction) # Pass interaction for updates
        # If already playing, ensure the next few are downloaded in background
        elif is_playing:
             self.bot.loop.create_task(self.manage_downloads(interaction.guild_id))

    @app_commands.command(name="pause", description="Pause the current song.")
    async def pause(self, interaction: discord.Interaction):
        """Pause the current song."""
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("❌ Nothing is playing to pause!", ephemeral=True)
        
        vc.pause()
        await interaction.response.send_message("⏸️ Paused!", ephemeral=False)

    @app_commands.command(name="resume", description="Resume the current song.")
    async def resume(self, interaction: discord.Interaction):
        """Resume the current song."""
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            return await interaction.response.send_message("❌ Nothing is paused!", ephemeral=True)
        
        vc.resume()
        await interaction.response.send_message("▶️ Resumed!", ephemeral=False)

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            return await interaction.response.send_message("❌ Nothing is playing to skip!", ephemeral=True)
        
        interaction.guild.voice_client.stop() 
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=False)

    @app_commands.command(name="stop", description="Stop music and clear queue.")
    async def stop(self, interaction: discord.Interaction):
        """Stop music and clear queue."""
        await self.stop_playback(interaction)

    @app_commands.command(name="queue", description="Show the current music queue.")
    async def queue(self, interaction: discord.Interaction):
        """Show the current music queue."""
        guild_id = interaction.guild_id
        q = self.music_queues.get(guild_id, [])
        now_playing = self.current_song.get(guild_id, {}).get('title', "Nothing")

        desc = f"**Now Playing:** {now_playing}\n\n**Up Next:**\n"
        for i, song in enumerate(q[:10], 1):
            desc += f"`{i}.` {song['title']} ({song['user']})\n"
        
        if len(q) > 10:
            desc += f"\n*...and {len(q)-10} more.*"

        embed = discord.Embed(title="🎵 Music Queue", description=desc, color=discord.Color(0xff90aa))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shuffle", description="Toggle permanent shuffle mode.")
    async def shuffle(self, interaction: discord.Interaction):
        """Toggle permanent shuffle mode."""
        config = self.load_config(interaction.guild_id)
        current_state = config.get('shuffle', False)
        new_state = not current_state
        config['shuffle'] = new_state
        self.save_config(interaction.guild_id, config)
        
        msg = f"🔀 Shuffle is now **{'ON' if new_state else 'OFF'}**."
        
        # If turned on, shuffle the current queue immediately
        if new_state and interaction.guild_id in self.music_queues:
            if len(self.music_queues[interaction.guild_id]) > 0:
                random.shuffle(self.music_queues[interaction.guild_id])
                msg += " Queue shuffled!"
                self.bot.loop.create_task(self.manage_downloads(interaction.guild_id)) # Update preloads
        
        await interaction.response.send_message(msg, ephemeral=False)

    # --- SLASH COMMANDS (Config) ---

    @app_commands.command(name="checkmusic", description="Checks all music API statuses.")
    @app_commands.default_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        """Checks all music API statuses."""
        is_valid = await self.load_youtube_service()
        
        yt_msg = f"✅ **YouTube License Valid!**" if is_valid else "❌ **YouTube License Broken.**"
        spot_msg = "✅ **Spotify Ready!**" if self.spotify else "❌ **Spotify Not Loaded.**"
        
        await interaction.response.send_message(f"{yt_msg}\n{spot_msg}", ephemeral=True)

    @app_commands.command(name="ytauth", description="Starts the OAuth flow to renew YouTube license.")
    @app_commands.default_permissions(administrator=True)
    async def ytauth(self, interaction: discord.Interaction):
        """Starts the OAuth flow to renew YouTube license."""
        # 1. Reload Spotify
        self.load_music_services()
        spot_status = "✅ **Spotify reloaded!**" if self.spotify else "❌ **Spotify NOT found!**"

        # 2. Start YouTube Flow
        if not os.path.exists('client_secret.json'):
             return await interaction.response.send_message(f"{spot_status}\n❌ Missing `client_secret.json`!", ephemeral=True)
        
        try:
            self.auth_flow = Flow.from_client_secrets_file(
                'client_secret.json',
                scopes=['https://www.googleapis.com/auth/youtube'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            auth_url, _ = self.auth_flow.authorization_url(prompt='consent')
            
            ytcode_cmd = discord.utils.get(self.bot.tree.get_commands(), name="ytcode")
            cmd_mention = "` /ytcode <code> `"
            if ytcode_cmd:
                 pass

            await interaction.response.send_message(
                f"{spot_status}\n🔄 **YouTube API Renewal Started!**\n1. Click: [Auth Link](<{auth_url}>)\n2. Run: {cmd_mention} (paste the code)", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ytcode", description="Completes the YouTube renewal with the code.")
    @app_commands.describe(code="The code from the Auth Link")
    @app_commands.default_permissions(administrator=True)
    async def ytcode(self, interaction: discord.Interaction, code: str):
        """Completes the YouTube renewal with the code."""
        if not self.auth_flow:
            return await interaction.response.send_message("❌ Run `/ytauth` first!", ephemeral=True)
        
        try:
            self.auth_flow.fetch_token(code=code)
            
            global_config = self.bot.db.get_collection("global_music_settings")
            if isinstance(global_config, list): global_config = {}
            global_config['youtube_token_json'] = self.auth_flow.credentials.to_json()
            self.bot.db.save_collection("global_music_settings", global_config)
            
            await self.load_youtube_service()
            await interaction.response.send_message("✅ **Success!** License renewed and saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="playlist", description="Set the YouTube Playlist Link or ID.")
    @app_commands.describe(playlist="The YouTube Playlist Link or ID")
    @app_commands.default_permissions(administrator=True)
    async def playlist(self, interaction: discord.Interaction, playlist: str):
        """Set the YouTube Playlist Link or ID."""
        match = re.search(r'list=([a-zA-Z0-9_-]+)', playlist)
        
        if match:
            clean_id = match.group(1)
        else:
            clean_id = playlist

        config = self.load_config(interaction.guild_id)
        config['playlist_id'] = clean_id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Playlist set to ID: `{clean_id}`", ephemeral=True)

    @app_commands.command(name="musicchannel", description="Set the music sharing channel.")
    @app_commands.describe(channel="The channel for music links")
    @app_commands.default_permissions(administrator=True)
    async def musicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the music sharing channel."""
        config = self.load_config(interaction.guild_id)
        config['music_channel_id'] = channel.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ music channel set to {channel.mention}.", ephemeral=True)

    # --- LISTENER ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        config = self.load_config(message.guild.id)
        
        if config['music_channel_id'] != 0 and message.channel.id != config['music_channel_id']:
            return
            
        content = message.content
        
        # Regex Definitions
        spotify_match = re.search(r'(https?://(?:open\.|www\.)?spotify\.com/(?:track|album|playlist|artist)/[a-zA-Z0-9_-]+)', content)
        yt_music_match = re.search(r'https?://music\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', content)
        yt_standard_match = re.search(r'https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', content)
        yt_short_match = re.search(r'https?://youtu\.be/([a-zA-Z0-9_-]+)', content)

        # 1. Handle Spotify
        if spotify_match:
            result = await self.process_spotify_link(spotify_match.group(1), message.guild.id)
            if result is True:
                await message.add_reaction("🎵")
            else:
                await message.channel.send(f"⚠️ **Error:** Spotify link failed.\n`{result}`", delete_after=10)

        # 2. Handle YouTube (Music, Standard, Short)
        elif self.youtube and (yt_music_match or yt_standard_match or yt_short_match):
            v_id = None
            if yt_music_match: v_id = yt_music_match.group(1)
            elif yt_standard_match: v_id = yt_standard_match.group(1)
            elif yt_short_match: v_id = yt_short_match.group(1)

            if v_id:
                try:
                    self.youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": config['playlist_id'],
                                "resourceId": {"kind": "youtube#video", "videoId": v_id}
                            }
                        }
                    ).execute()
                    await message.add_reaction("🎵")
                except Exception as e:
                    await message.channel.send(f"⚠️ **Error:** YouTube link failed.\n`{e}`", delete_after=10)

async def setup(bot):
    await bot.add_cog(Music(bot))
