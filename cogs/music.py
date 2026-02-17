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
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # List of active YouTube service objects for rotation
        self.youtube_services = [] 
        self.spotify = None
        self.auth_flow = None
        self.auth_flow_slot = 1 # Track which slot is currently auth'ing

        # Start services
        self.load_music_services()
        self.bot.loop.create_task(self.load_youtube_service())
        self.check_token_validity_task.start()
        self.license_reminder_task.start()

    def cog_unload(self):
        self.check_token_validity_task.cancel()
        self.license_reminder_task.cancel()

    # --- HELPERS ---

    def _get_secret_filename(self, slot):
        """Returns the filename for the client secret of a given slot."""
        return 'client_secret.json' if slot == 1 else f'client_secret_{slot}.json'

    def _get_token_key(self, slot):
        """Returns the DB key for the token of a given slot."""
        return 'youtube_token_json' if slot == 1 else f'youtube_token_{slot}_json'

    async def execute_api_call(self, request_builder):
        """
        Executes a YouTube API request with automatic rotation on quota errors.
        
        Args:
            request_builder: A function that takes a 'service' object and returns an executable request.
                             Example: lambda service: service.search().list(...)
        """
        if not self.youtube_services:
            # Try reloading if empty
            await self.load_youtube_service()
            if not self.youtube_services:
                raise Exception("No active YouTube services available.")

        last_error = None
        # Try each available service in the pool
        for i, service in enumerate(self.youtube_services):
            try:
                request = request_builder(service)
                # Run in executor to prevent blocking
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, request.execute)
            except HttpError as e:
                # Check for Quota Exceeded (403)
                if e.resp.status == 403 and 'quotaExceeded' in str(e):
                    print(f"⚠️ Quota exceeded on License #{i+1}. Rotating...")
                    continue # Try next service
                
                # If it's not a quota error, we don't rotate, just fail
                raise e
            except Exception as e:
                last_error = e
                # General connection errors might be worth retrying, but usually we fail fast
                raise e
        
        # If we ran out of services
        raise Exception(f"All YouTube licenses exhausted or failed. Last error: {last_error}")

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
        """Loads all available YouTube API services from stored tokens."""
        self.youtube_services = []
        
        global_config = self.bot.db.get_collection("global_music_settings")
        if isinstance(global_config, list): 
            if global_config: global_config = global_config[0]
            else: global_config = {}

        # Check slots 1 through 5 (arbitrary limit)
        for slot in range(1, 6):
            secret_file = self._get_secret_filename(slot)
            
            # Skip if no secret file for this slot
            if not os.path.exists(secret_file):
                continue

            token_key = self._get_token_key(slot)
            token_json = global_config.get(token_key)

            # Fallback to local file for Slot 1 only (legacy support)
            if slot == 1 and not token_json and os.path.exists('token.json'):
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
                            global_config[token_key] = creds.to_json()
                            self.bot.db.save_collection("global_music_settings", global_config)
                        except Exception as e: 
                            print(f"Failed to refresh token for Slot {slot}: {e}")
                            continue

                    if creds.valid:
                        service = build('youtube', 'v3', credentials=creds)
                        self.youtube_services.append(service)
                        print(f"✅ Loaded YouTube License Slot {slot}")
                except Exception as e: 
                    print(f"Failed to load token for Slot {slot}: {e}")
        
        return len(self.youtube_services) > 0

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
        try:
            # Use the wrapper to search
            response = await self.execute_api_call(
                lambda s: s.search().list(
                    part="snippet",
                    maxResults=1,
                    q=query,
                    type="video"
                )
            )

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
        if not self.youtube_services: errors.append("YouTube API not loaded.")

        config = self.load_config(guild_id)
        if not config['playlist_id']: errors.append("Playlist ID not set.")

        if errors:
            return "Setup Errors:\n" + "\n".join([f"- {e}" for e in errors])

        clean_url = url.split("?")[0]
        loop = asyncio.get_running_loop()

        try:
            # 1. Get Track Info from Spotify
            try:
                track = await loop.run_in_executor(None, self.spotify.track, clean_url)
            except Exception as e:
                return f"Spotify Error: {e}"

            search_query = f"{track['artists'][0]['name']} - {track['name']}"

            # 2. Search on YouTube
            video_id = await self.search_youtube_official(search_query)

            if not video_id: 
                return f"Could not find '{search_query}' on YouTube."

            # 3. Add to YouTube Playlist (Using Rotation)
            try:
                await self.execute_api_call(
                    lambda s: s.playlistItems().insert(
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
                )
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
        status = f"{len(self.youtube_services)} Licenses Active" if valid else "No Active Licenses"
        print(f"[music] Daily Token Check: {status}")

    @check_token_validity_task.before_loop
    async def before_check_token(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def license_reminder_task(self):
        """Checks if it's been 6 days since renewal."""
        # Note: Keeps tracking the reminder timestamp from global config.
        # This will mostly reflect the last token renewed.
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
                        "It has been 6 days since you renewed a YouTube license. "
                        "It expires in roughly 24 hours.\n\n"
                        "Please run `/ytauth` and then `/ytcode` again soon to keep the music playing!"
                    )
                except Exception as e:
                    print(f"Failed to DM user for license reminder: {e}")

            global_config['reminder_sent'] = True
            self.bot.db.save_collection("global_music_settings", global_config)

    @license_reminder_task.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    # --- SLASH COMMANDS (Top Level) ---

    @app_commands.command(name="checkmusic", description="Checks all music API statuses.")
    @app_commands.default_permissions(administrator=True)
    async def checkmusic(self, interaction: discord.Interaction):
        """Checks all music API statuses."""
        await self.load_youtube_service()
        count = len(self.youtube_services)
        
        yt_msg = f"✅ **YouTube:** {count} Active License(s)" if count > 0 else "❌ **YouTube:** No Licenses."
        spot_msg = "✅ **Spotify:** Ready!" if self.spotify else "❌ **Spotify:** Not Loaded."

        await interaction.response.send_message(f"{yt_msg}\n{spot_msg}", ephemeral=True)

    @app_commands.command(name="ytauth", description="Starts the OAuth flow. Specify slot number (e.g. 1, 2).")
    @app_commands.describe(slot="License slot number")
    @app_commands.default_permissions(administrator=True)
    async def ytauth(self, interaction: discord.Interaction, slot: int):
        """Starts the OAuth flow to renew YouTube license."""
        # 1. Reload Spotify
        self.load_music_services()
        spot_status = "✅ **Spotify reloaded!**" if self.spotify else "❌ **Spotify NOT found!**"

        secret_file = self._get_secret_filename(slot)
        if not os.path.exists(secret_file):
             return await interaction.response.send_message(
                 f"{spot_status}\n❌ Missing `{secret_file}` for Slot {slot}!", ephemeral=True
             )

        try:
            self.auth_flow = Flow.from_client_secrets_file(
                secret_file,
                scopes=['https://www.googleapis.com/auth/youtube'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            self.auth_flow_slot = slot # Remember which slot we are auth'ing
            auth_url, _ = self.auth_flow.authorization_url(prompt='consent')

            # --- UX FIX: Clickable Command Link ---
            cmd_mention = "`/ytcode`" # Default text fallback
            try:
                # Fetch commands dynamically to get the ID for clickable link
                # We try global commands first
                cmds = await self.bot.tree.fetch_commands()
                ytcode_cmd = discord.utils.get(cmds, name="ytcode")
                
                # If not found globally, try guild-specific (if applicable)
                if not ytcode_cmd and interaction.guild:
                    guild_cmds = await self.bot.tree.fetch_commands(guild=interaction.guild)
                    ytcode_cmd = discord.utils.get(guild_cmds, name="ytcode")
                
                if ytcode_cmd:
                    # Syntax: </commandname:id>
                    cmd_mention = f"</ytcode:{ytcode_cmd.id}>"
            except Exception as e:
                print(f"Link generation failed: {e}")

            await interaction.response.send_message(
                f"{spot_status}\n🔄 **YouTube API Renewal (Slot {slot})!**\n"
                f"1. Click: [Auth Link](<{auth_url}>)\n"
                f"2. Run: {cmd_mention} `code` `slot:{slot}`", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="ytcode", description="Completes the YouTube renewal.")
    @app_commands.describe(code="The code from the Auth Link", slot="License slot number")
    @app_commands.default_permissions(administrator=True)
    async def ytcode(self, interaction: discord.Interaction, code: str, slot: int):
        """Completes the YouTube renewal with the code."""
        if not self.auth_flow:
            return await interaction.response.send_message("❌ Run `/ytauth` first!", ephemeral=True)

        if slot != self.auth_flow_slot:
            return await interaction.response.send_message(f"❌ You started auth for Slot {self.auth_flow_slot}, but submitted for Slot {slot}. Please match them.", ephemeral=True)

        try:
            self.auth_flow.fetch_token(code=code)

            # Save Token for specific slot
            global_config = self.bot.db.get_collection("global_music_settings")
            if isinstance(global_config, list):
                 if global_config: global_config = global_config[0]
                 else: global_config = {}

            token_key = self._get_token_key(slot)
            global_config[token_key] = self.auth_flow.credentials.to_json()

            # Reminder Setup (Updates the timestamp to now + 6 days)
            reminder_time = datetime.datetime.now().timestamp() + (6 * 24 * 60 * 60)
            global_config['reminder_timestamp'] = reminder_time
            global_config['reminder_user_id'] = interaction.user.id
            global_config['reminder_sent'] = False

            self.bot.db.save_collection("global_music_settings", global_config)

            await self.load_youtube_service()
            active_count = len(self.youtube_services)
            await interaction.response.send_message(f"✅ **Success!** License for Slot {slot} renewed. Total Active Licenses: {active_count}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="playlist", description="Set the YouTube Playlist Link or ID.")
    @app_commands.describe(playlist="The YouTube Playlist Link or ID")
    @app_commands.default_permissions(administrator=True)
    async def playlist(self, interaction: discord.Interaction, playlist: str):
        """Set the YouTube Playlist Link or ID."""
        match = re.search(r'list=([a-zA-Z0-9_-]+)', playlist)
        clean_id = match.group(1) if match else playlist

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

        if not self.youtube_services:
            return await interaction.followup.send("❌ YouTube API not loaded.")

        config = self.load_config(interaction.guild_id)
        playlist_id = config.get('playlist_id')

        if not playlist_id:
            return await interaction.followup.send("❌ No playlist configured.")

        # Extract Video ID
        video_id = None
        yt_match = re.search(r'(?:v=|\/)([a-zA-Z0-9_-]{11})', query)
        if yt_match:
            video_id = yt_match.group(1)
        else:
            if len(query) == 11: video_id = query

        if not video_id:
             return await interaction.followup.send("❌ Could not parse Video ID from query.")

        try:
            # Using execute_api_call for rotation safely
            # We need to build a paginated search here, which is tricky with the lambda wrapper
            # But we can wrap the specific calls inside the loop
            
            # NOTE: For complex logic like pagination where state is maintained between calls,
            # using the wrapper for EVERY call is safest.
            
            next_page_token = None
            target_item_id = None
            video_title = "?"
            
            # Limit to 5 pages to save quota
            for _ in range(5):
                response = await self.execute_api_call(
                    lambda s: s.playlistItems().list(
                        part="id,snippet",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=next_page_token
                    )
                )

                for item in response.get('items', []):
                    if item['snippet']['resourceId']['videoId'] == video_id:
                        target_item_id = item['id']
                        video_title = item['snippet']['title']
                        break
                
                if target_item_id: break
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token: break

            if not target_item_id:
                return await interaction.followup.send(f"❌ Video ID `{video_id}` not found in the first 250 items of the playlist.")

            # Delete it
            await self.execute_api_call(
                lambda s: s.playlistItems().delete(id=target_item_id)
            )

            await interaction.followup.send(f"✅ Removed **{video_title}** from the playlist.")

        except Exception as e:
            await interaction.followup.send(f"❌ Error removing song: {e}")

    # --- LISTENER ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return

        config = self.load_config(message.guild.id)
        if config['music_channel_id'] != 0 and message.channel.id != config['music_channel_id']:
            return

        content = message.content
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

        # 2. Handle YouTube
        elif self.youtube_services and (yt_music_match or yt_standard_match or yt_short_match):
            v_id = None
            if yt_music_match: v_id = yt_music_match.group(1)
            elif yt_standard_match: v_id = yt_standard_match.group(1)
            elif yt_short_match: v_id = yt_short_match.group(1)

            if v_id:
                try:
                    await self.execute_api_call(
                        lambda s: s.playlistItems().insert(
                            part="snippet",
                            body={
                                "snippet": {
                                    "playlistId": config['playlist_id'],
                                    "resourceId": {"kind": "youtube#video", "videoId": v_id}
                                }
                            }
                        )
                    )
                    await message.add_reaction("🎵")
                except Exception as e:
                    await message.channel.send(f"⚠️ **Error:** YouTube link failed.\n`{e}`", delete_after=10)

async def setup(bot):
    await bot.add_cog(Music(bot))
