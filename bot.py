@bot.command(name="герой", aliases=["hero"])
async def hero_command(ctx, *, hero_query: str):
    async with ctx.typing():
        try:
            if 'load_item_names' in globals() and callable(load_item_names):
                await load_item_names()

            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.opendota.com/api/heroStats") as resp:
                    if resp.status != 200:
                        await ctx.send("❌ Не удалось получить данные от OpenDota.")
                        return
                    heroes_data = await resp.json()

            # Используем нашу функцию поиска по прозвищам и нечетким совпадениям
            target_hero = find_hero_by_query(hero_query, heroes_data)

            if not target_hero:
                await ctx.send(f"❌ Герой **{hero_query}** не найден.")
                return

            hero_id = target_hero.get('id')
            hero_name_clean = target_hero.get('name', '').replace('npc_dota_hero_', '')
            image_url = f"https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{hero_name_clean}.png"

            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.opendota.com/api/heroes/{hero_id}/itemPopularity") as item_resp:
                    items_data = await item_resp.json() if item_resp.status == 200 else {}
                
                async with session.get(f"https://api.opendota.com/api/heroes/{hero_id}/matchups") as matchup_resp:
                    matchups_data = await matchup_resp.json() if matchup_resp.status == 200 else []

            embed = discord.Embed(
                title=f"🛡️ Разбор героя: {target_hero['localized_name']}",
                color=0x8B0000
            )
            embed.set_thumbnail(url=image_url)
            
            attr_map = {"str": "Сила 💪", "agi": "Ловкость 🏃‍♂️", "int": "Интеллект 🧠", "all": "Универсальный ✨"}
            primary_attr = attr_map.get(target_hero.get('primary_attr'), "Неизвестно")

            attack_map = {"Melee": "Ближний бой ⚔️", "Ranged": "Дальний бой 🏹"}
            attack_type = attack_map.get(target_hero.get('attack_type', ''), target_hero.get('attack_type', 'Неизвестно'))

            roles_map = {
                "Carry": "Керри", "Support": "Саппорт", "Nuker": "Нюкер",
                "Disabler": "Дизейблер", "Durable": "Тяжеловес", "Escape": "Эскейпер",
                "Pusher": "Пушер", "Initiator": "Инициатор", "Jungler": "Лесник"
            }

            pro_pick = target_hero.get('pro_pick', 0)
            pro_ban = target_hero.get('pro_ban', 0)
            
            pub_pick = sum(target_hero.get(f'{i}_pick', 0) for i in range(1, 9))
            pub_win = sum(target_hero.get(f'{i}_win', 0) for i in range(1, 9))
            pub_winrate = f"{(pub_win / pub_pick * 100):.1f}%" if pub_pick > 0 else "Нет данных"

            raw_roles = target_hero.get('roles', [])
            translated_roles = [roles_map.get(role, role) for role in raw_roles]
            roles_str = ", ".join(translated_roles) if translated_roles else "Универсал"

            def get_names_from_dict(sub_dict):
                if not sub_dict or not isinstance(sub_dict, dict):
                    return "По ситуации"
                sorted_items = sorted(sub_dict.items(), key=lambda x: x[1], reverse=True)[:3]
                names = []
                for item_id_str, count in sorted_items:
                    try:
                        item_id = int(item_id_str)
                        name = ITEM_NAMES_CACHE.get(item_id, f"Предмет #{item_id}")
                        names.append(name)
                    except ValueError:
                        continue
                return ", ".join(names) if names else "По ситуации"

            early_items = get_names_from_dict(items_data.get('start_game_items', {}))
            core_items = get_names_from_dict(items_data.get('mid_game_items', {}))
            late_items = get_names_from_dict(items_data.get('late_game_items', {}))

            items_text = (
                f"🟢 **Старт:** {early_items}\n"
                f"🟡 **Мидгейм:** {core_items}\n"
                f"🔴 **Лейт:** {late_items}"
            )

            hero_name_map = {h['id']: h['localized_name'] for h in heroes_data}

            valid_matchups = []
            for m in matchups_data:
                games = m.get('games_played', 0)
                if games > 50: 
                    wins = m.get('wins', 0)
                    winrate = wins / games
                    opp_id = m.get('hero_id')
                    if opp_id in hero_name_map:
                        valid_matchups.append((hero_name_map[opp_id], winrate))

            valid_matchups.sort(key=lambda x: x[1], reverse=True)
            strong_against = [x[0] for x in valid_matchups[:3]]
            weak_against = [x[0] for x in valid_matchups[-3:]]

            matchups_text = (
                f"🟢 **Силен против:** {', '.join(strong_against) if strong_against else 'Нет данных'}\n"
                f"🔴 **Слаб против (Контрят):** {', '.join(reversed(weak_against)) if weak_against else 'Нет данных'}"
            )

            embed.add_field(name="Основной атрибут", value=primary_attr, inline=True)
            embed.add_field(name="Атакующий тип", value=attack_type, inline=True)
            embed.add_field(name="🎯 Роли", value=roles_str, inline=True)
            
            embed.add_field(name="📊 Винрейт в пабликах", value=pub_winrate, inline=True)
            embed.add_field(name="🏆 Про-пики / Баны", value=f"👤 {pro_pick} / 🚫 {pro_ban}", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            embed.add_field(name="🎒 Сборки по таймингам", value=items_text, inline=False)
            embed.add_field(name="⚔️ Матчапы и Контрпики", value=matchups_text, inline=False)

            embed.set_footer(text=f"ID героя: {hero_id} | Мета-анализ OpenDota")

            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"⚠️ Ошибка: `{e}`")
