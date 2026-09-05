import datetime
import os
import asyncio
import random
import time
import sqlite3
import json
import aiohttp
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from aiohttp import web

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ============================================================
# OpenDota API (Dota 2) — конфигурация
# ============================================================
# Раньше здесь использовался STRATZ GraphQL API, но запросы с IP хостинга
# (Render) блокировались Cloudflare-проверкой ("Just a moment...") ещё до
# того, как доходили до сервера — при этом с домашнего ПК тем же токеном
# всё работало. Так что дело было не в токене и не в коде, а в репутации
# IP-адресов Render у Cloudflare.
#
# OpenDota — открытый REST API без обязательного токена (есть бесплатный
# лимит запросов без ключа), обычно гораздо мягче относится к запросам с
# облачных хостингов. Если и он вдруг начнёт блокироваться — можно завести
# бесплатный API-ключ на opendota.com и передавать его через переменную
# окружения OPENDOTA_API_KEY.
OPENDOTA_BASE_URL = "https://api.opendota.com/api"
OPENDOTA_API_KEY = os.getenv("OPENDOTA_API_KEY", "")  # необязательно

OPENDOTA_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PRACHKA-DRACHKA-DiscordBot/1.0",
}

# Кэш названий героев (hero_id -> человекочитаемое имя), чтобы не запрашивать
# список героев при каждом вызове команды.
HERO_NAMES_CACHE = {}


def format_duration(total_seconds):
    if total_seconds is None:
        return "??:??"
    minutes = int(total_seconds) // 60
    seconds = int(total_seconds) % 60
    return f"{minutes}:{seconds:02d}"


async def load_hero_names():
    """Загружает и кэширует список героев Dota 2 (id -> имя) с OpenDota."""
    global HERO_NAMES_CACHE
    if HERO_NAMES_CACHE:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{OPENDOTA_BASE_URL}/heroes", headers=OPENDOTA_HEADERS
            ) as resp:
                if resp.status == 200:
                    heroes = await resp.json()
                    HERO_NAMES_CACHE = {
                        h["id"]: h.get("localized_name", f"Герой #{h['id']}")
                        for h in heroes
                    }
    except Exception as e:
        print(f"Не удалось загрузить список героев с OpenDota: {e}")


def get_hero_name(hero_id: int) -> str:
    return HERO_NAMES_CACHE.get(hero_id, f"Герой #{hero_id}")


async def fetch_opendota_match(match_id: int):
    """Делает запрос к OpenDota REST API и возвращает данные матча (dict) либо кидает исключение."""
    await load_hero_names()

    url = f"{OPENDOTA_BASE_URL}/matches/{match_id}"
    params = {}
    if OPENDOTA_API_KEY:
        params["api_key"] = OPENDOTA_API_KEY

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url, headers=OPENDOTA_HEADERS, params=params
        ) as resp:
            raw_text = await resp.text()
            content_type = resp.headers.get("Content-Type", "")

            # Если сервер вернул не JSON (например html-страницу блокировки),
            # сразу даём понятную диагностику вместо падения с невнятной ошибкой.
            if "application/json" not in content_type:
                snippet = raw_text.strip().replace("\n", " ")[:200]
                raise RuntimeError(
                    f"OpenDota API вернул не-JSON ответ (статус {resp.status}, "
                    f"Content-Type: {content_type or 'не указан'}). Начало ответа: «{snippet}»"
                )

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Не удалось разобрать JSON от OpenDota API (статус {resp.status})."
                )

            if resp.status != 200:
                raise RuntimeError(
                    f"OpenDota API вернул статус {resp.status}: {data}"
                )

            if not data or "match_id" not in data:
                raise ValueError("Матч не найден. Проверь ID матча.")

            return data


