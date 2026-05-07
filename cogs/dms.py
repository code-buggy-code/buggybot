import discord
from discord.ext import commands
from discord import app_commands
import re
from typing import Literal

# Function/Class List:
# class DMRequests(commands.Cog)
# - __init__(bot)
# - get_dm_settings(guild_id)
# - save_dm_settings(guild_id, data)
# - handle_dm_request(message)
# - on_message(message)
# - on_raw_reaction_add(payload)
# - dmconfig(interaction, role1, role2, role3, emoji1, emoji2, emoji3) [Slash]
# - dmchannel(interaction, action, channel) [Slash]
# - setup(bot)

class DMRequests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "DM Request system."
        # Added grey question mark for More Info
        self.DEFAULT_DM_REACTS = ["👍", "👎", "❔"]

    # --- HELPERS ---

    def get_dm_settings(self, guild_id):
        """Fetches DM settings for a specific guild."""
        collection = self.bot.db.get_collection("dm_settings")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                if "reacts" not in doc: doc["reacts"] = self.DEFAULT_DM_REACTS.copy()
                # Ensure 3 emojis exist if migrated from older format
                while len(doc["reacts"]) < 3:
                    doc["reacts"].append(self.DEFAULT_DM_REACTS[len(doc["reacts"])])
                if "roles" not in doc: doc["roles"] = [0, 0, 0]
                if "channels" not in doc: doc["channels"] = []
                return doc
        
        return {
            "guild_id": guild_id,
            "channels": [],
            "roles": [0, 0, 0],
            "reacts": self.DEFAULT_DM_REACTS.copy()
        }

    def save_dm_settings(self, guild_id, data):
        """Saves DM settings for a guild."""
        collection = self.bot.db.get_collection("dm_settings")
        collection = [d for d in collection if d['guild_id'] != guild_id]
        collection.append(data)
        self.bot.db.save_collection("dm_settings", collection)

    async def handle_dm_request(self, message):
        settings = self.get_dm_settings(message.guild.id)
        
        if message.channel.id not in settings['channels']:
            return

        is_admin = message.author.guild_permissions.administrator
        cleaned_content = message.content.strip()
        match = re.match(r'^<@!?(\d+)>\s+(.+)', cleaned_content, re.DOTALL)
        
        valid_request = False
        target_member = None
        
        if match:
            user_id = int(match.group(1))
            target_member = message.guild.get_member(user_id)
            if target_member and not target_member.bot:
                valid_request = True
        
        if not is_admin:
            if not valid_request:
                try:
                    await message.delete()
                    msg = f"{message.author.mention} Please include text with your mention to make a request."
                    await message.channel.send(msg, delete_after=5)
                except: pass
                return
        
        if valid_request and target_member:
            target = target_member
            roles = settings['roles']
            
            # Roles Config: [Role1_Open, Role2_Closed, Role3_Reactions]
            has_role_1 = any(r.id == roles[0] for r in target.roles)
            has_role_2 = any(r.id == roles[1] for r in target.roles)
            has_role_3 = any(r.id == roles[2] for r in target.roles)
            
            if has_role_1:
                # Open DMs
                msg = await message.channel.send(f"{message.author.mention}, {target.display_name} has open dms 😐")
                await msg.edit(content=f"{message.author.mention}, {target.mention} has open dms 😐")
            
            elif has_role_2:
                # Closed DMs
                msg = await message.channel.send(f"{message.author.mention}, {target.display_name} has closed dms 😐")
                await msg.edit(content=f"{message.author.mention}, {target.mention} has closed dms 😐")
            
            elif has_role_3:
                # Reactions route (we DO want to ping target here, so no display_name edit needed)
                try:
                    for e in settings['reacts']:
                        await message.add_reaction(e)
                except: pass
                
                await message.channel.send(f"{target.mention} please react to the request with your answer")
            else:
                # No roles found
                msg = await message.channel.send(f"Sorry, **{target.display_name}** doesn't have DM roles set up yet. Buggy's working on this!")
                await msg.edit(content=f"Sorry, {target.mention} doesn't have DM roles set up yet. Buggy's working on this!")

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handles DM Request parsing."""
        if not message.guild or message.author.bot:
            return
        await self.handle_dm_request(message)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles DM Request reactions."""
        if payload.user_id == self.bot.user.id: return
        if not payload.guild_id: return

        settings = self.get_dm_settings(payload.guild_id)

        if payload.channel_id not in settings['channels']: return
        if str(payload.emoji) not in settings['reacts']: return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel: return

        try:
            message = await channel.fetch_message(payload.message_id)
            if not message.mentions: return
            
            # Find the actual target safely
            target_member = None
            for user in message.mentions:
                if user.bot: continue
                target_member = user
                break
                
            if not target_member: return
            
            # Only the requested user can react to their request
            if payload.user_id != target_member.id: return 

            msg_type = -1
            if str(payload.emoji) == settings['reacts'][0]: msg_type = 0 # Accept
            elif str(payload.emoji) == settings['reacts'][1]: msg_type = 1 # Deny
            elif str(payload.emoji) == settings['reacts'][2]: msg_type = 2 # Info
            
            if msg_type != -1:
                requester = message.author
                requested_name = target_member.display_name
                
                # 1. Clear the two unclicked reactions
                for e in settings['reacts']:
                    if e != str(payload.emoji):
                        try: await message.clear_reaction(e)
                        except: pass
                
                # 2. Delete the prompt message linking to this user
                # We search the last 20 messages for the bot's prompt containing their mention
                async for past_msg in channel.history(limit=20, after=message.created_at):
                    if past_msg.author == self.bot.user and target_member.mention in past_msg.content:
                        # Ensure we're deleting the prompt specifically
                        if "please react" in past_msg.content.lower():
                            try: await past_msg.delete()
                            except: pass
                
                # 3. Process the final text
                if msg_type == 0:
                    text_initial = f"{requester.mention}, {requested_name} accepts your dm request! :D"
                    text_final = f"{requester.mention}, {target_member.mention} accepts your dm request! :D"
                elif msg_type == 1:
                    text_initial = f"{requester.mention}, {requested_name} denies your dm request. please respect their boundaries! :D"
                    text_final = f"{requester.mention}, {target_member.mention} denies your dm request. please respect their boundaries! :D"
                else:
                    text_initial = f"{requester.mention}, {requested_name} needs more info. please send another request with more detail! :D"
                    text_final = f"{requester.mention}, {target_member.mention} needs more info. please send another request with more detail! :D"
                
                # 4. Send the message with display_name, then edit to mention to prevent a double ping
                # Automatically delete after 24 hours (86400 seconds) via Discord's delete_after
                sent_msg = await channel.send(content=text_initial, delete_after=86400)
                await sent_msg.edit(content=text_final)

        except Exception as e:
            print(f"DM Req Reaction Error: {e}")

    # --- SLASH COMMANDS ---
    
    @app_commands.command(name="dmconfig", description="Configure DM Request settings (Roles & Emojis).")
    @app_commands.describe(
        role1="Role 1 (Open DMs)",
        role2="Role 2 (Closed DMs)",
        role3="Role 3 (Triggers Reactions)",
        emoji1="Emoji 1 (Accept)",
        emoji2="Emoji 2 (Deny)",
        emoji3="Emoji 3 (More Info)"
    )
    @app_commands.default_permissions(administrator=True)
    async def dmconfig(self, interaction: discord.Interaction, 
                       role1: discord.Role, role2: discord.Role, role3: discord.Role,
                       emoji1: str, emoji2: str, emoji3: str):
        
        settings = self.get_dm_settings(interaction.guild_id)
        
        settings['roles'] = [role1.id, role2.id, role3.id]
        settings['reacts'] = [emoji1, emoji2, emoji3]
        
        if 'messages' in settings: del settings['messages']
        
        self.save_dm_settings(interaction.guild_id, settings)
        
        embed = discord.Embed(title="✅ DM Request Config Updated", color=discord.Color(0xff90aa))
        embed.add_field(name="Roles", value=f"Open: {role1.mention}\nClosed: {role2.mention}\nReactions: {role3.mention}", inline=False)
        embed.add_field(name="Reactions", value=f"Accept: {emoji1}\nDeny: {emoji2}\nMore Info: {emoji3}", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="dmchannel", description="Manage channels where DM Requests are active.")
    @app_commands.describe(action="Add, Remove, or List", channel="Channel to configure")
    @app_commands.default_permissions(administrator=True)
    async def dmchannel(self, interaction: discord.Interaction, action: Literal["Add", "Remove", "List"], channel: discord.TextChannel = None):
        """Manage channels where DM Requests are active."""
        settings = self.get_dm_settings(interaction.guild_id)
        channels = settings.get('channels', [])
        
        if action == "List":
            if not channels:
                return await interaction.response.send_message("📝 No DM Request channels configured.", ephemeral=True)
            mentions = [f"<#{c_id}>" for c_id in channels]
            await interaction.response.send_message(f"**DM Request Channels:**\n" + ", ".join(mentions), ephemeral=True)
            return

        if not channel:
            return await interaction.response.send_message("❌ You must specify a channel to Add or Remove.", ephemeral=True)

        if action == "Add":
            if channel.id not in channels:
                settings['channels'].append(channel.id)
                self.save_dm_settings(interaction.guild_id, settings)
                await interaction.response.send_message(f"✅ Added {channel.mention} to DM Request channels.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {channel.mention} is already in the list.", ephemeral=True)
        
        elif action == "Remove":
            if channel.id in channels:
                settings['channels'].remove(channel.id)
                self.save_dm_settings(interaction.guild_id, settings)
                await interaction.response.send_message(f"✅ Removed {channel.mention} from DM Request channels.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {channel.mention} was not in the list.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(DMRequests(bot))
