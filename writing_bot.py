import json
import os
from typing import Optional

import discord
from discord import app_commands
from keep_alive import keep_alive

DATA_FILE = "goals.json"
LEADERBOARD_SIZE = 15

UNITS = ("words", "pages", "hours")
UNIT_CHOICES = [app_commands.Choice(name=u.capitalize(), value=u) for u in UNITS]

NO_GOAL_MSG = "You haven't set a goal yet! Use /set_goal first."
WHOLE_NUMBER_MSG = "Words and pages need to be whole numbers (only hours can have decimals)."

# Embed stripe colors: change these to whatever you like
COLOR_PROGRESS = discord.Color.from_str("#FFAB91")  # peach
COLOR_SUCCESS = discord.Color.green()
COLOR_UNDO = discord.Color.orange()
COLOR_ERROR = discord.Color.red()
COLOR_NEUTRAL = discord.Color.light_grey()

# ---------- 1. Bot setup ----------

class WritingBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Runs once at startup (on_ready can fire multiple times)
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} command(s)")

    async def on_ready(self):
        print(f"Logged in as {self.user}")


client = WritingBot()
tree = client.tree


# ---------- 2. Helpers ----------

# Data layout: { guild_id: { user_id: {username, unit, goal, current, last_add} } }
# Entries saved before goal types existed have no "unit" and are treated as words.

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_data(data):
    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(temp_file, DATA_FILE)


def get_entry(interaction):
    """Return (all_data, this user's entry or None) for the current server."""
    data = load_data()
    entry = data.get(str(interaction.guild_id), {}).get(str(interaction.user.id))
    return data, entry


def make_progress_bar(current, goal, bar_length=10):
    if goal <= 0:
        return "░" * bar_length
    fill_fraction = min(current / goal, 1.0)
    filled = int(fill_fraction * bar_length)
    return "▓" * filled + "░" * (bar_length - filled)


def get_percentage(current, goal):
    return int((current / goal) * 100) if goal > 0 else 0


def display_name(user):
    return user.global_name or user.name


def normalize_amount(value, unit):
    """Hours can have decimals (rounded to 2 places); words/pages must be whole.
    Returns None if the value isn't valid for the unit."""
    if unit == "hours":
        return round(value, 2)
    if value != int(value):
        return None
    return int(value)


def fmt(value):
    """Format a number nicely: 20000 -> 20,000 and 2.50 -> 2.5"""
    if isinstance(value, float):
        if value.is_integer():
            value = int(value)
        else:
            return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,}"


def progress_lines(entry):
    """The bar, percentage, and 'current / goal unit' lines shared by several commands."""
    current, goal = entry["current"], entry["goal"]
    unit = entry.get("unit", "words")
    return (
        f"{make_progress_bar(current, goal)}  {get_percentage(current, goal)}%\n"
        f"📊 {fmt(current)} / {fmt(goal)} {unit}"
    )


# ---------- Embed helpers ----------

