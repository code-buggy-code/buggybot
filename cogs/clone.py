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
# class Lead(commands.Cog)
# - __init__(bot)
# - cog_unload()
# - get_config(guild_id)
# - save_config(guild_id, config)
# - update_user_points(guild_id, group_key, user_id, points)
# - get_group_points(guild_id, group_key)
# - get_user_points(guild_id, user_id)
# - clear_points_by_group(guild_id, group_key)
# - get_tracked_groups(channel, config)
# - add_points_to_cache(user_id, guild_id, group_key, points)
# - create_leaderboard_embed(guild, group_key, group_data)
# - on_message(message)
# - on_reaction_add(reaction, user)
# - on_voice_state_update(member, before, after)
# - voice_time_checker()
# - point_saver()
# - lead (Group) [Slash - Admin]
#   - create(interaction, name)
#   - edit(interaction, group_num, name)
#   - delete(interaction, group_num)
#   - list(interaction)
#   - track(interaction, group_num, target)
#   - untrack(interaction, group_num, target)
#   - config(interaction, activity, amount)
#   - reset(interaction, group_num)
#   - award(interaction, member, amount)
#   - deduct(interaction, member, amount)
# - leaderboard(interaction, group_num) [Slash - Public]
# - points(interaction, user) [Slash - Public]
# setup(bot)

