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
    async def anon(self, interaction: discord.Interaction, message: str, name: str = "Anonymous"):
        """Sends a message anonymously to the current channel."""
        # Defer the interaction ephemerally so the user sees "Thinking..." briefly,
        # preventing the "Application did not respond" error.
        await interaction.response.defer(ephemeral=True)
        
        # Send the actual anonymous message to the channel
        await interaction.channel.send(f"**{name}**: {message}")
        
        # Delete the deferred response to remove the "Thinking..." message
        await interaction.delete_original_response()

async def setup(bot):
    await bot.add_cog(Anon(bot))
