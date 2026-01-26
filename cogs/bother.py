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
# - get_options(guild_id)
# - save_options(guild_id, options)
# - get_dashboards()
# - save_dashboards(dashboards)
# - create_dashboard_embed(guild, options)
# - bb(interaction, action, label, key, ping_text) [Slash Command]
# setup(bot)

BUGGY_ID = 1433003746719170560

class BotherButton(discord.ui.Button):
    def __init__(self, label, custom_id, ping_text):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id)
        self.ping_text = ping_text

    async def callback(self, interaction: discord.Interaction):
        """Sends the private message to buggy."""
        buggy = interaction.client.get_user(BUGGY_ID)
        if not buggy:
            try:
                buggy = await interaction.client.fetch_user(BUGGY_ID)
            except:
                return await interaction.response.send_message("❌ I couldn't find buggy to bother! Is the ID correct?", ephemeral=True)

        embed = discord.Embed(
            title="🔔 Bother Buggy Alert",
            description=f"**User:** {interaction.user.mention} ({interaction.user.id})\n**Channel:** {interaction.channel.mention}\n**Button Clicked:** {self.label}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Message Content", value=self.ping_text or "No specific text set.")
        
        try:
            await buggy.send(embed=embed)
            await interaction.response.send_message(f"✅ You have successfully bothered buggy about **{self.label}**!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't DM buggy! Make sure his DMs are open.", ephemeral=True)

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
            options = self.get_options(guild_id)
            if options:
                view = BotherView(self.bot, options, guild_id)
                self.bot.add_view(view)
                count += 1
        print(f"✅ Restored {count} Bother Buggy dashboard views, you genius!")

    # --- DB HELPERS ---

    def get_options(self, guild_id):
        """Returns the list of bother options for a guild."""
        collection = self.bot.db.get_collection("bb_options")
        # Structure: [{"guild_id": 123, "options": [...]}]
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        return doc['options'] if doc else []

    def save_options(self, guild_id, options):
        """Saves the list of options for a guild."""
        collection = self.bot.db.get_collection("bb_options")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append({"guild_id": guild_id, "options": options})
        self.bot.db.save_collection("bb_options", collection)

    def get_dashboards(self):
        """Returns active dashboard messages to restore views."""
        return self.bot.db.get_collection("bb_dashboards")

    def save_dashboards(self, dashboards):
        """Saves active dashboards."""
        self.bot.db.save_collection("bb_dashboards", dashboards)

    def create_dashboard_embed(self, guild, options):
        """Creates the embed for the dashboard."""
        embed = discord.Embed(
            title="🔔 Bother Buggy",
            description="Click a button below to bother buggy! Please be patient for a response.",
            color=discord.Color.gold()
        )
        if options:
            option_list = "\n".join([f"• **{o['label']}**" for o in options])
            embed.add_field(name="Available Options", value=option_list, inline=False)
        else:
            embed.description = "No options configured yet, buggy!"
        
        embed.set_footer(text=f"Server: {guild.name}")
        return embed

    # --- SLASH COMMAND ---

    @app_commands.command(name="bb", description="Manage the Bother Buggy Dashboard.")
    @app_commands.describe(
        action="What would you like to do?",
        label="[Add] Text shown on the button",
        key="[Add/Remove] Unique one-word ID for the option",
        ping_text="[Add] Message sent to buggy"
    )
    @app_commands.default_permissions(administrator=True)
    async def bb(self, interaction: discord.Interaction, 
                 action: Literal["Add", "Remove", "List", "Spawn"], 
                 label: str = None, 
                 key: str = None, 
                 ping_text: str = None):
        
        options = self.get_options(interaction.guild_id)

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
            self.save_options(interaction.guild_id, options)
            await interaction.response.send_message(f"✅ Added **{label}** to the list! Use `/bb action:Spawn` to update the dashboard.", ephemeral=True)

        # --- ACTION: REMOVE ---
        elif action == "Remove":
            if not key:
                return await interaction.response.send_message("❌ For 'Remove', you must provide the `key`.", ephemeral=True)

            initial_len = len(options)
            options = [o for o in options if o['key'] != key.lower()]
            
            if len(options) < initial_len:
                self.save_options(interaction.guild_id, options)
                await interaction.response.send_message(f"✅ Removed the `{key}` option for you!", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ I couldn't find an option with the key `{key}`.", ephemeral=True)

        # --- ACTION: LIST ---
        elif action == "List":
            if not options:
                return await interaction.response.send_message("📝 You haven't added any options yet, buggy!", ephemeral=True)
            
            text = "**📋 Bother Buggy Options:**\n"
            for o in options:
                text += f"• `{o['key']}`: **{o['label']}** (Ping: {o['ping_text']})\n"
            await interaction.response.send_message(text, ephemeral=True)

        # --- ACTION: SPAWN ---
        elif action == "Spawn":
            if not options:
                return await interaction.response.send_message("❌ You need to add some options first, buggy!", ephemeral=True)

            embed = self.create_dashboard_embed(interaction.guild, options)
            view = BotherView(self.bot, options, interaction.guild_id)
            
            await interaction.response.send_message("✅ Dashboard spawned! I've recorded its location for persistence.", ephemeral=True)
            await interaction.edit_original_response(content=None, embed=embed, view=view)
            msg = await interaction.original_response()

            dashboards = self.get_dashboards()
            dashboards = [d for d in dashboards if d['guild_id'] != interaction.guild_id]
            
            dashboards.append({
                "guild_id": interaction.guild_id,
                "channel_id": interaction.channel_id,
                "message_id": msg.id
            })
            self.save_dashboards(dashboards)

async def setup(bot):
    await bot.add_cog(BotherBuggy(bot))
