import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from typing import Literal

# Function/Class List:
# class Tickets(commands.Cog)
# - __init__(bot)
# - get_setup(role_id)
# - get_active_ticket(channel_id)
# - find_ticket_by_user_and_role(user_id, role_id)
# - save_active_ticket(data)
# - delete_active_ticket(channel_id)
# - ticket(interaction, action, role, ticket_name, prompt, category, admin, message_id, emoji, access, demessage_id) [Slash]
# - close(interaction) [Slash - Top Level]
# - accept(interaction) [Slash - Top Level]
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
    
    @app_commands.command(name="ticket", description="Manage the ticket system.")
    @app_commands.describe(
        action="What would you like to do?",
        role="The role for the setup (Required for add/edit/remove)",
        ticket_name="Name of the channel (use {user})",
        prompt="Message sent in ticket (use {user}, {admin}, \\n for line)",
        category="Optional: Category to create tickets in",
        admin="Optional: buggy role for this ticket",
        message_id="Optional: Gate Message ID for reaction verification",
        emoji="Optional: Gate Emoji for reaction verification",
        access="Optional: Role to give when ticket is accepted",
        demessage_id="Optional: Message ID to remove access role on ANY reaction"
    )
    @app_commands.default_permissions(administrator=True)
    async def ticket(self, interaction: discord.Interaction, 
                  action: Literal["add", "edit", "remove", "list"],
                  role: discord.Role = None, 
                  ticket_name: str = None, 
                  prompt: str = None,
                  category: discord.CategoryChannel = None,
                  admin: discord.Role = None, 
                  message_id: str = None, 
                  emoji: str = None, 
                  access: discord.Role = None,
                  demessage_id: str = None):
        
        # --- LIST ---
        if action == "list":
            setups = self.bot.db.get_collection("ticket_setups")
            current_guild_roles = {r.id for r in interaction.guild.roles}
            filtered_setups = [s for s in setups if s['role_id'] in current_guild_roles]

            if not filtered_setups:
                return await interaction.response.send_message("📝 No ticket setups found for this server.", ephemeral=True)
            
            embed = discord.Embed(title="🎫 Ticket Setups", color=discord.Color.blue())
            
            for s in filtered_setups:
                # Resolve objects to names/mentions for clarity
                trigger_role = interaction.guild.get_role(s['role_id'])
                t_role_name = trigger_role.name if trigger_role else f"ID: {s['role_id']}"
                
                admin_role = interaction.guild.get_role(s['admin_role_id']) if s.get('admin_role_id') else None
                admin_text = admin_role.mention if admin_role else "None"
                
                cat_obj = interaction.guild.get_channel(s['category_id']) if s.get('category_id') else None
                cat_text = cat_obj.name if cat_obj else "None"
                
                acc_role = interaction.guild.get_role(s['access_role_id']) if s.get('access_role_id') else None
                acc_text = acc_role.mention if acc_role else "None"
                
                gate_info = f"Msg: {s.get('gate_message_id')}\nEmoji: {s.get('gate_emoji')}" if s.get('gate_message_id') else "None"
                demsg_info = f"Msg: {s.get('demessage_id')}" if s.get('demessage_id') else "None"
                
                # Show raw prompt so user can see if {user} is there
                raw_prompt = s.get('prompt', 'No prompt set')
                if len(raw_prompt) > 100:
                    raw_prompt = raw_prompt[:97] + "..."
                
                info_block = (
                    f"**Ticket Name:** `{s.get('ticket_name')}`\n"
                    f"**Category:** {cat_text}\n"
                    f"**Admin Role:** {admin_text}\n"
                    f"**Access Role:** {acc_text}\n"
                    f"**Gate:** {gate_info}\n"
                    f"**De-Msg:** {demsg_info}\n"
                    f"**Prompt:**\n`{raw_prompt}`"
                )
                
                embed.add_field(name=f"Trigger: {t_role_name}", value=info_block, inline=False)
            
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # For Add/Edit/Remove, Role is required
        if not role:
            return await interaction.response.send_message("❌ Error: You must specify a `role` for this action.", ephemeral=True)

        # --- ADD ---
        if action == "add":
            if not ticket_name or not prompt:
                return await interaction.response.send_message("❌ Error: `ticket_name` and `prompt` are required for adding a setup.", ephemeral=True)

            if (message_id and not emoji) or (emoji and not message_id):
                return await interaction.response.send_message("❌ Error: `message_id` and `emoji` (Gate) must be provided together or not at all.", ephemeral=True)

            existing = self.get_setup(role.id)
            if existing:
                return await interaction.response.send_message(f"❌ Error: A setup for {role.mention} already exists. Use `action: edit` instead.", ephemeral=True)

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
            return await interaction.response.send_message(f"✅ Ticket setup added! Assigning {role.mention} will now trigger a ticket.", ephemeral=True)

        # --- EDIT ---
        elif action == "edit":
            setup = self.get_setup(role.id)
            if not setup:
                return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)

            if ticket_name: setup['ticket_name'] = ticket_name
            if prompt: setup['prompt'] = prompt
            if category: setup['category_id'] = category.id
            if admin: setup['admin_role_id'] = admin.id
            
            if message_id is not None or emoji is not None:
                if (message_id and not emoji) or (emoji and not message_id):
                    return await interaction.response.send_message("❌ Error: To update the gate, provide both `message_id` and `emoji`.", ephemeral=True)
                setup['gate_message_id'] = int(message_id)
                setup['gate_emoji'] = emoji
            
            if demessage_id is not None:
                setup['demessage_id'] = int(demessage_id)
            
            if access: setup['access_role_id'] = access.id

            self.bot.db.update_doc("ticket_setups", "role_id", role.id, setup)
            return await interaction.response.send_message(f"✅ Updated ticket setup for {role.mention}.", ephemeral=True)

        # --- REMOVE ---
        elif action == "remove":
            setup = self.get_setup(role.id)
            if not setup:
                return await interaction.response.send_message(f"❌ Error: No setup found for {role.mention}.", ephemeral=True)
            
            self.bot.db.delete_doc("ticket_setups", "role_id", role.id)
            return await interaction.response.send_message(f"✅ Removed ticket setup for {role.mention}.", ephemeral=True)

    # --- TOP LEVEL COMMANDS ---

    @app_commands.command(name="close", description="Close and delete this ticket.")
    async def close(self, interaction: discord.Interaction):
        """Closes the ticket (Deny/Finish)."""
        ticket_data = self.get_active_ticket(interaction.channel.id)
        if not ticket_data:
            return await interaction.response.send_message("❌ This command can only be used in an active ticket channel.", ephemeral=True)

        user_id = ticket_data['user_id']
        setup_role_id = ticket_data['setup_role_id']
        
        member = interaction.guild.get_member(user_id)
        # Fetch if missing (e.g. they left) to try and clean roles if they returned or bot cache is stale
        if not member:
            try: member = await interaction.guild.fetch_member(user_id)
            except: member = None

        await interaction.response.send_message("🔒 Closing ticket...")

        # Remove Trigger Role (Cleanup)
        trigger_role = interaction.guild.get_role(setup_role_id)
        if member and trigger_role:
            try: await member.remove_roles(trigger_role)
            except Exception as e: print(f"Failed to remove trigger role: {e}")

        # Clean DB and Delete Channel
        await self.delete_active_ticket(interaction.channel.id)
        await asyncio.sleep(2)
        await interaction.channel.delete()

    @app_commands.command(name="accept", description="Accept the ticket, grant the role, and close.")
    async def accept(self, interaction: discord.Interaction):
        """Accepts the ticket, grants access role, and closes."""
        ticket_data = self.get_active_ticket(interaction.channel.id)
        if not ticket_data:
            return await interaction.response.send_message("❌ This command can only be used in an active ticket channel.", ephemeral=True)

        user_id = ticket_data['user_id']
        setup_role_id = ticket_data['setup_role_id']
        
        member = interaction.guild.get_member(user_id)
        if not member:
            try: member = await interaction.guild.fetch_member(user_id)
            except: member = None

        await interaction.response.send_message("✅ **Accepted!** Granting role and closing...")

        if member:
            # 1. Grant Access Role
            setup = self.get_setup(setup_role_id)
            if setup and setup.get('access_role_id'):
                access_role = interaction.guild.get_role(setup['access_role_id'])
                if access_role:
                    try: await member.add_roles(access_role)
                    except Exception as e: print(f"Failed to add access role: {e}")

            # 2. Remove Trigger Role (Cleanup)
            trigger_role = interaction.guild.get_role(setup_role_id)
            if trigger_role:
                try: await member.remove_roles(trigger_role)
                except Exception as e: print(f"Failed to remove trigger role: {e}")

        # 3. Clean DB and Delete Channel
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
        # Ensure we use the current member's mention to avoid "stuck" pings from user error
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
