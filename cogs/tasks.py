import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from typing import Literal

# List of functions/classes in this file:
# class TaskView(discord.ui.View):
#   - __init__(self, cog, user_id, total, state=None, message_id=None)
#   - get_emoji_bar(self)
#   - update_message(self, interaction, finished=False, congratulation=None)
#   - update_db(self)
#   - get_next_index(self)
#   - check_completion(self, interaction)
#   - finish_logic(self, interaction)
#   - done_button(self, interaction, button)
#   - skip_button(self, interaction, button)
#   - undo_button(self, interaction, button)
#   - finish_button(self, interaction, button)
# class Tasks(commands.Cog, name="tasks"):
#   - __init__(self, bot)
#   - cog_load(self)
#   - restore_views(self)
#   - taskchannel(self, interaction)
#   - tasks(self, interaction, mode: Literal["Set", "Change"], number: int)
#   - progress(self, interaction)
#   - setup(bot)

# UPDATED: No separate JSONStore. Using bot.db (main.py)
# Collections: "tasks_active", "tasks_config"

class TaskView(discord.ui.View):
    def __init__(self, cog, user_id, total, state=None, message_id=None):
        super().__init__(timeout=None) # Persistent
        self.cog = cog
        self.user_id = user_id
        self.total = total
        # State Codes: 0 = White (Todo), 1 = Green (Done), 2 = Blue (Skipped)
        self.state = state if state else [0] * total
        self.message_id = message_id
        self.history = [] # Stack for Undo

    def get_emoji_bar(self):
        if self.total == 0: return ""
        
        # Grid Size: 16 Columns x 2 Rows = 32 Squares total
        cols = 16
        rows = 2
        total_visual_blocks = cols * rows
        
        visual_state = []
        
        # Create a visual representation by repeating task states proportionally
        current_visual_count = 0
        for i in range(self.total):
            # Calculate how many visual blocks this task should take up
            target_visual_count = int((i + 1) * total_visual_blocks / self.total)
            blocks_for_this_task = target_visual_count - current_visual_count
            
            visual_state.extend([self.state[i]] * blocks_for_this_task)
            current_visual_count += blocks_for_this_task
            
        # Safety check to ensure exactly 32 blocks
        if len(visual_state) < total_visual_blocks:
            visual_state.extend([0] * (total_visual_blocks - len(visual_state)))
        elif len(visual_state) > total_visual_blocks:
            visual_state = visual_state[:total_visual_blocks]

        # Symbols - Using standard large square emojis
        SYM_DONE = "🟩" # Green Square
        SYM_SKIP = "🟦" # Blue Square
        SYM_TODO = "⬜" # White Large Square

        # Construct the 2 rows string
        row0 = "-# "
        row1 = "-# "
        
        for i in range(total_visual_blocks):
            val = visual_state[i]
            if val == 1: sym = SYM_DONE
            elif val == 2: sym = SYM_SKIP
            else: sym = SYM_TODO
            
            if i % 2 == 0:
                row0 += sym
            else:
                row1 += sym
                
        return f"{row0}\n{row1}"

    async def update_message(self, interaction, finished=False, congratulation=None):
        completed_tasks = self.state.count(1) + self.state.count(2)
        content = f"<@{self.user_id}>'s tasks: {completed_tasks}/{self.total}\n{self.get_emoji_bar()}"
        
        view = self
        if finished:
            if congratulation:
                content += f"\n🎉 **{congratulation}**"
            view = None # Remove buttons

        if interaction:
            # If the interaction has been responded to (deferred), we follow up/edit
            if interaction.response.is_done():
                 await interaction.edit_original_response(content=content, view=view)
            else:
                 await interaction.response.edit_message(content=content, view=view)
        
        # DB Update
        if finished:
            # Use main DB method
            self.cog.bot.db.delete_doc("tasks_active", "message_id", self.message_id)
        else:
            await self.update_db()

    async def update_db(self):
        if self.message_id:
            # Update 'state' in 'tasks_active' collection where message_id matches
            self.cog.bot.db.update_doc("tasks_active", "message_id", self.message_id, {"state": self.state})

    def get_next_index(self):
        try:
            return self.state.index(0)
        except ValueError:
            return -1

    async def check_completion(self, interaction):
        if 0 not in self.state:
            await self.finish_logic(interaction)
        else:
            await self.update_message(interaction)

    async def finish_logic(self, interaction):
        # 1. Convert remaining '0' (Todo) to '2' (Skipped)
        self.state = [2 if x == 0 else x for x in self.state]
        
        # 2. Calculate score (Only '1's count towards the percentage)
        greens = [x for x in self.state if x == 1]
        percent_complete = int((len(greens) / self.total) * 100) if self.total > 0 else 0
        
        # Fetch celebration messages from config
        # Config structure: {guild_id: {task_channel_id, celebratory_messages}}
        guild_id = str(interaction.guild_id)
        config_data = self.cog.bot.db.get_collection("tasks_config")
        guild_config = config_data.get(guild_id, {})
        celebratory_messages = guild_config.get("celebratory_messages", {
            "1": "Good start! Keep it up!",           # 0-24
            "2": "You're making progress!",           # 25-49
            "3": "Almost there, doing great!",        # 50-74
            "4": "AMAZING! You finished the list!"    # 75-100
        })

        msg_key = "1"
        if 25 <= percent_complete < 50: msg_key = "2"
        elif 50 <= percent_complete < 75: msg_key = "3"
        elif 75 <= percent_complete: msg_key = "4"
        
        celebration = celebratory_messages.get(msg_key, "Good job!")
        
        await self.update_message(interaction, finished=True, congratulation=celebration)

    # --- BUTTONS ---
    
    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, custom_id="bb_done")
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your list, buggy!", ephemeral=True)
        
        idx = self.get_next_index()
        if idx == -1:
            return await self.finish_logic(interaction)

        self.history.append((idx, 0))
        self.state[idx] = 1 # Green (Done)
        await self.check_completion(interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, custom_id="bb_skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your list, buggy!", ephemeral=True)

        idx = self.get_next_index()
        if idx == -1:
            return await self.finish_logic(interaction)

        self.history.append((idx, 0))
        self.state[idx] = 2 # Blue (Skipped)
        await self.check_completion(interaction)

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, custom_id="bb_undo")
    async def undo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your list, buggy!", ephemeral=True)
        
        if not self.history:
            return await interaction.response.send_message("Nothing to undo!", ephemeral=True)

        last_idx, last_val = self.history.pop()
        self.state[last_idx] = last_val
        await self.update_message(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, custom_id="bb_finish")
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your list, buggy!", ephemeral=True)

        await self.finish_logic(interaction)


