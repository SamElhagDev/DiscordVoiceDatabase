import discord
from discord.ext import commands
from discord.ext.commands import Context


class General(commands.Cog, name="general"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="help", description="List all commands the bot has loaded."
    )
    async def help(self, context: Context) -> None:
        prefix = self.bot.bot_prefix

        COMMAND_ICONS = {
            "join":        "✅",
            "leave":       "🚫",
            "participants":"👥",
            "record":      "⏺️",
            "stoprecord":  "⏹️",
            "listclips":   "📋",
            "playclip":    "▶️",
            "clip":        "💾",
            "transcribe":  "📝",
            "search":      "🔍",
            "stop":        "⏹️",
            "help":        "❓",
            "ping":        "🏓",
        }

        # Group voicedatabase commands into sub-sections to stay under 1024 char limit
        VOICEDB_SECTIONS = {
            "👥 Participation": ["join", "leave", "participants"],
            "⏺️ Recording":     ["record", "stoprecord"],
            "🎵 Playback":      ["listclips", "playclip", "clip", "stop", "search"],
            "📝 Transcription": ["transcribe"],
        }

        embed = discord.Embed(
            title="📖  Voice Database — Command Reference",
            description=f"Use `{prefix}<command>` or `/<command>` for any command below.",
            color=0x5865F2,
        )

        for cog_name in self.bot.cogs:
            if cog_name == "owner" and not (await self.bot.is_owner(context.author)):
                continue
            cog = self.bot.get_cog(cog_name.lower())
            cog_commands = {cmd.name: cmd for cmd in cog.get_commands()}
            if not cog_commands:
                continue

            if cog_name.lower() == "voicedatabase":
                for section_name, cmd_names in VOICEDB_SECTIONS.items():
                    lines = []
                    for name in cmd_names:
                        cmd = cog_commands.get(name)
                        if cmd is None:
                            continue
                        icon = COMMAND_ICONS.get(name, "•")
                        desc = cmd.description.partition("\n")[0]
                        lines.append(f"{icon} **`{prefix}{name}`** — {desc}")
                    if lines:
                        embed.add_field(name=section_name, value="\n".join(lines), inline=False)
            else:
                section_icon = "🔧" if cog_name.lower() == "general" else "👑"
                lines = []
                for name, cmd in cog_commands.items():
                    icon = COMMAND_ICONS.get(name, "•")
                    desc = cmd.description.partition("\n")[0]
                    lines.append(f"{icon} **`{prefix}{name}`** — {desc}")
                embed.add_field(
                    name=f"{section_icon}  {cog_name.capitalize()}",
                    value="\n".join(lines),
                    inline=False,
                )

        embed.set_footer(text="Slash commands are also supported for all commands above.")
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="ping",
        description="Check if the bot is alive.",
    )
    async def ping(self, context: Context) -> None:
        embed = discord.Embed(
            title="Pong!",
            description=f"The bot latency is {round(self.bot.latency * 1000)}ms.",
            color=0xBEBEFE,
        )
        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(General(bot))
