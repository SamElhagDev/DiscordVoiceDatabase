import logging
import logging.handlers
import os
import platform
import random
import sys

import aiosqlite
import discord
from discord.ext import commands, tasks
from discord.ext.commands import Context
from dotenv import load_dotenv

from database import DatabaseManager

load_dotenv()

# Configurable paths — override via .env or environment variables
# Defaults work for both Windows direct-run and Docker
DB_PATH = os.getenv("DiscordVoiceDatabase_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "database.db"))
RECORDINGS_PATH = os.getenv("DiscordVoiceDatabase_RECORDINGS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"))
CLIPS_PATH = os.getenv("DiscordVoiceDatabase_CLIPS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips"))

# Ensure directories exist
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(RECORDINGS_PATH, exist_ok=True)
os.makedirs(CLIPS_PATH, exist_ok=True)


# Only enable intents the bot actually uses — reduces gateway event volume.
# Notably excludes presences (heaviest intent), typing, reactions, bans, etc.
intents = discord.Intents.none()
intents.guilds = True          # guild create/update/delete, channel events
intents.voice_states = True    # voice state updates (core functionality)
intents.guild_messages = True  # prefix command processing
intents.message_content = True # read message content for prefix commands
intents.members = True         # member lookups (get_member, display_name)


class LoggingFormatter(logging.Formatter):
    """Coloured console formatter with per-level ANSI codes (cached)."""

    black = "\x1b[30m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"
    gray = "\x1b[38m"
    reset = "\x1b[0m"
    bold = "\x1b[1m"

    COLORS = {
        logging.DEBUG: gray + bold,
        logging.INFO: blue + bold,
        logging.WARNING: yellow + bold,
        logging.ERROR: red,
        logging.CRITICAL: red + bold,
    }

    def __init__(self):
        super().__init__()
        # Pre-build a Formatter per log level so format() doesn't construct one per call
        self._formatters: dict[int, logging.Formatter] = {}
        for level, color in self.COLORS.items():
            fmt = (
                f"{self.black}{self.bold}{{asctime}}{self.reset} "
                f"{color}{{levelname:<8}}{self.reset} "
                f"{self.green}{self.bold}{{name}}{self.reset} {{message}}"
            )
            self._formatters[level] = logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S", style="{")

    def format(self, record):
        formatter = self._formatters.get(record.levelno)
        if formatter is None:
            # Fallback for unexpected log levels — cache on first encounter
            fmt = (
                f"{self.black}{self.bold}{{asctime}}{self.reset} "
                f"{self.gray}{{levelname:<8}}{self.reset} "
                f"{self.green}{self.bold}{{name}}{self.reset} {{message}}"
            )
            formatter = logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S", style="{")
            self._formatters[record.levelno] = formatter
        return formatter.format(record)


logger = logging.getLogger("discord_bot")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(LoggingFormatter())

# Single rotating file handler shared by all loggers — caps disk usage
file_handler = logging.handlers.RotatingFileHandler(
    filename="discord.log", encoding="utf-8", mode="a",
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,              # keep 5 rotated files (50 MB total)
)
file_handler_formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
)
file_handler.setFormatter(file_handler_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Library loggers share the same rotating file handler (single fd, no buffering quirks)
_voice_logger = logging.getLogger("discord.voice_client")
_voice_logger.setLevel(logging.DEBUG)
_voice_logger.addHandler(file_handler)

_gateway_logger = logging.getLogger("discord.gateway")
_gateway_logger.setLevel(logging.WARNING)
_gateway_logger.addHandler(file_handler)


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(os.getenv("DiscordVoiceDatabase_PREFIX", "!")),
            intents=intents,
            help_command=None,
        )
        self.logger = logger
        self.database = None
        self.bot_prefix = os.getenv("DiscordVoiceDatabase_PREFIX", "!")
        self.invite_link = os.getenv("DiscordVoiceDatabase_INVITE_LINK", "")

    async def _init_schema(self, connection: aiosqlite.Connection) -> None:
        """Initialize database schema on the live connection."""
        self.logger.info("Initializing database schema...")
        with open(
            os.path.join(os.path.realpath(os.path.dirname(__file__)), "database", "schema.sql"),
            encoding="utf-8",
        ) as file:
            await connection.executescript(file.read())
        await connection.commit()

        # Apply migrations (safe to re-run)
        migrations = [
            "ALTER TABLE segments ADD COLUMN transcript TEXT",
        ]
        for sql in migrations:
            try:
                await connection.execute(sql)
                await connection.commit()
                self.logger.info(f"Applied migration: {sql}")
            except Exception:
                pass  # column already exists
        self.logger.info("Database schema ready")

    async def load_cogs(self) -> None:
        for file in os.listdir(
            os.path.join(os.path.realpath(os.path.dirname(__file__)), "cogs")
        ):
            if file.endswith(".py"):
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    self.logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    self.logger.error(
                        f"Failed to load extension {extension}\n{exception}"
                    )

    @tasks.loop(minutes=1.0)
    async def status_task(self) -> None:
        statuses = [
            "Recording voices...",
            "Listening carefully...",
            "Archiving audio...",
        ]
        await self.change_presence(activity=discord.Game(random.choice(statuses)))

    @status_task.before_loop
    async def before_status_task(self) -> None:
        await self.wait_until_ready()

    async def setup_hook(self) -> None:
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info(f"Database: {DB_PATH}")
        self.logger.info(f"Recordings: {RECORDINGS_PATH}")
        self.logger.info(f"Clips: {CLIPS_PATH}")
        self.logger.info("-------------------")

        # Open the long-lived connection, enable WAL for concurrent read/write,
        # then init schema — all on the SAME connection object.
        connection = await aiosqlite.connect(DB_PATH)
        await connection.execute("PRAGMA journal_mode=WAL")
        await self._init_schema(connection)

        self.database = DatabaseManager(connection=connection)
        await self.load_cogs()
        self.status_task.start()

    async def close(self) -> None:
        self.logger.info("Bot shutting down — closing database connection...")
        if self.database:
            await self.database.connection.close()
            self.logger.info("Database connection closed")
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_completion(self, context: Context) -> None:
        full_command_name = context.command.qualified_name
        split = full_command_name.split(" ")
        executed_command = str(split[0])
        if context.guild is not None:
            self.logger.info(
                f"Executed {executed_command} command in {context.guild.name} (ID: {context.guild.id}) by {context.author} (ID: {context.author.id})"
            )
        else:
            self.logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id}) in DMs"
            )

    async def on_command_error(self, context: Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(error.retry_after, 60)
            hours, minutes = divmod(minutes, 60)
            hours = hours % 24
            embed = discord.Embed(
                description=f"**Please slow down** - You can use this command again in {f'{round(hours)} hours' if round(hours) > 0 else ''} {f'{round(minutes)} minutes' if round(minutes) > 0 else ''} {f'{round(seconds)} seconds' if round(seconds) > 0 else ''}.",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.NotOwner):
            embed = discord.Embed(
                description="You are not the owner of the bot!", color=0xE02B2B
            )
            await context.send(embed=embed)
            if context.guild:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the guild {context.guild.name} (ID: {context.guild.id}), but the user is not an owner of the bot."
                )
            else:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the bot's DMs, but the user is not an owner of the bot."
                )
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="You are missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to execute this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                description="I am missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to fully perform this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Error!",
                description=str(error).capitalize(),
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.CommandNotFound):
            pass  # ignore unknown commands silently
        elif isinstance(error, commands.CheckFailure):
            pass  # handled by cog-level checks (e.g. guild-only cog_check)
        elif isinstance(error, commands.CommandInvokeError):
            self.logger.error(
                f"Command '{context.command}' raised an exception: {error.original}",
                exc_info=error.original,
            )
            embed = discord.Embed(
                title="Error!",
                description=f"An internal error occurred: `{type(error.original).__name__}: {error.original}`",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        else:
            raise error


bot = DiscordBot()
bot.run(os.getenv("DiscordVoiceDatabase_TOKEN"))