def build_match_embed(match_data: dict, match_id: int) -> discord.Embed:
    radiant_win = match_data.get("radiant_win")
    duration = format_duration(match_data.get("duration"))
    players = match_data.get("players") or []

    winner_text = "🟢 Победа Radiant" if radiant_win else "🔴 Победа Dire"

    embed = discord.Embed(
        title=f"🎮 Матч Dota 2 #{match_id}",
        description=f"{winner_text} • ⏱️ Длительность: **{duration}**",
        color=0x8B0000,
    )

    radiant_lines = []
    dire_lines = []

    for p in players:
        hero_name = get_hero_name(p.get("hero_id", 0))
        acc_name = p.get("personaname") or "Аноним"
        kills = p.get("kills", 0)
        deaths = p.get("deaths", 0)
        assists = p.get("assists", 0)
        gpm = p.get("gold_per_min", 0)
        xpm = p.get("xp_per_min", 0)

        line = (
            f"**{hero_name}** ({acc_name})\n"
            f"⚔️ `{kills}/{deaths}/{assists}` • 💰 GPM `{gpm}` • ✨ XPM `{xpm}`"
        )

        # isRadiant может отсутствовать в некоторых ответах — тогда определяем
        # команду по player_slot (0-127 = Radiant, 128+ = Dire).
        is_radiant = p.get("isRadiant")
        if is_radiant is None:
            is_radiant = (p.get("player_slot", 0) or 0) < 128

        if is_radiant:
            radiant_lines.append(line)
        else:
            dire_lines.append(line)

    embed.add_field(
        name="🟢 Radiant",
        value="\n\n".join(radiant_lines) if radiant_lines else "Нет данных",
        inline=True,
    )
    embed.add_field(
        name="🔴 Dire",
        value="\n\n".join(dire_lines) if dire_lines else "Нет данных",
        inline=True,
    )

    embed.set_footer(text="Данные предоставлены OpenDota API")
    return embed


@bot.command(name="матч", aliases=["дота", "dota", "opendota"])
async def dota_match(ctx, match_id: int = None):
    """Анализ матча Dota 2 по его ID через OpenDota API."""
    if match_id is None:
        await ctx.send(
            f"⚠️ {ctx.author.mention}, укажи ID матча! Пример: `!матч 7891234567`",
            delete_after=10,
        )
        return

    loading_msg = await ctx.send(f"🔎 Ищу данные о матче `#{match_id}` в OpenDota...")

    try:
        match_data = await fetch_opendota_match(match_id)
        embed = build_match_embed(match_data, match_id)
        await loading_msg.edit(content=None, embed=embed)
    except ValueError as e:
        await loading_msg.edit(content=f"❌ {e}")
    except asyncio.TimeoutError:
        await loading_msg.edit(content="❌ OpenDota API не ответил вовремя. Попробуй позже.")
    except Exception as e:
        await loading_msg.edit(
            content=f"❌ Не удалось получить данные о матче: `{e}`"
        )


# ============================================================
# Сравнение с про-игроком (OpenDota Explorer API)
# ============================================================
# OpenDota предоставляет доступ к своей базе через произвольные SQL-запросы
# (эндпоинт /explorer). Это позволяет находить профессиональные матчи с тем
# же героем против максимально похожего пика врагов и сравнивать билд предметов.

ITEM_NAMES_CACHE = {}
PRO_PLAYERS_CACHE = {}


async def load_item_names():
    """Загружает и кэширует список предметов Dota 2 (id -> имя) с OpenDota."""
    global ITEM_NAMES_CACHE
    if ITEM_NAMES_CACHE:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{OPENDOTA_BASE_URL}/constants/items", headers=OPENDOTA_HEADERS
            ) as resp:
                if resp.status == 200:
                    items = await resp.json()
                    cache = {}
                    for item in items.values():
                        item_id = item.get("id")
                        if item_id:
                            cache[item_id] = (
                                item.get("dname") or item.get("name") or f"Предмет #{item_id}"
                            )
                    ITEM_NAMES_CACHE = cache
    except Exception as e:
        print(f"Не удалось загрузить список предметов с OpenDota: {e}")


async def load_pro_players():
    """Загружает и кэширует список известных про-игроков (account_id -> имя)."""
    global PRO_PLAYERS_CACHE
    if PRO_PLAYERS_CACHE:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{OPENDOTA_BASE_URL}/proPlayers", headers=OPENDOTA_HEADERS
            ) as resp:
                if resp.status == 200:
                    pros = await resp.json()
                    PRO_PLAYERS_CACHE = {
                        p["account_id"]: (
                            p.get("name") or p.get("personaname") or f"Игрок #{p['account_id']}"
                        )
                        for p in pros
                        if p.get("account_id")
                    }
    except Exception as e:
        print(f"Не удалось загрузить список про-игроков с OpenDota: {e}")


