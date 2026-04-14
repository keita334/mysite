def canon(cmd: str) -> str:
    c = cmd.lower()
    if c == "attack":
        return "Attack"
    if c == "potion":
        return "Potion"
    if c == "guard":
        return "Guard"
    if c == "buff":
        return "Buff"
    if c == "anger":
        return "Anger"
    if c == "breath":
        return "Breath"
    if c == "intimidate":
        return "Intimidate"
    return cmd


def read_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("整数を入力してください。例: 100")


def read_action(index: int, is_hunter_turn: bool) -> str:
    actor = "Hunter" if is_hunter_turn else "Monster"
    while True:
        raw = input(f"Action {index} ({actor}): ").strip()
        if not raw:
            print("行動を入力してください。")
            continue

        parts = raw.split()
        action = canon(parts[0])

        valid_hunter = {"Attack", "Potion", "Guard", "Buff"}
        valid_monster = {"Attack", "Anger", "Breath", "Intimidate"}

        if is_hunter_turn:
            if action not in valid_hunter:
                print("Hunterの行動は Attack / Potion / Guard / Buff x y です。")
                continue
            if action == "Buff":
                if len(parts) != 3:
                    print("Buff は 'Buff x y' 形式で入力してください。")
                    continue
                try:
                    x = int(parts[1])
                    y = int(parts[2])
                except ValueError:
                    print("x, y は整数で入力してください。")
                    continue
                if not (1 <= x <= 100 and 1 <= y <= 100):
                    print("x, y は 1〜100 の範囲で入力してください。")
                    continue
                return f"Buff {x} {y}"
            return action

        if action not in valid_monster:
            print("Monsterの行動は Attack / Anger / Breath / Intimidate です。")
            continue
        return action


def solve() -> None:
    print("A (Hunter初期HP) を入力してください:")
    max_hunter_hp = read_int("> ")

    print("B (Monster初期HP) を入力してください:")
    max_monster_hp = read_int("> ")

    print("N (行動回数) を入力してください:")
    n = read_int("> ")

    actions = []
    print("行動を1行ずつ入力してください。先手はHunterです。")
    for i in range(n):
        actions.append(read_action(i + 1, i % 2 == 0))

    hunter_hp = max_hunter_hp
    monster_hp = max_monster_hp

    guard_active = False
    anger_turns = 0
    buffs = []  # {"remaining": int, "bonus": int}

    winner = None

    for i, raw in enumerate(actions):
        is_hunter_turn = (i % 2 == 0)
        parts = raw.split()
        action = canon(parts[0])

        if is_hunter_turn:
            guard_active = False
            old_buffs_count = len(buffs)

            if action == "Attack":
                bonus = sum(b["bonus"] for b in buffs)
                damage = 40 + bonus
                monster_hp = max(0, monster_hp - damage)
            elif action == "Potion":
                hunter_hp = min(max_hunter_hp, hunter_hp + 80)
            elif action == "Guard":
                guard_active = True
            elif action == "Buff":
                x = int(parts[1])
                y = int(parts[2])
                buffs.append({"remaining": x, "bonus": y})

            for idx in range(old_buffs_count):
                buffs[idx]["remaining"] -= 1
            buffs = [b for b in buffs if b["remaining"] > 0]

        else:
            angry_now = anger_turns > 0

            if action == "Attack":
                damage = 80 if angry_now else 40
                if guard_active:
                    damage = 0
                hunter_hp = max(0, hunter_hp - damage)
            elif action == "Anger":
                anger_turns = 3
            elif action == "Breath":
                if buffs:
                    target = max(buffs, key=lambda b: (b["bonus"], b["remaining"]))
                    buffs.remove(target)

            if action != "Anger" and anger_turns > 0:
                anger_turns -= 1

        if monster_hp == 0:
            winner = "Hunter"
            break
        if hunter_hp == 0:
            winner = "Monster"
            break

    if winner is None:
        winner = "Monster"

    hp = hunter_hp if winner == "Hunter" else monster_hp
    print(f"{winner} {hp}")


if __name__ == "__main__":
    solve()
