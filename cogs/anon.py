import discord
from discord import app_commands
from discord.ext import commands

# Function/Class List:
# class Anon(commands.Cog)
# - anon(interaction, message, name)
# - setup(bot)

class Anon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.description = "Anonymous messaging commands."

    @app_commands.command(name="anon", description="Send a message anonymously.", extras={'public': True})
    @app_commands.describe(message="The message you want to send", name="The name to display (optional)")
    async def anon(self, interaction: discord.Interaction, message: str, name: str = None):
        """Sends a message anonymously to the current channel."""
        # Defer the interaction ephemerally to prevent timeout errors while processing
        await interaction.response.defer(ephemeral=True)
        
        if name:
            # If a name is provided, show it
            await interaction.channel.send(f"**{name}**: {message}")
        else:
            # If no name is provided, just send the raw message
            await interaction.channel.send(message)
        
        # Delete the hidden loading state so the command looks invisible
        await interaction.delete_original_response()

async def setup(bot):
    await bot.add_cog(Anon(bot))