def make_embed(description, *, title=None, color=COLOR_PROGRESS, author_user=None, thumbnail_user=None):
    """Build an embed in the style of Carl-bot's: colored stripe on the left,
    optional user name + avatar at the top, and a timestamp in the footer.

    author_user    -> small name + avatar line above the title
    thumbnail_user -> avatar in the top-right corner (used when the title already names the user)
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    if author_user is not None:
        embed.set_author(name=display_name(author_user), icon_url=author_user.display_avatar.url)
    return embed


async def send_notice(interaction, message, color=COLOR_ERROR):
    """Errors and 'nothing here' messages: embedded too, but only visible to the user."""
    embed = discord.Embed(description=message, color=color)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- 3. Commands ----------

@tree.command(name="set_goal", description="Set a writing goal in words, pages, or hours")
@app_commands.describe(
    goal="The total amount you want to reach",
    unit="What you're measuring (default: words)",
    reset="Also reset your current progress to 0 (default: keep it)",
)
@app_commands.choices(unit=UNIT_CHOICES)
@app_commands.guild_only()
async def set_goal(interaction: discord.Interaction, goal: float, unit: str = "words", reset: bool = False):
    goal = normalize_amount(goal, unit)
    if goal is None:
        await send_notice(interaction, WHOLE_NUMBER_MSG)
        return
    if goal <= 0:
        await send_notice(interaction, "Please set a goal greater than 0!")
        return

    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    data = load_data()
    guild_data = data.setdefault(guild_id, {})

    existing = guild_data.get(user_id)
    old_unit = existing.get("unit", "words") if existing else unit
    unit_changed = existing is not None and old_unit != unit

    # Progress in words can't carry over to hours (etc.), so switching units resets it
    current = 0 if (reset or existing is None or unit_changed) else existing["current"]

    guild_data[user_id] = {
        "username": display_name(interaction.user),
        "unit": unit,
        "goal": goal,
        "current": current,
        "last_add": 0,
    }
    save_data(data)

    msg = f" Your new target is **{fmt(goal)}** {unit}."
    if unit_changed:
        msg += f" Since you switched from {old_unit} to {unit}, your progress was reset to 0."
    elif current:
        msg += f" You're keeping your **{fmt(current)}** {unit} of progress."
    msg += " You got this!"

    embed = make_embed(msg, title="📝 Goal set!", thumbnail_user=interaction.user)
    await interaction.response.send_message(embed=embed)


@tree.command(name="add_progress", description="Add to your current progress")
@app_commands.describe(amount="How much you just did (in the unit of your goal)")
@app_commands.guild_only()
async def add_progress(interaction: discord.Interaction, amount: float):
    data, entry = get_entry(interaction)
    if entry is None:
        await send_notice(interaction, NO_GOAL_MSG)
        return

    unit = entry.get("unit", "words")
    amount = normalize_amount(amount, unit)
    if amount is None:
        await send_notice(interaction, WHOLE_NUMBER_MSG)
        return
    if amount <= 0:
        await send_notice(interaction, "Please add a positive amount!")
        return

    entry["current"] = normalize_amount(entry["current"] + amount, unit)
    entry["last_add"] = amount  # remembered so /undo can reverse it
    entry["unit"] = unit
    entry["username"] = display_name(interaction.user)
    save_data(data)

    desc = progress_lines(entry)
    reached = entry["current"] >= entry["goal"]
    if reached:
        desc += "\n\n🎉 **Congratulations! You hit your goal!**"

    embed = make_embed(
        desc,
        title=f"➕ Added {fmt(amount)} {unit}!",
        color=COLOR_SUCCESS if reached else COLOR_PROGRESS,
        author_user=interaction.user,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="undo", description="Undo your most recent /add_progress")
@app_commands.guild_only()
async def undo(interaction: discord.Interaction):
    data, entry = get_entry(interaction)
    if entry is None:
        await send_notice(interaction, NO_GOAL_MSG)
        return

    unit = entry.get("unit", "words")
    last_add = entry.get("last_add", 0)
    if last_add <= 0:
        await send_notice(interaction, "There's nothing to undo!")
        return

    entry["current"] = max(0, normalize_amount(entry["current"] - last_add, unit))
    entry["last_add"] = 0  # only the most recent addition can be undone
    save_data(data)

    embed = make_embed(
        progress_lines(entry),
        title=f"↩️ Removed {fmt(last_add)} {unit}",
        color=COLOR_UNDO,
        author_user=interaction.user,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="set_progress", description="Set your total progress to an exact number")
@app_commands.describe(total="Your correct total so far (in the unit of your goal)")
@app_commands.guild_only()
async def set_progress(interaction: discord.Interaction, total: float):
    data, entry = get_entry(interaction)
    if entry is None:
        await send_notice(interaction, NO_GOAL_MSG)
        return

    unit = entry.get("unit", "words")
    total = normalize_amount(total, unit)
    if total is None:
        await send_notice(interaction, WHOLE_NUMBER_MSG)
        return
    if total < 0:
        await send_notice(interaction, "Your total can't be negative!")
        return

    entry["current"] = total
    entry["last_add"] = 0
    save_data(data)

    desc = progress_lines(entry)
    reached = total >= entry["goal"]
    if reached:
        desc += "\n\n🎉 **Congratulations! You hit your goal!**"

    embed = make_embed(
        desc,
        title=f"✏️ Total updated to {fmt(total)} {unit}",
        color=COLOR_SUCCESS if reached else COLOR_PROGRESS,
        author_user=interaction.user,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="status", description="Check your current writing goal progress")
@app_commands.guild_only()
async def status(interaction: discord.Interaction):
    _, entry = get_entry(interaction)
    if entry is None:
        await send_notice(interaction, NO_GOAL_MSG)
        return

    embed = make_embed(
        progress_lines(entry),
        title=f"📝 {interaction.user.display_name}'s Progress",
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="leaderboard", description="See everyone's writing progress")
@app_commands.describe(unit="Show only one goal type, ranked by amount (default: everyone, ranked by % complete)")
@app_commands.choices(unit=UNIT_CHOICES)
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction, unit: Optional[str] = None):
    guild_data = load_data().get(str(interaction.guild_id), {})
    entries = list(guild_data.values())

    if unit:
        entries = [e for e in entries if e.get("unit", "words") == unit]

    if not entries:
        msg = f"Nobody has a {unit} goal yet!" if unit else "Nobody has set any writing goals yet!"
        await send_notice(interaction, msg, color=COLOR_NEUTRAL)
        return

    # Different units can't be compared by raw amount (500 words vs 3 hours),
    # so the mixed board ranks by % of goal complete. A single-unit board
    # ranks by the actual amount, since everyone is measuring the same thing.
    if unit:
        entries.sort(key=lambda e: e["current"], reverse=True)
        subtitle = f"{unit.capitalize()} only, ranked by amount"
    else:
        entries.sort(key=lambda e: e["current"] / e["goal"], reverse=True)
        subtitle = "Ranked by % of goal complete"

    medals = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
    lines = [f"*{subtitle}*", ""]

    for rank, info in enumerate(entries[:LEADERBOARD_SIZE], start=1):
        prefix = medals.get(rank, f"**#{rank}** ")
        entry_unit = info.get("unit", "words")
        pct = get_percentage(info["current"], info["goal"])
        lines.append(
            f"{prefix}**{info['username']}**: {fmt(info['current'])} / {fmt(info['goal'])} {entry_unit} ({pct}%)"
        )

    if len(entries) > LEADERBOARD_SIZE:
        lines.append(f"...and {len(entries) - LEADERBOARD_SIZE} more writers")

    embed = make_embed("\n".join(lines), title="🍑 Peachy Leaderboard 🏆")
    await interaction.response.send_message(embed=embed)


# ---------- 4. Run ----------
keep_alive()
# Set your token as an environment variable instead of pasting it in the file:
#   export DISCORD_TOKEN="your-token"
client.run(os.environ["DISCORD_TOKEN"])
