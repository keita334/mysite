import sys


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


def solve() -> None:
    lines = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if len(lines) < 3:
        return

    max_hunter_hp = int(lines[0])
    max_monster_hp = int(lines[1])
    n = int(lines[2])

    actions = lines[3:3 + n]

    hunter_hp = max_hunter_hp
    monster_hp = max_monster_hp

    # Guard: 次のハンター行動まで、モンスターAttackを0ダメージ化
    guard_active = False

    # Anger: 次のモンスター行動3回の間有効（再使用で3にリセット）
    anger_turns = 0

    # Buffは独立管理
    # 各要素: {"remaining": 残り有効ハンター行動数, "bonus": 攻撃上昇量}
    buffs = []

    winner = None

    for i, raw in enumerate(actions):
        is_hunter_turn = (i % 2 == 0)
        parts = raw.split()
        if not parts:
            continue
        action = canon(parts[0])

        if is_hunter_turn:
            # Guardは「次にハンターが行動するまで」なので、
            # ハンター行動開始時点で前回Guard効果は終了
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
                # Buff x y
                x = int(parts[1])
                y = int(parts[2])
                buffs.append({"remaining": x, "bonus": y})

            # このターン開始時に存在したBuffだけ残り回数を減らす
            for idx in range(old_buffs_count):
                buffs[idx]["remaining"] -= 1

            buffs = [b for b in buffs if b["remaining"] > 0]

        else:
            # 現在怒り状態か（このターンの行動に適用）
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
                    # 最大bonus、同値なら残りremainingが大きい（=より遅く切れる）ものを解除
                    target = max(buffs, key=lambda b: (b["bonus"], b["remaining"]))
                    buffs.remove(target)

            elif action == "Intimidate":
                pass

            # Anger行動でなければ、怒り残り回数を1減らす
            if action != "Anger" and anger_turns > 0:
                anger_turns -= 1

        # 戦闘終了判定（0になった時点で即終了）
        if monster_hp == 0:
            winner = "Hunter"
            break
        if hunter_hp == 0:
            winner = "Monster"
            break

    # 既定行動完了時、どちらも生存ならMonster勝利
    if winner is None:
        winner = "Monster"

    hp = hunter_hp if winner == "Hunter" else monster_hp
    print(winner, hp)


if __name__ == "__main__":
    solve()
