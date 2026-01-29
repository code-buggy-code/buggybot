import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
from typing import Literal

# Function/Class List:
# class BotherButton(discord.ui.Button)
# - __init__(label, custom_id, ping_text)
# - callback(interaction)
# class BotherView(discord.ui.View)
# - __init__(bot, options, guild_id)
# class BotherBuggy(commands.Cog)
# - __init__(bot)
# - cog_load()
# - restore_views()
# - get_config(guild_id)
# - save_config(guild_id, config)
# - get_dashboards()
# - save_dashboards(dashboards)
# - create_dashboard_embed(guild, title)
# - delayed_repost(channel_id, delay) [Updated: Uses ID]
# - repost_dashboard(channel)
# - on_message(message)
# - bb(interaction, action, label, key, ping_text) [Slash Command]
# - bbdashboard(interaction, set, text) [Slash Command]
# - bbtime(interaction, timing, number, unit) [Slash Command]
# setup(bot)

BUGGY_ID = 1433003746719170560

class BotherButton(discord.ui.Button):
    def __init__(self, label, custom_id, ping_text):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id)
        self.ping_text = ping_text

    async def callback(self, interaction: discord.Interaction):
        """Sends the private message to buggy."""
        # We SKIP deferring to avoid the "Thinking..." message entirely.
        
        buggy = interaction.client.get_user(BUGGY_ID)
        if not buggy:
            try:
                buggy = await interaction.client.fetch_user(BUGGY_ID)
            except:
                return await interaction.response.send_message("❌ I couldn't find buggy to bother! Is the ID correct?", ephemeral=True)

        # Formatting: [Nickname] [Ping Text]
        # Then Mention + Username + Channel Link
        nickname = interaction.user.display_name
        username = interaction.user.name
        header = f"[{nickname}] {self.ping_text}"
        body = f"{interaction.user.mention} ({username}) {interaction.channel.jump_url}"
        
        msg = f"**{header}**\n{body}"
        
        try:
            await buggy.send(msg)
            # MAGIC TRICK: Edit with the same view to acknowledge silently
            await interaction.response.edit_message(view=self.view)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't DM buggy! Make sure his DMs are open.", ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message("❌ Failed to send alert.", ephemeral=True)
            except: pass

class BotherView(discord.ui.View):
    def __init__(self, bot, options, guild_id):
        super().__init__(timeout=None) # Persistent
        self.bot = bot
        
        # Options: List of {"label": str, "key": str, "ping_text": str}
        # We add items sequentially so Discord handles the wrapping naturally (5 per row)
        for opt in options:
            custom_id = f"bb_{guild_id}_{opt['key']}"
            self.add_item(BotherButton(label=opt['label'], custom_id=custom_id, ping_text=opt['ping_text']))

