import discord
from discord import app_commands
from discord.ext import commands
import wavelink
import os
import asyncio
import random
from typing import cast
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
import json
import base64

# Define the scopes
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# Client Config for OAuth
# Make sure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set in your environment variables
CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flow = None
        self.credentials = None
        
        # --- QUOTA FIX: API Key Rotation ---
        # 1. Look for YOUTUBE_API_KEYS (comma separated list) in env
        # 2. Fallback to YOUTUBE_API_KEY (single key)
        keys_env = os.getenv("YOUTUBE_API_KEYS", os.getenv("YOUTUBE_API_KEY", ""))
        self.api_keys = [k.strip() for k in keys_env.split(',') if k.strip()]
        self.current_key_index = 0
        
        # Initialize the YouTube Data API service
        self.youtube = self._build_youtube_service()

    def _build_youtube_service(self):
        """Builds the YouTube service using the current API Key index."""
        if not self.api_keys:
            return None
        
        key = self.api_keys[self.current_key_index]
        try:
            return googleapiclient.discovery.build("youtube", "v3", developerKey=key)
        except Exception as e:
            print(f"Error building YouTube service: {e}")
            return None

    def _rotate_key(self):
        """Rotates to the next available API key in the list."""
        if not self.api_keys or len(self.api_keys) < 2:
            return False
            
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        print(f"⚠️ YouTube Quota Exceeded. Rotating to API Key index: {self.current_key_index}")
        self.youtube = self._build_youtube_service()
        return True

    def execute_api_call(self, build_request_func):
        """
        Executes a YouTube API call with automatic key rotation on quota errors.
        
        Args:
            build_request_func: A lambda that takes 'service' and returns a request object.
        """
        # Calculate max retries based on number of keys
        retries = len(self.api_keys) if self.api_keys else 1
        if retries < 1: retries = 1
        
        last_error = None
        
        # Try once with current key, then rotate if needed
        for _ in range(retries + 1):
            if not self.youtube:
                self.youtube = self._build_youtube_service()
                if not self.youtube: break

            try:
                request = build_request_func(self.youtube)
                return request.execute()
            except googleapiclient.errors.HttpError as e:
                # Check for 403 Forbidden with reason 'quotaExceeded'
                if e.resp.status == 403 and 'quotaExceeded' in str(e):
                    if self.api_keys and len(self.api_keys) > 1:
                        if self._rotate_key():
                            continue # Retry with the new key
                
                last_error = e
                # If it's not a quota error, raise it immediately
                if e.resp.status != 403:
                    raise e
        
        if last_error:
            raise last_error
        return None

    @app_commands.command(name="ytauth", description="Authorize the bot to access your YouTube account")
    async def ytauth(self, interaction: discord.Interaction):
        self.flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_config(
            CLIENT_CONFIG, SCOPES
        )
        self.flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = self.flow.authorization_url(prompt="consent")

        # --- UX FIX: Clickable Command Link ---
        # Default to text
        ytcode_mention = "`/ytcode`"
        try:
            # Attempt to fetch command ID to make it clickable (</ytcode:ID>)
            # 1. Try fetching global commands
            cmds = await self.bot.tree.fetch_commands()
            ytcode_cmd = discord.utils.get(cmds, name="ytcode")
            
            # 2. If not found and in guild, try guild commands
            if not ytcode_cmd and interaction.guild:
                guild_cmds = await self.bot.tree.fetch_commands(guild=interaction.guild)
                ytcode_cmd = discord.utils.get(guild_cmds, name="ytcode")
            
            if ytcode_cmd:
                ytcode_mention = f"</ytcode:{ytcode_cmd.id}>"
        except Exception as e:
            # If fetching commands fails (e.g. rate limit), keep text default
            pass

        await interaction.response.send_message(
            f"Please go to this URL to authorize the bot:\n{auth_url}\n\n"
            f"After authorizing, copy the code and run {ytcode_mention} `<code>`.",
            ephemeral=True
        )

    @app_commands.command(name="ytcode", description="Submit the authorization code")
    async def ytcode(self, interaction: discord.Interaction, code: str):
        if not self.flow:
            await interaction.response.send_message("You need to run /ytauth first.", ephemeral=True)
            return

        try:
            self.flow.fetch_token(code=code)
            self.credentials = self.flow.credentials
            # Switch service to use the authenticated credentials
            self.youtube = googleapiclient.discovery.build("youtube", "v3", credentials=self.credentials)
            await interaction.response.send_message("Successfully authorized!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to authorize: {e}", ephemeral=True)

    # --- Helper: Use this for Playlist Logic to fix the Quota Crash ---
    def get_playlist_items(self, playlist_id):
        results = []
        next_page_token = None
        
        while True:
            try:
                # WRAPPER USAGE: Prevents crash on quota exceeded
                response = self.execute_api_call(
                    lambda s: s.playlistItems().list(
                        part="snippet",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=next_page_token
                    )
                )
                
                results.extend(response.get('items', []))
                next_page_token = response.get('nextPageToken')
                
                if not next_page_token:
                    break
            except Exception as e:
                print(f"Error fetching playlist items: {e}")
                # Break to return what we have so far instead of crashing
                break
                
        return results

    # NOTE: You will need to ensure your existing 'play' command uses 'get_playlist_items'
    # or calls 'execute_api_call' for any other YouTube API requests.

async def setup(bot):
    await bot.add_cog(Music(bot))
