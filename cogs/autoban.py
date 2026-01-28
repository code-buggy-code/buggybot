import discord
from discord.ext import commands
from discord import app_commands
import datetime

# Function/Class List:
# class Autoban(commands.Cog)
# - __init__(bot)
# - get_autoban_roles(guild_id)
# - save_autoban_roles(guild_id, roles)
# - log_to_channel(guild, embed)
# - on_member_update(before, after)
# - autoban(interaction, role) [Slash]
# setup(bot)

class Autoban(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Persistent autoban for roles."

    # --- HELPERS ---

    def get_autoban_roles(self, guild_id):
        """Fetches list of autoban role IDs for a guild."""
        collection = self.bot.db.get_collection("autoban_configs")
        doc = next((d for d in collection if d['guild_id'] == guild_id), None)
        if doc:
            return doc.get('roles', [])
        return []

    def save_autoban_roles(self, guild_id, roles):
        """Saves autoban roles."""
        collection = self.bot.db.get_collection("autoban_configs")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append({"guild_id": guild_id, "roles": roles})
        self.bot.db.save_collection("autoban_configs", collection)

    async def log_to_channel(self, guild, embed):
        """Helper to send logs (replicated)."""
        settings = self.bot.db.get_collection("log_settings")
        guild_setting = next((s for s in settings if s['guild_id'] == guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Checks for persistent autoban roles."""
        autoban_roles = self.get_autoban_roles(after.guild.id)
        if autoban_roles:
            banned_role_found = False
            for role in after.roles:
                if role.id in autoban_roles:
                    banned_role_found = True
                    break
            
            if banned_role_found:
                if after.id == after.guild.owner_id or after == self.bot.user or after.top_role >= after.guild.me.top_role:
                    return

                try:
                    await after.ban(reason="Autoban: User acquired a blacklisted role.")
                    embed = discord.Embed(
                        title="🔨 Auto-Banned User",
                        description=f"{after.mention} was banned for having a blacklisted role.",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now()
                    )
                    await self.log_to_channel(after.guild, embed)
                except discord.Forbidden:
                    pass

    # --- SLASH COMMANDS ---

    @app_commands.command(name="autoban", description="Toggle persistent autoban for a role.")
    @app_commands.describe(role="The role to autoban")
    @app_commands.default_permissions(administrator=True)
    async def autoban(self, interaction: discord.Interaction, role: discord.Role):
        """Toggles persistent autoban for a role."""
        roles = self.get_autoban_roles(interaction.guild_id)
        
        if role.id in roles:
            roles.remove(role.id)
            self.save_autoban_roles(interaction.guild_id, roles)
            await interaction.response.send_message(f"✅ Stopped autobanning for **{role.name}**.", ephemeral=True)
        else:
            roles.append(role.id)
            self.save_autoban_roles(interaction.guild_id, roles)
            await interaction.response.send_message(f"🚨 **Autoban ENABLED** for **{role.name}**. I will ban anyone who has this role now and in the future.", ephemeral=True)
            
            # Run the immediate purge
            count = 0
            failed = 0
            msg = await interaction.followup.send(f"⏳ Scanning for existing members with {role.mention}...", ephemeral=True)
            
            for member in role.members:
                if member == interaction.guild.owner or member == self.bot.user or member.top_role >= interaction.guild.me.top_role:
                    failed += 1
                    continue
                try:
                    await member.ban(reason=f"Autoban command by {interaction.user} (Role: {role.name})")
                    count += 1
                except:
                    failed += 1
            
            await interaction.followup.send(f"✅ Initial scan complete. Banned **{count}** users. Failed to ban **{failed}**.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Autoban(bot))
