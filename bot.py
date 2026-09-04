import discord
from discord.ext import commands
from discord.ui import Button, View
import sqlite3
import datetime
import random
import time
import os
import aiohttp

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "database.db"
cooldowns = {}

# Конфигурация STRATZ API
STRATZ_API_URL = "https://api.stratz.com/graphql"
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN", "") # Токен можно прописать в переменные окружения

# Конфигурация ролей и структуры сервера
SERVER_ROLES = [
    {"name": "⚡ Штурмфюрер", "color": discord.Color.dark_red(), "permissions": discord.Permissions(administrator=True), "hoist": True},
    {"name": "👁️ Смотрящий", "color": discord.Color.purple(), "permissions": discord.Permissions(manage_channels=True, manage_roles=True, kick_members=True, ban_members=True), "hoist": True},
    {"name": "🎮 Боевой товарищ", "color": discord.Color.blue(), "permissions": discord.Permissions(send_messages=True, view_channel=True), "hoist": True}
]

SERVER_STRUCTURE = {
    "📢 ИНФОРМАЦИЯ": [
        {"name": "📢-новости", "type": "text", "desc": "Официальные объявления сервера."},
        {"name": "📜-правила", "type": "text", "desc": "Свод законов и порядков."}
    ],
    "💬 ОБЩЕНИЕ": [
        {"name": "💬-курилка", "type": "text", "desc": "Общие разговоры обо всем."},
        {"name": "🔊 Общий войс", "type": "voice"}
    ],
    "👁️ СЕКРЕТНАЯ ЗОНА": [
        {"name": "👁️‍🗨️-бункер", "type": "text", "desc": "Закрытая комната для руководства."},
        {"name": "🔒 Старший войс", "type": "voice"}
    ],
    "🔒 ЛОГИ": [
        {"name": "🔒-logs", "type": "text", "desc": "Системный журнал модерации."}
    ]
}

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            exp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_daily TEXT DEFAULT ""
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_data(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT exp, level, last_daily FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"exp": row["exp"], "level": row["level"], "last_daily": row["last_daily"]}
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (user_id, exp, level, last_daily) VALUES (?, 0, 1, '')", (user_id,))
        conn.commit()
        conn.close()
        return {"exp": 0, "level": 1, "last_daily": ""}

