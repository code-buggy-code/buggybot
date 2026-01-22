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
        # Send the message to the channel with the display name
        await interaction.channel.send(f"**{name}**: {message}")
        
        # Confirm to the user (hidden) so they know it worked
        await interaction.response.send_message(f"✅ Sent anonymously as **{name}**!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Anon(bot))
