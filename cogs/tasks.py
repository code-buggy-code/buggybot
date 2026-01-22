import discord
from discord.ext import commands
from discord.ui import View, Button
import math

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
#   - taskchannel(self, ctx)
#   - tasks(self, ctx, number: int)
#   - progress(self, ctx)
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
    # Empty docstring ensures no description in help command

    def __init__(self, bot):
        self.bot = bot
        # Structure: {user_id: {'total': int, 'current': int, 'last_msg': (channel_id, message_id)}}
        self.user_data = {}
        self.task_channel_id = None

    @commands.command(name="taskchannel")
    @commands.is_owner()
    async def taskchannel(self, ctx):
        """Sets the current channel as the only channel for task commands."""
        self.task_channel_id = ctx.channel.id
        await ctx.send(f"Task commands are now restricted to this channel: {ctx.channel.mention}")

    @commands.command(name="tasks")
    async def tasks(self, ctx, number: int):
        """Sets up how many tasks you have."""
        # Check restriction
        if self.task_channel_id and ctx.channel.id != self.task_channel_id:
            await ctx.send(f"Please use <#{self.task_channel_id}> for task commands!", delete_after=5)
            return

        if number <= 0:
            await ctx.send("You need to have at least 1 task, buggy!")
            return

        # Handle previous progress bar if it exists
        if ctx.author.id in self.user_data:
            old_data = self.user_data[ctx.author.id]
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
        self.user_data[ctx.author.id] = {
            'total': number,
            'current': 0
        }

        await ctx.send(f"I've set your tasks to {number}! Run `/progress` to see your bar.")

    @commands.command(name="progress")
    async def progress(self, ctx):
        """Shows your progress bar and buttons."""
        # Check restriction
        if self.task_channel_id and ctx.channel.id != self.task_channel_id:
            await ctx.send(f"Please use <#{self.task_channel_id}> for task commands!", delete_after=5)
            return

        if ctx.author.id not in self.user_data:
            await ctx.send("You haven't set up any tasks yet! Use `/tasks [number]` first.")
            return

        data = self.user_data[ctx.author.id]
        
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
        view = TaskView(self, ctx.author.id, data['total'], data['current'])
        
        embed = discord.Embed(
            title="Task Progress",
            description=view.update_bar(),
            color=discord.Color.blue()
        )
        
        msg = await ctx.send(embed=embed, view=view)
        
        # Save this message location so we can disable it later
        self.user_data[ctx.author.id]['last_msg'] = (ctx.channel.id, msg.id)

async def setup(bot):
    await bot.add_cog(Tasks(bot))