class Lead(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Default Point Values (Fallback)
        self.DEFAULT_POINT_VALUES = {
            'message': 1,
            'attachment': 2,
            'voice_minute': 1,
            'reaction_add': 1,
            'reaction_receive': 2
        }

        # Caches
        self.voice_tracker = {} 
        self.point_cache = {}          
        self.leaderboard_cache = {}    

        # Start tasks
        self.voice_time_checker.start()
        self.point_saver.start()

    def cog_unload(self):
        self.voice_time_checker.cancel()
        self.point_saver.cancel()

    # --- DB HELPERS (Centralized) ---

    async def get_config(self, guild_id):
        guild_id = str(guild_id)
        configs = self.bot.db.get_collection("leaderboard_configs")
        if isinstance(configs, list): configs = {}

        if guild_id not in configs:
             configs[guild_id] = {
                "groups": {
                    "1": {"name": "General", "tracked_ids": [], "last_lb_msg": None}
                },
                "point_values": self.DEFAULT_POINT_VALUES.copy()
             }
             self.bot.db.save_collection("leaderboard_configs", configs)
        
        return configs[guild_id]

    async def save_config(self, guild_id, config):
        guild_id = str(guild_id)
        configs = self.bot.db.get_collection("leaderboard_configs")
        if isinstance(configs, list): configs = {}
        
        configs[guild_id] = config
        self.bot.db.save_collection("leaderboard_configs", configs)

    async def update_user_points(self, guild_id, group_key, user_id, points):
        guild_id = str(guild_id)
        user_id = str(user_id)
        
        collection = self.bot.db.get_collection("leaderboard_points")
        
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
            
        self.bot.db.save_collection("leaderboard_points", collection)

    async def get_group_points(self, guild_id, group_key):
        guild_id = str(guild_id)
        collection = self.bot.db.get_collection("leaderboard_points")
        results = {}
        for doc in collection:
            if doc.get("guild_id") == guild_id and doc.get("group_key") == group_key:
                results[doc["user_id"]] = doc.get("points", 0)
        return results

    async def get_user_points(self, guild_id, user_id):
        guild_id = str(guild_id)
        user_id = str(user_id)
        collection = self.bot.db.get_collection("leaderboard_points")
        results = {}
        for doc in collection:
            if doc.get("guild_id") == guild_id and doc.get("user_id") == user_id:
                results[doc["group_key"]] = int(doc.get("points", 0))
        return results

    async def clear_points_by_group(self, guild_id, group_key):
        guild_id = str(guild_id)
        collection = self.bot.db.get_collection("leaderboard_points")
        initial_count = len(collection)
        
        new_collection = [
            doc for doc in collection 
            if not (doc.get("guild_id") == guild_id and doc.get("group_key") == group_key)
        ]
        
        self.bot.db.save_collection("leaderboard_points", new_collection)
        return initial_count - len(new_collection)

    # --- HELPERS ---

    def get_tracked_groups(self, channel, config):
        tracked_groups = []
        if not config or "groups" not in config:
            return []

        for group_key, group_data in config["groups"].items():
            tracked_ids = group_data.get("tracked_ids", [])
            if channel.id in tracked_ids:
                tracked_groups.append(group_key)
                continue
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
        
        cache_entry = self.leaderboard_cache.get(guild_id, {}).get(group_key)
        
        if not cache_entry:
            points_data = await self.get_group_points(guild_id, group_key)
            sorted_users = sorted(points_data.items(), key=lambda x: x[1], reverse=True)
            top_users = sorted_users[:20]
        else:
            top_users = cache_entry['top_users']

        embed = discord.Embed(title=f"🏆 {group_name} Leaderboard", color=discord.Color.gold())
        
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
            p_vals = config.get("point_values", self.DEFAULT_POINT_VALUES)
            points = p_vals.get('message', 1)
            extras = (len(message.attachments) + len(message.embeds)) * p_vals.get('attachment', 2)
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
            p_vals = config.get("point_values", self.DEFAULT_POINT_VALUES)
            
            for group_key in tracked_groups:
                self.add_points_to_cache(user.id, reaction.message.guild.id, group_key, p_vals.get('reaction_add', 1))
            
            author = reaction.message.author
            if not author.bot and author.id != user.id:
                for group_key in tracked_groups:
                    self.add_points_to_cache(author.id, reaction.message.guild.id, group_key, p_vals.get('reaction_receive', 2))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        
        user_id = str(member.id)
        
        if user_id in self.voice_tracker:
            if not after.channel or (before.channel and before.channel.id != after.channel.id):
                del self.voice_tracker[user_id]

        if after.channel:
            self.voice_tracker[user_id] = {
                'time': time.time(),
                'guild_id': member.guild.id,
                'channel_id': after.channel.id
            }

    # --- TASKS ---

    @tasks.loop(seconds=60.0)
    async def voice_time_checker(self):
        for user_id, data in list(self.voice_tracker.items()):
            guild_id = data['guild_id']
            channel_id = data['channel_id']
            
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            
            member = guild.get_member(int(user_id))
            if not member or not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
                if user_id in self.voice_tracker: del self.voice_tracker[user_id]
                continue

            non_bots = [m for m in member.voice.channel.members if not m.bot]
            if len(non_bots) < 2:
                continue

            if time.time() - data['time'] >= 60.0:
                config = await self.get_config(guild_id)
                p_vals = config.get("point_values", self.DEFAULT_POINT_VALUES)
                
                channel = member.voice.channel
                tracked_groups = self.get_tracked_groups(channel, config)
                
                if tracked_groups:
                    pts = p_vals.get('voice_minute', 1)
                    for group_key in tracked_groups:
                        self.add_points_to_cache(user_id, guild_id, group_key, pts)
                
                self.voice_tracker[user_id]['time'] = time.time()

    @tasks.loop(seconds=300.0)
    async def point_saver(self):
        if self.point_cache:
            for guild_id, groups in self.point_cache.items():
                for group_key, users in groups.items():
                    for user_id, points in users.items():
                        await self.update_user_points(guild_id, group_key, user_id, points)
            self.point_cache = {}

        configs = self.bot.db.get_collection("leaderboard_configs")
        if isinstance(configs, list): configs = {}

        for guild_id in list(configs.keys()):
            config = configs[guild_id]
            guild = self.bot.get_guild(int(guild_id))
            if not guild: continue

            if guild_id not in self.leaderboard_cache:
                self.leaderboard_cache[guild_id] = {}

            for group_key, group_data in config.get("groups", {}).items():
                points = await self.get_group_points(guild_id, group_key)
                sorted_users = sorted(points.items(), key=lambda x: x[1], reverse=True)
                self.leaderboard_cache[guild_id][group_key] = {
                    'updated': time.time(),
                    'top_users': sorted_users[:20]
                }

                lb_info = group_data.get("last_lb_msg")
                if lb_info:
                    try:
                        chan = guild.get_channel(lb_info['channel_id'])
                        if chan:
                            msg = await chan.fetch_message(lb_info['message_id'])
                            embed = await self.create_leaderboard_embed(guild, group_key, group_data)
                            await msg.edit(embed=embed)
                    except (discord.NotFound, discord.Forbidden):
                        group_data["last_lb_msg"] = None
                        await self.save_config(guild_id, config)

    # --- ADMIN SLASH COMMANDS ---

    lead = app_commands.Group(name="lead", description="Manage leaderboard settings", default_permissions=discord.Permissions(administrator=True))

    @lead.command(name="create", description="Create a new leaderboard group.")
    @app_commands.describe(name="Name of the new group")
    async def lead_create(self, interaction: discord.Interaction, name: str):
        config = await self.get_config(interaction.guild_id)
        
        existing_ids = [int(k) for k in config["groups"].keys()]
        next_id = str(max(existing_ids) + 1 if existing_ids else 1)
        
        config["groups"][next_id] = {
            "name": name,
            "tracked_ids": [],
            "last_lb_msg": None
        }
        await self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Created group **{name}** (ID: {next_id})", ephemeral=True)

    @lead.command(name="edit", description="Rename a leaderboard group.")
    @app_commands.describe(group_num="The Group ID", name="New Name")
    async def lead_edit(self, interaction: discord.Interaction, group_num: int, name: str):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)
        
        if group_key not in config["groups"]:
            return await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            
        old_name = config["groups"][group_key]["name"]
        config["groups"][group_key]["name"] = name
        await self.save_config(interaction.guild_id, config)
        
        await interaction.response.send_message(f"✅ Renamed Group {group_num} from **{old_name}** to **{name}**.", ephemeral=True)

    @lead.command(name="delete", description="Delete a leaderboard group.")
    @app_commands.describe(group_num="The Group ID to delete")
    async def lead_delete(self, interaction: discord.Interaction, group_num: int):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            return await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)

        name = config["groups"][group_key]["name"]
        del config["groups"][group_key]
        await self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"🗑️ Deleted group **{name}** (ID: {group_num}).", ephemeral=True)

    @lead.command(name="list", description="List all leaderboard groups.")
    async def lead_list(self, interaction: discord.Interaction):
        config = await self.get_config(interaction.guild_id)
        groups = config.get("groups", {})

        if not groups:
            return await interaction.response.send_message("📝 No leaderboard groups set up.", ephemeral=True)

        text = "**📊 Leaderboard Groups**\n"
        for group_key, data in groups.items():
            name = data.get("name", "Unnamed")
            tracked = data.get("tracked_ids", [])
            
            tracked_names = []
            for tid in tracked:
                obj = interaction.guild.get_channel(int(tid))
                if obj:
                    tracked_names.append(obj.mention)
                else:
                    tracked_names.append(f"ID:{tid} (Deleted)")
            
            track_str = ", ".join(tracked_names) if tracked_names else "None"
            text += f"**[{group_key}] {name}**\nTracking: {track_str}\n\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    @lead.command(name="track", description="Add a channel/category to a group.")
    @app_commands.describe(group_num="The Group ID", target="The channel or category to track")
    async def lead_track(self, interaction: discord.Interaction, group_num: int, target: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            return await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
        
        if target.id in config["groups"][group_key]["tracked_ids"]:
            return await interaction.response.send_message(f"⚠️ **{target.name}** is already tracked by this group.", ephemeral=True)

        config["groups"][group_key]["tracked_ids"].append(target.id)
        await self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Group {group_num} is now tracking **{target.name}**.", ephemeral=True)

    @lead.command(name="untrack", description="Stop tracking a channel/category.")
    @app_commands.describe(group_num="The Group ID", target="The channel or category to remove")
    async def lead_untrack(self, interaction: discord.Interaction, group_num: int, target: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]):
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            return await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
        
        if target.id not in config["groups"][group_key]["tracked_ids"]:
            return await interaction.response.send_message(f"⚠️ **{target.name}** was not being tracked.", ephemeral=True)

        config["groups"][group_key]["tracked_ids"].remove(target.id)
        await self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Stopped tracking **{target.name}** for Group {group_num}.", ephemeral=True)

    @lead.command(name="config", description="Configure point values.")
    @app_commands.describe(activity="The activity type", amount="Points amount")
    @app_commands.choices(activity=[
        app_commands.Choice(name="Message", value="message"),
        app_commands.Choice(name="Attachment", value="attachment"),
        app_commands.Choice(name="Give Reaction", value="reaction_add"),
        app_commands.Choice(name="Receive Reaction", value="reaction_receive"),
        app_commands.Choice(name="Voice (1 min)", value="voice_minute")
    ])
    async def lead_config(self, interaction: discord.Interaction, activity: app_commands.Choice[str], amount: int):
        config = await self.get_config(interaction.guild_id)
        p_vals = config.get("point_values", self.DEFAULT_POINT_VALUES)
        
        p_vals[activity.value] = amount
        config['point_values'] = p_vals
        await self.save_config(interaction.guild_id, config)
        await interaction.response.send_message(f"✅ Set **{activity.name}** to **{amount}** points.", ephemeral=True)

    @lead.command(name="reset", description="Clear all points for a group.")
    @app_commands.describe(group_num="The Group ID")
    async def lead_reset(self, interaction: discord.Interaction, group_num: int):
        group_key = str(group_num)
        config = await self.get_config(interaction.guild_id)
        if group_key not in config["groups"]:
            return await interaction.response.send_message(f"❌ Group ID {group_num} not found.", ephemeral=True)
            
        count = await self.clear_points_by_group(interaction.guild_id, group_key)
        
        gid = str(interaction.guild_id)
        if gid in self.point_cache and group_key in self.point_cache[gid]:
            del self.point_cache[gid][group_key]
            
        await interaction.response.send_message(f"Values reset! Cleared points for {count} users in Group {group_num}.", ephemeral=True)

    @lead.command(name="award", description="Manually give points to a user.")
    @app_commands.describe(member="The user", amount="Amount of points")
    async def lead_award(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        config = await self.get_config(interaction.guild_id)
        # Apply to all groups that track THIS channel. 
        # If run in non-tracked channel, warn user?
        # Standard behavior: Admin usually runs this in the context they want to award.
        
        tracked = self.get_tracked_groups(interaction.channel, config)
        
        if not tracked:
            return await interaction.response.send_message("⚠️ This channel is not tracked by any leaderboard groups. Please run this command in a tracked channel.", ephemeral=True)
            
        for group_key in tracked:
            await self.update_user_points(interaction.guild_id, group_key, member.id, amount)
            
        group_names = [config["groups"][k]["name"] for k in tracked]
        await interaction.response.send_message(f"✅ Awarded **{amount}** points to {member.mention} in groups: {', '.join(group_names)}.", ephemeral=True)

    @lead.command(name="deduct", description="Manually remove points from a user.")
    @app_commands.describe(member="The user", amount="Amount to remove")
    async def lead_deduct(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        config = await self.get_config(interaction.guild_id)
        tracked = self.get_tracked_groups(interaction.channel, config)
        
        if not tracked:
            return await interaction.response.send_message("⚠️ This channel is not tracked by any leaderboard groups.", ephemeral=True)
            
        neg_amount = -abs(amount)
        for group_key in tracked:
            await self.update_user_points(interaction.guild_id, group_key, member.id, neg_amount)
            
        group_names = [config["groups"][k]["name"] for k in tracked]
        await interaction.response.send_message(f"✅ Removed **{amount}** points from {member.mention} in groups: {', '.join(group_names)}.", ephemeral=True)

    # --- PUBLIC COMMANDS ---

    @app_commands.command(name="leaderboard", description="Show the leaderboard.", extras={'public': True})
    @app_commands.describe(group_num="The Group ID (Default: 1)")
    async def show_leaderboard(self, interaction: discord.Interaction, group_num: int = 1):
        """Show the leaderboard."""
        config = await self.get_config(interaction.guild_id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
             # Try to find default
            if "1" in config["groups"]: group_key = "1"
            else: return await interaction.response.send_message(f"❌ Leaderboard group not found.", ephemeral=True)

        embed = await self.create_leaderboard_embed(interaction.guild, group_key, config["groups"][group_key])
        
        # If admin runs it, update the "pinned" message tracking
        if interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=embed)
            msg = await interaction.original_response()
            config["groups"][group_key]["last_lb_msg"] = {
                "channel_id": interaction.channel_id,
                "message_id": msg.id
            }
            await self.save_config(interaction.guild_id, config)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="points", description="Check points for yourself or another user.", extras={'public': True})
    @app_commands.describe(user="The user to check (leave empty for yourself)")
    async def points(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Check points for yourself or another user."""
        target = user or interaction.user
        config = await self.get_config(interaction.guild_id)
        
        tracked_keys = self.get_tracked_groups(interaction.channel, config)
        
        if not tracked_keys:
            # Fallback: Show ALL points if channel not tracked? 
            # Or just warn? Old behavior warned.
            # Let's show all for better UX if not in tracked channel.
            tracked_keys = list(config["groups"].keys())

        if not tracked_keys:
             return await interaction.response.send_message("⚠️ No leaderboards set up yet.", ephemeral=True)

        embed = discord.Embed(title=f"🌟 Points for {target.display_name}", color=discord.Color.purple())
        found_any = False
        
        for group_key in tracked_keys:
            data = await self.get_user_points(interaction.guild_id, target.id)
            pts = data.get(group_key, 0)
            
            # Add cached points
            gid = str(interaction.guild_id)
            if gid in self.point_cache and group_key in self.point_cache[gid]:
                pts += self.point_cache[gid][group_key].get(str(target.id), 0)
                
            group_name = config["groups"][group_key]["name"]
            embed.add_field(name=group_name, value=f"{pts} pts", inline=False)
            found_any = True
            
        if not found_any:
            embed.description = "No points found."
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Lead(bot))
