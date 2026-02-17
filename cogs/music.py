import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import re
import datetime
import sys

# music APIs
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

# Function/Class List:
# class Music(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - load_config(guild_id)
# - save_config(guild_id, config)
# - load_youtube_service()
# - load_music_services()
# - process_spotify_link(url, guild_id)
# - search_youtube_official(query)
# - check_token_validity_task()
# - license_reminder_task() <--- NEW
# - checkmusic(interaction) [Slash]
# - ytauth(interaction) [Slash]
# - ytcode(interaction, code) [Slash]
# - playlist(interaction, playlist) [Slash]
# - musicchannel(interaction, channel) [Slash]
# - removesong(interaction, query) [Slash]
# - on_message(message)
# setup(bot)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.youtube = None
        self.spotify = None
        self.auth_flow = None
        
        # Start services
        self.load_music_services()
        self.bot.loop.create_task(self.load_youtube_service())
        self.check_token_validity_task.start()
        self.license_reminder_task.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()
        self.license_reminder_task.cancel()

    def load_config(self, guild_id):
        """Loads music config for a specific guild from DB."""
        # Using "music_config" collection (Dict of {guild_id: config})
        data = self.bot.db.get_collection("music_config")
        if isinstance(data, list): data = {} # Migration safety
        
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
        
        # Token is global, not per-server
        global_config = self.bot.db.get_collection("global_music_settings")
        # If it returns a list (default empty), make it a dict
        if isinstance(global_config, list): 
            if global_config: global_config = global_config[0]
            else: global_config = {}

        token_json = global_config.get('youtube_token_json')
        
        # Fallback to local file if DB is empty
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
                        # Save Global settings
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
        """Loads Spotify service only (YTM removed)."""
        # 1. Spotify
        # Load from spotify.json
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
            # Perform a standard YouTube Search
            request = self.youtube.search().list(
                part="snippet",
                maxResults=1,
                q=query,
                type="video"
            )
            response = await loop.run_in_executor(None, request.execute)
            
            if response.get('items'):
                # Return the video ID of the first result
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

        # Logic: Link is already regex-verified by on_message, so we can trust it.
        # We strip query parameters just to be safe.
        clean_url = url.split("?")[0]
        
        loop = asyncio.get_running_loop()

        try:
            # 1. Get Track Info from Spotify
            try:
                track = await loop.run_in_executor(None, self.spotify.track, clean_url)
            except Exception as e:
                return f"Spotify Error: {e}"

            # Make search query
            search_query = f"{track['artists'][0]['name']} - {track['name']}"

            # 2. Search on YouTube (Official API)
            video_id = await self.search_youtube_official(search_query)

            if not video_id: 
                return f"Could not find '{search_query}' on YouTube."

            # 3. Add to YouTube Playlist
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
        """Daily check for token validity."""
        valid = await self.load_youtube_service()
        status = "Valid" if valid else "Expired/Broken"
        print(f"[music] Daily Token Check: {status}")

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def license_reminder_task(self):
        """Checks if it's been 6 days since renewal to ping the user."""
        global_config = self.bot.db.get_collection("global_music_settings")
        # Handle the list vs dict structure safely
        if isinstance(global_config, list): 
             if global_config: global_config = global_config[0]
             else: return

        # Check if reminder is needed
        if not global_config.get('reminder_timestamp') or global_config.get('reminder_sent'):
            return

        remind_ts = global_config['reminder_timestamp']
        
        # If current time is past the reminder timestamp
        if datetime.datetime.now().timestamp() >= remind_ts:
            user_id = global_config.get('reminder_user_id')
            if user_id:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await user.send(
                        "⚠️ **YouTube License Reminder!**\n"
                        "It has been 6 days since you renewed the YouTube license. "
                        "It expires in roughly 24 hours.\n\n"
                        "Please run `/ytauth` and then `/ytcode` again soon to keep the music playing!"
                    )
                except Exception as e:
                    print(f"Failed to DM user for license reminder: {e}")
            
            # Mark as sent so we don't spam
            global_config['reminder_sent'] = True
            
            # We must wrap it back in a list if the DB expects it (based on other methods)
            # But the save method typically handles the structure passed to it.
            # safe save:
            self.bot.db.save_collection("global_music_settings", global_config)

    @license_reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    # --- SLASH COMMANDS (Top Level) ---

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
            
            # Helper to find command ID for clickable link
            cmd_mention = "` /ytcode <code> `" # Fallback
            
            # Try to find the command in the tree or cache
            # Note: IDs are only available after sync.
            ytcode_cmd = discord.utils.get(self.bot.tree.get_commands(), name="ytcode")
            if ytcode_cmd:
                # We try to use the cache from main.py if it exists
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
        """Completes the YouTube renewal with the code."""
        if not self.auth_flow:
            return await interaction.response.send_message("❌ Run `/ytauth` first!", ephemeral=True)
        
        try:
            self.auth_flow.fetch_token(code=code)
            
            # Save Global Token
            global_config = self.bot.db.get_collection("global_music_settings")
            # Ensure we get the dict object correctly
            if isinstance(global_config, list):
                 if global_config: global_config = global_config[0]
                 else: global_config = {}
            
            global_config['youtube_token_json'] = self.auth_flow.credentials.to_json()
            
            # NEW: Set reminder for 6 days from now
            # 6 days * 24 hours * 60 mins * 60 secs
            reminder_time = datetime.datetime.now().timestamp() + (6 * 24 * 60 * 60)
            global_config['reminder_timestamp'] = reminder_time
            global_config['reminder_user_id'] = interaction.user.id
            global_config['reminder_sent'] = False
            
            self.bot.db.save_collection("global_music_settings", global_config)
            
            await self.load_youtube_service()
            await interaction.response.send_message("✅ **Success!** License renewed and saved. I will ping you in 6 days to renew it again!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="playlist", description="Set the YouTube Playlist Link or ID.")
    @app_commands.describe(playlist="The YouTube Playlist Link or ID")
    @app_commands.default_permissions(administrator=True)
    async def playlist(self, interaction: discord.Interaction, playlist: str):
        """Set the YouTube Playlist Link or ID."""
        # Extract ID if a full link is provided
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

    @app_commands.command(name="removesong", description="Remove a song from the playlist by URL or ID.")
    @app_commands.describe(query="The YouTube URL or Video ID to remove")
    @app_commands.default_permissions(administrator=True)
    async def removesong(self, interaction: discord.Interaction, query: str):
        """Remove a song from the playlist by URL or ID."""
        await interaction.response.defer(ephemeral=True)
        
        if not self.youtube:
            return await interaction.followup.send("❌ YouTube API not loaded.")

        config = self.load_config(interaction.guild_id)
        playlist_id = config.get('playlist_id')
        
        if not playlist_id:
            return await interaction.followup.send("❌ No playlist configured.")

        # Extract Video ID
        video_id = None
        # Regex for standard/short/music youtube links to grab ID
        yt_match = re.search(r'(?:v=|\/)([a-zA-Z0-9_-]{11})', query)
        if yt_match:
            video_id = yt_match.group(1)
        else:
            # Maybe it is just the ID? (11 chars)
            if len(query) == 11:
                video_id = query
        
        if not video_id:
             return await interaction.followup.send("❌ Could not parse Video ID from query.")

        loop = asyncio.get_running_loop()

        # Search Playlist for this Video ID
        try:
            request = self.youtube.playlistItems().list(
                part="id,snippet",
                playlistId=playlist_id,
                maxResults=50
            )
            
            target_item_id = None
            video_title = "?"
            
            # Simple pagination handling
            while request:
                response = await loop.run_in_executor(None, request.execute)
                
                for item in response.get('items', []):
                    # Check if this item's video ID matches our target
                    if item['snippet']['resourceId']['videoId'] == video_id:
                        target_item_id = item['id']
                        video_title = item['snippet']['title']
                        break
                
                if target_item_id:
                    break
                    
                request = self.youtube.playlistItems().list_next(request, response)
            
            if not target_item_id:
                return await interaction.followup.send(f"❌ Video ID `{video_id}` not found in the playlist.")
            
            # Delete it
            del_req = self.youtube.playlistItems().delete(id=target_item_id)
            await loop.run_in_executor(None, del_req.execute)
            
            await interaction.followup.send(f"✅ Removed **{video_title}** from the playlist.")

        except Exception as e:
            await interaction.followup.send(f"❌ Error removing song: {e}")

    # --- LISTENER ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        config = self.load_config(message.guild.id)
        
        # Check if in music channel
        # If music_channel_id is 0, we allow all channels.
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
            # Extract Video ID based on which one matched
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
