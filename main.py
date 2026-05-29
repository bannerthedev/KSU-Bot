import asyncio
import datetime
import re
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

# ============ CONFIG ============
TOKEN = "MTUwOTcwODA1NDQ0MDk2ODE5Mg.GIZTj1.6ECt-iPD_IIvv0wLS9FaEt8RDzUh2AKchBspjo"
GUILD_ID = 1500141037354750104

# Roles
CAP_ROLE_ID    = 1509721823779225630  # global cap role (caps can manage all)
MAIN_ROSTER_ROLE_ID = 1500150907118157884
MID_ROSTER_ROLE_ID  = 1500150844417507411
LOW_ROSTER_ROLE_ID  = 1500150962923376771

# Channels
TEAMS_ACTIVITY_CHANNEL_ID = 1509713989414682644
ACTIVITY_CHANNEL_ID       = 1500144024500179026
TRYOUT_CHANNEL_ID         = 1500144266960437300
TRANSACTIONS_CHANNEL_ID   = 1509024624032088154
MATCH_CHANNEL_ID          = 1509719985680023552  # match times channel ID
SCORES_CHANNEL_ID         = 1509720002016837753   # scores channel

MAIN_ROSTER_CHANNEL_ID    = 1500144572662288404 # main roster channel ID
MID_ROSTER_CHANNEL_ID     = 1500144509231829103 #Invite roster channel ID
LOW_ROSTER_CHANNEL_ID     = 1500144648453226547 # sub roster channel ID
 
WELCOME_CHANNEL_ID        = 1500141038596132866  # welcome channel

# Emojis / IDs
TEAM_EMOJI_ID    = 1509027449743212645  # custom emoji ID for logo reaction
GOAT_EMOJI_ID    = 1509027449743212645
KITSUNE_EMOJI_ID = 1509027449743212645  # <:Kitsune:1509027449743212645>
CAPTAIN_ID       = 1115806934919024671  # single captain for all rosters
CO_CAPTAIN_ID    = 1368269727385780296   # <-- put your co-captain's user ID here

# Tryout / scrim settings
REQUIRED_REACTIONS = 7
TIMEZONE = ZoneInfo("America/New_York")
MAX_LINEUP = 4

# ==================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# TRYOUT state
tryout_codes: dict[int, str] = {}
interested_users: dict[int, set[int]] = {}
tryout_tasks: dict[int, asyncio.Task] = {}

# SCRIMS state
SCRIMS: dict[str, dict] = {}  # in-memory


# ---------- Helpers ----------

def get_latest_scrim() -> dict | None:
    if not SCRIMS:
        return None
    # scrim_id is ms timestamp string, so max() = latest
    latest_id = max(SCRIMS.keys(), key=lambda k: int(k))
    return SCRIMS.get(latest_id)


def get_team_emoji(guild: discord.Guild) -> discord.Emoji | None:
    return guild.get_emoji(TEAM_EMOJI_ID)


