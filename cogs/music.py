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

        # Initialize YTDL Here for Safety
        self.ytdl = None
        if yt_dlp:
            yt_dlp.utils.bug_reports_message = lambda: ''
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
            }
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
        """Loads music config