class Tasks(commands.Cog, name="tasks"):
    """ """ 
    # Empty docstring for no description in help

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Restore views logic
        asyncio.create_task(self.restore_views())

    async def restore_views(self):
        # We need to wait until the bot is ready to add views
        await self.bot.wait_until_ready()
        
        # Load all active tasks from main DB
        active_tasks = self.bot.db.get_collection("tasks_active")
        
        count = 0
        for doc in active_tasks:
            try:
                view = TaskView(
                    cog=self,
                    user_id=doc['user_id'], 
                    total=doc['total'], 
                    state=doc['state'], 
                    message_id=doc['message_id']
                )
                self.bot.add_view(view)
                count += 1
            except Exception as e:
                print(f"Failed to restore task view: {e}")
        print(f"Restored {count} active task trackers in Tasks Cog.")

    def get_task_channel_id(self, guild_id):
        configs = self.bot.db.get_collection("tasks_config")
        return configs.get(str(guild_id), {}).get("task_channel_id")

    @app_commands.command(name="taskchannel", description="Sets the current channel as the only channel for task commands (Owner Only).")
    async def taskchannel(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only my owner can use this command!", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        configs = self.bot.db.get_collection("tasks_config")
        if isinstance(configs, list): configs = {} # safety
        
        if guild_id not in configs: configs[guild_id] = {}
        configs[guild_id]["task_channel_id"] = interaction.channel_id
        
        self.bot.db.save_collection("tasks_config", configs)
        await interaction.response.send_message(f"Task commands are now restricted to this channel: {interaction.channel.mention}")

    @app_commands.command(name="tasks", description="Sets up or changes your task count.", extras={'public': True})
    @app_commands.describe(mode="Set new list (resets progress) or Change total (keeps progress)", number="Number of tasks")
    async def tasks(self, interaction: discord.Interaction, mode: Literal["Set", "Change"], number: int):
        # Restriction Check
        channel_limit = self.get_task_channel_id(interaction.guild_id)
        if channel_limit and interaction.channel_id != channel_limit:
            await interaction.response.send_message(f"Please use <#{channel_limit}> for task commands!", ephemeral=True)
            return

        if number > 100:
             await interaction.response.send_message(f"That's too many tasks! Try 100 or less, buggy.", ephemeral=True)
             return
        if number < 1:
             await interaction.response.send_message("You need at least 1 task!", ephemeral=True)
             return

        # Find existing tasks for this user in this server
        active_tasks = self.bot.db.get_collection("tasks_active")
        existing_doc = next((doc for doc in active_tasks if doc['user_id'] == interaction.user.id), None)
        # Note: We technically could filter by guild_id here if we want tasks to be per-server.
        # But for now, we'll keep it per-user as before, or add guild_id check if desired. 
        # Adding guild_id to search for better separation:
        existing_doc = next((doc for doc in active_tasks if doc['user_id'] == interaction.user.id and doc.get('guild_id') == interaction.guild_id), None)

        # --- LOGIC FOR 'SET' ---
        if mode == "Set":
            if existing_doc:
                # Cleanup old message
                try:
                    chan = self.bot.get_channel(existing_doc.get('channel_id'))
                    if chan:
                        msg = await chan.fetch_message(existing_doc.get('message_id'))
                        await msg.edit(view=None)
                except: pass
                
                # Remove from DB
                self.bot.db.delete_doc("tasks_active", "message_id", existing_doc['message_id'])

            await interaction.response.send_message(f"I've set your tasks to {number}! Run `/progress` to see your bar.", ephemeral=True)
            
            # Create new entry
            new_doc = {
                 "user_id": interaction.user.id,
                 "guild_id": interaction.guild.id, # ADDED guild_id
                 "total": number,
                 "state": [0] * number,
                 "message_id": None, 
                 "channel_id": interaction.channel_id
            }
            # Append directly to collection list via update_doc helper logic or manual append
            # To be safe, we fetch fresh collection list
            current_active = self.bot.db.get_collection("tasks_active")
            current_active.append(new_doc)
            self.bot.db.save_collection("tasks_active", current_active)

        # --- LOGIC FOR 'CHANGE' ---
        elif mode == "Change":
            if not existing_doc:
                await interaction.response.send_message("You don't have an active task list to change! Use 'Set' mode first.", ephemeral=True)
                return
            
            old_total = existing_doc['total']
            state = existing_doc['state']
            
            # Resize state list
            if number > old_total:
                # Add more 'Todo' (0) to the end
                state.extend([0] * (number - old_total))
            elif number < old_total:
                # Truncate list
                state = state[:number]
            
            # Update DB
            self.bot.db.update_doc("tasks_active", "message_id", existing_doc['message_id'], {"total": number, "state": state})
            
            await interaction.response.send_message(f"I've changed your total tasks to {number}! Your progress has been saved. Run `/progress` to refresh the bar.", ephemeral=True)


    @app_commands.command(name="progress", description="Shows your progress bar and buttons.", extras={'public': True})
    async def progress(self, interaction: discord.Interaction):
        # Restriction Check
        channel_limit = self.get_task_channel_id(interaction.guild_id)
        if channel_limit and interaction.channel_id != channel_limit:
            await interaction.response.send_message(f"Please use <#{channel_limit}> for task commands!", ephemeral=True)
            return

        active_tasks = self.bot.db.get_collection("tasks_active")
        doc = next((d for d in active_tasks if d['user_id'] == interaction.user.id and d.get('guild_id') == interaction.guild_id), None)
        
        if not doc:
            await interaction.response.send_message("You haven't set up any tasks yet! Use `/tasks [set] [number]` first.", ephemeral=True)
            return

        # If there was an old message for this same task list, remove its buttons
        if doc.get('message_id'):
            try:
                chan = self.bot.get_channel(doc.get('channel_id'))
                if chan:
                    old_msg = await chan.fetch_message(doc['message_id'])
                    await old_msg.edit(view=None)
            except:
                pass

        # Create new View
        view = TaskView(
            cog=self,
            user_id=interaction.user.id,
            total=doc['total'],
            state=doc['state']
        )

        content = f"<@{interaction.user.id}>'s tasks: {doc['state'].count(1) + doc['state'].count(2)}/{doc['total']}\n{view.get_emoji_bar()}"
        
        await interaction.response.send_message(content, view=view)
        
        # Update DB with new message ID
        msg = await interaction.original_response()
        view.message_id = msg.id
        
        self.bot.db.update_doc("tasks_active", "message_id", doc['message_id'], # Use old message_id to find doc
                               {"message_id": msg.id, "channel_id": interaction.channel_id}) # Update to new

async def setup(bot):
    await bot.add_cog(Tasks(bot))