def parse_time_string(time_str: str) -> datetime.datetime | None:
    s = time_str.strip().upper().replace(" ", "")
    for suffix in ("EST", "EDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    try:
        if "AM" in s or "PM" in s:
            if "AM" in s:
                base, _ = s.split("AM")
                ampm = "AM"
            else:
                base, _ = s.split("PM")
                ampm = "PM"
            if base.endswith(":"):
                base = base[:-1]
            hour_str, minute_str = base.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
            if ampm == "AM":
                if hour == 12:
                    hour = 0
            else:
                if hour != 12:
                    hour += 12
        else:
            hour_str, minute_str = s.split(":")
            hour = int(hour_str)
            minute = int(minute_str)

        now_local = datetime.datetime.now(TIMEZONE)
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate += datetime.timedelta(days=1)
        return candidate.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def get_allowed_rosters(member: discord.Member) -> list[str]:
    role_ids = {r.id for r in member.roles}
    if CAP_ROLE_ID in role_ids:
        # main, mid, low = Main, Invite, Sub
        return ["main", "mid", "low"]
    return []


def roster_template(title: str, kitsune_emoji: str, captain_mention: str, cocap_mention: str) -> str:
    # title: "Main", "Invite", "Sub"
    upper = title.upper()

    header_top = "╔⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤╗"
    header_mid = f"              **〘 𓆩⊶⊶ {upper} ROSTER ⊶⊶𓆪 〙**"
    header_bot = "╚⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤╝"

    players_title = f"** KSU {upper} PLAYERS:**"
    kitsune_block = "\n".join(
        f"{i}. **-〚{kitsune_emoji}〛-** @"
        for i in range(1, 16)
    )

    footer_sep = "━━━━━━━━━━━━━━━━━━━━━━"
    footer_mid = "**»»——————————————————««**"
    footer_bot = "╚⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤⏤╝"

    return (
        f"{header_top}  \n"
        f"{header_mid}  \n"
        f"{header_bot}  \n\n"
        f"**Captain:** {captain_mention} \n"
        f"**Co-Captain:** {cocap_mention}\n\n"
        f"{footer_sep}  \n"
        f"{players_title}  \n"
        f"{kitsune_block}\n"
        f"{footer_sep}  \n"
        f"{footer_mid}  \n"
        f"{footer_bot}"
    )


# ---------- Transactions UI ----------
class RosterActionSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, mode: str, allowed_rosters: list[str]):
        self.target_member = member
        self.mode = mode
        all_options = {
            "main": discord.SelectOption(label="Main Roster", value="main"),
            "mid":  discord.SelectOption(label="Invite Roster", value="mid"),
            "low":  discord.SelectOption(label="Sub Roster", value="low"),
        }
        options = [all_options[r] for r in allowed_rosters if r in all_options]
        super().__init__(placeholder="Select roster...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        roster_value = self.values[0]
        guild = interaction.guild

        if roster_value == "main":
            roster_name = "Main Roster"
            roster_channel = guild.get_channel(MAIN_ROSTER_CHANNEL_ID)
            roster_role = guild.get_role(MAIN_ROSTER_ROLE_ID)
        elif roster_value == "mid":
            roster_name = "Invite Roster"
            roster_channel = guild.get_channel(MID_ROSTER_CHANNEL_ID)
            roster_role = guild.get_role(MID_ROSTER_ROLE_ID)
        else:
            roster_name = "Sub Roster"
            roster_channel = guild.get_channel(LOW_ROSTER_CHANNEL_ID)
            roster_role = guild.get_role(LOW_ROSTER_ROLE_ID)

        tx_channel = guild.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_channel is None or roster_channel is None:
            await interaction.response.edit_message(content="Configured channels not found.", view=None)
            return

        kitsune_emoji = f"<:Kitsune:{KITSUNE_EMOJI_ID}>"
        captain_mention = f"<@{CAPTAIN_ID}>"
        cocap_mention = f"<@{CO_CAPTAIN_ID}>"

        # ADD
        if self.mode == "add":
            if roster_role:
                try:
                    await self.target_member.add_roles(roster_role, reason=f"Added to {roster_name}")
                except Exception as e:
                    print("role add error", e)
            await tx_channel.send(f"{self.target_member.mention} Has Been added to **{roster_name}**")

            try:
                last_msg = None
                async for m in roster_channel.history(limit=1, oldest_first=False):
                    last_msg = m
                    break

                if last_msg is None:
                    base = roster_template(
                        roster_name.replace(" Roster", ""),
                        kitsune_emoji,
                        captain_mention,
                        cocap_mention,
                    )
                    lines = base.split("\n")
                    # replace first '@' in a player slot
                    for i, line in enumerate(lines):
                        if kitsune_emoji in line and "@" in line and "<@" not in line:
                            lines[i] = line.replace("@", self.target_member.mention, 1)
                            break
                    await roster_channel.send("\n".join(lines))
                else:
                    lines = last_msg.content.split("\n")
                    for i, line in enumerate(lines):
                        if kitsune_emoji in line and "@" in line and "<@" not in line:
                            lines[i] = line.replace("@", self.target_member.mention, 1)
                            break
                    await last_msg.edit(content="\n".join(lines))
            except Exception as e:
                print("roster edit send error", e)

            await interaction.response.edit_message(
                content=f"Added {self.target_member.mention} to **{roster_name}**.",
                view=None
            )
            return

        # KICK
        if self.mode == "kick":
            if roster_role:
                try:
                    await self.target_member.remove_roles(roster_role, reason=f"Kicked from {roster_name}")
                except Exception as e:
                    print("role remove error", e)
            await tx_channel.send(f"{self.target_member.mention} Has Been kicked off of **{roster_name}**")

            try:
                last_msg = None
                async for m in roster_channel.history(limit=1, oldest_first=False):
                    last_msg = m
                    break
                if last_msg:
                    lines = last_msg.content.split("\n")
                    mention = self.target_member.mention
                    for i, line in enumerate(lines):
                        if mention in line and kitsune_emoji in line:
                            # remove mention and restore '@'
                            lines[i] = line.replace(mention, "@")
                    await last_msg.edit(content="\n".join(lines))
            except Exception as e:
                print("kick edit error", e)

            await interaction.response.edit_message(
                content=f"Kicked {self.target_member.mention} from **{roster_name}**.",
                view=None
            )
            return


class AddMemberSelect(discord.ui.UserSelect):
    def __init__(self, allowed_rosters: list[str]):
        self.allowed_rosters = allowed_rosters
        super().__init__(placeholder="Select a member to add...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.values[0].id)
        if not member:
            await interaction.response.edit_message(content="Could not find that member.", view=None)
            return
        view = discord.ui.View(timeout=60)
        view.add_item(RosterActionSelect(member, mode="add", allowed_rosters=self.allowed_rosters))
        await interaction.response.edit_message(content=f"Selected member: {member.mention}\nNow select a roster:", view=view)


class KickMemberSelect(discord.ui.UserSelect):
    def __init__(self, allowed_rosters: list[str]):
        self.allowed_rosters = allowed_rosters
        super().__init__(placeholder="Select a member to kick...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.values[0].id)
        if not member:
            await interaction.response.edit_message(content="Could not find that member.", view=None)
            return
        view = discord.ui.View(timeout=60)
        view.add_item(RosterActionSelect(member, mode="kick", allowed_rosters=self.allowed_rosters))
        await interaction.response.edit_message(content=f"Selected member to kick: {member.mention}\nNow select their roster:", view=view)


class ActionSelect(discord.ui.Select):
    def __init__(self, is_cap: bool, allowed_rosters: list[str]):
        self.is_cap = is_cap
        self.allowed_rosters = allowed_rosters
        options = [
            discord.SelectOption(label="Add", value="add"),
            discord.SelectOption(label="Kick", value="kick"),
        ]
        super().__init__(placeholder="Select a transaction type...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "add":
            view = discord.ui.View(timeout=60)
            view.add_item(AddMemberSelect(self.allowed_rosters))
            await interaction.response.edit_message(content="Select a member to add:", view=view)
        elif choice == "kick":
            view = discord.ui.View(timeout=60)
            view.add_item(KickMemberSelect(self.allowed_rosters))
            await interaction.response.edit_message(content="Select a member to kick:", view=view)


class TransactionsView(discord.ui.View):
    def __init__(self, is_cap: bool, allowed_rosters: list[str]):
        super().__init__(timeout=120)
        self.add_item(ActionSelect(is_cap=is_cap, allowed_rosters=allowed_rosters))


# ---------- Slash commands (transactions + activity + tryout) ----------
@bot.tree.command(name="transactions", description="Manage roster transactions (caps only).")
async def transactions(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("You must be in a guild.", ephemeral=True)
        return
    member = interaction.user
    role_ids = {r.id for r in member.roles}
    is_cap = CAP_ROLE_ID in role_ids
    allowed_rosters = get_allowed_rosters(member)
    if not allowed_rosters:
        await interaction.response.send_message("You must be a cap to use this command.", ephemeral=True)
        return
    view = TransactionsView(is_cap=is_cap, allowed_rosters=allowed_rosters)
    await interaction.response.send_message("Select a transaction type:", view=view, ephemeral=True)


@bot.tree.command(name="team-activity-check", description="Post a team-wide activity check (final warning) message.")
async def team_activity_check(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    guild = interaction.guild
    channel = guild.get_channel(TEAMS_ACTIVITY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("Teams activity-check channel not found.", ephemeral=True)
        return

    content = (
        "|| @everyone ||\n\n"
        "# TEAM ACTIVITY CHECK! \n\n"
        "** Make sure to type in <#1500196910890352760>\n"
    )

    msg = await channel.send(content)
    team_emoji = get_team_emoji(guild)
    if team_emoji:
        try:
            await msg.add_reaction(team_emoji)
        except Exception as e:
            print("react error", e)

    await interaction.response.send_message("Team activity check message sent.", ephemeral=True)


@bot.tree.command(name="activity-check", description="Post a general activity check message.")
async def activity_check(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    guild = interaction.guild
    channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("Activity-check channel not found.", ephemeral=True)
        return

    content = (
        "|| @everyone ||\n\n"
        "# ACTIVITY CHECK! \n\n"
        "** Make sure to type in <#1500142823570145321>\n"
    )

    msg = await channel.send(content)
    team_emoji = get_team_emoji(guild)
    if team_emoji:
        try:
            await msg.add_reaction(team_emoji)
        except Exception as e:
            print("react error", e)

    await interaction.response.send_message("Activity check message sent.", ephemeral=True)


@bot.tree.command(name="tryout", description="Create a tryout announcement that DMs the code at the given time.")
@app_commands.describe(
    time="Time the tryout starts (e.g. '11:12PM EST')",
    code="Code / lobby / extra info (will be DMed to people)",
    date="Date of the tryout (just for display)"
)
async def tryout(interaction: discord.Interaction, time: str, code: str, date: str):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    start_utc = parse_time_string(time)
    if start_utc is None:
        await interaction.response.send_message("Invalid time format. Examples: `11:12PM`, `11:12 PM EST`", ephemeral=True)
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    delay_seconds = (start_utc - now_utc).total_seconds()
    if delay_seconds <= 0:
        await interaction.response.send_message("The time you entered is in the past. Please enter a future time.", ephemeral=True)
        return

    start_local = start_utc.astimezone(TIMEZONE)
    start_display = start_local.strftime("%I:%M%p %Z")

    guild = interaction.guild
    channel = guild.get_channel(TRYOUT_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("Tryout channel not found. Check TRYOUT_CHANNEL_ID.", ephemeral=True)
        return

    # Channel message
    content = (
        "@everyone\n"
        "# TRYOUT Announcement 🚨\n"
        f"If you want to tryout at **{start_display}**, please react to this message with the team logo.\n"
        f"(**{REQUIRED_REACTIONS} Or More Reactions Required**)\n"
        "Once you have react, you will receive the code in your DMs when the tryout starts."
    )

    msg = await channel.send(content)

    tryout_codes[msg.id] = code
    interested_users[msg.id] = set()

    team_emoji = get_team_emoji(guild)
    if team_emoji:
        try:
            await msg.add_reaction(team_emoji)
        except Exception as e:
            print("Failed to add team emoji (tryout):", e)

    async def send_dms_at(message_id: int, delay: float):
        await asyncio.sleep(delay)

        g = bot.get_guild(GUILD_ID)
        if g is None:
            return
        ch = g.get_channel(TRYOUT_CHANNEL_ID)
        if ch is None:
            return

        try:
            m = await ch.fetch_message(message_id)
        except Exception:
            return

        emoji_inner = get_team_emoji(g)
        if not emoji_inner:
            return

        total_reactions = 0
        for r in m.reactions:
            if getattr(r.emoji, "id", None) == getattr(emoji_inner, "id", None) or str(r.emoji) == str(emoji_inner):
                total_reactions = r.count

        if total_reactions < REQUIRED_REACTIONS:
            return

        code_to_send = tryout_codes.get(message_id)
        if code_to_send is None:
            return

        users_to_dm = interested_users.get(message_id, set())

        for uid in list(users_to_dm):
            user = g.get_member(uid)
            if user is None or user.bot:
                continue
            try:
                dm = await user.create_dm()
                await dm.send(
                    f"Your tryout code is:\n"
                    f"# {code_to_send}\n"
                    f"You will have **5 minutes** to join the code that is sent to you. "
                    f"If this is not done in this period of time, the message will auto-delete.\n\n"
                    f"Good luck in the tryout!",
                    delete_after=300
                )
            except Exception as e:
                print(f"Failed to DM {user} ({user.id}): {e}")

    task = asyncio.create_task(send_dms_at(msg.id, delay_seconds))
    tryout_tasks[msg.id] = task

    await interaction.response.send_message(f"Tryout announcement posted for {date}. Starts at {start_display}.", ephemeral=True)


@bot.tree.command(
    name="submit-score",
    description="Post a scrim score (admins only)."
)
@app_commands.describe(
    team="Opponent team name",
    result="Wins or Lose",
    score="Score (e.g. 5-0)",
    record="Record (e.g. 5W/0L)",
    official="Official or Unofficial"
)
@app_commands.choices(
    result=[app_commands.Choice(name="Wins", value="Wins"), app_commands.Choice(name="Lose", value="Lose")],
    official=[app_commands.Choice(name="Official", value="Official"), app_commands.Choice(name="Unofficial", value="Unofficial")]
)
async def submit_score(
    interaction: discord.Interaction,
    team: str,
    result: app_commands.Choice[str],
    score: str,
    record: str,
    official: app_commands.Choice[str],
):
    if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
        await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(SCORES_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("Scores channel not found. Check SCORES_CHANNEL_ID.", ephemeral=True)
        return

    scrim = get_latest_scrim()
    lineup_ids = scrim["lineup"] if scrim else []
    # cap lineup display at 4 lines
    lines = []
    for uid in lineup_ids[:4]:
        lines.append(f"> <@{uid}>")
    while len(lines) < 4:
        lines.append("> -")

    msg = (
        f"# GOAT vs {team}\n"
        f"## GOAT {result.value} ({score})\n"
        f"### Record ({record})\n"
        f"## __Lineup__\n"
        + "\n".join(lines) + "\n"
        f"# {official.value}"
    )

    await channel.send(msg)
    await interaction.response.send_message("Score submitted.", ephemeral=True)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
    if payload.channel_id != TRYOUT_CHANNEL_ID:
        return
    if payload.message_id not in tryout_codes:
        return
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    emoji_obj = get_team_emoji(guild)
    if emoji_obj is None:
        return

    # compare emoji ids if possible
    if getattr(payload.emoji, "id", None) != getattr(emoji_obj, "id", None):
        # also allow string compare for unicode fallback
        if str(payload.emoji) != str(emoji_obj):
            return

    interested_users.setdefault(payload.message_id, set()).add(payload.user_id)


# ---------- SCRIM implementation ----------
MENTION_RE = re.compile(r"<@!?(\d+)>")
ID_RE = re.compile(r"\b\d{17,20}\b")


def parse_people_field(people_str: str) -> list[int]:
    ids = []
    for m in MENTION_RE.finditer(people_str):
        ids.append(int(m.group(1)))
    if not ids:
        for m in ID_RE.finditer(people_str):
            ids.append(int(m.group(0)))
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def scrim_message_content(scrim) -> str:
    team = scrim["team"]
    time = scrim["time"]
    official = scrim["official"]
    lineup = scrim["lineup"]
    cap = scrim["capacity"]
    lines = []
    lines.append(f"# GOAT vs {team}")
    lines.append(f"Time: {time}")
    lines.append(f"# {official}")
    lines.append("LineUp:")
    if lineup:
        for i, uid in enumerate(lineup, start=1):
            lines.append(f"> {i}. <@{uid}>")
    else:
        for i in range(1, cap + 1):
            lines.append(f"> {i}.")
    lines.append(f"**{cap}v{cap}**")
    lines.append("*Do you accept or deny?*")
    return "\n".join(lines)


class ScrimView(discord.ui.View):
    def __init__(self, scrim_id: str):
        super().__init__(timeout=None)
        self.scrim_id = scrim_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="scrim_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = SCRIMS.get(self.scrim_id)
        if scrim is None:
            await interaction.response.send_message("This scrim no longer exists.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in scrim["lineup"]:
            await interaction.response.send_message("You are already in the lineup.", ephemeral=True)
            return

        if len(scrim["lineup"]) >= scrim["capacity"]:
            await interaction.response.send_message("Lineup is full.", ephemeral=True)
            return

        scrim["lineup"].append(uid)
        await update_all_scrim_dms(scrim)
        await interaction.response.send_message("You have been added to the lineup.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="scrim_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = SCRIMS.get(self.scrim_id)
        if scrim is None:
            await interaction.response.send_message("This scrim no longer exists.", ephemeral=True)
            return

        uid = interaction.user.id
        if uid in scrim["lineup"]:
            scrim["lineup"].remove(uid)
            await update_all_scrim_dms(scrim)
            await interaction.response.send_message("You have been removed from the lineup.", ephemeral=True)
            return

        await interaction.response.send_message("You are not in the lineup.", ephemeral=True)


async def update_all_scrim_dms(scrim):
    view = ScrimView(scrim["id"])
    for user_id, msg_id in list(scrim["dm_messages"].items()):
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            channel = user.dm_channel or await user.create_dm()
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(content=f"<@{user_id}>\n{scrim_message_content(scrim)}", view=view)
            except discord.NotFound:
                new_msg = await channel.send(f"<@{user_id}>\n{scrim_message_content(scrim)}", view=view)
                scrim["dm_messages"][user_id] = new_msg.id
        except Exception:
            continue


@bot.tree.command(name="scrim", description="Invite listed people to a scrim (admins only).")
@app_commands.describe(team="Opposing team name", time="Time (e.g. 'Today at 2:30PM EST')", official="official or unofficial", people="Mentions or IDs separated by space")
@app_commands.choices(official=[app_commands.Choice(name="official", value="official"), app_commands.Choice(name="unofficial", value="unofficial")])
async def scrim_cmd(interaction: discord.Interaction, team: str, time: str, official: app_commands.Choice[str], people: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return

    user_ids = parse_people_field(people)
    if not user_ids:
        await interaction.response.send_message("No valid people found in the people field.", ephemeral=True)
        return

    if len(user_ids) > 10:
        await interaction.response.send_message("Please list up to 10 people.", ephemeral=True)
        return

    scrim_id = str(int(datetime.datetime.utcnow().timestamp() * 1000))
    scrim = {
        "id": scrim_id,
        "team": team,
        "time": time,
        "official": official.value,
        "owner": interaction.user.id,
        "capacity": MAX_LINEUP,
        "lineup": [],
        "dm_messages": {},
        "match_message_id": None,  # will store message id in match channel
    }
    SCRIMS[scrim_id] = scrim

    # send DMs
    view = ScrimView(scrim_id)
    failed = []
    for uid in user_ids:
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            channel = user.dm_channel or await user.create_dm()
            msg = await channel.send(f"<@{uid}>\n{scrim_message_content(scrim)}", view=view)
            scrim["dm_messages"][uid] = msg.id
        except Exception:
            failed.append(uid)
            continue

    # send summary to match times channel
    match_channel = interaction.guild.get_channel(MATCH_CHANNEL_ID)
    if match_channel is not None:
        invited_mentions = " ".join(f"<@{u}>" for u in user_ids)
        match_content = (
            f"@here\n"
            f"# GOAT vs {team}\n"
            f"Time: {time}\n"
            f"# {official.value}\n\n"
            f"Invited lineup (accepts via DM):\n{invited_mentions}\n\n"
            f"**{MAX_LINEUP}v{MAX_LINEUP}**"
        )
        try:
            mmsg = await match_channel.send(match_content)
            scrim["match_message_id"] = mmsg.id
        except Exception as e:
            print("Failed to send match times message:", e)

    # reply to admin
    if failed:
        failed_mentions = " ".join(f"<@{u}>" for u in failed)
        await interaction.response.send_message(f"Scrim created but failed to DM: {failed_mentions}", ephemeral=True)
    else:
        await interaction.response.send_message("Scrim DMs sent and match posted.", ephemeral=True)


# ---------- Ensure rosters on startup ----------
async def ensure_rosters_exist():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print("Guild not found in ensure_rosters_exist.")
        return

    kitsune_emoji = f"<:Kitsune:{KITSUNE_EMOJI_ID}>"
    captain_mention = f"<@{CAPTAIN_ID}>"
    cocap_mention = f"<@{CO_CAPTAIN_ID}>"

    roster_targets = [
        (MAIN_ROSTER_CHANNEL_ID, "Main"),
        (MID_ROSTER_CHANNEL_ID, "Invite"),
        (LOW_ROSTER_CHANNEL_ID, "Sub"),
    ]

    for channel_id, title in roster_targets:
        channel = guild.get_channel(channel_id)
        if channel is None:
            print(f"Roster channel {channel_id} not found for {title} roster.")
            continue

        try:
            async for m in channel.history(limit=50):
                await m.delete()
        except Exception as e:
            print(f"Failed to clear messages in {channel_id}: {e}")

        content = roster_template(title, kitsune_emoji, captain_mention, cocap_mention)
        try:
            await channel.send(content)
            print(f"Created {title} roster template in channel {channel_id}.")
        except Exception as e:
            print(f"Failed to send roster template to {channel_id}:", e)


# ---------- Events ----------
@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        return

    msg = await channel.send(
        f"Welcome {member.mention} to **KSU**! *Please Read The Server Rules*"
    )

    # Get the emoji object from the guild by ID
    emoji = discord.utils.get(member.guild.emojis, id=GOAT_EMOJI_ID)
    if emoji is None:
        print("Emoji not found in this guild.")
        return

    try:
        await msg.add_reaction(emoji)
    except discord.HTTPException as e:
        print(f"Failed to add reaction: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("bot.application_id:", bot.application_id)

    print("Local commands in bot.tree:")
    for c in bot.tree.get_commands():
        print(" -", c.name)

    print("Configured GUILD_ID:", GUILD_ID)
    print("Bot is in guilds:")
    for g in bot.guilds:
        print(" -", g.name, g.id)

    guild = discord.Object(id=GUILD_ID)

    try:
        # Copy all global commands to this guild, then sync them
        bot.tree.copy_global_to(guild=guild)
        registered = await bot.tree.sync(guild=guild)
        print("Guild sync returned", len(registered), "commands:")
        for c in registered:
            print(" -", c.name)
    except Exception as e:
        print("Guild sync error:", repr(e))

    # Ensure rosters exist on startup
    await ensure_rosters_exist()


# ---------- Run ----------
bot.run(TOKEN)