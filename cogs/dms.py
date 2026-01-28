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
# - dmconfig(interaction, role1, role2, role3, emoji1, emoji2, message1...) [Slash]
# - dmchannel(interaction, action, channel) [Slash]
# setup(bot)

class DMRequests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "DM Request system."
        
        self.DEFAULT_DM_MESSAGES = {
            "0": "{mention} Please include text with your mention to make a request.",
            "1": "Request Accepted!",
            "2": "Request Denied.",
            "3": "DM Request (Role 2) sent to {requested}.",
            "4": "DM Request (Role 3) sent to {requested}.",
            "5": "sorry they dont have dm roles yet :sob:, buggy's working on this"
        }
        self.DEFAULT_DM_REACTS = ["👍", "👎"]

    # --- HELPERS ---

    def get_dm_settings(self, guild_id):
        """Fetches DM settings for a specific guild."""
        collection = self.bot.db.get_collection("dm_settings")
        for doc in collection:
            if doc['guild_id'] == guild_id:
                if "messages" not in doc: doc["messages"] = self.DEFAULT_DM_MESSAGES.copy()
                if "reacts" not in doc: doc["reacts"] = self.DEFAULT_DM_REACTS.copy()
                if "roles" not in doc: doc["roles"] = [0, 0, 0]
                if "channels" not in doc: doc["channels"] = []
                return doc
        
        return {
            "guild_id": guild_id,
            "channels": [],
            "roles": [0, 0, 0],
            "reacts": self.DEFAULT_DM_REACTS.copy(),
            "messages": self.DEFAULT_DM_MESSAGES.copy()
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
                    raw_msg = settings['messages'].get("0", "Error: No text.")
                    formatted_msg = raw_msg.replace("{mention}", message.author.mention).replace("{requester}", message.author.mention)
                    await message.channel.send(formatted_msg, delete_after=5)
                except: pass
                return
        
        if valid_request and target_member:
            target = target_member
            roles = settings['roles']
            
            has_role_1 = any(r.id == roles[0] for r in target.roles)
            has_role_2 = any(r.id == roles[1] for r in target.roles)
            has_role_3 = any(r.id == roles[2] for r in target.roles)
            
            raw_msg = ""
            if has_role_1:
                try:
                    for e in settings['reacts']:
                        await message.add_reaction(e)
                except: pass
            
            elif has_role_2:
                raw_msg = settings['messages'].get("3", "")
            elif has_role_3:
                raw_msg = settings['messages'].get("4", "")
            else:
                raw_msg = settings['messages'].get("5", "")
            
            if raw_msg:
                formatted_msg = raw_msg.replace("{mention}", message.author.mention)\
                                       .replace("{requester}", message.author.mention)\
                                       .replace("{requested}", f"**{target.display_name}**")\
                                       .replace("{requested_nickname}", target.display_name)
                await message.channel.send(formatted_msg)

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
            target_member = message.mentions[0] 
            
            if payload.user_id != target_member.id: return 

            msg_index = -1
            if str(payload.emoji) == settings['reacts'][0]: msg_index = "1"
            elif str(payload.emoji) == settings['reacts'][1]: msg_index = "2"
            
            if msg_index != -1:
                raw_msg = settings['messages'].get(msg_index, "")
                formatted_msg = raw_msg.replace("{mention}", message.author.mention)\
                                       .replace("{requester}", message.author.mention)\
                                       .replace("{requested}", f"**{target_member.display_name}**")\
                                       .replace("{requested_nickname}", target_member.display_name)
                
                await channel.send(formatted_msg)
                
                try:
                    for e in settings['reacts']:
                        await message.remove_reaction(e, self.bot.user)
                except: pass

        except Exception as e:
            print(f"DM Req Reaction Error: {e}")

    # --- SLASH COMMANDS ---
    
    @app_commands.command(name="dmconfig", description="Configure DM Request settings (Roles, Emojis, Messages).")
    @app_commands.describe(
        role1="Role 1 (Triggers Reactions)",
        role2="Role 2 (Triggers Message 3)",
        role3="Role 3 (Triggers Message 4)",
        emoji1="Emoji 1 (For Role 1 reaction)",
        emoji2="Emoji 2 (For Role 1 reaction)",
        message1="Sent when user clicks Emoji 1",
        message2="Sent when user clicks Emoji 2",
        message3="Sent if user has Role 2",
        message4="Sent if user has Role 3",
        message5="Sent if user has NO roles (Fallback)"
    )
    @app_commands.default_permissions(administrator=True)
    async def dmconfig(self, interaction: discord.Interaction, 
                       role1: discord.Role, role2: discord.Role, role3: discord.Role,
                       emoji1: str, emoji2: str,
                       message1: str, message2: str, message3: str, message4: str, message5: str):
        
        settings = self.get_dm_settings(interaction.guild_id)
        
        settings['roles'] = [role1.id, role2.id, role3.id]
        settings['reacts'] = [emoji1, emoji2]
        settings['messages']['1'] = message1
        settings['messages']['2'] = message2
        settings['messages']['3'] = message3
        settings['messages']['4'] = message4
        settings['messages']['5'] = message5
        
        self.save_dm_settings(interaction.guild_id, settings)
        
        embed = discord.Embed(title="✅ DM Request Config Updated", color=discord.Color(0xff90aa))
        embed.add_field(name="Roles", value=f"1: {role1.mention}\n2: {role2.mention}\n3: {role3.mention}", inline=False)
        embed.add_field(name="Reactions", value=f"1: {emoji1} -> {message1}\n2: {emoji2} -> {message2}", inline=False)
        embed.add_field(name="Auto Responses", value=f"Role 2: {message3}\nRole 3: {message4}\nNone: {message5}", inline=False)
        
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
