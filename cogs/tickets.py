import discord
from discord import app_commands
from discord.ext import commands
import asyncio

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- HELPERS ---
    def get_setup(self, role_id):
        """Finds a ticket setup by the trigger role ID."""
        setups = self.bot.db.get_collection("ticket_setups")
        for setup in setups:
            if setup['role_id'] == role_id:
                return setup
        return None

    def get_active_ticket(self, channel_id):
        """Finds an active ticket by channel ID."""
        tickets = self.bot.db.get_collection("active_tickets")
        for t in tickets:
            if t['channel_id'] == channel_id:
                return t
        return None
    
    def find_ticket_by_user_and_role(self, user_id, role_id):
        """Finds a ticket for a specific user and setup role."""
        tickets = self.bot.db.get_collection("active_tickets")
        for t in tickets:
            if t['user_id'] == user_id and t['setup_role_id'] == role_id:
                return t
        return None

    async def save_active_ticket(self, data):
        """Saves a new active ticket to the DB."""
        tickets = self.bot.db.get_collection("active_tickets")
        # Remove existing for this channel if any (cleanup)
        tickets = [t for t in tickets if t['channel_id'] != data['channel_id']]
        tickets.append(data)
        self.bot.db.save_collection("active_tickets", tickets)

    async def delete_active_ticket(self, channel_id):
        """Removes a ticket from the DB."""
        tickets = self.bot.db.get_collection("active_tickets")
        tickets = [t for t in tickets if t['channel_id'] != channel_id]
        self.bot.db.save_collection("active_tickets", tickets)

    # --- COMMANDS ---
    
    ticket_group = app_commands.Group(name="ticket", description="Manage the ticket system")

    @ticket_group.command(name="add", description="Create a new ticket setup linked to a role.")
    @app_commands.describe(
        role="The role that triggers the ticket creation",
        ticket_name="Name of the channel (use {user})",
        prompt="Message sent in ticket (use {user} and {admin})",
        admin="Optional: Admin role for this ticket",
        message_id="Optional: Message ID for reaction verification",
        emoji="Optional: Emoji for reaction verification",
        access="Optional: Role to give when ticket is accepted"
    )
    async def add(self, interaction: discord.Interaction, 
                  role: discord.Role, 
                  ticket_name: str, 
                  prompt: str, 
                  admin: discord.Role = None, 
                  message_id: str = None, 
                  emoji: str = None, 
                  access: discord.Role = None):
        
        # Validation: message_id and emoji must appear together or not at all
        if (message_id and not emoji) or (emoji and not message_id):
            return await interaction.response.send_message("❌ Error: `message_id` and `emoji` must be provided together or not at all.", ephemeral=True)

        existing = self.get_setup(role.id)
        if existing:
            return await interaction.response.send_message(f"❌ Error: A setup for {role.mention} already exists. Use `/ticket edit` instead.", ephemeral=True)

        new_setup = {
            "role_id": role.id,
            "ticket_name": ticket_name,
            "prompt": prompt,
            "admin_role_id": admin.id if admin else None,
            "gate_message_id": int(message_id) if message_id else None,
            "gate_emoji": emoji,
            "access_role_id": access.id if access else None
        }

        self.bot.db.update_doc("ticket_setups", "role_id", role.id, new_setup)
        await interaction.response.send_message(f"✅ Ticket setup added! Assigning {role.mention} will now trigger a ticket.", ephemeral=True)

    @ticket_group.command(name="edit", description="Edit an existing ticket setup.")
    async def edit(self, interaction: discord.Interaction, 
                   role: discord.Role, 
                   ticket_name: str = None, 
                   prompt: str = None, 
                   admin: discord.Role = None, 
                   message_id: str = None, 
                   emoji: str = None, 
                   access: discord.Role = None):
        
        setup = self.get_setup(role.id)
        if not setup:
            return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)

        # Update fields if provided
        if ticket_name: setup['ticket_name'] = ticket_name
        if prompt: setup['prompt'] = prompt
        if admin: setup['admin_role_id'] = admin.id
        
        # Handle the gate logic update carefully
        if message_id is not None or emoji is not None:
             if (message_id and not emoji) or (emoji and not message_id):
                 return await interaction.response.send_message("❌ Error: To update the gate, provide both `message_id` and `emoji`.", ephemeral=True)
             setup['gate_message_id'] = int(message_id)
             setup['gate_emoji'] = emoji
        
        if access: setup['access_role_id'] = access.id

        self.bot.db.update_doc("ticket_setups", "role_id", role.id, setup)
        await interaction.response.send_message(f"✅ Updated ticket setup for {role.mention}.", ephemeral=True)

    @ticket_group.command(name="remove", description="Remove a ticket setup.")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        setup = self.get_setup(role.id)
        if not setup:
            return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)
        
        setups = self.bot.db.get_collection("ticket_setups")
        setups = [s for s in setups if s['role_id'] != role.id]
        self.bot.db.save_collection("ticket_setups", setups)
        
        await interaction.response.send_message(f"✅ Removed ticket setup for {role.mention}.", ephemeral=True)

    @ticket_group.command(name="list", description="List all ticket setups.")
    async def list_setups(self, interaction: discord.Interaction):
        setups = self.bot.db.get_collection("ticket_setups")
        if not setups:
            return await interaction.response.send_message("📝 No ticket setups found.", ephemeral=True)
        
        text = "**🎫 Ticket Setups:**\n"
        for s in setups:
            role_ping = f"<@&{s['role_id']}>"
            admin_ping = f"<@&{s['admin_role_id']}>" if s['admin_role_id'] else "None"
            gate = "Yes" if s['gate_message_id'] else "No"
            text += f"• **Role:** {role_ping} | **Admin:** {admin_ping} | **Gate:** {gate}\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    @ticket_group.command(name="close", description="Close the current ticket.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Accept (Give Access Role)", value="accept"),
        app_commands.Choice(name="Deny (Just Close)", value="deny")
    ])
    async def close(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        # 1. Check if we are in a ticket
        ticket_data = self.get_active_ticket(interaction.channel.id)
        if not ticket_data:
            return await interaction.response.send_message("❌ This command can only be used in an active ticket channel.", ephemeral=True)

        user_id = ticket_data['user_id']
        setup_role_id = ticket_data['setup_role_id']
        
        member = interaction.guild.get_member(user_id)
        if not member:
            # Try to fetch if not cached
            try: member = await interaction.guild.fetch_member(user_id)
            except: member = None

        await interaction.response.send_message("🔒 Closing ticket...")

        # 2. Logic: Remove Trigger Role
        trigger_role = interaction.guild.get_role(setup_role_id)
        if member and trigger_role:
            try: await member.remove_roles(trigger_role)
            except Exception as e: print(f"Failed to remove trigger role: {e}")

        # 3. Logic: Handle Accept (Give Access Role)
        if action.value == "accept" and member:
            setup = self.get_setup(setup_role_id)
            if setup and setup['access_role_id']:
                access_role = interaction.guild.get_role(setup['access_role_id'])
                if access_role:
                    try: await member.add_roles(access_role)
                    except Exception as e: print(f"Failed to add access role: {e}")

        # 4. Clean DB and Delete Channel
        await self.delete_active_ticket(interaction.channel.id)
        await asyncio.sleep(2)
        await interaction.channel.delete()

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Watches for the Trigger Role being added to a user."""
        if before.roles == after.roles:
            return

        # Check for new roles
        new_roles = set(after.roles) - set(before.roles)
        for role in new_roles:
            setup = self.get_setup(role.id)
            if setup:
                await self.create_ticket(after, setup)

    async def create_ticket(self, member, setup):
        guild = member.guild
        
        # Check if they already have a ticket for this setup
        existing = self.find_ticket_by_user_and_role(member.id, setup['role_id'])
        if existing:
            # Optionally ping them in the existing one, but mostly we ignore
            return

        # 1. Format Name
        raw_name = setup['ticket_name'].replace("{user}", member.name).lower()
        # Clean special chars roughly
        chan_name = "".join(c for c in raw_name if c.isalnum() or c in "-_")

        # 2. Permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Admin Perms
        admin_role = guild.get_role(setup['admin_role_id']) if setup['admin_role_id'] else None
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # User Perms (Check Gate)
        has_gate = setup['gate_message_id'] is not None
        
        # If gate exists, Read ONLY. If no gate, Read AND Write.
        overwrites[member] = discord.PermissionOverwrite(
            read_messages=True, 
            send_messages=not has_gate
        )

        # 3. Create Channel
        try:
            channel = await guild.create_text_channel(chan_name, overwrites=overwrites)
        except Exception as e:
            print(f"Failed to create ticket channel: {e}")
            return

        # 4. Send Prompt
        prompt_text = setup['prompt']\
            .replace("{user}", member.mention)\
            .replace("{admin}", admin_role.mention if admin_role else "")
        
        await channel.send(prompt_text)

        # 5. Save Active Ticket
        ticket_data = {
            "channel_id": channel.id,
            "user_id": member.id,
            "setup_role_id": setup['role_id'],
            "is_gated": has_gate
        }
        await self.save_active_ticket(ticket_data)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles the Write Access logic (Reaction Gate)."""
        if payload.user_id == self.bot.user.id: return

        # Check if this reaction matches any setup's gate
        setups = self.bot.db.get_collection("ticket_setups")
        matched_setup = None
        
        for s in setups:
            if s['gate_message_id'] == payload.message_id and str(payload.emoji) == s['gate_emoji']:
                matched_setup = s
                break
        
        if matched_setup:
            # User reacted to the gate. Find their ticket for this setup.
            ticket_data = self.find_ticket_by_user_and_role(payload.user_id, matched_setup['role_id'])
            
            if ticket_data and ticket_data.get('is_gated'):
                guild = self.bot.get_guild(payload.guild_id)
                channel = guild.get_channel(ticket_data['channel_id'])
                member = guild.get_member(payload.user_id)
                
                if channel and member:
                    # 1. Grant Write Access
                    await channel.set_permissions(member, read_messages=True, send_messages=True)
                    
                    # 2. Update DB (Gate passed)
                    ticket_data['is_gated'] = False
                    await self.save_active_ticket(ticket_data) # Update state
                    
                    await channel.send(f"🔓 **Access Granted:** {member.mention} has verified and can now speak.")

            # 3. Remove Reaction (so others don't see it piling up)
            # We need to fetch the channel where the reaction happened
            gate_channel = self.bot.get_channel(payload.channel_id)
            if gate_channel:
                try:
                    message = await gate_channel.fetch_message(payload.message_id)
                    member = guild.get_member(payload.user_id)
                    if member:
                        await message.remove_reaction(payload.emoji, member)
                except Exception as e:
                    print(f"Failed to remove reaction: {e}")

async def setup(bot):
    await bot.add_cog(Tickets(bot))
