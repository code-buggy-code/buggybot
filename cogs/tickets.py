import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# Function/Class List:
# class Tickets(commands.Cog)
# - __init__(bot)
# - get_setup(role_id)
# - get_active_ticket(channel_id)
# - find_ticket_by_user_and_role(user_id, role_id)
# - save_active_ticket(data)
# - delete_active_ticket(channel_id)
# - ticket (Group) [Slash]
#   - setup(interaction, role, ticket_name, prompt, category, admin, message_id, emoji, access, demessage_id)
#   - edit(interaction, role, ticket_name, prompt, category, admin, message_id, emoji, access, demessage_id)
#   - remove(interaction, role)
#   - list(interaction)
#   - close(interaction, accept)
# - on_member_update(before, after)
# - create_ticket(member, setup)
# - on_raw_reaction_add(payload)
# setup(bot)

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Ticket system configuration and management."

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

    # --- SLASH COMMANDS ---
    
    ticket = app_commands.Group(name="ticket", description="Manage the ticket system", default_permissions=discord.Permissions(administrator=True))

    @ticket.command(name="setup", description="Create a new ticket setup linked to a role.")
    @app_commands.describe(
        role="The role that triggers the ticket creation",
        ticket_name="Name of the channel (use {user})",
        prompt="Message sent in ticket (use {user}, {admin}, \\n for line)",
        category="Optional: Category to create tickets in",
        admin="Optional: buggy role for this ticket",
        message_id="Optional: Gate Message ID for reaction verification",
        emoji="Optional: Gate Emoji for reaction verification",
        access="Optional: Role to give when ticket is accepted",
        demessage_id="Optional: Message ID to remove access role on ANY reaction"
    )
    async def ticket_setup(self, interaction: discord.Interaction, 
                  role: discord.Role, 
                  ticket_name: str, 
                  prompt: str,
                  category: discord.CategoryChannel = None,
                  admin: discord.Role = None, 
                  message_id: str = None, 
                  emoji: str = None, 
                  access: discord.Role = None,
                  demessage_id: str = None):
        
        # Validation: message_id and emoji must appear together or not at all (Gate)
        if (message_id and not emoji) or (emoji and not message_id):
            return await interaction.response.send_message("❌ Error: `message_id` and `emoji` (Gate) must be provided together or not at all.", ephemeral=True)

        existing = self.get_setup(role.id)
        if existing:
            return await interaction.response.send_message(f"❌ Error: A setup for {role.mention} already exists. Use `/ticket edit` instead.", ephemeral=True)

        new_setup = {
            "guild_id": interaction.guild.id, 
            "role_id": role.id,
            "ticket_name": ticket_name,
            "prompt": prompt,
            "category_id": category.id if category else None,
            "admin_role_id": admin.id if admin else None,
            "gate_message_id": int(message_id) if message_id else None,
            "gate_emoji": emoji,
            "access_role_id": access.id if access else None,
            "demessage_id": int(demessage_id) if demessage_id else None
        }

        self.bot.db.update_doc("ticket_setups", "role_id", role.id, new_setup)
        await interaction.response.send_message(f"✅ Ticket setup added! Assigning {role.mention} will now trigger a ticket.", ephemeral=True)

    @ticket.command(name="edit", description="Edit an existing ticket setup.")
    @app_commands.describe(
        role="The role to edit setup for",
        ticket_name="Name of the channel (use {user})",
        prompt="Message (use {user}, {admin}, \\n for line)",
        category="Category to create tickets in",
        admin="buggy role for this ticket",
        message_id="Gate Message ID",
        emoji="Gate Emoji",
        access="Role to give on accept",
        demessage_id="Message ID for removal"
    )
    async def ticket_edit(self, interaction: discord.Interaction, 
                   role: discord.Role, 
                   ticket_name: str = None, 
                   prompt: str = None, 
                   category: discord.CategoryChannel = None, 
                   admin: discord.Role = None, 
                   message_id: str = None, 
                   emoji: str = None, 
                   access: discord.Role = None,
                   demessage_id: str = None):
        
        setup = self.get_setup(role.id)
        if not setup:
            return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)

        # Update fields if provided
        if ticket_name: setup['ticket_name'] = ticket_name
        if prompt: setup['prompt'] = prompt
        if category: setup['category_id'] = category.id
        if admin: setup['admin_role_id'] = admin.id
        
        # Handle the gate logic update
        if message_id is not None or emoji is not None:
             if (message_id and not emoji) or (emoji and not message_id):
                 return await interaction.response.send_message("❌ Error: To update the gate, provide both `message_id` and `emoji`.", ephemeral=True)
             setup['gate_message_id'] = int(message_id)
             setup['gate_emoji'] = emoji
        
        # Handle the demessage logic update
        if demessage_id is not None:
             setup['demessage_id'] = int(demessage_id)
        
        if access: setup['access_role_id'] = access.id

        self.bot.db.update_doc("ticket_setups", "role_id", role.id, setup)
        await interaction.response.send_message(f"✅ Updated ticket setup for {role.mention}.", ephemeral=True)

    @ticket.command(name="remove", description="Remove a ticket setup.")
    async def ticket_remove(self, interaction: discord.Interaction, role: discord.Role):
        setup = self.get_setup(role.id)
        if not setup:
            return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)
        
        self.bot.db.delete_doc("ticket_setups", "role_id", role.id)
        await interaction.response.send_message(f"✅ Removed ticket setup for {role.mention}.", ephemeral=True)

    @ticket.command(name="list", description="List all ticket setups.")
    async def ticket_list(self, interaction: discord.Interaction):
        setups = self.bot.db.get_collection("ticket_setups")
        
        # Filter for current guild
        current_guild_roles = {r.id for r in interaction.guild.roles}
        filtered_setups = [s for s in setups if s['role_id'] in current_guild_roles]

        if not filtered_setups:
            return await interaction.response.send_message("📝 No ticket setups found for this server.", ephemeral=True)
        
        text = "**🎫 Ticket Setups:**\n"
        for s in filtered_setups:
            role_ping = f"<@&{s['role_id']}>"
            admin_ping = f"<@&{s['admin_role_id']}>" if s['admin_role_id'] else "None"
            cat_ping = f"<#{s.get('category_id')}>" if s.get('category_id') else "None"
            gate = "Yes" if s.get('gate_message_id') else "No"
            demessage = "Yes" if s.get('demessage_id') else "No"
            text += f"• **Role:** {role_ping} | **buggy:** {admin_ping} | **Cat:** {cat_ping} | **Gate:** {gate} | **DeMsg:** {demessage}\n"
        
        await interaction.response.send_message(text, ephemeral=True)

    @ticket.command(name="close", description="Close the ticket. Optional: Set accept to True to grant access role.")
    @app_commands.describe(accept="If True, grant the access role (Accept). If False/Empty, just delete (Deny).")
    async def ticket_close(self, interaction: discord.Interaction, accept: bool = False):
        # 1. Check if we are in a ticket
        ticket_data = self.get_active_ticket(interaction.channel.id)
        if not ticket_data:
            return await interaction.response.send_message("❌ This command can only be used in an active ticket channel.", ephemeral=True)

        user_id = ticket_data['user_id']
        setup_role_id = ticket_data['setup_role_id']
        
        member = interaction.guild.get_member(user_id)
        if not member:
            try: member = await interaction.guild.fetch_member(user_id)
            except: member = None

        await interaction.response.send_message("🔒 Closing ticket...")

        # 2. Logic: Remove Trigger Role
        trigger_role = interaction.guild.get_role(setup_role_id)
        if member and trigger_role:
            try: await member.remove_roles(trigger_role)
            except Exception as e: print(f"Failed to remove trigger role: {e}")

        # 3. Logic: Handle Accept (Give Access Role)
        if accept and member:
            setup = self.get_setup(setup_role_id)
            if setup and setup.get('access_role_id'):
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
            # Buggy requested: If ticket exists, clean it up and start fresh.
            # This handles phantom tickets where the channel was deleted manually.
            try:
                # Try to find and delete the old channel
                old_channel = guild.get_channel(existing['channel_id'])
                if old_channel:
                    await old_channel.delete()
            except Exception as e:
                print(f"Cleanup error (ignorable): {e}")

            # Always remove the old DB entry
            await self.delete_active_ticket(existing['channel_id'])

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

        # 3. Determine Category
        category = None
        if setup.get('category_id'):
            category = guild.get_channel(setup['category_id'])

        # 4. Create Channel
        try:
            channel = await guild.create_text_channel(chan_name, overwrites=overwrites, category=category)
        except Exception as e:
            print(f"Failed to create ticket channel: {e}")
            return

        # 5. Send Prompt
        # Added .replace("\\n", "\n") so users can type literal \n in the slash command to get a new line
        prompt_text = setup['prompt']\
            .replace("{user}", member.mention)\
            .replace("{admin}", admin_role.mention if admin_role else "")\
            .replace("\\n", "\n")
        
        await channel.send(prompt_text)

        # 6. Save Active Ticket
        ticket_data = {
            "channel_id": channel.id,
            "guild_id": guild.id, # ADDED Guild ID
            "user_id": member.id,
            "setup_role_id": setup['role_id'],
            "is_gated": has_gate
        }
        await self.save_active_ticket(ticket_data)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handles the Gate (Write Access) and Demessage (Remove Access) logic."""
        if payload.user_id == self.bot.user.id: return

        setups = self.bot.db.get_collection("ticket_setups")
        
        # --- 1. Gate Logic (Unlock Ticket) ---
        matched_gate = None
        for s in setups:
            if s.get('gate_message_id') == payload.message_id and str(payload.emoji) == s.get('gate_emoji'):
                matched_gate = s
                break
        
        if matched_gate:
            # User reacted to the gate. Find their ticket for this setup.
            ticket_data = self.find_ticket_by_user_and_role(payload.user_id, matched_gate['role_id'])
            
            if ticket_data and ticket_data.get('is_gated'):
                guild = self.bot.get_guild(payload.guild_id)
                channel = guild.get_channel(ticket_data['channel_id'])
                member = guild.get_member(payload.user_id)
                
                if channel and member:
                    # Grant Write Access
                    await channel.set_permissions(member, read_messages=True, send_messages=True)
                    
                    # Update DB (Gate passed)
                    ticket_data['is_gated'] = False
                    await self.save_active_ticket(ticket_data)
                    
                    await channel.send(f"🔓 **Access Granted:** {member.mention} has verified and can now speak.")

            # Remove Reaction from Gate
            gate_channel = self.bot.get_channel(payload.channel_id)
            if gate_channel:
                try:
                    message = await gate_channel.fetch_message(payload.message_id)
                    member = self.bot.get_guild(payload.guild_id).get_member(payload.user_id)
                    if member:
                        await message.remove_reaction(payload.emoji, member)
                except Exception as e:
                    print(f"Failed to remove gate reaction: {e}")
        
        # --- 2. Demessage Logic (Remove Access Role) ---
        matched_demessage = None
        for s in setups:
            # Only match Message ID, any emoji triggers it
            if s.get('demessage_id') == payload.message_id:
                matched_demessage = s
                break
        
        if matched_demessage:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild: return
            
            member = guild.get_member(payload.user_id)
            if not member: return

            role_id = matched_demessage.get('access_role_id')
            if role_id:
                role = guild.get_role(role_id)
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role)
                    except Exception as e:
                        print(f"Failed to remove access role via demessage: {e}")

            # Remove Reaction from Demessage
            try:
                channel = self.bot.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, member)
            except: pass

async def setup(bot):
    await bot.add_cog(Tickets(bot))