def resolve_hero_id(query: str):
    """Находит hero_id по (частичному) названию героя, например 'viper' или 'legion'."""
    query_norm = query.strip().lower()
    if not query_norm:
        return None

    for hid, name in HERO_NAMES_CACHE.items():
        if name.lower() == query_norm:
            return hid

    for hid, name in HERO_NAMES_CACHE.items():
        if query_norm in name.lower():
            return hid

    return None


def is_player_radiant(p: dict) -> bool:
    is_radiant = p.get("isRadiant")
    if is_radiant is None:
        is_radiant = (p.get("player_slot", 0) or 0) < 128
    return bool(is_radiant)


async def fetch_opendota_explorer(sql: str):
    """Выполняет SQL-запрос через OpenDota Explorer API и возвращает список строк."""
    url = f"{OPENDOTA_BASE_URL}/explorer"
    params = {"sql": sql}
    if OPENDOTA_API_KEY:
        params["api_key"] = OPENDOTA_API_KEY

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url, headers=OPENDOTA_HEADERS, params=params
        ) as resp:
            raw_text = await resp.text()
            content_type = resp.headers.get("Content-Type", "")

            if "application/json" not in content_type:
                snippet = raw_text.strip().replace("\n", " ")[:200]
                raise RuntimeError(
                    f"OpenDota Explorer вернул не-JSON ответ (статус {resp.status}). "
                    f"Начало ответа: «{snippet}»"
                )

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Не удалось разобрать JSON от OpenDota Explorer (статус {resp.status})."
                )

            if resp.status != 200:
                raise RuntimeError(
                    f"OpenDota Explorer вернул статус {resp.status}: {data}"
                )

            return data.get("rows") or []


async def find_similar_pro_match(hero_id: int, enemy_hero_ids):
    """Ищет среди недавних про-матчей с этим героем тот, где вражеский пик
    максимально похож на переданный список enemy_hero_ids."""
    sql = f"""
        SELECT pm.match_id, pm.account_id, pm.player_slot, pm.kills, pm.deaths, pm.assists,
               pm.gold_per_min, pm.xp_per_min,
               pm.item_0, pm.item_1, pm.item_2, pm.item_3, pm.item_4, pm.item_5,
               m.start_time,
               (
                 SELECT array_agg(pm2.hero_id)
                 FROM player_matches pm2
                 WHERE pm2.match_id = pm.match_id
                   AND (pm2.player_slot < 128) <> (pm.player_slot < 128)
               ) AS enemy_heroes
        FROM player_matches pm
        JOIN matches m ON m.match_id = pm.match_id
        WHERE pm.hero_id = {int(hero_id)}
          AND m.leagueid IS NOT NULL
        ORDER BY m.start_time DESC
        LIMIT 40
    """
    rows = await fetch_opendota_explorer(sql)

    enemy_set = set(h for h in (enemy_hero_ids or []) if h)
    best_row = None
    best_overlap = -1

    for row in rows:
        row_enemies = set(row.get("enemy_heroes") or [])
        overlap = len(row_enemies & enemy_set)
        if overlap > best_overlap:
            best_overlap = overlap
            best_row = row

    return best_row


