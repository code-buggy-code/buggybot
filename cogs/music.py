import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import re
import datetime
import sys
import traceback

# music APIs
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

# Try importing yt_dlp
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.youtube = None
        self.spotify = None
        self.auth_flow = None
        
        # --- Local Cache / yt-dlp Config ---
        self.download_dir = "music_cache"
        self.server_playlist_url = os.getenv("SERVER_PLAYLIST_URL", None) 
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)
            
        if yt_dlp is None:
            print("WARNING: yt-dlp is not installed. Playback features will fail.")
        # -----------------------------------

        # Start services
        self.load_music_services()
        self.bot.loop.create_task(self.load_youtube_service())
        self.check_token_validity_task.start()
        self.license_reminder_task.start()
        
        # Start Local Cache Tasks
        self.sync_server_playlist.start()
        self.manage_cache.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()
        self.license_reminder_task.cancel()
        self.sync_server_playlist.cancel()
        self.manage_cache.cancel()

    def load_config(self, guild_id):
        """Loads music config for a specific guild from DB."""
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {} 
        
        return data.get(str(guild_id), {
            "playlist_id": "",
            "music_channel_id": 0
        })

    def save_config(self, guild_id, config):
        """Saves guild config to DB."""
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {}
        
        data[str(guild_id)] = config
        self.bot.db.save_collection("music_config", data)

    async def load_youtube_service(self):
        """Loads the YouTube API service from stored token."""
        self.youtube = None
        
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
        """Loads Spotify service only."""
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

    # --- TASKS ---

    @tasks.loop(hours=24)
    async def check_token_validity_task(self):
        valid = await self.load_youtube_service()
        status = "Valid" if valid else "Expired/Broken"
        print(f"[music] Daily Token Check: {status}")

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def license_reminder_task(self):
        global_config = self.bot.db.get_collection("global_music_settings")
        if isinstance(global_config, list): 
             if global_config: global_config = global_config[0]
             else: return

        if not global_config.get('reminder_timestamp') or global_config.get('reminder_sent'):
            return

        remind_ts = global_config['reminder_timestamp']
        if datetime.datetime.now().timestamp() >= remind_ts:
            user_id = global_config.get('reminder_user_id')
            if user_id:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await user.send(
                        "⚠️ **YouTube License Reminder!**\n"
                        "It has been 6 days since you renewed the YouTube license. "
                        "Please run `/ytauth` and `/ytcode` soon."
                    )
                except Exception as e:
                    print(f"Failed to DM user: {e}")
            
            global_config['reminder_sent'] = True
            self.bot.db.save_collection("global_music_settings", global_config)

    @license_reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def sync_server_playlist(self):
        if not self.server_playlist_url:
            return
        await self.download_content(self.server_playlist_url, play_mode=False)

    @sync_server_playlist.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def manage_cache(self):
        pass

    # --- HELPERS (YT-DLP) ---

    async def download_content(self, query, play_mode=True):
        if yt_dlp is None:
            return None

        archive_path = os.path.join(self.download_dir, 'archive.txt')
        cookies_path = os.path.abspath(os.path.join(os.getcwd(), "..", "cookies.txt"))
        
        cookie_args = {}
        if os.path.exists(cookies_path):
            cookie_args['cookiefile'] = cookies_path
        elif os.path.exists("cookies.txt"):
            cookie_args['cookiefile'] = "cookies.txt"

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(self.download_dir, '%(title)s - %(uploader)s.%(ext)s'),
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'opus', 'preferredquality': '0'},
                {'key': 'EmbedThumbnail'},
                {'key': 'FFmpegMetadata', 'add_metadata': True},
            ],
            'writethumbnail': True,
            'download_archive': archive_path,
            'ignoreerrors': False,
            'noplaylist': False,
            'nocheckcertificate': True,
            'source_address': '0.0.0.0',
            'retries': 5,
            **cookie_args
        }

        if not re.match(r'https?://', query):
            search_query = f"ytsearch:{query}"
        else:
            search_query = query

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, search_query, download=True)

            if not info:
                return None

            results = []

            def process_entry(entry):
                title = entry.get('title', 'Unknown Title')
                uploader = entry.get('uploader', 'Unknown Uploader')
                vid_id = entry.get('id')
                try:
                    pre_filename = ydl.prepare_filename(entry)
                    base_name = os.path.splitext(pre_filename)[0]
                    final_filename = f"{base_name}.opus"
                except Exception:
                    final_filename = entry.get('filepath')

                return {
                    'title': title,
                    'id': vid_id,
                    'url': final_filename, 
                    'uploader': uploader
                }

            if 'entries' in info:
                entries = [e for e in info['entries'] if e]
                if search_query.startswith("ytsearch:"):
                    results.append(process_entry(entries[0]))
                else:
                    for entry in entries:
                        results.append(process_entry(entry))
            else:
                results.append(process_entry(info))

            return results

        except Exception as e:
            print(f"Error downloading with yt-dlp: {e}")
            return None

    # --- SLASH COMMANDS ---

    @app_commands.command(name="checkmusic", description="Checks all music API statuses.")
    @app_commands.default_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        is_valid = await self.load_youtube_service()
        yt_msg = f"✅ **YouTube License Valid!**" if is_valid else "❌ **YouTube License Broken.**"
        spot_msg = "✅ **Spotify Ready!**" if self.spotify else "❌ **Spotify Not Loaded.**"
        await interaction.response.send_message(f"{yt_msg}\n{spot_msg}", ephemeral=True)

    @app_commands.command(name="ytauth", description="Starts the OAuth flow to renew YouTube license.")
    @app_commands.default_permissions(administrator=True)
    async def ytauth(self, interaction: discord.Interaction):
        self.load_music_services()
        spot_status = "✅ **Spotify reloaded!**" if self.spotify else "❌ **Spotify NOT found!**"

        if not os.path.exists('client_secret.json'):
             return await interaction.response.send_message(f"{spot_status}\n❌ Missing `client_secret.json`!", ephemeral=True)
        
        try:
            self.auth_flow = Flow.from_client_secrets_file(
                'client_secret.json',
                scopes=['https://www.googleapis.com/auth/youtube'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            auth_url, _ = self.auth_flow.authorization_url(prompt='consent')
            
            cmd_mention = "` /ytcode <code> `" 
            ytcode_cmd = discord.utils.get(self.bot.tree.get_commands(), name="ytcode")
            if ytcode_cmd:
                if hasattr(self.bot, 'cmd_cache') and interaction.guild_id in self.bot.cmd_cache:
                    cmd_id = self.bot.cmd_cache[interaction.guild_id].get("ytcode")
                    if cmd_id:
                        cmd_mention = f"</ytcode:{cmd_id}>"

            await interaction.response.send_message(
                f"{spot_status}\n🔄 **YouTube API Renewal Started!**\n1. Click: [Auth Link](<{auth_url}>)\n2. Run: {cmd_mention} (paste the code)", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ytcode", description="Completes the YouTube renewal with the code.")
    @app_commands.describe(code="The code from the Auth Link")
    @app_commands.default_permissions(administrator=True)
    async def ytcode(self, interaction: discord.Interaction, code: str):
        if not self.auth_flow:
            return await interaction.response.send_message("❌ Run `/ytauth` first!", ephemeral=True)
        
        try:
            self.auth_flow.fetch_token(code=code)
            
            global_config = self.bot.db.get_collection("global_music_settings")
            if isinstance(global_config, list):
                 if global_config: global_config = global_config[0]
                 else: global_config = {}
            
            global_config['youtube_token_json'] = self.auth_flow.credentials.to_json()
            
            reminder_time = datetime.datetime.now().timestamp() + (6 * 24 * 60 * 60)
            global_config['reminder_timestamp'] = reminder_time
            global_config['reminder_user_id'] = interaction.user.id
            global_config['reminder_sent'] = False
            
            self.bot.db.save_collection("global_music_settings", global_config)
            
            await self.load_youtube_service()
            await interaction.response.send_message("✅ **Success!** License renewed and saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="serverplaylist", description="Set the YouTube Playlist Link or ID.")
    @app_commands.describe(playlist="The YouTube Playlist Link or ID")
    @app_commands.default_permissions(administrator=True)
    async def serverplaylist(self, interaction: discord.Interaction, playlist: str):
        """Set the YouTube Playlist Link or ID."""
        match = re.search(r'list=([a-zA-Z0-9_-]+)', playlist)
        
        if match:
            clean_id = match.group(1)
        else:
            clean_id = playlist

        config = self.load_config(interaction.guild_id)
        config['playlist_id'] = clean_id
        self.save_config(interaction.guild_id, config)
        
        # Also update the sync URL so the bot downloads it
        self.server_playlist_url = playlist
        await interaction.response.send_message(f"✅ Server Playlist ID set to: `{clean_id}`\nℹ️ Syncing content...", ephemeral=True)
        # Trigger sync
        if self.sync_server_playlist.is_running():
            self.sync_server_playlist.restart()
        else:
            self.sync_server_playlist.start()

    @app_commands.command(name="musicchannel", description="Set the music sharing channel.")
    @app_commands.describe(channel="The channel for music links")
    @app_commands.default_permissions(administrator=True)
    async def musicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        config = self.load_config(interaction.guild_id)
        config['music_channel_id'] = channel.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ music channel set to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="playlist", description="List cached songs (Downloaded)")
    async def playlist(self, interaction: discord.Interaction):
        """Lists all songs in the music_cache directory."""
        if not os.path.exists(self.download_dir):
            await interaction.response.send_message("No music cache found.", ephemeral=True)
            return

        try:
            files = []
            for f in os.listdir(self.download_dir):
                if f.endswith('.opus') or f.endswith('.webm') or f.endswith('.mp3') or f.endswith('.m4a'):
                    full_path = os.path.join(self.download_dir, f)
                    files.append((f, os.path.getmtime(full_path)))
            
            files.sort(key=lambda x: x[1], reverse=True)
            
            if not files:
                await interaction.response.send_message("The playlist is empty.", ephemeral=True)
                return

            output = "**Cached Songs:**\n"
            for i, (filename, _) in enumerate(files):
                name_without_ext = os.path.splitext(filename)[0]
                line = f"{i+1}. {name_without_ext}\n"
                if len(output) + len(line) > 1900:
                    output += "... (truncated)"
                    break
                output += line
            
            await interaction.response.send_message(output)
        except Exception as e:
            await interaction.response.send_message(f"Error listing playlist: {e}", ephemeral=True)

    @app_commands.command(name="remove", description="Remove a song from the playlist and local cache.")
    @app_commands.describe(query="Playlist Index number (from /playlist) OR YouTube URL/ID")
    @app_commands.default_permissions(administrator=True)
    async def remove(self, interaction: discord.Interaction, query: str):
        """Removes a song from the local cache AND the server playlist."""
        await interaction.response.defer(ephemeral=True)
        log = []
        
        # 1. IDENTIFY TARGETS (Local File & API Video ID)
        target_filename = None
        target_video_id = None
        target_title = None

        # Check if input is a Number (Index)
        if query.isdigit():
            index = int(query)
            if os.path.exists(self.download_dir):
                files = []
                for f in os.listdir(self.download_dir):
                    if f.endswith('.opus') or f.endswith('.webm') or f.endswith('.mp3') or f.endswith('.m4a'):
                        full_path = os.path.join(self.download_dir, f)
                        files.append((f, os.path.getmtime(full_path)))
                files.sort(key=lambda x: x[1], reverse=True)
                
                if 1 <= index <= len(files):
                    target_filename = files[index-1][0]
                    log.append(f"ℹ️ Found local file at index {index}: `{target_filename}`")
                    # Try to guess title from filename for API search
                    # Filename format: Title - Uploader.opus
                    name_part = os.path.splitext(target_filename)[0]
                    # This is a loose guess, but useful for searching the playlist
                    target_title = name_part.split(' - ')[0] if ' - ' in name_part else name_part
                else:
                    return await interaction.followup.send(f"❌ Invalid index {index}. Check `/playlist`.")
        
        # Check if input is URL/ID (or if we didn't use index)
        if not target_filename:
            # Check for YouTube URL/ID regex
            yt_match = re.search(r'(?:v=|\/)([a-zA-Z0-9_-]{11})', query)
            if yt_match:
                target_video_id = yt_match.group(1)
            elif len(query) == 11:
                target_video_id = query
            
            if target_video_id:
                log.append(f"ℹ️ Parsed Video ID: `{target_video_id}`")

        # 2. PERFORM DELETION - LOCAL
        if target_filename:
            try:
                os.remove(os.path.join(self.download_dir, target_filename))
                log.append(f"✅ Deleted local file: `{target_filename}`")
            except Exception as e:
                log.append(f"❌ Failed to delete local file: {e}")
        elif target_video_id:
            # Try to find a local file that matches the ID? 
            # We don't have ID in filenames easily, so we might skip or do a fuzzy search if we fetch API title first.
            pass 

        # 3. PERFORM DELETION - API (Server Playlist)
        if self.youtube:
            config = self.load_config(interaction.guild_id)
            playlist_id = config.get('playlist_id')
            
            if playlist_id:
                loop = asyncio.get_running_loop()
                try:
                    # We need to find the Playlist Item ID. 
                    # If we have target_video_id, scan for that.
                    # If we have target_title (from filename), scan for that.
                    
                    request = self.youtube.playlistItems().list(
                        part="id,snippet",
                        playlistId=playlist_id,
                        maxResults=50
                    )
                    
                    found_item_id = None
                    deleted_title = "?"
                    
                    while request and not found_item_id:
                        response = await loop.run_in_executor(None, request.execute)
                        
                        for item in response.get('items', []):
                            snippet = item['snippet']
                            # Match by ID
                            if target_video_id and snippet['resourceId']['videoId'] == target_video_id:
                                found_item_id = item['id']
                                deleted_title = snippet['title']
                                break
                            # Match by Title (Fuzzy/Exact)
                            if target_title and (target_title in snippet['title'] or snippet['title'] in target_title):
                                found_item_id = item['id']
                                deleted_title = snippet['title']
                                # If we found it via title, we can also now try to delete local file if we hadn't already
                                if not target_filename: 
                                    # Logic to find file by title would go here, but omitting for complexity
                                    pass
                                break
                        
                        if found_item_id: break
                        request = self.youtube.playlistItems().list_next(request, response)
                    
                    if found_item_id:
                        del_req = self.youtube.playlistItems().delete(id=found_item_id)
                        await loop.run_in_executor(None, del_req.execute)
                        log.append(f"✅ Removed **{deleted_title}** from Server Playlist (API).")
                    else:
                        log.append("⚠️ Could not find song in Server Playlist (API).")
                
                except Exception as e:
                    log.append(f"❌ API Error: {e}")
            else:
                log.append("ℹ️ No Server Playlist configured (API skipped).")
        else:
            log.append("ℹ️ YouTube API not loaded (API skipped).")

        # Conclusion
        if not log:
            log.append("❌ Could not identify song by Index or URL.")
        
        await interaction.followup.send("\n".join(log))

    @app_commands.command(name="play", description="Download and play a song/playlist (Uses yt-dlp)")
    @app_commands.describe(query="The song to search for or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if yt_dlp is None:
            await interaction.response.send_message("Missing 'yt-dlp'. Cannot play.", ephemeral=True)
            return

        await interaction.response.defer()
        
        results = await self.download_content(query, play_mode=True)
        
        if not results:
            await interaction.followup.send("Failed to find or download content.")
            return

        if not interaction.guild.voice_client:
            if interaction.user.voice:
                await interaction.user.voice.channel.connect()
            else:
                await interaction.followup.send("You are not in a voice channel.")
                return

        count = len(results)
        first_song = results[0]
        
        if count > 1:
            await interaction.followup.send(f"Queued {count} songs (Cached locally).")
        else:
            await interaction.followup.send(f"Queued **{first_song['title']}** (Cached locally).")

        player_cog = self.bot.get_cog("Player")
        if player_cog:
            for track in results:
                if hasattr(player_cog, 'add_to_queue'):
                    await player_cog.add_to_queue(interaction.guild.id, track['url'], interaction)
                else:
                    print("Error: Player cog missing add_to_queue method")
        else:
             print("Player cog not loaded.")

    @app_commands.command(name="test_play", description="Debug: Try to play a song and report detailed errors")
    @app_commands.describe(query="The song to test")
    async def test_play(self, interaction: discord.Interaction, query: str = "never gonna give you up"):
        await interaction.response.defer()
        log = ["**Diagnostic Playback Test**"]
        
        try:
            if yt_dlp is None:
                log.append("❌ `yt_dlp` library not found.")
                await interaction.followup.send("\n".join(log))
                return
            log.append("✅ `yt_dlp` library present.")
            log.append(f"ℹ️ Download dir: `{self.download_dir}` (Exists: {os.path.exists(self.download_dir)})")
            
            if not interaction.user.voice:
                log.append("❌ You are not in a voice channel.")
                await interaction.followup.send("\n".join(log))
                return
            
            permissions = interaction.user.voice.channel.permissions_for(interaction.guild.me)
            if not permissions.connect or not permissions.speak:
                log.append(f"❌ Missing permissions (Connect: {permissions.connect}, Speak: {permissions.speak})")
                await interaction.followup.send("\n".join(log))
                return

            vc = interaction.guild.voice_client
            if not vc:
                try:
                    vc = await interaction.user.voice.channel.connect()
                    log.append("✅ Connected to voice channel.")
                except Exception as e:
                    log.append(f"❌ Failed to connect: {e}")
                    await interaction.followup.send("\n".join(log))
                    return
            else:
                log.append("✅ Already connected to voice.")

            log.append(f"ℹ️ Attempting download for: `{query}`")
            results = await self.download_content(query, play_mode=True)
            
            if not results:
                log.append("❌ `download_content` returned None.")
                await interaction.followup.send("\n".join(log))
                return
            
            log.append(f"✅ Download successful. Got {len(results)} results.")
            first = results[0]
            log.append(f"ℹ️ File: `{first['url']}`")
            if os.path.exists(first['url']):
                log.append("✅ File exists on disk.")
            else:
                log.append(f"❌ File missing from disk at: `{first['url']}`")

            player_cog = self.bot.get_cog("Player")
            if player_cog:
                log.append("✅ Player cog loaded.")
                if hasattr(player_cog, 'add_to_queue'):
                    log.append("ℹ️ Calling `add_to_queue`...")
                    try:
                        await player_cog.add_to_queue(interaction.guild.id, first['url'], interaction)
                        log.append("✅ `add_to_queue` executed.")
                    except Exception as e:
                        log.append(f"❌ Error in `add_to_queue`: {e}")
                else:
                    log.append("❌ Player cog missing `add_to_queue`.")
            else:
                log.append("❌ Player cog not loaded.")

            await interaction.followup.send("\n".join(log))

        except Exception as e:
            log.append(f"❌ **Unexpected Error:** {e}")
            log.append(f"
