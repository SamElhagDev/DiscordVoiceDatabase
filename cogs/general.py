import discord
from discord.ext import commands
from discord.ext.commands import Context


# Deliberately shorter than the slash-command descriptions, which have to be
# verbose for Discord's autocomplete tooltip.
_ICONS = {
    "join":            "✅",
    "leave":           "🚫",
    "participants":    "👥",
    "record":          "⏺️",
    "stoprecord":      "⏹️",
    "recordingstatus": "📊",
    "setchannel":      "📌",
    "retention":       "🗓️",
    "listclips":       "📋",
    "listtext":        "📄",
    "playclip":        "▶️",
    "clip":            "💾",
    "search":          "🔍",
    "searchtext":      "🔎",
    "searchclips":     "📥",
    "favoritesclip":   "⭐",
    "favoriteslist":   "⭐",
    "stop":            "🛑",
    "transcribe":      "📝",
    "perfstats":       "⚡",
    "help":            "❓",
    "ping":            "🏓",
    "sync":            "🔄",
    "unsync":          "🔇",
    "load":            "📦",
    "unload":          "📤",
    "reload":          "🔁",
    "shutdown":        "🔌",
}

_DESCS = {
    "join":            "Opt in to voice recording",
    "leave":           "Opt out of recording",
    "participants":    "List opted-in members",
    "record":          "Start recording",
    "stoprecord":      "Stop recording & disconnect",
    "recordingstatus": "Recording status & talk-time stats",
    "setchannel":      "Set primary auto-join channel",
    "retention":       "Set how long recordings are kept",
    "listclips":       "Browse recorded segments by date",
    "listtext":        "View transcript text for a segment",
    "playclip":        "Play a clip in voice chat",
    "clip":            "Download a clip",
    "search":          "Search transcripts & play a match",
    "searchtext":      "Search transcripts & view text",
    "searchclips":     "Search transcripts & download a clip",
    "favoritesclip":   "Download a saved favorite",
    "favoriteslist":   "Play a saved favorite in voice",
    "stop":            "Stop current playback",
    "transcribe":      "Backfill missing transcripts",
    "perfstats":       "Pipeline processing time stats",
    "help":            "Show this menu",
    "ping":            "Check bot latency",
    "sync":            "Sync slash commands (global or guild)",
    "unsync":          "Remove slash commands",
    "load":            "Load a cog",
    "unload":          "Unload a cog",
    "reload":          "Reload a cog",
    "shutdown":        "Shut down the bot",
}

# (title, [command names]) — each renders as its own full-width embed field.
_VDB_SECTIONS = [
    ("👥  Participation", ["join", "leave", "participants"]),
    ("⏺️  Recording",     ["record", "stoprecord", "recordingstatus"]),
    ("🎵  Playback",      ["listclips", "listtext", "playclip", "clip", "stop"]),
    ("🔍  Search",        ["search", "searchclips", "searchtext"]),
    ("⭐  Favorites",     ["favoritesclip", "favoriteslist"]),
    ("⚙️  Settings",      ["setchannel", "retention"]),
    ("📝  Transcription", ["transcribe", "perfstats"]),
]


def _fmt_line(prefix: str, name: str, cmd) -> str:
    """One command block: usage line (with parameters) then its description below."""
    icon = _ICONS.get(name, "•")
    desc = _DESCS.get(name, cmd.description.partition("\n")[0])
    sig = cmd.signature  # e.g. "<user> [date] [end_date]" — <> required, [] optional
    usage = f"{prefix}{name}" + (f" {sig}" if sig else "")
    return f"{icon}  `{usage}`\n*{desc}*"


def _fmt_section(prefix: str, names: list, cmds: dict) -> str:
    """Join each command's block with a blank line so the field has room to breathe."""
    blocks = [_fmt_line(prefix, n, cmds[n]) for n in names if n in cmds]
    return "\n\n".join(blocks)


class General(commands.Cog, name="general"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="help", description="List all commands the bot has loaded."
    )
    async def help(self, context: Context) -> None:
        prefix = self.bot.bot_prefix

        embed = discord.Embed(
            title="📖  Voice Database — Commands",
            description=(
                f"Every command works with the **`{prefix}`** prefix or as a **`/`** slash command.\n"
                "In each usage line:  `<>` = required  ·  `[]` = optional"
            ),
            color=0x5865F2,
        )

        # ── Voice Database ──────────────────────────────────────────────
        vdb = self.bot.get_cog("voicedatabase")
        if vdb:
            vdb_cmds = {cmd.name: cmd for cmd in vdb.get_commands()}
            for title, names in _VDB_SECTIONS:
                value = _fmt_section(prefix, names, vdb_cmds)
                if value:
                    embed.add_field(name=title, value=value, inline=False)

        # ── General ─────────────────────────────────────────────────────
        gen = self.bot.get_cog("general")
        if gen:
            gen_cmds = {cmd.name: cmd for cmd in gen.get_commands()}
            value = _fmt_section(prefix, list(gen_cmds.keys()), gen_cmds)
            if value:
                embed.add_field(name="🔧  General", value=value, inline=False)

        # ── Owner ───────────────────────────────────────────────────────
        if await self.bot.is_owner(context.author):
            owner = self.bot.get_cog("owner")
            if owner:
                owner_cmds = {cmd.name: cmd for cmd in owner.get_commands()}
                value = _fmt_section(prefix, list(owner_cmds.keys()), owner_cmds)
                if value:
                    embed.add_field(name="👑  Owner", value=value, inline=False)

        embed.set_footer(text=f"💡  Slash commands also offer inline parameter hints & autocomplete.  ·  v{self.bot.version}")
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