def update_user_data(user_id: int, exp: int, level: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET exp = ?, level = ? WHERE user_id = ?", (exp, level, user_id))
    conn.commit()
    conn.close()

def update_user_full(user_id: int, exp: int, level: int, last_daily: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET exp = ?, level = ?, last_daily = ? WHERE user_id = ?", (exp, level, last_daily, user_id))
    conn.commit()
    conn.close()

def get_rank_title(level: int):
    if level < 5:
        return "Салага"
    elif level < 10:
        return "Боец"
    elif level < 20:
        return "Ветеран"
    elif level < 35:
        return "Авторитет"
    else:
        return "Легенда улиц"

class HubView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📊 Профиль", style=discord.ButtonStyle.primary, custom_id="hub_profile")
    async def profile_button(self, interaction: discord.Interaction, button: Button):
        target = interaction.user
        user_data = get_user_data(target.id)
        current_lvl = user_data["level"]
        current_exp = user_data["exp"]
        exp_needed = current_lvl * 100
        rank_title = get_rank_title(current_lvl)

        embed = discord.Embed(title=f"📊 Профиль: {target.name}", color=0x8B0000)
        embed.add_field(name="Звание / Титул", value=f"🛡️ **{rank_title}**", inline=False)
        embed.add_field(name="Уровень", value=f"⭐ **LVL {current_lvl}**", inline=True)
        embed.add_field(name="Опыт", value=f"💬 `{current_exp} / {exp_needed} XP`", inline=True)
        embed.set_thumbnail(url=target.avatar.url if target.avatar else None)
        embed.set_footer(text="ПРАЧКА ДРАЧКА • Система уровней")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🏆 Топ", style=discord.ButtonStyle.success, custom_id="hub_top")
    async def top_button(self, interaction: discord.Interaction, button: Button):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, exp, level FROM users ORDER BY level DESC, exp DESC LIMIT 10")
        top_rows = cursor.fetchall()
        conn.close()

        desc = ""
        for index, row in enumerate(top_rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else "Боец"
            rank_title = get_rank_title(row["level"])
            desc += f"**{index}.** {name} — **LVL {row['level']}** *({rank_title})* (`{row['exp']} XP`)\n"

        embed = discord.Embed(title="🏆 ТОП-10 БОЙЦОВ СЕРВЕРА", description=desc or "Пока пусто.", color=0x8B0000)
        embed.set_footer(text="ПРАЧКА ДРАЧКА • Таблица лидеров")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎁 Ежедневка", style=discord.ButtonStyle.secondary, custom_id="hub_daily")
    async def daily_button(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        data = get_user_data(user_id)
        today = datetime.datetime.now().strftime("%Y-%m-%d")

        if data["last_daily"] == today:
            await interaction.response.send_message("⏳ Ты уже забрал свою ежедневную награду сегодня! Загляни завтра.", ephemeral=True)
            return

        reward = random.randint(50, 150)
        new_exp = data["exp"] + reward
        level = data["level"]
        exp_needed = level * 100

        msg = f"🎁 Ты успешно забрал ежедневный бонус и получил `+{reward} XP`!"
        if new_exp >= exp_needed:
            level += 1
            new_exp -= exp_needed
            rank_title = get_rank_title(level)
            msg += f"\n🎉 Поздравляем с повышением уровня! Теперь у тебя **LVL {level}** *({rank_title})*!"

        update_user_full(user_id, new_exp, level, today)
        await interaction.response.send_message(msg, ephemeral=True)

class FortuneWheelView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=60)
        self.guild = guild
        self.participants = set()

    @discord.ui.button(label="🎰 Запрыгнуть в розыгрыш", style=discord.ButtonStyle.success, custom_id="wheel_join")
    async def join_wheel(self, interaction: discord.Interaction, button: Button):
        if interaction.user in self.participants:
            await interaction.response.send_message("⚠️ Ты уже участвуешь в розыгрыше!", ephemeral=True)
            return
        self.participants.add(interaction.user)
        await interaction.response.send_message("✅ Ты успешно ворвался в список участников Колеса Фортуны!", ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        
        if not self.participants:
            return

        winner = random.choice(list(self.participants))
        role = discord.utils.get(self.guild.roles, name="⚡ Штурмфюрер")
        
        if role:
            try:
                await winner.add_roles(role)
            except:
                pass

class DuelAcceptView(View):
    def __init__(self, challenger: discord.Member, target: discord.Member, bet: int):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.target = target
        self.bet = bet
        self.value = None

    @discord.ui.button(label="⚔️ Принять вызов", style=discord.ButtonStyle.danger)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.target:
            await interaction.response.send_message("❌ Этот вызов брошен не тебе!", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Отказаться", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.target:
            await interaction.response.send_message("❌ Не лезь в чужой махач!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.send_message(f"🛡️ {self.target.mention} благоразумно уклонился от драки.", ephemeral=False)

class SborView(View):
    def __init__(self, game_name: str):
        super().__init__(timeout=None)
        self.game_name = game_name
        self.ready_users = set()

    @discord.ui.button(label="🎮 Врываюсь в катку", style=discord.ButtonStyle.success, custom_id="sbor_join")
    async def join_sbor(self, interaction: discord.Interaction, button: Button):
        self.ready_users.add(interaction.user.display_name)
        names = ", ".join(self.ready_users)
        
        embed = interaction.message.embeds[0]
        embed.clear_fields()
        embed.add_field(name="🛡️ Готовы ворваться:", value=names if names else "Никого", inline=False)
        
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("✅ Ты записан в состав!", ephemeral=True)

@bot.command(name="команды", aliases=["помощь", "commands"])
async def commands_list(ctx):
    is_staff = any(role.name in ["⚡ Штурмфюрер", "👁️ Смотрящий"] for role in ctx.author.roles) or ctx.author.guild_permissions.administrator

    embed = discord.Embed(
        title="📜 СПИСОК ДОСТУПНЫХ КОМАНД",
        description="Вот всё, чем ты можешь пользоваться на сервере. Не забывай про префикс `!`",
        color=0x8B0000
    )

    general_cmds = (
        "`!профиль` (или `!ур`) — Показать твой уровень, звание и опыт.\n"
        "`!топ` (или `!лидеры`) — Таблица топ-10 игроков сервера.\n"
        "`!ежедневка` (или `!бонус`) — Забрать ежедневную награду (XP).\n"
        "`!казино [ставка]` (или `!кости`) — Сыграть в кости на XP против бота (ставка от 10 до 500).\n"
        "`!дуэль @Юзер [ставка]` (или `!махач`) — Устроить уличный замес на XP с другим бойцом.\n"
        "`!матч [ID]` (или `!стратз`) — Анализ матча Dota 2 по ID через STRATZ API.\n"
        "`!команды` (или `!помощь`) — Вызвать это справочное меню в ЛС."
    )
    embed.add_field(name="⭐ Игровые и общие команды", value=general_cmds, inline=False)

    if is_staff:
        staff_cmds = (
            "`!хаб` — Отправить интерактивную панель управления.\n"
            "`!сбор [игра]` — Объявить общий сбор игроков (требуется роль Штурмфюрер/Смотрящий).\n"
            "`!колесо` — Запустить Колесо Фортуны на розыгрыш временной роли (Штурмфюрер/Смотрящий).\n"
            "`!очистить [кол-во]` — Удалить сообщения в канале.\n"
            "`!мут @Юзер [мин] [причина]` — Отправить нарушителя в таймаут.\n"
            "`!кик @Юзер [причина]` — Выгнать игрока с сервера.\n"
            "`!настройка` — Полностью создать структуру каналов и ролей с нуля (Админ).\n"
            "`!секрет` — Открыть доступ к скрытому бункеру (Штурмфюрер/Смотрящий)."
        )
        embed.add_field(name="🛡️ Команды руководства и модерации", value=staff_cmds, inline=False)

    embed.set_footer(text="ПРАЧКА ДРАЧКА • Информационный терминал")

    try:
        await ctx.author.send(embed=embed)
        await ctx.message.delete()
        await ctx.send(f"📬 {ctx.author.mention}, список доступных команд отправлен тебе в личные сообщения!", delete_after=6)
    except discord.Forbidden:
        await ctx.send(f"⚠️ {ctx.author.mention}, у тебя закрыты личные сообщения! Открой их, чтобы получать справку, либо смотри сюда:", embed=embed)

@bot.command(name="хаб", aliases=["hub"])
@commands.has_permissions(administrator=True)
async def hub(ctx):
    embed = discord.Embed(
        title="⚡ ПАНЕЛЬ УПРАВЛЕНИЯ БОЙЦА",
        description="Используй кнопки ниже, чтобы быстро управлять профилем, смотреть топ или забирать ежедневные награды.",
        color=0x8B0000
    )
    embed.set_footer(text="ПРАЧКА ДРАЧКА • Интерактивный терминал")
    await ctx.send(embed=embed, view=HubView())
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="ежедневка", aliases=["daily", "бонус"])
async def daily(ctx):
    user_id = ctx.author.id
    data = get_user_data(user_id)
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if data["last_daily"] == today:
        await ctx.send(f"⏳ {ctx.author.mention}, ты уже забирал бонус сегодня. Приходи завтра!", delete_after=7)
        return

    reward = random.randint(50, 150)
    new_exp = data["exp"] + reward
    level = data["level"]
    exp_needed = level * 100

    msg = f"🎁 {ctx.author.mention}, ежедневный бонус забран: `+{reward} XP`!"
    if new_exp >= exp_needed:
        level += 1
        new_exp -= exp_needed
        rank_title = get_rank_title(level)
        msg += f"\n🎉 Новый уровень! Теперь у тебя **LVL {level}** *({rank_title})*!"

    update_user_full(user_id, new_exp, level, today)
    await ctx.send(msg)

@bot.command(name="казино", aliases=["roll", "кости", "рулетка"])
async def roll(ctx, bet: int = 50):
    user_id = ctx.author.id
    data = get_user_data(user_id)

    if bet < 10 or bet > 500:
        await ctx.send(f"⚠️ {ctx.author.mention}, ставка в казино должна быть от **10** до **500 XP**!", delete_after=7)
        return

    if data["exp"] < bet:
        await ctx.send(f"⚠️ {ctx.author.mention}, у тебя недостаточно опыта (`{bet} XP`) для игры в казино!", delete_after=7)
        return

    user_roll = random.randint(1, 6) + random.randint(1, 6)
    bot_roll = random.randint(1, 6) + random.randint(1, 6)

    exp = data["exp"]
    level = data["level"]

    if user_roll > bot_roll:
        exp += bet
        result = f"🎰 **Казино:** Ты выбросил `{user_roll}`, бот — `{bot_roll}`.\n🏆 Победа! Ты выигрываешь `+{bet} XP`!"
    elif user_roll < bot_roll:
        exp -= bet
        if exp < 0: exp = 0
        result = f"🎰 **Казино:** Ты выбросил `{user_roll}`, бот — `{bot_roll}`.\n💥 Проигрыш! Ты теряешь `-{bet} XP`."
    else:
        result = f"🎰 **Казино:** Ничья на кубиках (`{user_roll}:{bot_roll}`). Ставка возвращена на баланс."

    update_user_data(user_id, exp, level)
    await ctx.send(f"{ctx.author.mention}\n{result}")

@bot.command(name="профиль", aliases=["lvl", "ур"])
async def lvl(ctx, member: discord.Member = None):
    target = member or ctx.author
    user_data = get_user_data(target.id)

    current_lvl = user_data["level"]
    current_exp = user_data["exp"]
    exp_needed = current_lvl * 100
    rank_title = get_rank_title(current_lvl)

    embed = discord.Embed(
        title=f"📊 Профиль: {target.name}",
        color=0x8B0000,
    )
    embed.add_field(name="Звание / Титул", value=f"🛡️ **{rank_title}**", inline=False)
    embed.add_field(name="Уровень", value=f"⭐ **LVL {current_lvl}**", inline=True)
    embed.add_field(
        name="Опыт", value=f"💬 `{current_exp} / {exp_needed} XP`", inline=True
    )
    embed.set_thumbnail(url=target.avatar.url if target.avatar else None)
    embed.set_footer(text="ПРАЧКА ДРАЧКА • Система уровней")

    await ctx.send(embed=embed)

@bot.command(name="топ", aliases=["top", "лидеры"])
async def top(ctx):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, exp, level FROM users ORDER BY level DESC, exp DESC LIMIT 10")
    top_rows = cursor.fetchall()
    conn.close()

    desc = ""
    for index, row in enumerate(top_rows, start=1):
        member = ctx.guild.get_member(row["user_id"])
        name = member.display_name if member else "Боец"
        rank_title = get_rank_title(row["level"])
        desc += f"**{index}.** {name} — **LVL {row['level']}** *({rank_title})* (`{row['exp']} XP`)\n"

    embed = discord.Embed(title="🏆 ТОП-10 БОЙЦОВ СЕРВЕРА", description=desc or "Пока пусто.", color=0x8B0000)
    embed.set_footer(text="ПРАЧКА ДРАЧКА • Таблица лидеров")
    await ctx.send(embed=embed)

@bot.command(name="матч", aliases=["match", "стратз"])
async def match_stats(ctx, match_id: int):
    query = """
    {
      match(id: %d) {
        id
        didRadiantWin
        duration
        gameMode
        startDateTime
        players {
          isRadiant
          heroId
          playerStat {
            kda
            gpm
            xpm
            networth
          }
          steamAccount {
            name
          }
        }
      }
    }
    """ % match_id

    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "StratzDiscordBot"
    }

    async with ctx.typing():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(STRATZ_API_URL, json={"query": query}, headers=headers) as response:
                    if response.status != 200:
                        await ctx.send(f"❌ Ошибка соединения со STRATZ API (Код: {response.status})", delete_after=7)
                        return
                    
                    data = await response.json()
                    
                    if "errors" in data:
                        await ctx.send(f"⚠️ Ошибка в запросе к API: {data['errors'][0]['message']}", delete_after=7)
                        return

                    match_data = data.get("data", {}).get("match")
                    if not match_data:
                        await ctx.send(f"❌ Матч с ID `{match_id}` не найден или еще не обработан парсером.", delete_after=7)
                        return

                    radiant_win = match_data.get("didRadiantWin")
                    duration_min = match_data.get("duration", 0) // 60
                    duration_sec = match_data.get("duration", 0) % 60
                    
                    winner_text = "🟢 Победа Сил Света (Radiant)" if radiant_win else "🔴 Победа Сил Тьмы (Dire)"
                    embed_color = 0x57F287 if radiant_win else 0xED4245

                    embed = discord.Embed(
                        title=f"📊 Анализ матча Dota 2 #{match_id}",
                        description=f"**Итог:** {winner_text}\n**Длительность:** {duration_min} мин. {duration_sec} сек.",
                        color=embed_color
                    )

                    radiant_players = []
                    dire_players = []

                    for p in match_data.get("players", []):
                        name = p.get("steamAccount") and p.get("steamAccount").get("name") or "Аноним"
                        stat = p.get("playerStat") or {}
                        kda = stat.get("kda", "N/A")
                        gpm = stat.get("gpm", 0)
                        
                        player_line = f"• **{name}** — KDA: `{kda}` | GPM: `{gpm}`"
                        
                        if p.get("isRadiant"):
                            radiant_players.append(player_line)
                        else:
                            dire_players.append(player_line)

                    embed.add_field(name="🟢 Radiant", value="\n".join(radiant_players[:5]) or "Нет данных", inline=False)
                    embed.add_field(name="🔴 Dire", value="\n".join(dire_players[:5]) or "Нет данных", inline=False)
                    embed.set_footer(text="ПРАЧКА ДРАЧКА • Аналитика STRATZ API")

                    await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Произошла ошибка при запросе: {e}", delete_after=7)

@bot.command(name="колесо", aliases=["wheel"])
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def wheel(ctx):
    embed = discord.Embed(
        title="🎰 КОЛЕСО ФОРТУНЫ ЗАПУЩЕНО!",
        description="Срочно жми на кнопку ниже, чтобы ворваться в розыгрыш эксклюзивной временной роли на 24 часа! Успей запрыгнуть, пока идет таймер (1 минута).",
        color=0x8B0000
    )
    embed.set_footer(text="ПРАЧКА ДРАЧКА • Испытай удачу")
    
    view = FortuneWheelView(ctx.guild)
    await ctx.send(embed=embed, view=view)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="дуэль", aliases=["duel", "драка", "махач"])
async def duel(ctx, member: discord.Member, bet: int = 50):
    user = ctx.author
    now = time.time()

    if member.bot:
        await ctx.send("🤖 С железом воевать — себя не уважать. Зови живого бойца!", delete_after=5)
        return
    
    if member == user:
        await ctx.send("🤡 Сам с собой в зеркало захотел подраться? Охлади пыл.", delete_after=5)
        return

    if bet < 10 or bet > 500:
        await ctx.send(f"⚠️ {user.mention}, ставка должна быть в пределах от **10** до **500 XP**!", delete_after=7)
        return

    if user.id in cooldowns and now < cooldowns[user.id]["time"]:
        left = int((cooldowns[user.id]["time"] - now) / 60) + 1
        status = cooldowns[user.id]["status"]
        reason = "отлеживаешься в больнице после прошлых замесов" if status == "hospital" else "отдыхаешь в баре с пивом"
        await ctx.send(f"⏳ {user.mention}, погоди! Ты сейчас {reason}. Еще примерно **{left} мин.** не до драк.", delete_after=7)
        return

    if member.id in cooldowns and now < cooldowns[member.id]["time"]:
        left = int((cooldowns[member.id]["time"] - now) / 60) + 1
        status = cooldowns[member.id]["status"]
        reason = "валяется в больнице" if status == "hospital" else "отдыхает в баре"
        await ctx.send(f"⏳ {member.mention} сейчас занят — {reason} (еще ~{left} мин.). Не трогай калеку.", delete_after=7)
        return

    user_data = get_user_data(user.id)
    member_data = get_user_data(member.id)

    if user_data["exp"] < bet:
        await ctx.send(f"⚠️ {user.mention}, у тебя не хватает опыта (`{bet} XP`), чтобы вывозить этот базар!", delete_after=7)
        return

    if member_data["exp"] < bet:
        await ctx.send(f"⚠️ У {member.mention} пустые карманы, у него нет столько опыта для ставки!", delete_after=7)
        return

    view = DuelAcceptView(challenger=user, target=member, bet=bet)
    msg = await ctx.send(f"⚔️ {member.mention}, тебе бросил вызов на уличный замес боец {user.mention}!\n💰 **Ставка:** `{bet} XP`.\nПримешь вызов?", view=view)
    
    await view.wait()

    if view.value is None:
        await msg.edit(content=f"⌛ Время вызова истекло. {member.mention} проигнорировал махач.", view=None)
        return
    elif view.value is False:
        await msg.edit(content=f"🛡️ {member.mention} отказался от драки.", view=None)
        return

    user_data = get_user_data(user.id)
    member_data = get_user_data(member.id)
    if user_data["exp"] < bet or member_data["exp"] < bet:
        await msg.edit(content="❌ У кого-то из участников внезапно не хватило опыта на балансе для проведения боя!", view=None)
        return

    round_phrases = [
        "прописывает сокрушительный лоу-кик в область печени!",
        "исполняет жесткую вертуху из девяностых прямо в челюсть!",
        "пробивает глухую защиту резким боковым ударом!",
        "проводит молниеносный борцовский проход в две ноги!",
        "врубает жесткий префаер и ловит оппонента на противоходе!",
        "налетает с яростью бешеного пса и забивает у сетки!",
        "ловко уворачивается от летящего кулака и контратакует в корпус!"
    ]

    user_score = 0
    member_score = 0
    round_logs = []

    for round_num in range(1, 4):
        if user_score == 2 or member_score == 2:
            break

        u_roll = random.randint(1, 100)
        m_roll = random.randint(1, 100)

        if u_roll > m_roll:
            user_score += 1
            action = random.choice(round_phrases)
            round_logs.append(f"🥊 **Раунд {round_num}:** {user.mention} {action} *({user_score}:{member_score})*")
        elif m_roll > u_roll:
            member_score += 1
            action = random.choice(round_phrases)
            round_logs.append(f"💥 **Раунд {round_num}:** {member.mention} {action} *({user_score}:{member_score})*")
        else:
            round_logs.append(f"🤝 **Раунд {round_num}:** Обоюдный плотный обмен ударами, ничья в раунде! *({user_score}:{member_score})*")

    embed = discord.Embed(title="⚔️ ПОДПОЛЬНЫЙ ТУРНИР: УЛИЧНЫЙ ЗАМЕС", color=0x8B0000)
    
    if user_score > member_score:
        winner, loser = user, member
        w_data = user_data
        l_data = member_data
        
        w_data["exp"] += bet
        l_data["exp"] -= bet
        if l_data["exp"] < 0: l_data["exp"] = 0

        update_user_data(winner.id, w_data["exp"], w_data["level"])
        update_user_data(loser.id, l_data["exp"], l_data["level"])
        
        cooldowns[loser.id] = {"time": now + 600, "status": "hospital"}
        cooldowns[winner.id] = {"time": now + 300, "status": "bar"}

        result_text = (
            f"🏆 **Победитель серии:** {winner.mention} со счетом **{user_score}:{member_score}**!\n"
            f"💰 Куш забран: `+{bet} XP`\n\n"
            f"🚑 **{loser.mention}** отлетает в больницу на **10 минут**.\n"
            f"🍻 **{winner.mention}** уходит в бар бухать на **5 минут** отмывать кровь."
        )
    elif member_score > user_score:
        winner, loser = member, user
        w_data = member_data
        l_data = user_data

        w_data["exp"] += bet
        l_data["exp"] -= bet
        if l_data["exp"] < 0: l_data["exp"] = 0

        update_user_data(winner.id, w_data["exp"], w_data["level"])
        update_user_data(loser.id, l_data["exp"], l_data["level"])

        cooldowns[loser.id] = {"time": now + 600, "status": "hospital"}
        cooldowns[winner.id] = {"time": now + 300, "status": "bar"}

        result_text = (
            f"🏆 **Победитель серии:** {winner.mention} оформляет камбэк со счетом **{member_score}:{user_score}**!\n"
            f"💰 Куш забран: `+{bet} XP`\n\n"
            f"🚑 **{loser.mention}** отлетает в больницу на **10 минут**.\n"
            f"🍻 **{winner.mention}** уходит в бар отдыхать на **5 минут**."
        )
    else:
        result_text = f"🤝 Плотная ничья по итогам раундов (`{user_score}:{member_score}`). Никто не пострадал, разойдитесь по домам."

    embed.description = "\n".join(round_logs) + f"\n\n-------------------\n{result_text}"
    await msg.edit(content=None, embed=embed, view=None)

@bot.command(name="настройка", aliases=["setup"])
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild
    await ctx.send("⚡ Навожу порядок и настраиваю права для своих...")

    for role_data in SERVER_ROLES:
        existing_role = discord.utils.get(guild.roles, name=role_data["name"])
        if not existing_role:
            await guild.create_role(
                name=role_data["name"],
                color=role_data["color"],
                permissions=role_data["permissions"],
                hoist=role_data["hoist"],
            )

    for category_name, channels in SERVER_STRUCTURE.items():
        overwrites = {}

        if "СЕКРЕТНАЯ ЗОНА" in category_name or "ДОПОЛНИТЕЛЬНО" in category_name:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True),
            }

        category = await guild.create_category(category_name, overwrites=overwrites)

        for ch_data in channels:
            if ch_data["type"] == "text":
                channel = await guild.create_text_channel(
                    ch_data["name"], category=category
                )
                embed = discord.Embed(
                    title=f"Канал: #{ch_data['name']}",
                    description=ch_data["desc"],
                    color=0x8B0000,
                )
                await channel.send(embed=embed)
            elif ch_data["type"] == "voice":
                await guild.create_voice_channel(ch_data["name"], category=category)

    await ctx.send(
        "✅ Всё готово! Сервер полностью укомплектован под совместные катки."
    )

@bot.command(name="сбор", aliases=["call"])
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def сбор(ctx, *, game_name: str = "в катку"):
    role = discord.utils.get(ctx.guild.roles, name="🎮 Боевой товарищ")
    role_mention = role.mention if role else "@everyone"

    embed = discord.Embed(
        title="🚨 СБОР СОСТАВА",
        description=(
            f"**{ctx.author.mention}** объявляет общий сбор!\nСрочно залетаем"
            f" в голосовой канал `{game_name}`."
        ),
        color=0x8B0000,
    )
    embed.add_field(name="🛡️ Готовы ворваться:", value="Никого", inline=False)
    embed.set_footer(text="Дисциплина — залог победы.")

    news_channel = discord.utils.get(ctx.guild.text_channels, name="📢-новости")
    target_channel = news_channel if news_channel else ctx.channel

    view = SborView(game_name)
    await target_channel.send(content=role_mention, embed=embed, view=view)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="очистить", aliases=["clear"])
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def clear(ctx, amount: int = 10):
    await ctx.channel.purge(limit=amount + 1)
    log_channel = discord.utils.get(ctx.guild.text_channels, name="🔒-logs")
    if log_channel:
        await log_channel.send(
            f"🧹 **[ОЧИСТКА]** В канале {ctx.channel.mention} стерто {amount}"
            f" сообщений ({ctx.author.mention})."
        )

