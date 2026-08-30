import datetime
import os
import asyncio
import discord
from discord.ext import commands
from aiohttp import web

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
intents.members = True  # Обязательно для выдачи ролей и пингов

bot = commands.Bot(command_prefix="!", intents=intents)

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


@bot.event
async def on_ready():
    print(f"Бот {bot.user} в деле!")
    # Запускаем веб-сервер в фоне для Render
    asyncio.create_task(start_web_server())
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
