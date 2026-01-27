import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
from typing import Literal
import secrets

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
# - bbdashboard(interaction, text) [Slash Command]
# setup(bot)

BUGGY_ID = 1433003746719170560

class BotherButton(discord.ui.Button):
    def __init__(self, label, custom_id, ping_text):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id)
        self.ping_text = ping_text

    async def callback(self, interaction: discord.Interaction):
        """Sends the private message to buggy."""
        # Defer immediately to "acknowledge" silently so we can delete it later
        await interaction.response.defer(ephemeral=True)

        buggy = interaction.client.get_user(BUGGY_ID)
        if not buggy:
            try:
                buggy = await interaction.client.fetch_user(BUGGY_ID)
            except:
                return await interaction.followup.send("❌ I couldn't find buggy to bother! Is the ID correct?", ephemeral=True)

        # Formatting: [Nickname] [Ping Text]
        # Then Mention + Username + Channel Link
        nickname = interaction.user.display_name
        username = interaction.user.name
        header = f"[{nickname}] {self.ping_text}"
        body = f"{interaction.user.mention} ({username}) {interaction.channel.jump_url}"
        
        msg = f"**{header}**\n{body}"
        
        try:
            await buggy.send(msg)
            # Success! Delete the "Thinking..." state so it appears invisible
            await interaction.delete_original_response()
        except discord.Forbidden:
            await interaction.followup.send("❌ I couldn't DM buggy! Make sure his DMs are open.", ephemeral=True)

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
        # Structure: [{"guild_id": 123, "options": [...], "title": "...", "hash": "..."}]
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        
        # Default structure
        if not doc:
            doc = {
                "guild_id": guild_id, 
                "options": [], 
                "title": "🔔 Bother Buggy",
                "hash": secrets.token_hex(16)
            }
        
        # Ensure fields exist (migration safe)
        if "title" not in doc:
            doc["title"] = "🔔 Bother Buggy"
        if "hash" not in doc:
            doc["hash"] = secrets.token_hex(16)
            
        return doc

    def save_config(self, guild_id, config):
        """Saves the config for a guild using update_doc to prevent race conditions."""
        # Using update_doc with 'upsert' logic is safer than overwriting the whole list
        # We try to update, if it fails (doesn't exist), we append.
        updated = self.bot.db.update_doc("bb_options", "guild_id", guild_id, config)
        if not updated:
            collection = self.bot.db.get_collection("bb_options")
            # Double check it's not there to avoid dups on race
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
        # 1. Fetch CURRENT data fresh from DB to minimize race condition window
        dashboards = self.get_dashboards()
        dashboard_data = next((d for d in dashboards if d['guild_id'] == channel.guild.id), None)
        
        # 2. Delete old message
        try:
            if dashboard_data and dashboard_data.get('message_id'):
                old_msg = await channel.fetch_message(dashboard_data['message_id'])
                await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass # Message might already be gone

        # 3. Prepare new message
        config = self.get_config(channel.guild.id)
        if not config['options']: return # Should have options if it was spawned
        
        embed = self.create_dashboard_embed(channel.guild, config['title'])
        view = BotherView(self.bot, config['options'], channel.guild.id)
        
        # 4. Send new message
        try:
            new_msg = await channel.send(embed=embed, view=view)
            
            # 5. Update DB using ATOMIC UPDATE
            # This prevents overwriting other guilds' dashboards if multiple events happen simultaneously
            updated = self.bot.db.update_doc("bb_dashboards", "guild_id", channel.guild.id, {
                "message_id": new_msg.id,
                "channel_id": channel.id
            })
            
            # If the doc didn't exist (unlikely if we just fetched it, but possible on first spawn), append it
            if not updated:
                 new_dash = {
                     "guild_id": channel.guild.id,
                     "channel_id": channel.id,
                     "message_id": new_msg.id
                 }
                 # Refresh list again before append
                 dashboards = self.get_dashboards()
                 dashboards.append(new_dash)
                 self.save_dashboards(dashboards)
                 
        except Exception as e:
            print(f"Failed to repost Bother Buggy dashboard: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Checks for the specific sticky HASH to trigger a dashboard repost."""
        # Note: We do NOT check for bot authors here, because the sticky bot is likely a bot.
        if not message.guild: return
        
        config = self.get_config(message.guild.id)
        target_hash = config.get('hash')
        
        if not target_hash: return

        # Check if the message IS the hash
        if message.content.strip() == target_hash:
            # 1. Delete the hash trigger immediately
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            
            # 2. Trigger repost
            # We don't manually mess with the DB list here anymore to avoid race conditions.
            # repost_dashboard handles the atomic update.
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
            
            content = f"**Title:** {config['title']}\n**Sticky Hash:** `{config['hash']}`\n**📋 Options:**\n"
            for o in options:
                content += f"• `{o['key']}`: **{o['label']}** (Ping: {o['ping_text']})\n"
            await interaction.response.send_message(content, ephemeral=True)

    @app_commands.command(name="bbdashboard", description="Spawn the Bother Buggy Dashboard in this channel.")
    @app_commands.describe(text="[Optional] Set a new title for the dashboard before spawning.")
    @app_commands.default_permissions(administrator=True)
    async def bbdashboard(self, interaction: discord.Interaction, text: str = None):
        config = self.get_config(interaction.guild_id)
        
        # Ensure hash is saved/generated
        if 'hash' not in config:
            config['hash'] = secrets.token_hex(16)
            self.save_config(interaction.guild_id, config)

        # Update title if provided
        if text:
            config['title'] = text
            self.save_config(interaction.guild_id, config)
            
        if not config['options']:
            return await interaction.response.send_message("❌ You need to add some options first via `/bb action:Add`!", ephemeral=True)

        embed = self.create_dashboard_embed(interaction.guild, config['title'])
        view = BotherView(self.bot, config['options'], interaction.guild_id)
        
        # 1. Send Ephemeral Response with the Hash
        # This allows the user to copy the hash for their sticky bot
        await interaction.response.send_message(
            f"✅ **Dashboard Spawned!**\n\n"
            f"👇 **Sticky Bot Configuration** 👇\n"
            f"Copy this hash and set it up in your sticky message bot:\n"
            f"```\n{config['hash']}\n```\n"
            f"When your sticky bot posts this exact code, I will automatically delete it and repost the dashboard in its place!",
            ephemeral=True
        )
        
        # 2. Send the dashboard message initially
        msg = await interaction.channel.send(embed=embed, view=view)

        # 3. Save using atomic update or append
        updated = self.bot.db.update_doc("bb_dashboards", "guild_id", interaction.guild_id, {
            "channel_id": interaction.channel_id,
            "message_id": msg.id
        })
        
        if not updated:
            dashboards = self.get_dashboards()
            dashboards.append({
                "guild_id": interaction.guild_id,
                "channel_id": interaction.channel_id,
                "message_id": msg.id
            })
            self.save_dashboards(dashboards)

async def setup(bot):
    await bot.add_cog(BotherBuggy(bot))