@bot.command(name="мут", aliases=["mute"])
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def mute(
    ctx, member: discord.Member, minutes: int = 10, *, reason="Не указана"
):
    try:
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await ctx.send(
            f"🤐 {member.mention} отправлен в мут на {minutes} мин. Причина:"
            f" {reason}"
        )

        log_channel = discord.utils.get(ctx.guild.text_channels, name="🔒-logs")
        if log_channel:
            await log_channel.send(
                f"🤐 **[МУТ]** {member.mention} заглушен на {minutes} мин."
                f" Модератор: {ctx.author.mention}. Причина: {reason}"
            )
    except Exception as e:
        await ctx.send(f"Не удалось выдать мут: {e}", delete_after=5)

@bot.command(name="кик", aliases=["kick"])
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def kick(ctx, member: discord.Member, *, reason="Нарушение правил"):
    try:
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member.mention} выгнан с сервера. Причина: {reason}")

        log_channel = discord.utils.get(ctx.guild.text_channels, name="🔒-logs")
        if log_channel:
            await log_channel.send(
                f"👢 **[КИК]** {member} изгнан модератором {ctx.author.mention}."
                f" Причина: {reason}"
            )
    except Exception as e:
        await ctx.send(f"Не удалось кикнуть: {e}", delete_after=5)

@bot.command(name="секрет", aliases=["secret"])
async def secret(ctx):
    allowed_roles = ["⚡ Штурмфюрер", "👁️ Смотрящий"]
    has_access = any(role.name in allowed_roles for role in ctx.author.roles)

    if not has_access:
        await ctx.send(
            f"{ctx.author.mention}, доступ запрещен. Этот бункер только для"
            " старших по званию.",
            delete_after=7,
        )
        try:
            await ctx.message.delete()
        except:
            pass
        return

    hidden_channel = discord.utils.get(
        ctx.guild.text_channels, name="👁️‍🗨️-бункер"
    )
    if hidden_channel:
        await hidden_channel.set_permissions(
            ctx.author, view_channel=True, send_messages=True
        )
        await ctx.send(
            f"{ctx.author.mention}, проход в {hidden_channel.mention} открыт.",
            delete_after=10,
        )
    else:
        await ctx.send("Сначала прожми `!настройка`!", delete_after=5)
    try:
        await ctx.message.delete()
    except:
        pass

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("Ошибка: Токен не найден в переменных окружения TOKEN!")
    else:
        bot.run(token)
