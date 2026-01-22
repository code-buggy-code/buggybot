import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import time
from datetime import datetime, timezone
import asyncio
from typing import Literal, Optional, Union

# Function/Class List:
# class DatabaseHandler
# - __init__(db_name="leaderboardDB")
# - _load_from_file()
# - _save_to_file()
# - get_guild_config(guild_id)
# - save_guild_config(guild_id, config)
# - update_user_points(guild_id, group_key, user_id, points)
# - get_group_points(guild_id, group_key)
# - get_user_points(guild_id, user_id)
# - clear_points_by_group(guild_id, group_key)
# class Lead(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_config(guild_id)
# - get_tracked_groups(channel, config)
# - add_points_to_cache(user_id, guild_id, group_key, points)
# - create_leaderboard_embed(guild, group_key, group_data)
# - on_message(message)
# - on_reaction_add(reaction, user)
# - on_voice_state_update(member, before, after)
# - voice_time_checker()
# - point_saver()
# - lead_add(interaction, name)
# - lead_edit(interaction, group_num, name)
# - lead_track(interaction, group_num, target)
# - lead_untrack(interaction, group_num, target)
# - lead_remove(interaction, group_num)
# - lead_clear(interaction, group_num)
# - show_leaderboard(interaction, group_num)
# - award_points(interaction, user, amount)
# - check_points(ctx, member)
# setup(bot)

# --- DATABASE HANDLER ---
class DatabaseHandler:
    def __init__(self, db_name="leaderboardDB"):
        self.file_path = "leaderbug_database.json"
        self.data = self._load_from_file()

    def _load_from_file(self):
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"guild_configs": {}, "user_points": []}

    def _save_to_file(self):
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=4, default=str)

    # --- Guild Specific Configs ---
    async def get_guild_config(self, guild_id):
        guild_id = str(guild_id)
        configs = self.data.get("guild_configs", {})
        if guild_id not in configs:
            # Default Config Structure
            configs[guild_id] = {
                "groups": {
                    "1": {"name": "General", "tracked_ids": [], "last_lb_msg": None}
                }
            }
            self.data["guild_configs"] = configs
            self._save_to_file()
        return configs[guild_id]

    async def save_guild_config(self, guild_id, config):
        guild_id = str(guild_id)
        if "guild_configs" not in self.data:
            self.data["guild_configs"] = {}
        self.data["guild_configs"][guild_id] = config
        self._save_to_file()

    # --- Point Management ---
    async def update_user_points(self, guild_id, group_key, user_id, points):
        user_id = str(user_id)
        guild_id = str(guild_id)
        collection = self.data.get("user_points", [])
        
        found = False
        for doc in collection:
            if doc.get("guild_id") == guild_id and \
               doc.get("group_key") == group_key and \
               doc.get("user_id") == user_id:
                doc["points"] = int(doc.get("points", 0)) + int(points)
                found = True
                break
        
        if not found:
            new_doc = {
                "guild_id": guild_id,
                "group_key": group_key,
                "user_id": user_id,
                "points": int(points)
            }
            collection.append(new_doc)
            
        self.data["user_points"] = collection
        self._save_to_file()

    async def get_group_points(self, guild_id, group_key):
        guild_id = str(guild_id)
        collection = self.data.get("user_points", [])
        results = {}
        for doc in collection:
            if doc.get("guild_id") == guild_id and doc.get("group_key") == group_key:
                results[doc["user_id"]] = doc.get("points", 0)
        return results

    async def get_user_points(self, guild_id, user_id):
        guild_id = str(guild_id)
        user_id = str(user_id)
        collection = self.data.get("user_points", [])
        results = {}
        for doc in collection:
            if doc.get("guild_id") == guild_id and doc.get("user_id") == user_id:
                results[doc["group_key"]] = int(doc.get("points", 0))
        return results

    async def clear_points_by_group(self, guild_id, group_key):
        guild_id = str(guild_id)
        collection = self.data.get("user_points", [])
        initial_count = len(collection)
        
        new_collection = [
            doc for doc in collection 
            if not (doc.get("guild_id") == guild_id and doc.get("group_key") == group_key)
        ]
        
        self.data["user_points"] = new_collection
        self._save_to_file()
        return initial_count - len(new_collection)