def build_comparison_embed(you: dict, pro: dict, hero_id: int, enemy_heroes, match_id: int) -> discord.Embed:
    hero_data = get_hero_data(hero_id)
    hero_name = hero_data["localized_name"]
    hero_code_name = hero_data["name"]
    
    pro_account_id = pro.get("account_id")
    pro_name = PRO_PLAYERS_CACHE.get(pro_account_id, f"Игрок #{pro_account_id}")

    def item_set(entry):
        return {entry.get(f"item_{i}") for i in range(6)} - {0, None}

    you_items = item_set(you)
    pro_items = item_set(pro)

    missing_items = pro_items - you_items    # было у про, нет у тебя
    extra_items = you_items - pro_items      # было у тебя, нет у про

    def fmt_items(ids):
        names = [ITEM_NAMES_CACHE.get(i, f"Предмет #{i}") for i in ids]
        return ", ".join(sorted(names)) if names else "—"

    embed = discord.Embed(
        title=f"📊 Сравнение с про-игроком: {hero_name}",
        description=(
            f"Твой матч: `#{match_id}` • Похожая про-игра: `#{pro.get('match_id')}` "
            f"({pro_name})"
        ),
        color=0x8B0000,
    )

    # === ДОБАВЛЯЕМ КАРТИНКУ ГЕРОЯ СПРАВА ===
    if hero_code_name:
        image_url = f"https://cdn.opendota.com/apps/dota2/images/heroes/{hero_code_name}_full.png"
        embed.set_thumbnail(url=image_url)
    # ========================================

    you_kills, you_deaths, you_assists = you.get("kills", 0), you.get("deaths", 0), you.get("assists", 0)
    pro_kills, pro_deaths, pro_assists = pro.get("kills", 0), pro.get("deaths", 0), pro.get("assists", 0)
    you_gpm, you_xpm = you.get("gold_per_min", 0), you.get("xp_per_min", 0)
    pro_gpm, pro_xpm = pro.get("gold_per_min", 0), pro.get("xp_per_min", 0)

    embed.add_field(
        name="🧑 Ты",
        value=(
            f"⚔️ KDA: `{you_kills}/{you_deaths}/{you_assists}`\n"
            f"💰 GPM: `{you_gpm}`\n"
            f"✨ XPM: `{you_xpm}`"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"🌟 {pro_name}",
        value=(
            f"⚔️ KDA: `{pro_kills}/{pro_deaths}/{pro_assists}`\n"
            f"💰 GPM: `{pro_gpm}`\n"
            f"✨ XPM: `{pro_xpm}`"
        ),
        inline=True,
    )

    gpm_diff = pro_gpm - you_gpm
    xpm_diff = pro_xpm - you_xpm
    diff_lines = []
    if gpm_diff > 0:
        diff_lines.append(f"💰 У про GPM выше на `{gpm_diff}`")
    elif gpm_diff < 0:
        diff_lines.append(f"💰 У тебя GPM выше на `{-gpm_diff}`")
    if xpm_diff > 0:
        diff_lines.append(f"✨ У про XPM выше на `{xpm_diff}`")
    elif xpm_diff < 0:
        diff_lines.append(f"✨ У тебя XPM выше на `{-xpm_diff}`")
    embed.add_field(
        name="📈 Разница в показателях",
        value="\n".join(diff_lines) if diff_lines else "Показатели почти одинаковые",
        inline=False,
    )

    embed.add_field(
        name="🛒 Что купил про, а у тебя не было",
        value=fmt_items(missing_items),
        inline=False,
    )
    embed.add_field(
        name="🎒 Что было у тебя, а у про — нет",
        value=fmt_items(extra_items),
        inline=False,
    )

    enemy_names = ", ".join(get_hero_name(h) for h in enemy_heroes) if enemy_heroes else "неизвестно"
    embed.set_footer(text=f"Против: {enemy_names} • Данные: OpenDota API (Explorer)")
    return embed

@bot.command(name="сравнить", aliases=["анализ", "compare", "прокомпар"])
async def compare_with_pro(ctx, match_id: int = None, *, hero_query: str = None):
    """Сравнивает твою игру на герое с похожей игрой про-игрока (по билду и статам)."""
    if match_id is None or not hero_query:
        await ctx.send(
            f"⚠️ {ctx.author.mention}, укажи ID матча и название своего героя!\n"
            f"Пример: `!сравнить 8981903250 Viper`",
            delete_after=12,
        )
        return

    loading_msg = await ctx.send(
        f"🔎 Ищу твою партию и подбираю похожую про-игру для героя `{hero_query}`..."
    )

    try:
        await load_hero_names()
        await load_item_names()
        await load_pro_players()

        hero_id = resolve_hero_id(hero_query)
        if hero_id is None:
            await loading_msg.edit(
                content=(
                    f"❌ Не нашёл героя по запросу «{hero_query}». Проверь название "
                    f"(например: Pudge, Legion Commander, Spirit Breaker)."
                )
            )
            return

        match_data = await fetch_opendota_match(match_id)
        players = match_data.get("players") or []

        target_player = next((p for p in players if p.get("hero_id") == hero_id), None)
        if not target_player:
            await loading_msg.edit(
                content=f"❌ В матче `#{match_id}` не найден герой **{get_hero_name(hero_id)}**."
            )
            return

        target_side = is_player_radiant(target_player)
        enemy_heroes = [
            p.get("hero_id") for p in players
            if p.get("hero_id") and is_player_radiant(p) != target_side
        ]

        pro_match = await find_similar_pro_match(hero_id, enemy_heroes)
        if not pro_match:
            await loading_msg.edit(
                content=(
                    f"❌ Не нашёл в базе про-матчей с героем **{get_hero_name(hero_id)}**. "
                    f"Попробуй другого героя из этого матча."
                )
            )
            return

        embed = build_comparison_embed(target_player, pro_match, hero_id, enemy_heroes, match_id)
        await loading_msg.edit(content=None, embed=embed)

    except asyncio.TimeoutError:
        await loading_msg.edit(content="❌ OpenDota API не ответил вовремя. Попробуй позже.")
    except Exception as e:
        await loading_msg.edit(content=f"❌ Не удалось сделать сравнение: `{e}`")


# ============================================================
# Инициализация базы данных SQLite
# ============================================================
DB_FILE = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            exp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_daily TEXT DEFAULT '2000-01-01'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS temp_roles (
            user_id INTEGER PRIMARY KEY,
            role_id INTEGER,
            expires_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Функции работы с базой данных
def get_user_data(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT exp, level, last_daily FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"exp": row[0], "level": row[1], "last_daily": row[2]}
    return {"exp": 0, "level": 1, "last_daily": "2000-01-01"}

def update_user_full(user_id, exp, level, last_daily):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, exp, level, last_daily) 
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET 
            exp = excluded.exp, 
            level = excluded.level,
            last_daily = excluded.last_daily
    """, (user_id, exp, level, last_daily))
    conn.commit()
    conn.close()

def update_user_data(user_id, exp, level):
    data = get_user_data(user_id)
    update_user_full(user_id, exp, level, data["last_daily"])

# Словарь для отслеживания кулдаунов дуэлей/больницы
cooldowns = {}

# Функция для получения названия ранга по уровню
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
                "**Свои правила для своих:**\n\n1. Уважение в катках и"
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


# Обновление интерактивного статуса
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


# Фоновая задача: начисление опыта за нахождение в голосовых каналах
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


# Фоновая задача: очистка просроченных временных ролей
@tasks.loop(minutes=1)
async def temp_roles_check():
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, role_id FROM temp_roles WHERE expires_at <= ?", (now_str,))
    expired = cursor.fetchall()

    for uid, role_id in expired:
        for guild in bot.guilds:
            member = guild.get_member(uid)
            if member:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await role.delete(reason="Срок временной роли колеса фортуны истек")
                    except:
                        pass
        cursor.execute("DELETE FROM temp_roles WHERE user_id = ?", (uid,))
    
    conn.commit()
    conn.close()


# Фоновая задача: авто-топ раз в сутки
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
    asyncio.create_task(load_hero_names())
    if not auto_leaderboard.is_running():
        auto_leaderboard.start()
    if not voice_exp_loop.is_running():
        voice_exp_loop.start()
    if not temp_roles_check.is_running():
        temp_roles_check.start()
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


# --- ИНТЕРАКТИВНЫЕ МОДАЛЫ И КНОПКИ ---

class CustomRoleModal(Modal, title="Настройка своей временной роли"):
    role_name = TextInput(
        label="Название роли",
        placeholder="Например: Властелин дискорда",
        max_length=30,
    )
    role_color = TextInput(
        label="Цвет (HEX код, например #FF0000)",
        placeholder="#FFD700",
        max_length=7,
        default="#FFD700"
    )

    def __init__(self, guild: discord.Guild, member: discord.Member):
        super().__init__()
        self.guild = guild
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        color_str = self.role_color.value.strip()
        try:
            if not color_str.startswith("#"):
                color_str = "#" + color_str
            color_val = int(color_str.replace("#", ""), 16)
            discord_color = discord.Color(color_val)
        except ValueError:
            discord_color = discord.Color.gold()

        try:
            new_role = await self.guild.create_role(
                name=self.role_name.value,
                color=discord_color,
                hoist=True,
                reason="Победитель Колеса Фортуны"
            )
            await self.member.add_roles(new_role)

            expires = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO temp_roles (user_id, role_id, expires_at)
                VALUES (?, ?, ?)
            """, (self.member.id, new_role.id, expires))
            conn.commit()
            conn.close()

            await interaction.followup.send(f"✅ Роль **{self.role_name.value}** успешно создана и выдана тебе на 24 часа!", ephemeral=True)
            
            general_ch = discord.utils.get(self.guild.text_channels, name="💬-флудилка")
            if general_ch:
                await general_ch.send(f"👑 Колесо фортуны выбрало победителем игрока {self.member.mention}! Он забрал эксклюзивную роль **{self.role_name.value}** на 24 часа.")

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка при создании роли: {e}", ephemeral=True)


