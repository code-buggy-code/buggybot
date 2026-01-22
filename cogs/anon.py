import discord
from discord import app_commands
from discord.ext import commands

# Function/Class List:
# class Anon(commands.Cog)
# - anon(interaction, message)
# - setup(bot)

class Anon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Anonymous messaging commands."

    # Added extras={'public': True} to bypass the buggy_only_check
    @app_commands.command(name="anon", description="Send a message anonymously.", extras={'public': True})
    @app_commands.describe(message="The message you want to send anonymously")
    async def anon(self, interaction: discord.Interaction, message: str):
        """Sends a message anonymously to the current channel."""
        # Send the message to the channel without the author's name
        await interaction.channel.send(message)
        
        # Confirm to the user (hidden) so they know it worked
        await interaction.response.send_message("✅ Message sent anonymously!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Anon(bot))
