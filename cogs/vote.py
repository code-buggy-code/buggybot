import discord
from discord.ext import commands
from discord import app_commands
import datetime
from typing import Literal

# Function/Class List:
# class VoteKick(commands.Cog)
# - __init__(bot)
# - get_vote_data(guild_id)
# - save_vote_data(guild_id, data)
# - log_to_channel(guild, embed)
# - vote(interaction, member) [Slash]
# - voteset(interaction, channel) [Slash]
# - voterole(interaction, role) [Slash]
# - voteremove(interaction, member) [Slash]
# - vote_list(interaction) [Slash]
# setup(bot)

BUGGY_ID = 1433003746719170560

class VoteKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Vote kick system."
        self.VOTE_THRESHOLD = 3

    # --- HELPERS ---

    def get_vote_data(self, guild_id):
        """Fetches voting configuration and active votes for a guild."""
        collection = self.bot.db.get_collection("vote_data")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                if 'active_votes' not in doc: doc['active_votes'] = {}
                if 'voting_role_id' not in doc: doc['voting_role_id'] = None
                if 'voting_channel_id' not in doc: doc['voting_channel_id'] = None
                return doc
        
        return {
            "guild_id": guild_id,
            "voting_role_id": None,
            "voting_channel_id": None,
            "active_votes": {}
        }

    def save_vote_data(self, guild_id, data):
        """Saves vote data for a guild."""
        collection = self.bot.db.get_collection("vote_data")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append(data)
        self.bot.db.save_collection("vote_data", collection)

    async def log_to_channel(self, guild, embed):
        """Helper to send logs (replicated from Logger for independence)."""
        settings = self.bot.db.get_collection("log_settings")
        guild_setting = next((s for s in settings if s['guild_id'] == guild.id), None)
        
        if not guild_setting: return

        log_channel = self.bot.get_channel(guild_setting['log_channel_id'])
        if not log_channel: return

        try:
            await log_channel.send(embed=embed)
        except:
            pass

    # --- SLASH COMMANDS ---

    @app_commands.command(name="voteset", description="Set the channel where /vote can be used.")
    @app_commands.describe(channel="The channel for voting")
    @app_commands.default_permissions(administrator=True)
    async def voteset(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel where /vote can be used."""
        data = self.get_vote_data(interaction.guild.id)
        data['voting_channel_id'] = channel.id
        self.save_vote_data(interaction.guild_id, data)
        await interaction.response.send_message(f"✅ /vote is now restricted to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="vote", description="Vote to kick a user", extras={'public': True})
    @app_commands.describe(member="The member to vote kick")
    async def vote(self, interaction: discord.Interaction, member: discord.Member):
        data = self.get_vote_data(interaction.guild.id)
        voting_role_id = data['voting_role_id']
        active_votes = data['active_votes'] # {target_id_str: [list of voters]}
        voting_channel_id = data.get('voting_channel_id')

        if voting_channel_id and interaction.channel_id != voting_channel_id:
            return await interaction.response.send_message(f"❌ You can only use /vote in <#{voting_channel_id}>.", ephemeral=True)

        if voting_role_id is None:
            return await interaction.response.send_message("❌ The voting role has not been set yet. An admin must use `/voterole` first.", ephemeral=True)

        user_role_ids = [r.id for r in interaction.user.roles]
        if voting_role_id not in user_role_ids and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You do not have the required role to vote.", ephemeral=True)

        if member == interaction.user: return await interaction.response.send_message("❌ You cannot vote to kick yourself.", ephemeral=True)
        if member.bot: return await interaction.response.send_message("❌ You cannot vote to kick a bot.", ephemeral=True)

        target_id_str = str(member.id)
        
        if target_id_str not in active_votes: 
            active_votes[target_id_str] = []
        
        if interaction.user.id in active_votes[target_id_str]:
            return await interaction.response.send_message(f"⚠️ You have already voted to kick {member.display_name}.", ephemeral=True)

        active_votes[target_id_str].append(interaction.user.id)
        data['active_votes'] = active_votes
        self.save_vote_data(interaction.guild.id, data)
        
        current_votes = len(active_votes[target_id_str])
        
        embed = discord.Embed(description=f"🗳️ **Vote Cast**\n{interaction.user.mention} voted to kick {member.mention}.\nCurrent Votes: **{current_votes}/{self.VOTE_THRESHOLD}**", color=discord.Color.yellow(), timestamp=datetime.datetime.now())
        await self.log_to_channel(interaction.guild, embed)

        if current_votes >= self.VOTE_THRESHOLD:
            try:
                await member.kick(reason=f"Votekicked by {current_votes} users.")
                embed = discord.Embed(description=f"✅ **VOTEKICK SUCCESS**\n{member.mention} was kicked.\nTotal Votes: {current_votes}", color=discord.Color.green(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
                await interaction.response.send_message(f"✅ {member.mention} has been kicked by vote.", ephemeral=False) 
                
                # Cleanup and Save
                if target_id_str in active_votes: del active_votes[target_id_str]
                data['active_votes'] = active_votes
                self.save_vote_data(interaction.guild.id, data)

            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Vote threshold reached, but I do not have permission to kick this user.", ephemeral=True)
                embed = discord.Embed(description=f"❌ **VOTEKICK FAILED**\nTried to kick {member.mention} but lacked permissions.", color=discord.Color.red(), timestamp=datetime.datetime.now())
                await self.log_to_channel(interaction.guild, embed)
        else:
            await interaction.response.send_message(f"✅ Vote cast! {member.display_name} has {current_votes}/{self.VOTE_THRESHOLD} votes.", ephemeral=True)

    @app_commands.command(name="voterole", description="Set the role allowed to vote.")
    @app_commands.describe(role="The role to allow voting")
    @app_commands.default_permissions(administrator=True)
    async def voterole(self, interaction: discord.Interaction, role: discord.Role):
        """Set the role allowed to vote."""
        data = self.get_vote_data(interaction.guild_id)
        data['voting_role_id'] = role.id
        self.save_vote_data(interaction.guild_id, data)

        embed = discord.Embed(description=f"**Vote Role Updated**\nNew Role: {role.mention}\nSet By: {interaction.user.mention}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
        await self.log_to_channel(interaction.guild, embed)
        await interaction.response.send_message(f"✅ Voting role set to {role.mention}.", ephemeral=True)

    @app_commands.command(name="voteremove", description="Remove an active vote against a user.")
    @app_commands.describe(member="The member to clear votes for")
    @app_commands.default_permissions(administrator=True)
    async def voteremove(self, interaction: discord.Interaction, member: discord.Member):
        """Remove an active vote against a user (buggy only)."""
        data = self.get_vote_data(interaction.guild.id)
        target_id_str = str(member.id)
        
        if target_id_str in data['active_votes']:
            del data['active_votes'][target_id_str]
            self.save_vote_data(interaction.guild.id, data)
            
            embed = discord.Embed(description=f"**Vote Cancelled**\nVotes against {member.mention} were cleared by {interaction.user.mention}.", color=discord.Color.orange(), timestamp=datetime.datetime.now())
            await self.log_to_channel(interaction.guild, embed)
            await interaction.response.send_message(f"✅ Cleared all votes against {member.display_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ There are no active votes against {member.display_name}.", ephemeral=True)

    @app_commands.command(name="vote-list", description="List active vote kicks")
    async def vote_list(self, interaction: discord.Interaction):
        # BUGGY ONLY CHECK
        if interaction.user.id != BUGGY_ID:
            return await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)

        data = self.get_vote_data(interaction.guild.id)
        active_votes = data.get('active_votes', {})

        if not active_votes: 
            return await interaction.response.send_message("No active votes.", ephemeral=True)

        description = ""
        for target_id_str, voters in active_votes.items():
            try: target_id = int(target_id_str)
            except: continue
            
            member = interaction.guild.get_member(target_id)
            name = member.display_name if member else f"ID: {target_id}"
            description += f"**{name}**: {len(voters)}/{self.VOTE_THRESHOLD} votes\n"

        embed = discord.Embed(title="🗳️ Active Vote Kicks", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(VoteKick(bot))
