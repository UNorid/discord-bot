import datetime
import os
import asyncio
import random
import time
import sqlite3
import discord
from discord.ext import commands, tasks
from aiohttp import web

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Инициализация базы данных SQLite
DB_FILE = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            exp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Функции работы с базой данных
def get_user_data(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT exp, level FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"exp": row[0], "level": row[1]}
    return {"exp": 0, "level": 1}

def update_user_data(user_id, exp, level):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, exp, level) 
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET 
            exp = excluded.exp, 
            level = excluded.level
    """, (user_id, exp, level))
    conn.commit()
    conn.close()

# Словарь для отслеживания кулдаунов (больница / бар): {user_id: {"time": timestamp, "status": "hospital"/"bar"}}
cooldowns = {}

# Функция для получения названия ранга по уровню (все 100 уровней)
def get_rank_title(level):
    if level >= 90:
        return "👑 Легенда"
    elif level >= 80:
        return "🔴 Бессмертный"
    elif level >= 70:
        return "🟣 Владыка"
    elif level >= 60:
        return "🟣 Элита"
    elif level >= 50:
        return "🟠 Авторитет"
    elif level >= 40:
        return "🟠 Головорез"
    elif level >= 30:
        return "🟡 Ветеран"
    elif level >= 20:
        return "🟡 Служака"
    elif level >= 10:
        return "🟢 Боец"
    else:
        return "🟢 Новобранец"

# Простенький web-сервер для того, чтобы Render видел открытый порт
async def handle(request):
    return web.Response(text="Bot is running!")

app = web.Application()
app.router.add_get("/", handle)

async def start_web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")


SERVER_ROLES = [
    {
        "name": "⚡ Штурмфюрер",
        "color": discord.Color.dark_red(),
        "permissions": discord.Permissions(administrator=True),
        "hoist": True,
    },
    {
        "name": "👁️ Смотрящий",
        "color": discord.Color.blue(),
        "permissions": discord.Permissions(
            manage_messages=True, kick_members=True, ban_members=True
        ),
        "hoist": True,
    },
    {
        "name": "🎮 Боевой товарищ",
        "color": discord.Color.green(),
        "permissions": discord.Permissions(send_messages=True, view_channel=True),
        "hoist": True,
    },
]

SERVER_STRUCTURE = {
    "📜 ТЕКСТОВЫЕ КАНАЛЫ": [
        {
            "name": "📜-правила",
            "type": "text",
            "desc": (
                "**Свои правила для своих:**\n\n1. Уважение в катах и"
                " чате.\n2. Никакого токсичного мусора.\n*Залетел — играй"
                " до конца.*"
            ),
        },
        {
            "name": "💬-флудилка",
            "type": "text",
            "desc": "Общаемся, скидываем мемы и координируемся перед каткой.",
        },
        {
            "name": "📢-новости",
            "type": "text",
            "desc": "Важные анонсы и сбор состава на вечер.",
        },
    ],
    "🛠️ ДОПОЛНИТЕЛЬНО": [
        {
            "name": "🌍-команды-для-ботов",
            "type": "text",
            "desc": "Всякий мусор для музыки и ботов.",
        },
        {
            "name": "🔒-logs",
            "type": "text",
            "desc": "Служебный канал для логов сервера (только для своих).",
        },
    ],
    "🔊 ГОЛОСОВЫЕ КАНАЛЫ": [
        {
            "name": "🔥 Основной",
            "type": "voice",
            "desc": "Основная комната для посиделок.",
        },
        {
            "name": "🎮 ИГРУЛИ",
            "type": "voice",
            "desc": "Святая святых для каток.",
        },
        {
            "name": "⏱️ Дуэт",
            "type": "voice",
            "desc": "Приватный войс на двоих.",
        },
        {
            "name": "💀 АФК",
            "type": "voice",
            "desc": "Отошел попить чаю или в тильт.",
        },
    ],
    "✨ СЕКРЕТНАЯ ЗОНА": [
        {
            "name": "👁️‍🗨️-бункер",
            "type": "text",
            "desc": "Секретный схрон строго для Штурмфюрера и Смотрящего.",
        }
    ],
}


# Функция обновления интерактивного статуса
async def update_server_status(guild):
    gaming_vc = discord.utils.get(guild.voice_channels, name="🎮 ИГРУЛИ")
    main_vc = discord.utils.get(guild.voice_channels, name="🔥 Основной")
    duo_vc = discord.utils.get(guild.voice_channels, name="⏱️ Дуэт")

    gaming_count = len(gaming_vc.members) if gaming_vc else 0
    main_count = len(main_vc.members) if main_vc else 0
    duo_count = len(duo_vc.members) if duo_vc else 0

    total = gaming_count + main_count + duo_count

    if total > 0:
        parts = []
        if gaming_count > 0:
            parts.append(f"Игры: {gaming_count}")
        if main_count > 0:
            parts.append(f"Общение: {main_count}")
        if duo_count > 0:
            parts.append(f"Дуэт: {duo_count}")

        status_text = " | ".join(parts)
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=status_text
            )
        )
    else:
        await bot.change_presence(
            activity=discord.Game(name="Ждет сбора состава ⚡")
        )


# Фоновая задача: начисление опыта за нахождение в голосовых каналах (каждую минуту)
@tasks.loop(minutes=1)
async def voice_exp_loop():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            if vc.name == "💀 АФК" or len(vc.members) < 2:
                continue
            
            for member in vc.members:
                if member.bot:
                    continue
                if member.voice and (member.voice.self_mute or member.voice.self_deaf):
                    continue

                user_id = member.id
                data = get_user_data(user_id)
                exp = data["exp"] + 3
                level = data["level"]

                exp_needed = level * 100

                if exp >= exp_needed:
                    level += 1
                    exp -= exp_needed
                    rank_title = get_rank_title(level)
                    
                    channel = discord.utils.get(guild.text_channels, name="💬-флудилка")
                    if channel:
                        await channel.send(
                            f"🎉 {member.mention} поднял уровень за активность в войсе! Теперь у него **LVL {level}** *({rank_title})*!"
                        )
                
                update_user_data(user_id, exp, level)


# Фоновая задача: раз в сутки автоматически постит топ игроков в флудилку
@tasks.loop(hours=24)
async def auto_leaderboard():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, exp, level FROM users ORDER BY level DESC, exp DESC LIMIT 10")
    top_rows = cursor.fetchall()
    conn.close()

    if not top_rows:
        return

    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="💬-флудилка")
        if not channel:
            continue

        desc = ""
        for index, (uid, exp, lvl) in enumerate(top_rows, start=1):
            member = guild.get_member(uid)
            name = member.display_name if member else "Боец"
            rank_title = get_rank_title(lvl)
            desc += f"**{index}.** {name} — **LVL {lvl}** *({rank_title})* (`{exp} XP`)\n"

        embed = discord.Embed(
            title="🏆 ТАБЛИЦА РАНГОВ СЕРВЕРА",
            description=desc,
            color=0x8B0000,
        )
        embed.set_footer(text="Автоматическая сводка • ПРАЧКА ДРАЧКА")
        await channel.send(embed=embed)


@bot.event
async def on_ready():
    print(f"Бот {bot.user} в деле!")
    asyncio.create_task(start_web_server())
    if not auto_leaderboard.is_running():
        auto_leaderboard.start()
    if not voice_exp_loop.is_running():
        voice_exp_loop.start()
    for guild in bot.guilds:
        await update_server_status(guild)


@bot.event
async def on_member_join(member):
    role = discord.utils.get(member.guild.roles, name="🎮 Боевой товарищ")
    if role:
        try:
            await member.add_roles(role)
        except Exception as e:
            print(f"Не удалось выдать роль: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    await update_server_status(member.guild)


# Обработка текстовых сообщений для начисления опыта
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = message.author.id
    data = get_user_data(user_id)
    exp = data["exp"] + 15
    level = data["level"]

    exp_needed = level * 100

    if exp >= exp_needed:
        level += 1
        exp -= exp_needed
        rank_title = get_rank_title(level)
        await message.channel.send(
            f"🎉 {message.author.mention} повысил квалификацию! Теперь у него **LVL {level}** *({rank_title})*!"
        )

    update_user_data(user_id, exp, level)
    await bot.process_commands(message)


# Команда проверки профиля и уровня
@bot.command(name="lvl")
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


# Уличный турнир / Дуэли (Bo3, ставки 10-500 XP, больница/бар)
@bot.command(name="duel", aliases=["дуэль", "драка", "махач"])
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
    await ctx.send(embed=embed)


@bot.command()
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


# Команда быстрого сбора состава
@bot.command()
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
    embed.set_footer(text="Дисциплина — залог победы.")

    news_channel = discord.utils.get(ctx.guild.text_channels, name="📢-новости")
    target_channel = news_channel if news_channel else ctx.channel

    await target_channel.send(content=role_mention, embed=embed)
    try:
        await ctx.message.delete()
    except:
        pass


@bot.command()
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def call(ctx, *, game_name: str = "в катку"):
    await сбор(ctx, game_name=game_name)


# Команды модерации
@bot.command()
@commands.has_any_role("⚡ Штурмфюрер", "👁️ Смотрящий")
async def clear(ctx, amount: int = 10):
    await ctx.channel.purge(limit=amount + 1)
    log_channel = discord.utils.get(ctx.guild.text_channels, name="🔒-logs")
    if log_channel:
        await log_channel.send(
            f"🧹 **[ОЧИСТКА]** В канале {ctx.channel.mention} стерто {amount}"
            f" сообщений ({ctx.author.mention})."
        )


@bot.command()
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


@bot.command()
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


# Пасхалка в бункер
@bot.command()
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
        await ctx.send("Сначала прожми `!setup`!", delete_after=5)
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
