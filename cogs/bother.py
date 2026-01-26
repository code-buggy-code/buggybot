import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime

# Function/Class List:
# class RequestButton(discord.ui.Button)
# - __init__(label, custom_id, ping_text)
# - callback(interaction)
# class RequestView(discord.ui.View)
# - __init__(bot, options, guild_id)
# class Requests(commands.Cog)
# - __init__(bot)
# - cog_load()
# - restore_views()
# - get_options(guild_id)
# - save_options(guild_id, options)
# - get_dashboards()
# - save_dashboards(dashboards)
# - create_dashboard_embed(guild, options)
# - request (Group) [Prefix]
#   - request_add(ctx, label, key, ping_text)
#   - request_remove(ctx, key)
#   - request_list(ctx)
#   - request_spawn(ctx)
# setup(bot)

BUGGY_ID = 1433003746719170560

class RequestButton(discord.ui.Button):
    def __init__(self, label, custom_id, ping_text):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id)
        self.ping_text = ping_text

    async def callback(self, interaction: discord.Interaction):
        """Sends the private request to buggy."""
        buggy = interaction.client.get_user(BUGGY_ID)
        if not buggy:
            try:
                buggy = await interaction.client.fetch_user(BUGGY_ID)
            except:
                return await interaction.response.send_message("❌ I couldn't find buggy to send the request to! Is the ID correct?", ephemeral=True)

        embed = discord.Embed(
            title="📥 New Request Received",
            description=f"**User:** {interaction.user.mention} ({interaction.user.id})\n**Channel:** {interaction.channel.mention}\n**Option Selected:** {self.label}",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Ping Content", value=self.ping_text or "No specific text set.")
        
        try:
            await buggy.send(embed=embed)
            await interaction.response.send_message(f"✅ Your request for **{self.label}** has been sent to buggy!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't DM buggy! Make sure his DMs are open.", ephemeral=True)

class RequestView(discord.ui.View):
    def __init__(self, bot, options, guild_id):
        super().__init__(timeout=None) # Persistent
        self.bot = bot
        
        # Options: List of {"label": str, "key": str, "ping_text": str}
        for opt in options:
            # We use the "key" in the custom_id to make it unique
            custom_id = f"req_{guild_id}_{opt['key']}"
            self.add_item(RequestButton(label=opt['label'], custom_id=custom_id, ping_text=opt['ping_text']))

class Requests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "A dashboard system for users to send private requests to buggy."

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
                view = RequestView(self.bot, options, guild_id)
                self.bot.add_view(view)
                count += 1
        print(f"✅ Restored {count} Request Dashboard views, you genius!")

    # --- DB HELPERS ---

    def get_options(self, guild_id):
        """Returns the list of request options for a guild."""
        collection = self.bot.db.get_collection("request_options")
        # Structure: [{"guild_id": 123, "options": [...]}]
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        return doc['options'] if doc else []

    def save_options(self, guild_id, options):
        """Saves the list of options for a guild."""
        collection = self.bot.db.get_collection("request_options")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append({"guild_id": guild_id, "options": options})
        self.bot.db.save_collection("request_options", collection)

    def get_dashboards(self):
        """Returns active dashboard messages to restore views."""
        return self.bot.db.get_collection("request_dashboards")

    def save_dashboards(self, dashboards):
        """Saves active dashboards."""
        self.bot.db.save_collection("request_dashboards", dashboards)

    def create_dashboard_embed(self, guild, options):
        """Creates the embed for the dashboard."""
        embed = discord.Embed(
            title="📩 Request Dashboard",
            description="Click a button below to send a private request to buggy! Please be patient for a response.",
            color=discord.Color.blue()
        )
        if options:
            option_list = "\n".join([f"• **{o['label']}**" for o in options])
            embed.add_field(name="Available Options", value=option_list, inline=False)
        else:
            embed.description = "No options configured yet, buggy!"
        
        embed.set_footer(text=f"Server: {guild.name}")
        return embed

    # --- PREFIX COMMANDS ---

    @commands.group(name="request", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def request(self, ctx):
        """Manage the Request Dashboard (Admin Only)."""
        await ctx.send("🛠️ **Request Dashboard Commands:**\n"
                       "`?request add [label] [key] [ping_text]` - Add a button\n"
                       "`?request remove [key]` - Remove a button\n"
                       "`?request list` - Show current options\n"
                       "`?request spawn` - Post the dashboard here")

    @request.command(name="add")
    @commands.has_permissions(administrator=True)
    async def request_add(self, ctx, label: str, key: str, *, ping_text: str):
        """Adds a new button option. Key must be one word (no spaces)."""
        options = self.get_options(ctx.guild.id)
        
        if any(o['key'] == key for o in options):
            return await ctx.send(f"❌ An option with the key `{key}` already exists, buggy!")
        
        if len(options) >= 25:
            return await ctx.send("❌ Discord only allows 25 buttons per message, you popular thing!")

        options.append({
            "label": label,
            "key": key.lower(),
            "ping_text": ping_text
        })
        
        self.save_options(ctx.guild.id, options)
        await ctx.send(f"✅ Added **{label}** to your options! Re-spawn the dashboard to see it.")

    @request.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def request_remove(self, ctx, key: str):
        """Removes a button option by its key."""
        options = self.get_options(ctx.guild.id)
        initial_len = len(options)
        options = [o for o in options if o['key'] != key.lower()]
        
        if len(options) < initial_len:
            self.save_options(ctx.guild.id, options)
            await ctx.send(f"✅ Removed the `{key}` option for you!")
        else:
            await ctx.send(f"❌ I couldn't find an option with the key `{key}`.")

    @request.command(name="list")
    @commands.has_permissions(administrator=True)
    async def request_list(self, ctx):
        """Lists all configured request options."""
        options = self.get_options(ctx.guild.id)
        if not options:
            return await ctx.send("📝 You haven't added any options yet, buggy!")
        
        text = "**📋 Configured Request Options:**\n"
        for o in options:
            text += f"• `{o['key']}`: **{o['label']}** (Ping: {o['ping_text']})\n"
        await ctx.send(text)

    @request.command(name="spawn")
    @commands.has_permissions(administrator=True)
    async def request_spawn(self, ctx):
        """Spawns the dashboard in the current channel."""
        options = self.get_options(ctx.guild.id)
        if not options:
            return await ctx.send("❌ You need to add some options first with `?request add`!")

        embed = self.create_dashboard_embed(ctx.guild, options)
        view = RequestView(self.bot, options, ctx.guild.id)
        
        msg = await ctx.send(embed=embed, view=view)
        
        # Save dashboard info for view restoration
        dashboards = self.get_dashboards()
        # Remove old dashboard record for this guild if it exists
        dashboards = [d for d in dashboards if d['guild_id'] != ctx.guild.id]
        dashboards.append({
            "guild_id": ctx.guild.id,
            "channel_id": ctx.channel.id,
            "message_id": msg.id
        })
        self.save_dashboards(dashboards)
        
        await ctx.message.delete() # Clean up command

async def setup(bot):
    await bot.add_cog(Requests(bot))