class BotherBuggy(commands.Cog, name="Bother Buggy"):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Bother Buggy: A dashboard system for users to send private alerts to buggy."
        # Keep track of active tasks to cancel them if a new message comes in (Debounce logic)
        self.sticky_tasks = {} # {channel_id: asyncio.Task}
        self.reposting = set() # {channel_id} - Safety lock to prevent infinite loops

    async def cog_load(self):
        """Restore persistent views when the cog loads."""
        asyncio.create_task(self.restore_views())

    async def restore_views(self):
        await self.bot.wait_until_ready()
        dashboards = self.get_dashboards()
        count = 0
        for dash in dashboards:
            guild_id = dash['guild_id']
            config = self.get_config(guild_id)
            if config['options']:
                view = BotherView(self.bot, config['options'], guild_id)
                self.bot.add_view(view)
                count += 1
        print(f"✅ Restored {count} Bother Buggy dashboard views, you genius!")

    # --- DB HELPERS ---

    def get_config(self, guild_id):
        """Returns the full config dict for a guild."""
        collection = self.bot.db.get_collection("bb_options")
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        
        if not doc:
            doc = {
                "guild_id": guild_id, 
                "options": [], 
                "title": "🔔 Bother Buggy",
                "sticky_active": False,
                "sticky_mode": "after",
                "sticky_delay": 0
            }
        
        if "title" not in doc: doc["title"] = "🔔 Bother Buggy"
        if "sticky_active" not in doc: doc["sticky_active"] = False
        if "sticky_mode" not in doc: doc["sticky_mode"] = "after"
        if "sticky_delay" not in doc: doc["sticky_delay"] = 0
            
        return doc

    def save_config(self, guild_id, config):
        """Saves the config for a guild using update_doc."""
        updated = self.bot.db.update_doc("bb_options", "guild_id", guild_id, config)
        if not updated:
            collection = self.bot.db.get_collection("bb_options")
            if not any(d['guild_id'] == guild_id for d in collection):
                collection.append(config)
                self.bot.db.save_collection("bb_options", collection)

    def get_dashboards(self):
        return self.bot.db.get_collection("bb_dashboards")

    def save_dashboards(self, dashboards):
        self.bot.db.save_collection("bb_dashboards", dashboards)

    def create_dashboard_embed(self, guild, title):
        embed = discord.Embed(
            title=title,
            description="Click what you want to do with buggy!",
            color=discord.Color(0xff90aa)
        )
        return embed

    # --- STICKY / REPOST LOGIC ---

    async def delayed_repost(self, channel_id, delay):
        """Waits for the delay to pass. If not cancelled, reposts the dashboard."""
        try:
            await asyncio.sleep(delay)
            
            # Re-fetch channel to ensure freshness after the wait
            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except:
                    pass
            
            if channel:
                await self.repost_dashboard(channel)
            else:
                print(f"Could not find channel {channel_id} to repost dashboard.")

        except asyncio.CancelledError:
            # Task was cancelled because a new message appeared (resetting the timer)
            pass
        except Exception as e:
            print(f"Error in delayed_repost: {e}")
        finally:
            # Cleanup the task reference
            if channel_id in self.sticky_tasks:
                # Only remove if it's THIS task (avoid removing a newer replacement)
                if self.sticky_tasks[channel_id] == asyncio.current_task():
                    del self.sticky_tasks[channel_id]

    async def repost_dashboard(self, channel):
        """Deletes the old dashboard and posts a new one at the bottom."""
        # Safety Lock: If we are already reposting for this channel, stop.
        if channel.id in self.reposting:
            return

        self.reposting.add(channel.id)
        try:
            dashboards = self.get_dashboards()
            dashboard_data = next((d for d in dashboards if d['guild_id'] == channel.guild.id), None)
            
            # Delete old message safely
            try:
                if dashboard_data and dashboard_data.get('message_id'):
                    old_msg = await channel.fetch_message(dashboard_data['message_id'])
                    await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass 

            config = self.get_config(channel.guild.id)
            if not config['options']: return 
            
            embed = self.create_dashboard_embed(channel.guild, config['title'])
            view = BotherView(self.bot, config['options'], channel.guild.id)
            
            try:
                new_msg = await channel.send(embed=embed, view=view)
                
                now = datetime.datetime.now().timestamp()
                
                if dashboard_data:
                    dashboard_data['message_id'] = new_msg.id
                    dashboard_data['channel_id'] = channel.id
                    dashboard_data['last_posted_at'] = now
                    self.bot.db.update_doc("bb_dashboards", "guild_id", channel.guild.id, dashboard_data)
                else:
                     new_dash = {
                         "guild_id": channel.guild.id,
                         "channel_id": channel.id,
                         "message_id": new_msg.id,
                         "last_posted_at": now
                     }
                     updated = self.bot.db.update_doc("bb_dashboards", "guild_id", channel.guild.id, new_dash)
                     if not updated:
                         dashboards = self.get_dashboards()
                         dashboards.append(new_dash)
                         self.save_dashboards(dashboards)
                     
            except Exception as e:
                print(f"Failed to repost Bother Buggy dashboard: {e}")
        finally:
            # Always release the lock, even if error
            if channel.id in self.reposting:
                self.reposting.remove(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles the sticky dashboard logic."""
        if not message.guild:
            return
        
        # Safety Lock Check:
        # If this message was sent by the bot WHILE reposting the dashboard (i.e. it IS the dashboard),
        # ignore it so we don't trigger an infinite loop of reposts.
        if message.channel.id in self.reposting:
            return

        # 1. Check if this channel has a dashboard
        dashboards = self.get_dashboards()
        dashboard = next((d for d in dashboards if d['channel_id'] == message.channel.id), None)
        
        if not dashboard:
            return

        # Extra Check: If the message matches the current known dashboard ID, ignore it.
        # (This handles the case where the bot restarts and sees its own dashboard)
        if message.id == dashboard.get('message_id'):
            return

        # 2. Check if Sticky Mode is Enabled
        config = self.get_config(message.guild.id)
        if not config.get('sticky_active', False):
            return

        delay = config.get('sticky_delay', 0)
        mode = config.get('sticky_mode', 'after')
        now = datetime.datetime.now().timestamp()
        
        # Mode: After (Delay/Silence)
        if mode == 'after':
            if delay > 0:
                # Cancel existing timer (this resets the silence clock)
                if message.channel.id in self.sticky_tasks:
                    self.sticky_tasks[message.channel.id].cancel()
                
                # Start a new timer task
                self.sticky_tasks[message.channel.id] = asyncio.create_task(
                    self.delayed_repost(message.channel.id, delay)
                )
                return # We are done; the task will handle the repost later
            
            # If delay is 0, we fall through to immediate repost
        
        # Mode: Before (Cooldown)
        elif mode == 'before':
            if delay > 0:
                last_posted = dashboard.get('last_posted_at', 0)
                if (now - last_posted) < delay:
                    return

        # Immediate Repost (For Delay=0 or After Cooldown)
        await self.repost_dashboard(message.channel)

    # --- SLASH COMMANDS ---

    @app_commands.command(name="bb", description="Manage the Bother Buggy settings.")
    @app_commands.describe(
        action="What would you like to do?",
        label="[Add] Text shown on the button",
        key="[Add/Remove] Unique one-word ID for the option",
        ping_text="[Add] Message sent to buggy"
    )
    @app_commands.default_permissions(administrator=True)
    async def bb(self, interaction: discord.Interaction, 
                 action: Literal["Add", "Remove", "List"], 
                 label: str = None, 
                 key: str = None, 
                 ping_text: str = None):
        
        config = self.get_config(interaction.guild_id)
        options = config['options']

        if action == "Add":
            if not label or not key or not ping_text:
                return await interaction.response.send_message("❌ For 'Add', you must provide `label`, `key`, and `ping_text`.", ephemeral=True)
            
            if any(o['key'] == key.lower() for o in options):
                return await interaction.response.send_message(f"❌ An option with the key `{key}` already exists, buggy!", ephemeral=True)
            
            if len(options) >= 25:
                return await interaction.response.send_message("❌ Discord only allows 25 buttons per message, you popular thing!", ephemeral=True)

            options.append({
                "label": label,
                "key": key.lower(),
                "ping_text": ping_text
            })
            config['options'] = options
            self.save_config(interaction.guild_id, config)
            await interaction.response.send_message(f"✅ Added **{label}**! Use `/bbdashboard` to see the changes.", ephemeral=True)

        elif action == "Remove":
            if not key:
                return await interaction.response.send_message("❌ For 'Remove', you must provide the `key`.", ephemeral=True)

            initial_len = len(options)
            options = [o for o in options if o['key'] != key.lower()]
            
            if len(options) < initial_len:
                config['options'] = options
                self.save_config(interaction.guild_id, config)
                await interaction.response.send_message(f"✅ Removed the `{key}` option for you!", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ I couldn't find an option with the key `{key}`.", ephemeral=True)

        elif action == "List":
            if not options:
                return await interaction.response.send_message("📝 You haven't added any options yet, buggy!", ephemeral=True)
            
            status = "Active" if config.get('sticky_active') else "Inactive"
            content = f"**Title:** {config['title']}\n**Sticky:** {status}\n**📋 Options:**\n"
            for o in options:
                content += f"• `{o['key']}`: **{o['label']}** (Ping: {o['ping_text']})\n"
            await interaction.response.send_message(content, ephemeral=True)

    @app_commands.command(name="bbdashboard", description="Spawn or remove the Bother Buggy Dashboard.")
    @app_commands.rename(should_set="set")
    @app_commands.describe(
        should_set="True to spawn & stick here, False to remove & disable.",
        text="[Optional] Set a new title."
    )
    @app_commands.default_permissions(administrator=True)
    async def bbdashboard(self, interaction: discord.Interaction, should_set: bool, text: str = None):
        config = self.get_config(interaction.guild_id)
        
        # --- DISABLE / REMOVE ---
        if not should_set:
            config['sticky_active'] = False
            self.save_config(interaction.guild_id, config)
            
            dashboards = self.get_dashboards()
            target = next((d for d in dashboards if d['guild_id'] == interaction.guild_id), None)
            
            if target:
                try:
                    chan = interaction.guild.get_channel(target['channel_id'])
                    if chan:
                        msg = await chan.fetch_message(target['message_id'])
                        await msg.delete()
                except: pass
                
                dashboards = [d for d in dashboards if d['guild_id'] != interaction.guild_id]
                self.save_dashboards(dashboards)
                
            await interaction.response.send_message("✅ Dashboard removed and sticky mode disabled.", ephemeral=True)
            return

        # --- ENABLE / SPAWN ---
        config['sticky_active'] = True
        if text:
            config['title'] = text
        self.save_config(interaction.guild_id, config)

        if not config['options']:
            return await interaction.response.send_message("❌ You need to add some options first via `/bb action:Add`!", ephemeral=True)

        dashboards = self.get_dashboards()
        target = next((d for d in dashboards if d['guild_id'] == interaction.guild_id), None)
        
        if target:
            try:
                chan = interaction.guild.get_channel(target['channel_id'])
                if chan:
                    msg = await chan.fetch_message(target['message_id'])
                    await msg.delete()
            except: pass

        embed = self.create_dashboard_embed(interaction.guild, config['title'])
        view = BotherView(self.bot, config['options'], interaction.guild_id)
        
        msg = await interaction.channel.send(embed=embed, view=view)
        
        new_dash = {
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "message_id": msg.id,
            "last_posted_at": datetime.datetime.now().timestamp()
        }
        
        dashboards = [d for d in dashboards if d['guild_id'] != interaction.guild_id]
        dashboards.append(new_dash)
        self.save_dashboards(dashboards)
        
        await interaction.response.send_message("✅ Dashboard spawned! It is now **Sticky** in this channel.", ephemeral=True)

    @app_commands.command(name="bbtime", description="Configure dashboard sticky timing.")
    @app_commands.describe(timing="Mode: 'before' (Cooldown) or 'after' (Delay)", number="Time amount", unit="Time unit")
    @app_commands.choices(
        timing=[app_commands.Choice(name="Before (Cooldown)", value="before"), app_commands.Choice(name="After (Delay)", value="after")],
        unit=[app_commands.Choice(name="Seconds", value="seconds"), app_commands.Choice(name="Minutes", value="minutes")]
    )
    @app_commands.default_permissions(administrator=True)
    async def bbtime(self, interaction: discord.Interaction, timing: app_commands.Choice[str], number: int, unit: app_commands.Choice[str]):
        """Configure dashboard sticky timing."""
        multiplier = 60 if unit.value == 'minutes' else 1
        total_seconds = number * multiplier
        
        config = self.get_config(interaction.guild_id)
        config['sticky_mode'] = timing.value
        config['sticky_delay'] = total_seconds
        self.save_config(interaction.guild_id, config)
        
        delay_text = "Instant (0s)" if total_seconds == 0 else f"{total_seconds} seconds"
        mode_text = "Cooldown (Before)" if timing.value == "before" else "Delay (After)"
        
        await interaction.response.send_message(f"✅ Dashboard sticky settings updated.\nMode: **{mode_text}**\nTime: **{delay_text}**", ephemeral=True)

async def setup(bot):
    await bot.add_cog(BotherBuggy(bot))