# --- LEAD COG ---
class Lead(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseHandler()
        self.description = "leaderboards and point tracking"
        
        # Default Point Values (Global defaults, can be made per-server later if needed)
        self.POINT_VALUES = {
            'message': 1,
            'attachment': 2,
            'voice_interval': 1,
            'reaction_add': 1,
            'reaction_receive': 2
        }

        # Caches
        self.guild_configs = {} # Cache for guild settings
        self.voice_tracker = {} 
        self.point_cache = {}          
        self.leaderboard_cache = {}    

        # Start tasks
        self.voice_time_checker.start()
        self.point_saver.start()

    def cog_unload(self):
        self.voice_time_checker.cancel()
        self.point_saver.cancel()

    # --- HELPERS ---
    async def get_config(self, guild_id):
        """Helper to get guild config from cache or DB."""
        if str(guild_id) not in self.guild_configs:
            self.guild_configs[str(guild_id)] = await self.db.get_guild_config(guild_id)
        return self.guild_configs[str(guild_id)]

    def get_tracked_groups(self, channel, config):
        """Returns a list of group keys that track this channel/category."""
        tracked_groups = []
        if not config or "groups" not in config:
            return []

        for group_key, group_data in config["groups"].items():
            tracked_ids = group_data.get("tracked_ids", [])
            # Check channel ID
            if channel.id in tracked_ids:
                tracked_groups.append(group_key)
                continue
            # Check Category ID
            if hasattr(channel, "category") and channel.category and channel.category.id in tracked_ids:
                tracked_groups.append(group_key)
        
        return tracked_groups

    def add_points_to_cache(self, user_id, guild_id, group_key, points):
        user_id = str(user_id)
        guild_id = str(guild_id)
        
        if guild_id not in self.point_cache:
            self.point_cache[guild_id] = {}
        if group_key not in self.point_cache[guild_id]:
            self.point_cache[guild_id][group_key] = {}
            
        current = self.point_cache[guild_id][group_key].get(user_id, 0)
        self.point_cache[guild_id][group_key][user_id] = current + int(points) 

    async def create_leaderboard_embed(self, guild, group_key, group_data):
        guild_id = str(guild.id)
        group_name = group_data.get('name', f"Group {group_key}")
        
        # Check cache first
        cache_entry = self.leaderboard_cache.get(guild_id, {}).get(group_key)
        
        # If no cache, fetch from DB
        if not cache_entry:
            points_data = await self.db.get_group_points(guild_id, group_key)
            sorted_users = sorted(points_data.items(), key=lambda x: x[1], reverse=True)
            top_users = sorted_users[:20]
        else:
            top_users = cache_entry['top_users']

        embed = discord.Embed(title=f"🏆 {group_name} leaderboard", color=discord.Color.gold())
        
        desc = ""
        if not top_users:
            desc = "No points recorded yet."
        else:
            for rank, (uid, points) in enumerate(top_users, 1):
                user = guild.get_member(int(uid))
                name = user.display_name if user else "Unknown User"
                emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"**#{rank}**")
                desc += f"{emoji} **{name}**: {points} pts\n"
        
        embed.description = desc
        embed.set_footer(text=f"Updates every 5 minutes • Group {group_key}")
        return embed

    # --- LISTENERS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
            
        config = await self.get_config(message.guild.id)
        tracked_groups = self.get_tracked_groups(message.channel, config)
        
        if tracked_groups:
            points = self.POINT_VALUES['message']
            extras = (len(message.attachments) + len(message.embeds)) * self.POINT_VALUES['attachment']
            total = points + extras
            
            for group_key in tracked_groups:
                self.add_points_to_cache(message.author.id, message.guild.id, group_key, total)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot or not reaction.message.guild:
            return

        config = await self.get_config(reaction.message.guild.id)
        tracked_groups = self.get_tracked_groups(reaction.message.channel, config)
        
        if tracked_groups:
            # Reactor points
            for group_key in tracked_groups:
                self.add_points_to_cache(user.id, reaction.message.guild.id, group_key, self.POINT_VALUES['reaction_add'])
            
            # Author points
            author = reaction.message.author
            if not author.bot and author.id != user.id:
                for group_key in tracked_groups:
                    self.add_points_to_cache(author.id, reaction.message.guild.id, group_key, self.POINT_VALUES['reaction_receive'])

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        
        user_id = str(member.id)
        
        # Handle Leaving/Moving
        if user_id in self.voice_tracker:
            # Check if they left the tracked channel or VC entirely
            if not after.channel or (before.channel and before.channel.id != after.channel.id):
                # Calculate final time chunk if needed? 
                # Actually, the task handles the accumulation. We just remove them from tracking if they leave tracked areas.
                # But to be safe, we just remove them. The task adds points every 30s.
                del self.voice_tracker[user_id]

        # Handle Joining
        if after.channel:
            # We don't know the guild config here efficiently without fetching.
            # We will store the guild_id in tracker and check validity in the loop.
            self.voice_tracker[user_id] = {
                'time': time.time(),
                'guild_id': member.guild.id,
                'channel_id': after.channel.id
            }

    # --- TASKS ---

    @tasks.loop(seconds=30.0)
    async def voice_time_checker(self):
        # Iterate over a snapshot of items to safely modify
        for user_id, data in list(self.voice_tracker.items()):
            guild_id = data['guild_id']
            channel_id = data['channel_id']
            
            # Re-fetch guild/member objects
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            
            member = guild.get_member(int(user_id))
            if not member or not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
                # User left or moved, remove from tracker
                if user_id in self.voice_tracker: del self.voice_tracker[user_id]
                continue

            # Check if valid session (2+ people, non-bot)
            non_bots = [m for m in member.voice.channel.members if not m.bot]
            if len(non_bots) < 2:
                continue

            # Check time interval
            if time.time() - data['time'] >= 30.0:
                # Find which groups track this VC
                config = await self.get_config(guild_id)
                # Need a dummy channel object or fetch it
                channel = member.voice.channel
                tracked_groups = self.get_tracked_groups(channel, config)
                
                if tracked_groups:
                    pts = self.POINT_VALUES['voice_interval']
                    for group_key in tracked_groups:
                        self.add_points_to_cache(user_id, guild_id, group_key, pts)
                
                # Reset time for next interval
                self.voice_tracker[user_id]['time'] = time.time()

    @tasks.loop(seconds=300.0)
    async def point_saver(self):
        # 1. Save Points
        if self.point_cache:
            for guild_id, groups in self.point_cache.items():
                for group_key, users in groups.items():
                    for user_id, points in users.items():
                        await self.db.update_user_points(guild_id, group_key, user_id, points)
            self.point_cache = {}

        # 2. Update leaderboards & Perm Messages
        # Iterate over all guilds in cache
        for guild_id in list(self.guild_configs.keys()):
            config = self.guild_configs[guild_id]
            guild = self.bot.get_guild(int(guild_id))
            if not guild: continue

            if guild_id not in self.leaderboard_cache:
                self.leaderboard_cache[guild_id] = {}

            for group_key, group_data in config.get("groups", {}).items():
                # Refresh Data
                points = await self.db.get_group_points(guild_id, group_key)
                sorted_users = sorted(points.items(), key=lambda x: x[1], reverse=True)
                self.leaderboard_cache[guild_id][group_key] = {
                    'updated': time.time(),
                    'top_users': sorted_users[:20]
                }

                # Update Permanent Message if exists
                lb_info = group_data.get("last_lb_msg")
                if lb_info:
                    try:
                        chan = guild.get_channel(lb_info['channel_id'])
                        if chan:
                            msg = await chan.fetch_message(lb_info['message_id'])
                            embed = await self.create_leaderboard_embed(guild, group_key, group_data)
                            await msg.edit(embed=embed)
                    except (discord.NotFound, discord.Forbidden):
                        # Reset if deleted
                        group_data["last_lb_msg"] = None
                        await self.db.save_guild_config(guild_id, config)

    # --- COMMANDS ---
    
    # 1. /leaderboard GROUP command
    lead_group = app_commands.Group(name="leaderboard", description="Manage leaderboard groups")

    @lead_group.command(name="add", description="Create a new leaderboard group")
    @app_commands.describe(name="Name of the new group")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_add(self, interaction: discord.Interaction, name: str):
        config = await self.get_config(interaction.guild_id)
        
        # Find next available ID
        existing_ids = [int(k) for k in config["groups"].keys()]
        next_id = str(max(existing_ids) + 1 if existing_ids else 1)
        
        config["groups"][next_id] = {
            "name": name,
            "tracked_ids": [],
            "last_lb_msg": None
        }
        await self.db.save_guild_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Created group **{name}** (ID: {next_id})", ephemeral=True)

    @lead_group.command(name="edit", description="Edit a group's name or rename it")
    @app_commands.describe(group_num="The Group ID to edit", name="New name (optional)")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_edit(self, interaction: discord.Interaction, group_num: int, name: str):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)
        
        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return
            
        old_name = config["groups"][group_key]["name"]
        config["groups"][group_key]["name"] = name
        await self.db.save_guild_config(interaction.guild_id, config)
        
        await interaction.response.send_message(f"✅ Renamed Group {group_num} from **{old_name}** to **{name}**.", ephemeral=True)

    @lead_group.command(name="track", description="Track a channel or category for a group")
    @app_commands.describe(group_num="Group ID", target="Channel or Category to track")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_track(self, interaction: discord.Interaction, group_num: int, target: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel]):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return
        
        if target.id in config["groups"][group_key]["tracked_ids"]:
            await interaction.response.send_message(f"⚠️ **{target.name}** is already tracked by this group.", ephemeral=True)
            return

        config["groups"][group_key]["tracked_ids"].append(target.id)
        await self.db.save_guild_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Group {group_num} is now tracking **{target.name}**.", ephemeral=True)

    @lead_group.command(name="untrack", description="Stop tracking a channel or category")
    @app_commands.describe(group_num="Group ID", target="Channel or Category to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_untrack(self, interaction: discord.Interaction, group_num: int, target: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel]):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return
        
        if target.id not in config["groups"][group_key]["tracked_ids"]:
            await interaction.response.send_message(f"⚠️ **{target.name}** was not being tracked.", ephemeral=True)
            return

        config["groups"][group_key]["tracked_ids"].remove(target.id)
        await self.db.save_guild_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Stopped tracking **{target.name}** for Group {group_num}.", ephemeral=True)

    @lead_group.command(name="remove", description="Delete a group entirely")
    @app_commands.describe(group_num="Group ID to delete")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_remove(self, interaction: discord.Interaction, group_num: int):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return

        name = config["groups"][group_key]["name"]
        del config["groups"][group_key]
        await self.db.save_guild_config(interaction.guild_id, config)
        await interaction.response.send_message(f"🗑️ Deleted group **{name}** (ID: {group_num}).", ephemeral=True)

    @lead_group.command(name="clear", description="Clear all points for a group")
    @app_commands.describe(group_num="Group ID to clear points from")
    @app_commands.checks.has_permissions(administrator=True)
    async def lead_clear(self, interaction: discord.Interaction, group_num: int):
        group_key = str(group_num)
        config = await self.get_config(interaction.guild_id)
        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return
            
        count = await self.db.clear_points_by_group(interaction.guild_id, group_key)
        
        # Clear cache too
        gid = str(interaction.guild_id)
        if gid in self.point_cache and group_key in self.point_cache[gid]:
            del self.point_cache[gid][group_key]
            
        await interaction.response.send_message(f"Values reset! Cleared points for {count} users in Group {group_num}.", ephemeral=True)

    # 2. /leaderboard
    @app_commands.command(name="leaderboard", description="Show the leaderboard for a group")
    @app_commands.describe(group_num="The Group ID to show")
    async def show_leaderboard(self, interaction: discord.Interaction, group_num: int):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            return

        # Send Initial Message
        embed = await self.create_leaderboard_embed(interaction.guild, group_key, config["groups"][group_key])
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()

        # Update Permanent Link
        config["groups"][group_key]["last_lb_msg"] = {
            "channel_id": interaction.channel_id,
            "message_id": msg.id
        }
        await self.db.save_guild_config(interaction.guild_id, config)

    # 3. /award
    @app_commands.command(name="award", description="Award points to a user in the current channel's group(s)")
    @app_commands.checks.has_permissions(administrator=True)
    async def award_points(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        config = await self.get_config(interaction.guild_id)
        tracked = self.get_tracked_groups(interaction.channel, config)
        
        if not tracked:
            await interaction.response.send_message("❌ This channel is not tracked by any leaderboard groups.", ephemeral=True)
            return
            
        for group_key in tracked:
            await self.db.update_user_points(interaction.guild_id, group_key, user.id, amount)
            
        group_names = [config["groups"][k]["name"] for k in tracked]
        await interaction.response.send_message(f"✅ Awarded **{amount}** points to {user.mention} in groups: {', '.join(group_names)}.")

    # 4. ?points (Prefix Command)
    @commands.command(name="points")
    async def check_points(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        config = await self.get_config(ctx.guild.id)
        
        # Determine relevant groups based on the channel command was used in
        tracked_keys = self.get_tracked_groups(ctx.channel, config)
        
        if not tracked_keys:
            await ctx.send(f"⚠️ This channel isn't tracked by any groups, so I can't show context-specific points here.")
            return

        embed = discord.Embed(title=f"🌟 Points for {target.display_name}", color=discord.Color.purple())
        found_any = False
        
        # Check cache + DB
        # Note: Logic here simplifies to just checking DB because cache is flushed often, 
        # but for perfect accuracy we should check cache too. 
        # For simplicity in this structure, we query DB which is "safe enough" for a user check command.
        
        for group_key in tracked_keys:
            data = await self.db.get_user_points(ctx.guild.id, target.id)
            pts = data.get(group_key, 0)
            
            # Check pending cache
            gid = str(ctx.guild.id)
            if gid in self.point_cache and group_key in self.point_cache[gid]:
                pts += self.point_cache[gid][group_key].get(str(target.id), 0)
                
            group_name = config["groups"][group_key]["name"]
            embed.add_field(name=group_name, value=f"{pts} pts", inline=False)
            found_any = True
            
        if not found_any:
            embed.description = "No points in these groups yet."
            
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Lead(bot))