class FortuneWheelView(View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=60)
        self.guild = guild
        self.participants = set()

    @discord.ui.button(label="🎰 Участвовать в колесе", style=discord.ButtonStyle.success, custom_id="fortune_join")
    async def join_wheel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in self.participants:
            await interaction.response.send_message("Ты уже в списке участников этого розыгрыша!", ephemeral=True)
            return
        
        self.participants.add(interaction.user.id)
        await interaction.response.send_message("✅ Ты успешно залетел в колесо фортуны! Жди окончания розыгрыша.", ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        
        try:
            # Проверка лимита: активных временных ролей должно быть не более 15% от состава сервера
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM temp_roles")
            active_temp_count = cursor.fetchone()[0]
            conn.close()

            max_allowed = max(1, int(len(self.guild.members) * 0.15))

            if active_temp_count >= max_allowed:
                general_ch = discord.utils.get(self.guild.text_channels, name="💬-флудилка")
                if general_ch:
                    await general_ch.send("🎰 Колесо фортуны прокрутилось, но на сервере уже достигнут лимит временных элитных ролей (15%). Розыгрыш переносится!")
                return

            if self.participants:
                winner_id = random.choice(list(self.participants))
                winner_member = self.guild.get_member(winner_id)
                if winner_member:
                    try:
                        dm_embed = discord.Embed(
                            title="🎉 ПОЗДРАВЛЯЕМ С ПОБЕДОЙ!",
                            description="Ты выиграл в **Колесе Фортуны**! Нажми на кнопку ниже, чтобы выбрать кастомное название и цвет своей временной роли на 24 часа.",
                            color=0x8B0000
                        )
                        
                        class SetupButtonView(View):
                            def __init__(self, g, m):
                                super().__init__(timeout=300)
                                self.g = g
                                self.m = m

                            @discord.ui.button(label="🛠️ Настроить свою роль", style=discord.ButtonStyle.primary)
                            async def setup_role_btn(self, inter: discord.Interaction, btn: Button):
                                await inter.response.send_modal(CustomRoleModal(self.g, self.m))

                        await winner_member.send(embed=dm_embed, view=SetupButtonView(self.guild, winner_member))
                    except Exception:
                        fallback_role = await self.guild.create_role(name="⭐ Счастливчик", color=discord.Color.gold(), hoist=True)
                        await winner_member.add_roles(fallback_role)
                        expires = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        conn = sqlite3.connect(DB_FILE)
                        cursor = conn.cursor()
                        cursor.execute("INSERT OR REPLACE INTO temp_roles (user_id, role_id, expires_at) VALUES (?, ?, ?)", (winner_member.id, fallback_role.id, expires))
                        conn.commit()
                        conn.close()
                        
                        general_ch = discord.utils.get(self.guild.text_channels, name="💬-флудилка")
                        if general_ch:
                            await general_ch.send(f"👑 Колесо фортуны выбрало победителя: {winner_member.mention}! Он получает роль **⭐ Счастливчик**.")
            else:
                general_ch = discord.utils.get(self.guild.text_channels, name="💬-флудилка")
                if general_ch:
                    await general_ch.send("🎰 Колесо фортуны завершилось, но никто не нажал кнопку участия. Ничья!")
        except Exception as e:
            print(f"Ошибка в колесе фортуны: {e}")


class HubView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📊 Профиль", style=discord.ButtonStyle.primary, custom_id="hub_profile")
    async def profile_button(self, interaction: discord.Interaction, button: Button):
        user_data = get_user_data(interaction.user.id)
        current_lvl = user_data["level"]
        current_exp = user_data["exp"]
        exp_needed = current_lvl * 100
        rank_title = get_rank_title(current_lvl)

        embed = discord.Embed(
            title=f"📊 Профиль: {interaction.user.name}",
            color=0x8B0000,
        )
        embed.add_field(name="Звание / Титул", value=f"🛡️ **{rank_title}**", inline=False)
        embed.add_field(name="Уровень", value=f"⭐ **LVL {current_lvl}**", inline=True)
        embed.add_field(name="Опыт", value=f"💬 `{current_exp} / {exp_needed} XP`", inline=True)
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)
        embed.set_footer(text="ПРАЧКА ДРАЧКА • Интерактивный хаб")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🏆 Топ игроков", style=discord.ButtonStyle.success, custom_id="hub_top")
    async def top_button(self, interaction: discord.Interaction, button: Button):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, exp, level FROM users ORDER BY level DESC, exp DESC LIMIT 10")
        top_rows = cursor.fetchall()
        conn.close()

        desc = ""
        for index, (uid, exp, lvl) in enumerate(top_rows, start=1):
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else "Боец"
            rank_title = get_rank_title(lvl)
            desc += f"**{index}.** {name} — **LVL {lvl}** *({rank_title})* (`{exp} XP`)\n"

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


