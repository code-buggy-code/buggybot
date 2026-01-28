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
        # We rely on the DM being sent quickly (under 3 seconds).
        
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
            # MAGIC TRICK: We "edit" the message with the exact same view.
            # This counts as a valid response to stop the interaction loading state
            # WITHOUT showing any text or "Thinking..." message in the channel!
            await interaction.response.edit_message(view=self.view)
        except discord.Forbidden:
            # If it fails, we send an ephemeral error (only visible to the user)
            await interaction.response.send_message("❌ I couldn't DM buggy! Make sure his DMs are open.", ephemeral=True)
        except Exception as e:
            # Fallback if connection times out
            try:
                await interaction.response.send_message("❌ Failed to send alert.", ephemeral=True)
            except: pass

class BotherView(discord.ui.View):
    def __init__(self, bot, options, guild_id):
        super().__init__(timeout=None) # Persistent
        self.bot = bot
        
        # Options: List of {"label": str, "key": str, "ping_text": str}
        for opt in options:
            # We use the "key" in the custom_id to make it unique
            custom_id = f"bb_{guild_id}_{opt['key']}"
            self.add_item(BotherButton(label=opt['label'], custom_id=custom_id, ping_text=opt['ping_text']))

class BotherBuggy(commands.Cog, name="Bother Buggy"):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Bother Buggy: A dashboard system for users to send private alerts to buggy."

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
        # Structure: [{"guild_id": 123, "options": [...], "title": "...", "sticky_active": bool, ...}]
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        
        # Default structure
        if not doc:
            doc = {
                "guild_id": guild_id, 
                "options": [], 
                "title": "🔔 Bother Buggy",
                "sticky_active": False,
                "sticky_mode": "after",
                "sticky_delay": 0
            }
        
        # Ensure fields exist (migration safe)
        if "title" not in doc: doc["title"] = "🔔 Bother Buggy"
        if "sticky_active" not in doc: doc["sticky_active"] = False
        if "sticky_mode" not in doc: doc["sticky_mode"] = "after"
        if "sticky_delay" not in doc: doc["sticky_delay"] = 0
            
        return doc

    def save_config(self, guild_id, config):
        """Saves the config for a guild using update_doc to prevent race conditions."""
        updated = self.bot.db.update_doc("bb_options", "guild_id", guild_id, config)
        if not updated:
            collection = self.bot.db.get_collection("bb_options")
            if not any(d['guild_id'] == guild_id for d in collection):
                collection.append(config)
                self.bot.db.save_collection("bb_options", collection)

    def get_dashboards(self):
        """Returns active dashboard messages to restore views."""
        return self.bot.db.get_collection("bb_dashboards")

    def save_dashboards(self, dashboards):
        """Saves active dashboards."""
        self.bot.db.save_collection("bb_dashboards", dashboards)

    def create_dashboard_embed(self, guild, title):
        """Creates the minimal embed for the dashboard."""
        embed = discord.Embed(
            title=title,
            description="Click what you want to do with buggy!",
            color=discord.Color(0xff90aa)
        )
        return embed

    # --- STICKY / REPOST LOGIC ---

    async def repost_dashboard(self, channel):
        """Deletes the old dashboard and posts a new one at the bottom."""
        # 1. Fetch CURRENT data fresh from DB
        dashboards = self.get_dashboards()
        dashboard_data = next((d for d in dashboards if d['guild_id'] == channel.guild.id), None)
        
        # 2. Delete old message safely
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
        
        # 3. Send new message and update DB ATOMICALLY
        try:
            new_msg = await channel.send(embed=embed, view=view)
            
            # 4. Prepare data for update
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

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles the sticky dashboard logic."""
        if not message.guild or message.author.id == self.bot.user.id:
            return
        
        # 1. Check if this channel has a dashboard
        dashboards = self.get_dashboards()
        dashboard = next((d for d in dashboards if d['channel_id'] == message.channel.id), None)
        
        if not dashboard:
            return

        # 2. Check if Sticky Mode is Enabled
        config = self.get_config(message.guild.id)
        if not config.get('sticky_active', False):
            return

        # 3. Check Timing Logic
        delay = config.get('sticky_delay', 0)
        mode = config.get('sticky_mode', 'after')
        now = datetime.datetime.now().timestamp()
        
        # Mode: Before (Cooldown)
        if mode == 'before' and delay > 0:
            last_posted = dashboard.get('last_posted_at', 0)
            if (now - last_posted) < delay:
                return

        # Mode: After (Delay)
        if mode == 'after' and delay > 0:
            await asyncio.sleep(delay)
            # Re-fetch active dashboard status to ensure it wasn't disabled during sleep
            # (We rely on `repost_dashboard` to handle fresh DB state, but we can do a quick check)
            current_config = self.get_config(message.guild.id)
            if not current_config.get('sticky_active', False):
                return

        # 4. Trigger Repost
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

        # --- ACTION: ADD ---
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

        # --- ACTION: REMOVE ---
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

        # --- ACTION: LIST ---
        elif action == "List":
            if not options:
                return await interaction.response.send_message("📝 You haven't added any options yet, buggy!", ephemeral=True)
            
            status = "Active" if config.get('sticky_active') else "Inactive"
            content = f"**Title:** {config['title']}\n**Sticky:** {status}\n**📋 Options:**\n"
            for o in options:
                content += f"• `{o['key']}`: **{o['label']}** (Ping: {o['ping_text']})\n"
            await interaction.response.send_message(content, ephemeral=True)

    @app_commands.command(name="bbdashboard", description="Spawn or remove the Bother Buggy Dashboard.")
    @app_commands.rename(should_set="set") # Renaming 'should_set' to 'set' in the UI
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
            
            # Find and delete existing dashboard
            dashboards = self.get_dashboards()
            target = next((d for d in dashboards if d['guild_id'] == interaction.guild_id), None)
            
            if target:
                try:
                    chan = interaction.guild.get_channel(target['channel_id'])
                    if chan:
                        msg = await chan.fetch_message(target['message_id'])
                        await msg.delete()
                except: pass
                
                # Remove from DB
                dashboards = [d for d in dashboards if d['guild_id'] != interaction.guild_id]
                self.save_dashboards(dashboards)
                
            await interaction.response.send_message("✅ Dashboard removed and sticky mode disabled.", ephemeral=True)
            return

        # --- ENABLE / SPAWN ---
        # Update settings
        config['sticky_active'] = True
        if text:
            config['title'] = text
        self.save_config(interaction.guild_id, config)

        if not config['options']:
            return await interaction.response.send_message("❌ You need to add some options first via `/bb action:Add`!", ephemeral=True)

        # Handle existing dashboard (Delete old one to move it here)
        dashboards = self.get_dashboards()
        target = next((d for d in dashboards if d['guild_id'] == interaction.guild_id), None)
        
        if target:
            try:
                chan = interaction.guild.get_channel(target['channel_id'])
                if chan:
                    msg = await chan.fetch_message(target['message_id'])
                    await msg.delete()
            except: pass

        # Create and Send new
        embed = self.create_dashboard_embed(interaction.guild, config['title'])
        view = BotherView(self.bot, config['options'], interaction.guild_id)
        
        msg = await interaction.channel.send(embed=embed, view=view)
        
        # Save new location
        new_dash = {
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "message_id": msg.id,
            "last_posted_at": datetime.datetime.now().timestamp()
        }
        
        # Atomic Update / Save
        # Filter out old one from list (if it existed), add new one, save.
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
