import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
import os
import asyncio

class Overwatch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "overwatch_data.json"
        self.config_file = "overwatch_config.json"
        self.api_base = "https://overfast-api.tekrop.fr/players"
        self.load_data()

    def load_data(self):
        """Loads the saved user data from the JSON file."""
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w") as f:
                json.dump({}, f)
        with open(self.data_file, "r") as f:
            self.users = json.load(f)
            
        if not os.path.exists(self.config_file):
            with open(self.config_file, "w") as f:
                json.dump({}, f)
        with open(self.config_file, "r") as f:
            self.config = json.load(f)

    def save_data(self):
        """Saves the current user data to the JSON file."""
        with open(self.data_file, "w") as f:
            json.dump(self.users, f, indent=4)

    def save_config(self):
        """Saves the configuration to the JSON file."""
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=4)

    async def fetch_profile(self, battletag: str):
        """Fetches the profile summary from the OverFast API."""
        formatted_tag = battletag.replace("#", "-")
        url = f"{self.api_base}/{formatted_tag}/summary"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None

    @app_commands.command(name="overwatch", description="View Overwatch stats, or link/unlink your BattleTag.")
    @app_commands.rename(show_list="list")
    @app_commands.describe(
        link="Link your BattleTag (e.g., Player#1234)",
        unlink="Unlink your currently registered BattleTag",
        user="View another user's Overwatch stats",
        show_list="View an alphabetical list of all registered users"
    )
    async def overwatch(
        self,
        interaction: discord.Interaction,
        link: str = None,
        unlink: bool = False,
        user: discord.Member = None,
        show_list: bool = False
    ):
        user_id = str(interaction.user.id)

        # 1. HANDLE UNLINK
        if unlink:
            if user_id in self.users:
                del self.users[user_id]
                self.save_data()
                
                role_msg = ""
                role_id = self.config.get("linked_role_id")
                if role_id:
                    role = interaction.guild.get_role(role_id)
                    if role and role in interaction.user.roles:
                        try:
                            await interaction.user.remove_roles(role)
                        except discord.Forbidden:
                            role_msg = "\n*(Note: Could not remove the linked role due to missing permissions.)*"

                await interaction.response.send_message(f"✅ Your Overwatch profile has been unlinked successfully.{role_msg}", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ You do not have an Overwatch profile linked.", ephemeral=True)
            return

        # 2. HANDLE LINK
        if link:
            await interaction.response.defer(ephemeral=True)
            
            # Format verification (Must contain #)
            if "#" not in link:
                await interaction.followup.send("⚠️ Invalid format. Please include your BattleTag identifier (e.g. `Player#1234`).", ephemeral=True)
                return

            profile_data = await self.fetch_profile(link)
            
            # Not found or private
            if not profile_data or not profile_data.get("is_public", False):
                error_msg = (
                    f"**Profile Not Found / Private**\n"
                    f"Linked `{link}`. Cannot find profile, or career profile is private — please follow these steps to make it public:\n\n"
                    f"1. Launch Overwatch 2 and press Esc\n"
                    f"2. Click Options\n"
                    f"3. Click the Social tab\n"
                    f"4. Find Career Profile Visibility\n"
                    f"5. Switch it to Public\n\n"
                    f"*If you've already set your profile to public but it still isn't showing, this is a caching issue. Log into https://overwatch.blizzard.com/en-us/ and search for your own profile — this forces a cache refresh and should make your stats visible shortly after (wait about 10 minutes).*")
                await interaction.followup.send(error_msg, ephemeral=True)
                return
            
            # Success
            self.users[user_id] = link
            self.save_data()
            
            role_msg = ""
            role_id = self.config.get("linked_role_id")
            if role_id:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await interaction.user.add_roles(role)
                    except discord.Forbidden:
                        role_msg = "\n*(Note: Could not assign the linked role due to missing permissions.)*"

            await interaction.followup.send(f"✅ Successfully linked your profile to **{link}**!{role_msg}", ephemeral=True)
            return

        # 3. HANDLE LIST
        if show_list:
            if not self.users:
                await interaction.response.send_message("No users are currently registered.", ephemeral=True)
                return
            
            await interaction.response.defer()
            entries = []
            
            for uid, btag in self.users.items():
                member = interaction.guild.get_member(int(uid))
                display_name = member.display_name if member else f"Unknown User ({uid})"
                entries.append((display_name, btag))
            
            # Sort alphabetically by display name (case-insensitive)
            entries.sort(key=lambda x: x[0].lower())
            
            list_text = "**Registered Overwatch Players:**\n\n"
            for name, btag in entries:
                list_text += f"**{name}**\n└ ID: `{btag}`\n"
            
            # Send as embed to prevent wall of text looking ugly
            embed = discord.Embed(description=list_text, color=discord.Color.orange())
            await interaction.followup.send(embed=embed)
            return

        # 4. HANDLE VIEW STATS (Self or User)
        target_member = user or interaction.user
        target_id = str(target_member.id)

        if target_id not in self.users:
            if target_member == interaction.user:
                await interaction.response.send_message("⚠️ You are not registered! Please link your profile using `/overwatch link:<battletag>`", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ **{target_member.display_name}** is not registered with the bot.", ephemeral=True)
            return

        await interaction.response.defer()
        
        battletag = self.users[target_id]
        profile_data = await self.fetch_profile(battletag)

        if not profile_data:
            await interaction.followup.send("⚠️ An error occurred fetching the stats. The profile may have been made private or deleted.")
            return

        # Build Stats Embed
        embed = discord.Embed(
            title=f"Overwatch Stats: {battletag}",
            color=discord.Color.orange(),
            url=f"https://overwatch.blizzard.com/en-us/career/{battletag.replace('#', '-')}/"
        )
        
        if profile_data.get("avatar"):
            embed.set_thumbnail(url=profile_data["avatar"])
        
        # General Info
        title = profile_data.get("title", "No Title")
        endorsement = profile_data.get("endorsement", {}).get("level", 1)
        embed.add_field(name="Profile Info", value=f"**Title:** {title}\n**Endorsement:** Level {endorsement}", inline=False)

        # Competitive Stats (if available)
        comp_data = profile_data.get("competitive")
        if comp_data:
            # Check PC or Console stats
            platform_stats = comp_data.get("pc") or comp_data.get("console")
            if platform_stats:
                ranks = []
                for role in ["tank", "damage", "support"]:
                    role_info = platform_stats.get(role)
                    if role_info:
                        div = role_info.get("division", "Unknown").capitalize()
                        tier = role_info.get("tier", "")
                        ranks.append(f"**{role.capitalize()}:** {div} {tier}")
                
                if ranks:
                    embed.add_field(name="Competitive Ranks", value="\n".join(ranks), inline=False)
                else:
                    embed.add_field(name="Competitive Ranks", value="Unranked / No Data", inline=False)
        else:
            embed.add_field(name="Competitive Ranks", value="Unranked / No Data", inline=False)

        await interaction.followup.send(embed=embed)


    @app_commands.command(name="overrole", description="Assign a specific role to all users linked with the bot")
    @app_commands.default_permissions(manage_roles=True)
    async def overrole(self, interaction: discord.Interaction, role: discord.Role):
        # Prevent timeout for potentially long operations
        await interaction.response.defer(ephemeral=True)

        # Save the role to config so it is permanent and applies to new users
        self.config["linked_role_id"] = role.id
        self.save_config()

        if not self.users:
            await interaction.followup.send(f"✅ Linked role set to {role.mention}. No users are currently registered in the database to assign it to.")
            return

        assigned_count = 0
        failed_count = 0

        for uid in self.users.keys():
            member = interaction.guild.get_member(int(uid))
            if member:
                if role not in member.roles:
                    try:
                        await member.add_roles(role)
                        assigned_count += 1
                        # Tiny sleep to avoid Discord API rate-limits if giving roles to hundreds of people
                        await asyncio.sleep(0.5) 
                    except discord.Forbidden:
                        failed_count += 1
            else:
                failed_count += 1 # Member not in server anymore
        
        msg = f"✅ Success! Linked role set to {role.mention} and assigned to **{assigned_count}** existing users."
        if failed_count > 0:
            msg += f"\n⚠️ Skipped **{failed_count}** users (Missing permissions, or user left the server)."
            
        await interaction.followup.send(msg)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Automatically deregisters users when they leave the server."""
        user_id = str(member.id)
        if user_id in self.users:
            del self.users[user_id]
            self.save_data()

async def setup(bot):
    await bot.add_cog(Overwatch(bot))