# --- РУССКОЯЗЫЧНЫЕ КОМАНДЫ ---

@bot.command(name="команды", aliases=["help", "помощь"])
async def commands_list(ctx):
    # Проверяем, есть ли у пользователя права администратора/модератора
    is_staff = any(role.name in ["⚡ Штурмфюрер", "👁️ Смотрящий"] for role in ctx.author.roles) or ctx.author.guild_permissions.administrator

    embed = discord.Embed(
        title="📜 СПИСОК ДОСТУПНЫХ КОМАНД",
        description="Вот всё, чем ты можешь пользоваться на сервере. Не забывай про префикс `!`",
        color=0x8B0000
    )

    # Общие команды для всех
    general_cmds = (
        "`!профиль` (или `!ур`) — Показать твой уровень, звание и опыт.\n"
        "`!топ` (или `!лидеры`) — Таблица топ-10 игроков сервера.\n"
        "`!ежедневка` (или `!бонус`) — Забрать ежедневную награду (XP).\n"
        "`!казино [ставка]` (или `!кости`) — Сыграть в кости на XP против бота (ставка от 10 до 500).\n"
        "`!дуэль @Юзер [ставка]` (или `!махач`) — Устроить уличный замес на XP с другим бойцом.\n"
        "`!матч [ID]` (или `!дота`) — Анализ матча Dota 2 через OpenDota API.\n"
        "`!команды` (или `!помощь`) — Вызвать это справочное меню в ЛС."
    )
    embed.add_field(name="⭐ Игровые и общие команды", value=general_cmds, inline=False)

    # Админ/модер команды (показываются только если есть права)
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
        # Уведомление в чате, что справка отправлена в ЛС
        temp_msg = await ctx.send(f"📬 {ctx.author.mention}, список доступных команд отправлен тебе в личные сообщения!", delete_after=6)
    except discord.Forbidden:
        # Если у юзера закрыты ЛС, выводим прямо в канал
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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, exp, level FROM users ORDER BY level DESC, exp DESC LIMIT 10")
    top_rows = cursor.fetchall()
    conn.close()

    desc = ""
    for index, (uid, exp, lvl) in enumerate(top_rows, start=1):
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else "Боец"
        rank_title = get_rank_title(lvl)
        desc += f"**{index}.** {name} — **LVL {lvl}** *({rank_title})* (`{exp} XP`)\n"

    embed = discord.Embed(title="🏆 ТОП-10 БОЙЦОВ СЕРВЕРА", description=desc or "Пока пусто.", color=0x8B0000)
    embed.set_footer(text="ПРАЧКА ДРАЧКА • Таблица лидеров")
    await ctx.send(embed=embed)


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
