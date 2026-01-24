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
# - lead (Prefix Group)
#   - lead_add(ctx, name)
#   - lead_edit(ctx, group_num, name)
#   - lead_track(ctx, group_num, target)
#   - lead_untrack(ctx, group_num, target)
#   - lead_points(ctx, activity, amount)
#   - lead_delete(ctx, group_num)
#   - lead_clear(ctx, group_num)
#   - lead_list(ctx)
# - award(ctx, member, amount) [Prefix]
# - remove(ctx, member, amount) [Prefix]
# - show_leaderboard(ctx, group_num) [Prefix - CHANGED]
# - points(interaction, user) [Slash]
# - check_points(ctx, member) [Prefix]
# setup(bot)

# --- LEAD COG ---
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

    # --- PREFIX COMMANDS (ADMIN) ---

    @commands.group(name="lead", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def lead(self, ctx):
        """Manage leaderboard groups."""
        await ctx.send("Available commands: `add`, `edit`, `track`, `untrack`, `points`, `delete`, `clear`, `list`")

    @lead.command(name="add")
    @commands.has_permissions(administrator=True)
    async def lead_add(self, ctx, *, name: str):
        config = await self.get_config(ctx.guild.id)
        
        existing_ids = [int(k) for k in config["groups"].keys()]
        next_id = str(max(existing_ids) + 1 if existing_ids else 1)
        
        config["groups"][next_id] = {
            "name": name,
            "tracked_ids": [],
            "last_lb_msg": None
        }
        await self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Created group **{name}** (ID: {next_id})")

    @lead.command(name="edit")
    @commands.has_permissions(administrator=True)
    async def lead_edit(self, ctx, group_num: int, *, name: str):
        config = await self.get_config(ctx.guild.id)
        group_key = str(group_num)
        
        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return
            
        old_name = config["groups"][group_key]["name"]
        config["groups"][group_key]["name"] = name
        await self.save_config(ctx.guild.id, config)
        
        await ctx.send(f"✅ Renamed Group {group_num} from **{old_name}** to **{name}**.")

    @lead.command(name="track")
    @commands.has_permissions(administrator=True)
    async def lead_track(self, ctx, group_num: int, target: discord.abc.GuildChannel):
        config = await self.get_config(ctx.guild.id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return
        
        if target.id in config["groups"][group_key]["tracked_ids"]:
            await ctx.send(f"⚠️ **{target.name}** is already tracked by this group.")
            return

        config["groups"][group_key]["tracked_ids"].append(target.id)
        await self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Group {group_num} is now tracking **{target.name}**.")

    @lead.command(name="untrack")
    @commands.has_permissions(administrator=True)
    async def lead_untrack(self, ctx, group_num: int, target: discord.abc.GuildChannel):
        config = await self.get_config(ctx.guild.id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return
        
        if target.id not in config["groups"][group_key]["tracked_ids"]:
            await ctx.send(f"⚠️ **{target.name}** was not being tracked.")
            return

        config["groups"][group_key]["tracked_ids"].remove(target.id)
        await self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Stopped tracking **{target.name}** for Group {group_num}.")

    @lead.command(name="points")
    @commands.has_permissions(administrator=True)
    async def lead_points(self, ctx, activity: str = None, amount: int = None):
        config = await self.get_config(ctx.guild.id)
        p_vals = config.get("point_values", self.DEFAULT_POINT_VALUES)
        
        # Valid Keys Mapping
        key_map = {
            "msg": "message", "message": "message",
            "file": "attachment", "attach": "attachment", "attachment": "attachment",
            "react": "reaction_add", "give": "reaction_add", "reaction_add": "reaction_add",
            "get": "reaction_receive", "receive": "reaction_receive", "reaction_receive": "reaction_receive",
            "vc": "voice_minute", "voice": "voice_minute", "voice_minute": "voice_minute"
        }

        if activity is None:
            # Show list
            embed = discord.Embed(title="⚙️ Leaderboard Point Values", color=discord.Color.blue())
            embed.add_field(name="✉️ Message (message)", value=f"{p_vals.get('message', 1)} pts", inline=True)
            embed.add_field(name="📎 Attachment (attachment)", value=f"{p_vals.get('attachment', 2)} pts", inline=True)
            embed.add_field(name="😀 Give React (give)", value=f"{p_vals.get('reaction_add', 1)} pts", inline=True)
            embed.add_field(name="⭐ Get React (receive)", value=f"{p_vals.get('reaction_receive', 2)} pts", inline=True)
            embed.add_field(name="🎙️ VC 1min (voice)", value=f"{p_vals.get('voice_minute', 1)} pts", inline=True)
            embed.set_footer(text="Usage: ?lead points <type> <amount>")
            await ctx.send(embed=embed)
            return

        # Setting a value
        activity_key = key_map.get(activity.lower())
        if not activity_key:
            await ctx.send("❌ Invalid activity type. Use: message, attachment, give, receive, voice.")
            return
        
        if amount is None:
            await ctx.send(f"❌ Please specify an amount for **{activity}**.")
            return

        p_vals[activity_key] = amount
        config['point_values'] = p_vals
        await self.save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Set **{activity_key}** to **{amount}** points.")

    @lead.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def lead_delete(self, ctx, group_num: int):
        config = await self.get_config(ctx.guild.id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return

        name = config["groups"][group_key]["name"]
        del config["groups"][group_key]
        await self.save_config(ctx.guild.id, config)
        await ctx.send(f"🗑️ Deleted group **{name}** (ID: {group_num}).")

    @lead.command(name="clear")
    @commands.has_permissions(administrator=True)
    async def lead_clear(self, ctx, group_num: int):
        group_key = str(group_num)
        config = await self.get_config(ctx.guild.id)
        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return
            
        count = await self.clear_points_by_group(ctx.guild.id, group_key)
        
        gid = str(ctx.guild.id)
        if gid in self.point_cache and group_key in self.point_cache[gid]:
            del self.point_cache[gid][group_key]
            
        await ctx.send(f"Values reset! Cleared points for {count} users in Group {group_num}.")

    @lead.command(name="list")
    @commands.has_permissions(administrator=True)
    async def lead_list(self, ctx):
        config = await self.get_config(ctx.guild.id)
        groups = config.get("groups", {})

        if not groups:
            await ctx.send("📝 No leaderboard groups set up.")
            return

        text = "**📊 Leaderboard Groups**\n"
        for group_key, data in groups.items():
            name = data.get("name", "Unnamed")
            tracked = data.get("tracked_ids", [])
            
            tracked_names = []
            for tid in tracked:
                obj = ctx.guild.get_channel(int(tid))
                if obj:
                    tracked_names.append(obj.mention)
                else:
                    tracked_names.append(f"ID:{tid} (Deleted)")
            
            track_str = ", ".join(tracked_names) if tracked_names else "None"
            text += f"**[{group_key}] {name}**\nTracking: {track_str}\n\n"
        
        await ctx.send(text)

    # --- PREFIX POINTS MANAGEMENT ---

    @commands.command(name="award")
    @commands.has_permissions(administrator=True)
    async def award(self, ctx, member: discord.Member, amount: int):
        config = await self.get_config(ctx.guild.id)
        tracked = self.get_tracked_groups(ctx.channel, config)
        
        if not tracked:
            await ctx.send("❌ This channel is not tracked by any leaderboard groups.")
            return
            
        for group_key in tracked:
            await self.update_user_points(ctx.guild.id, group_key, member.id, amount)
            
        group_names = [config["groups"][k]["name"] for k in tracked]
        await ctx.send(f"✅ Awarded **{amount}** points to {member.mention} in groups: {', '.join(group_names)}.")

    @commands.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def remove_points(self, ctx, member: discord.Member, amount: int):
        config = await self.get_config(ctx.guild.id)
        tracked = self.get_tracked_groups(ctx.channel, config)
        
        if not tracked:
            await ctx.send("❌ This channel is not tracked by any leaderboard groups.")
            return
            
        # Removing points is just adding negative points
        neg_amount = -abs(amount)
        
        for group_key in tracked:
            await self.update_user_points(ctx.guild.id, group_key, member.id, neg_amount)
            
        group_names = [config["groups"][k]["name"] for k in tracked]
        await ctx.send(f"✅ Removed **{amount}** points from {member.mention} in groups: {', '.join(group_names)}.")

    # --- PUBLIC COMMANDS (Admin Protected where requested) ---

    @commands.command(name="leaderboard")
    @commands.has_permissions(administrator=True)
    async def show_leaderboard(self, ctx, group_num: int):
        config = await self.get_config(ctx.guild.id)
        group_key = str(group_num)

        if group_key not in config["groups"]:
            await ctx.send(f"❌ Group ID {group_num} not found.")
            return

        embed = await self.create_leaderboard_embed(ctx.guild, group_key, config["groups"][group_key])
        msg = await ctx.send(embed=embed)

        config["groups"][group_key]["last_lb_msg"] = {
            "channel_id": ctx.channel.id,
            "message_id": msg.id
        }
        await self.save_config(ctx.guild.id, config)

    @app_commands.command(name="points", description="Check points for yourself or another user", extras={'public': True})
    @app_commands.describe(user="The user to check points for (leave empty for yourself)")
    async def points(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        config = await self.get_config(interaction.guild_id)
        
        tracked_keys = self.get_tracked_groups(interaction.channel, config)
        
        if not tracked_keys:
            return await interaction.response.send_message("⚠️ This channel isn't tracked by any groups, so I can't show context-specific points here.", ephemeral=True)

        embed = discord.Embed(title=f"🌟 Points for {target.display_name}", color=discord.Color.purple())
        found_any = False
        
        for group_key in tracked_keys:
            data = await self.get_user_points(interaction.guild_id, target.id)
            pts = data.get(group_key, 0)
            
            gid = str(interaction.guild_id)
            if gid in self.point_cache and group_key in self.point_cache[gid]:
                pts += self.point_cache[gid][group_key].get(str(target.id), 0)
                
            group_name = config["groups"][group_key]["name"]
            embed.add_field(name=group_name, value=f"{pts} pts", inline=False)
            found_any = True
            
        if not found_any:
            embed.description = "No points in these groups yet."
            
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(30)
        try:
            await interaction.delete_original_response()
        except:
            pass

    @commands.command(name="points")
    async def check_points(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        config = await self.get_config(ctx.guild.id)
        
        tracked_keys = self.get_tracked_groups(ctx.channel, config)
        
        if not tracked_keys:
            await ctx.send(f"⚠️ This channel isn't tracked by any groups, so I can't show context-specific points here.")
            return

        embed = discord.Embed(title=f"🌟 Points for {target.display_name}", color=discord.Color.purple())
        found_any = False
        
        for group_key in tracked_keys:
            data = await self.get_user_points(ctx.guild.id, target.id)
            pts = data.get(group_key, 0)
            
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
