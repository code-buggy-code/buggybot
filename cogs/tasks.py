import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button

# List of functions/classes in this file:
# class TaskView(View):
#   - __init__(self, cog, user_id, total, current)
#   - update_bar(self)
#   - interaction_check(self, interaction)
#   - update_message(self, interaction)
#   - add_one(self, interaction, button)
#   - add_five(self, interaction, button)
#   - remove_one(self, interaction, button)
#   - remove_five(self, interaction, button)
#   - complete(self, interaction, button)
# class Tasks(commands.Cog, name="tasks"):
#   - __init__(self, bot)
#   - taskchannel(self, interaction)
#   - tasks(self, interaction, number: int)
#   - progress(self, interaction)
#   - setup(bot)

class TaskView(View):
    def __init__(self, cog, user_id, total, current):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.total = total
        self.current = current

    def update_bar(self):
        if self.total <= 0:
            percentage = 100
        else:
            percentage = (self.current / self.total) * 100
        
        # Clamp percentage between 0 and 100
        percentage = max(0, min(100, percentage))
        
        # Create the bar string (20 chars long)
        progress = int(percentage / 5)
        bar = "█" * progress + "░" * (20 - progress)
        
        return f"**Tasks:** {self.current}/{self.total}\n`[{bar}]` {int(percentage)}%"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your progress bar, silly!", ephemeral=True)
            return False
        return True

    async def update_message(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Task Progress",
            description=self.update_bar(),
            color=discord.Color.blue()
        )
        # Update the stored data so the next /progress knows the new current
        if self.user_id in self.cog.user_data:
            self.cog.user_data[self.user_id]['current'] = self.current
            
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="+1", style=discord.ButtonStyle.blurple)
    async def add_one(self, interaction: discord.Interaction, button: Button):
        if self.current < self.total:
            self.current += 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="+5", style=discord.ButtonStyle.blurple)
    async def add_five(self, interaction: discord.Interaction, button: Button):
        if self.current + 5 <= self.total:
            self.current += 5
        else:
            self.current = self.total
        await self.update_message(interaction)

    @discord.ui.button(label="-1", style=discord.ButtonStyle.gray)
    async def remove_one(self, interaction: discord.Interaction, button: Button):
        if self.current > 0:
            self.current -= 1
            await self.update_message(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="-5", style=discord.ButtonStyle.gray)
    async def remove_five(self, interaction: discord.Interaction, button: Button):
        if self.current - 5 >= 0:
            self.current -= 5
        else:
            self.current = 0
        await self.update_message(interaction)
        
    @discord.ui.button(label="Complete", style=discord.ButtonStyle.green)
    async def complete(self, interaction: discord.Interaction, button: Button):
        self.current = self.total
        # Disable all buttons since it's complete
        for child in self.children:
            child.disabled = True
        
        embed = discord.Embed(
            title="Task Progress",
            description=self.update_bar() + "\n\n**All tasks completed! Good job!**",
            color=discord.Color.green()
        )
        
        if self.user_id in self.cog.user_data:
            self.cog.user_data[self.user_id]['current'] = self.total
            
        await interaction.response.edit_message(embed=embed, view=self)

class Tasks(commands.Cog, name="tasks"):
    """ """
    def __init__(self, bot):
        self.bot = bot
        # Structure: {user_id: {'total': int, 'current': int, 'last_msg': (channel_id, message_id)}}
        self.user_data = {}
        self.task_channel_id = None

    @app_commands.command(name="taskchannel", description="Sets the current channel as the only channel for task commands (Owner Only).")
    async def taskchannel(self, interaction: discord.Interaction):
        # Check if user is owner
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only my owner can use this command!", ephemeral=True)
            return

        self.task_channel_id = interaction.channel_id
        await interaction.response.send_message(f"Task commands are now restricted to this channel: {interaction.channel.mention}")

    @app_commands.command(name="tasks", description="Sets up how many tasks you have.")
    async def tasks(self, interaction: discord.Interaction, number: int):
        # Check restriction
        if self.task_channel_id and interaction.channel_id != self.task_channel_id:
            await interaction.response.send_message(f"Please use <#{self.task_channel_id}> for task commands!", ephemeral=True)
            return

        if number <= 0:
            await interaction.response.send_message("You need to have at least 1 task, buggy!", ephemeral=True)
            return

        # Handle previous progress bar if it exists
        if interaction.user.id in self.user_data:
            old_data = self.user_data[interaction.user.id]
            if 'last_msg' in old_data:
                try:
                    chan_id, msg_id = old_data['last_msg']
                    channel = self.bot.get_channel(chan_id)
                    if channel:
                        msg = await channel.fetch_message(msg_id)
                        # Remove buttons from the old message
                        await msg.edit(view=None)
                except:
                    pass # Message might be deleted, that's okay

        # Initialize new data
        self.user_data[interaction.user.id] = {
            'total': number,
            'current': 0
        }

        await interaction.response.send_message(f"I've set your tasks to {number}! Run `/progress` to see your bar.")

    @app_commands.command(name="progress", description="Shows your progress bar and buttons.")
    async def progress(self, interaction: discord.Interaction):
        # Check restriction
        if self.task_channel_id and interaction.channel_id != self.task_channel_id:
            await interaction.response.send_message(f"Please use <#{self.task_channel_id}> for task commands!", ephemeral=True)
            return

        if interaction.user.id not in self.user_data:
            await interaction.response.send_message("You haven't set up any tasks yet! Use `/tasks [number]` first.", ephemeral=True)
            return

        data = self.user_data[interaction.user.id]
        
        # If there's an old message active, remove its buttons first
        if 'last_msg' in data:
            try:
                chan_id, msg_id = data['last_msg']
                channel = self.bot.get_channel(chan_id)
                if channel:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(view=None)
            except:
                pass 

        # Create new view and embed
        view = TaskView(self, interaction.user.id, data['total'], data['current'])
        
        embed = discord.Embed(
            title="Task Progress",
            description=view.update_bar(),
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, view=view)
        
        # Save this message location so we can disable it later
        # We need to fetch the original response message object to get its ID
        msg = await interaction.original_response()
        self.user_data[interaction.user.id]['last_msg'] = (interaction.channel_id, msg.id)

async def setup(bot):
    await bot.add_cog(Tasks(bot))
