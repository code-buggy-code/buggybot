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
# - checkmusic(interaction)
# - refreshmusic(interaction)
# - entercode(interaction, code)
# - setplaylist(interaction, playlist_id)
# - setmusicchannel(interaction, channel)
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

    def cog_unload(self):
        self.check_token_validity_task.cancel()

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
                        # We save it as a list with one item if we want to stick to list convention, 
                        # or just overwrite "global_music_settings" key with the dict. 
                        # DatabaseHandler supports generic data, so we'll save the dict directly.
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

        match = re.search(r'(https?://[^\s]+)', url)
        clean_url = match.group(0) if match else url
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

    # --- COMMANDS ---

    @app_commands.command(name="checkmusic", description="Admin: Checks all music API statuses.")
    @app_commands.checks.has_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        is_valid = await self.load_youtube_service()
        
        yt_msg = f"✅ **YouTube License Valid!**" if is_valid else "❌ **YouTube License Broken.**"
        spot_msg = "✅ **Spotify Ready!**" if self.spotify else "❌ **Spotify Not Loaded.**"
        
        await interaction.response.send_message(f"{yt_msg}\n{spot_msg}", ephemeral=True)

    @app_commands.command(name="refreshmusic", description="Admin: Starts the OAuth flow to renew YouTube license.")
    @app_commands.checks.has_permissions(administrator=True)
    async def refreshmusic(self, interaction: discord.Interaction):
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
            
            await interaction.response.send_message(
                f"{spot_status}\n🔄 **YouTube API Renewal Started!**\n1. Click: [Auth Link]({auth_url})\n2. Type: `/entercode <code>`",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="entercode", description="Admin: Completes the YouTube renewal with the code.")
    @app_commands.describe(code="The authorization code from Google")
    @app_commands.checks.has_permissions(administrator=True)
    async def entercode(self, interaction: discord.Interaction, code: str):
        if not self.auth_flow:
            return await interaction.response.send_message("❌ Run `/refreshmusic` first!", ephemeral=True)
        
        try:
            self.auth_flow.fetch_token(code=code)
            
            # Save Global Token
            global_config = self.bot.db.get_collection("global_music_settings")
            if isinstance(global_config, list): global_config = {}
            global_config['youtube_token_json'] = self.auth_flow.credentials.to_json()
            self.bot.db.save_collection("global_music_settings", global_config)
            
            await self.load_youtube_service()
            await interaction.response.send_message("✅ **Success!** License renewed and saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="setplaylist", description="Admin: Set the YouTube Playlist ID.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setplaylist(self, interaction: discord.Interaction, playlist_id: str):
        config = self.load_config(interaction.guild_id)
        config['playlist_id'] = playlist_id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Playlist ID set to `{playlist_id}`.", ephemeral=True)

    @app_commands.command(name="setmusicchannel", description="Admin: Set the music sharing channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setmusicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        config = self.load_config(interaction.guild_id)
        config['music_channel_id'] = channel.id
        self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ music channel set to {channel.mention}.", ephemeral=True)

    # --- LISTENER ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        config = self.load_config(message.guild.id)
        
        # Check if in music channel
        if config['music_channel_id'] != 0 and message.channel.id == config['music_channel_id']:
            
            # 1. Spotify Link
            if "spotify.com" in message.content.lower():
                result = await self.process_spotify_link(message.content, message.guild.id)
                if result is True:
                    await message.add_reaction("🎵")
                else:
                    await message.channel.send(f"⚠️ **Error:** Spotify link failed.\n`{result}`", delete_after=10)

            # 2. YouTube Link (Direct Insert)
            elif self.youtube and ("v=" in message.content or "youtu.be/" in message.content):
                v_id = None
                if "v=" in message.content: 
                    v_id = message.content.split("v=")[1].split("&")[0]
                elif "youtu.be/" in message.content: 
                    v_id = message.content.split("youtu.be/")[1].split("?")[0]
                
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
