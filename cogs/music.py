import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import re
import datetime
import sys

# Music APIs
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from ytmusicapi import YTMusic
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

# Function/Class List:
# class Music(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - load_config()
# - save_config()
# - load_youtube_service()
# - load_music_services()
# - process_spotify_link(url)
# - check_token_validity_task()
# - checkmusic(interaction)
# - refreshmusic(interaction)
# - entercode(interaction, code)
# - setupmusic(interaction, cookie, user_agent)
# - setplaylist(interaction, playlist_id)
# - setmusicchannel(interaction, channel)
# - on_message(message)
# setup(bot)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Music management: Spotify to YouTube sync."
        
        self.youtube = None
        self.ytmusic = None
        self.spotify = None
        self.auth_flow = None
        
        # Config cache
        self.config = {
            "playlist_id": "",
            "music_channel_id": 0,
            "youtube_token_json": ""
        }
        
        # Load initial state
        self.load_config()
        
        # Start services
        self.load_music_services()
        self.bot.loop.create_task(self.load_youtube_service())
        self.check_token_validity_task.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()

    def load_config(self):
        """Loads music config from the bot's DB."""
        # We'll store music settings in a 'music_config' collection
        data = self.bot.db.get_collection("music_config")
        if data:
            if isinstance(data, dict):
                 self.config.update(data)
            elif isinstance(data, list) and len(data) > 0:
                 # Handle if DB returns list of docs
                 self.config.update(data[0])

    def save_config(self):
        """Saves current config to DB."""
        # Save as a single document/dict
        self.bot.db.save_collection("music_config", self.config)

    async def load_youtube_service(self):
        """Loads the YouTube API service from stored token."""
        self.youtube = None
        token_json = self.config.get('youtube_token_json')
        
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
                        self.config['youtube_token_json'] = creds.to_json()
                        self.save_config()
                    except: 
                        return False
                
                if creds.valid:
                    self.youtube = build('youtube', 'v3', credentials=creds)
                    return True
            except: 
                return False
        return False

    def load_music_services(self):
        """Loads Spotify and YouTube Music services."""
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
        
        # 2. YouTube Music
        # Try to load from local file first (standard for ytmusicapi)
        if os.path.exists('browser.json'):
            try:
                self.ytmusic = YTMusic('browser.json')
                print("✅ YouTube Music Service Loaded.")
            except Exception as e:
                print(f"❌ Failed to load YouTube Music: {e}")
                self.ytmusic = None
        else:
            print("❌ browser.json not found for YTM.")

    async def process_spotify_link(self, url):
        """Converts Spotify link to YouTube video and adds to playlist."""
        errors = []
        if not self.spotify: errors.append("Spotify service not loaded.")
        if not self.ytmusic: errors.append("YouTube Music service not loaded.")
        if not self.youtube: errors.append("YouTube API not loaded.")
        if not self.config['playlist_id']: errors.append("Playlist ID not set.")

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

            search_query = f"{track['artists'][0]['name']} - {track['name']}"

            # 2. Search on YouTube Music
            try:
                # Lambda required because run_in_executor expects a callable
                search_results = await loop.run_in_executor(None, lambda: self.ytmusic.search(search_query, "songs"))
            except Exception as e:
                return f"YTM Search Error: {e}"

            if not search_results: 
                return f"Could not find '{search_query}' on YouTube Music."

            # 3. Add to YouTube Playlist
            try:
                req = self.youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": self.config['playlist_id'],
                            "resourceId": {
                                "kind": "youtube#video", 
                                "videoId": search_results[0]['videoId']
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
        print(f"[Music] Daily Token Check: {status}")

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    # --- COMMANDS ---

    @app_commands.command(name="checkmusic", description="Admin: Checks all music API statuses.")
    @app_commands.checks.has_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        is_valid = await self.load_youtube_service()
        
        yt_msg = f"✅ **YouTube License Valid!**" if is_valid else "❌ **YouTube License Broken.**"
        ytm_msg = "✅ **YouTube Music Ready!**" if self.ytmusic else "❌ **YouTube Music Not Loaded.**"
        spot_msg = "✅ **Spotify Ready!**" if self.spotify else "❌ **Spotify Not Loaded.**"
        
        await interaction.response.send_message(f"{yt_msg}\n{ytm_msg}\n{spot_msg}", ephemeral=True)

    @app_commands.command(name="refreshmusic", description="Admin: Starts the OAuth flow to renew YouTube license.")
    @app_commands.checks.has_permissions(administrator=True)
    async def refreshmusic(self, interaction: discord.Interaction):
        # 1. Reload YTM and Spotify
        self.load_music_services()
        ytm_status = "✅ **YouTube Music reloaded!**" if self.ytmusic else "❌ **YouTube Music NOT found!**"
        spot_status = "✅ **Spotify reloaded!**" if self.spotify else "❌ **Spotify NOT found!**"

        # 2. Start YouTube Flow
        if not os.path.exists('client_secret.json'):
             return await interaction.response.send_message(f"{ytm_status}\n{spot_status}\n❌ Missing `client_secret.json`!", ephemeral=True)
        
        try:
            self.auth_flow = Flow.from_client_secrets_file(
                'client_secret.json',
                scopes=['https://www.googleapis.com/auth/youtube'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            auth_url, _ = self.auth_flow.authorization_url(prompt='consent')
            
            await interaction.response.send_message(
                f"{ytm_status}\n{spot_status}\n🔄 **YouTube API Renewal Started!**\n1. Click: [Auth Link]({auth_url})\n2. Type: `/entercode <code>`",
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
            self.config['youtube_token_json'] = self.auth_flow.credentials.to_json()
            self.save_config()
            
            await self.load_youtube_service()
            await interaction.response.send_message("✅ **Success!** License renewed and saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="setupmusic", description="Admin: Manual setup for YTM with Cookie (User Agent optional).")
    @app_commands.describe(cookie="The raw Cookie string", user_agent="Optional: The raw User-Agent string (Defaults to Firefox 147)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setupmusic(self, interaction: discord.Interaction, cookie: str, user_agent: str = None):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 1. Handle Defaults
            if user_agent is None:
                # Default to the one you were using in your logs
                user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"

            # 2. Direct inputs
            # We MUST remove newlines/returns from headers to ensure valid HTTP requests.
            # This is the "cleaning" that is absolutely necessary for it to work.
            ua_clean = user_agent.replace("\n", "").strip()
            # Strip quotes and newlines just in case
            cookie_clean = cookie.replace("\n", "").replace("\r", "").strip().strip('"').strip("'")

            # 3. Construct dict
            header_dict = {
                "User-Agent": ua_clean,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": cookie_clean
            }

            # 4. DIRECT INITIALIZATION (Skips file reading ambiguity)
            # This feeds the dictionary directly to the API in memory.
            try:
                self.ytmusic = YTMusic(auth=header_dict)
                
                # If we get here without error, it worked! 
                # NOW we save it to the file so it loads next time you restart.
                with open('browser.json', 'w', encoding='utf-8') as f:
                    json.dump(header_dict, f, indent=4)
                    
                msg = f"✅ **Success!** YTM initialized directly and config saved!"
                
            except Exception as e:
                self.ytmusic = None
                # Show first 50 chars and last 50 chars for debug
                preview_start = cookie_clean[:50]
                preview_end = cookie_clean[-50:]
                msg = (f"⚠️ **Failed to Initialize (Direct):** `{e}`\n\n"
                       f"🔍 **Cookie Preview:**\n"
                       f"`{preview_start} ... {preview_end}`")

            await interaction.followup.send(msg, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="setplaylist", description="Admin: Set the YouTube Playlist ID.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setplaylist(self, interaction: discord.Interaction, playlist_id: str):
        self.config['playlist_id'] = playlist_id
        self.save_config()
        await interaction.response.send_message(f"✅ Playlist ID set to `{playlist_id}`.", ephemeral=True)

    @app_commands.command(name="setmusicchannel", description="Admin: Set the music sharing channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setmusicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.config['music_channel_id'] = channel.id
        self.save_config()
        await interaction.response.send_message(f"✅ Music channel set to {channel.mention}.", ephemeral=True)

    # --- LISTENER ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        # Check if in music channel
        if self.config['music_channel_id'] != 0 and message.channel.id == self.config['music_channel_id']:
            
            # 1. Spotify Link
            if "spotify.com" in message.content.lower():
                result = await self.process_spotify_link(message.content)
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
                                    "playlistId": self.config['playlist_id'],
                                    "resourceId": {"kind": "youtube#video", "videoId": v_id}
                                }
                            }
                        ).execute()
                        await message.add_reaction("🎵")
                    except Exception as e:
                        await message.channel.send(f"⚠️ **Error:** YouTube link failed.\n`{e}`", delete_after=10)

async def setup(bot):
    await bot.add_cog(Music(bot))
