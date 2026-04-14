import sys
import termios
import tty


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


def print_help() -> None:
    print("=== HELP ===")
    print("このゲームはターン制バトルです。先手はHunter、後手はMonster(自動行動)です。")
    print("最初に入力する値:")
    print("- A: Hunter初期HP (整数)")
    print("- B: Monster初期HP (整数)")
    print("- N: 合計行動回数 (整数)")
    print("Hunterの入力可能コマンド:")
    print("- Attack : Monsterに40ダメージ + Buff合計")
    print("- Potion : HunterのHPを80回復(最大Aまで)")
    print("- Guard  : 次のHunter行動までMonsterのAttackダメージを0にする")
    print("- Buff x y : この行動の後、Hunterがx回行動する間だけAttackダメージを+yする")
    print("             xは効果ターン数、yは1回のAttackに加算されるダメージ量")
    print("入力中に 'help' と打つとこの説明を再表示できます。")
    print("==============")


def print_title() -> None:
    print("==============================================================")
    print("                     Hunter VS Monster")
    print("==============================================================")
    print("                      |>>>                    |>>>")
    print("                      |                        |")
    print("                  _  _|_  _                _  _|_  _")
    print("                 |;|_|;|_|;|              |;|_|;|_|;|")
    print("                 \\\\..      /                \\\\..      /")
    print("                  \\\\..    /                  \\\\..    /")
    print("                   ||:  |                    ||:  |")
    print("                   ||:. |                    ||:. |")
    print("                   ||:  |                    ||:  |")
    print("                   ||:.,|                    ||:.,|")
    print("                   ||:  |                    ||:  |")
    print("                   ||:  |                    ||:  |")
    print("                __ ||_._| __              __ ||_._| __")
    print("               (___|___|___)            (___|___|___)")
    print("--------------------------------------------------------------")
    print("Hunter                                            Monster")
    print("  O                                                 /\\_/\\")
    print(" /|\\                                               ( o.o )")
    print(" / \\                                                > ^ <")
    print("==============================================================")


def select_start_or_cancel() -> bool:
    options = ["start", "cancel"]
    selected = 0

    # パイプ実行など矢印入力できない環境では従来入力へフォールバック
    if not sys.stdin.isatty():
        print("[Select] start / cancel")
        while True:
            choice = input("> ").strip().lower()
            if choice == "start":
                return True
            if choice == "cancel":
                return False
            print("start か cancel を入力してください。")

    def draw() -> None:
        print("[Select] ↑↓ で選択 / Enterで決定")
        for i, opt in enumerate(options):
            prefix = ">" if i == selected else " "
            print(f"{prefix} {opt}")

    def read_key() -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch1 = sys.stdin.read(1)
            if ch1 == "\x1b":
                ch2 = sys.stdin.read(1)
                ch3 = sys.stdin.read(1)
                seq = ch1 + ch2 + ch3
                if seq == "\x1b[A":
                    return "up"
                if seq == "\x1b[B":
                    return "down"
                return "other"
            if ch1 in ("\r", "\n"):
                return "enter"
            return ch1
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    draw()
    while True:
        key = read_key()
        if key == "up":
            selected = (selected - 1) % len(options)
            # メニュー行数分カーソルを戻して再描画
            print(f"\033[{len(options) + 1}A", end="")
            draw()
        elif key == "down":
            selected = (selected + 1) % len(options)
            print(f"\033[{len(options) + 1}A", end="")
            draw()
        elif key == "enter":
            print(f"選択: {options[selected]}")
            return options[selected] == "start"


RESET = "\033[0m"
BLUE = "\033[34m"
RED = "\033[31m"
STATUS_PANEL_LINES = 5


def hp_bar(current: int, maximum: int, width: int = 20) -> str:
    if maximum <= 0:
        return "-" * width
    filled = (current * width) // maximum
    if filled < 0:
        filled = 0
    if filled > width:
        filled = width
    return "=" * filled + "-" * (width - filled)


def print_hp_status(hunter_hp: int, max_hunter_hp: int, monster_hp: int, max_monster_hp: int) -> None:
    hunter_bar = hp_bar(hunter_hp, max_hunter_hp)
    monster_bar = hp_bar(monster_hp, max_monster_hp)
    print(f"{BLUE}Hunter [{hunter_bar}] {hunter_hp}/{max_hunter_hp}{RESET}")
    print(f"{RED}Monster[{monster_bar}] {monster_hp}/{max_monster_hp}{RESET}")


def render_dynamic_status(
    hunter_hp: int,
    max_hunter_hp: int,
    monster_hp: int,
    max_monster_hp: int,
    event_text: str,
) -> None:
    hunter_bar = hp_bar(hunter_hp, max_hunter_hp)
    monster_bar = hp_bar(monster_hp, max_monster_hp)
    line1 = f"{BLUE}Hunter [{hunter_bar}] {hunter_hp}/{max_hunter_hp}{RESET}"
    line2 = f"{RED}Monster[{monster_bar}] {monster_hp}/{max_monster_hp}{RESET}"
    line3 = f"Event: {event_text}"
    # 保存済みのステータスパネル先頭に戻って、同じ領域だけ上書きする
    print("\033[u", end="")
    print("\033[2K=== Battle Status ===")
    print("\033[2K" + line1)
    print("\033[2K" + line2)
    print("\033[2K" + line3)
    print("\033[2K=====================")
    # プロンプト入力を続けるため、パネルの下へ戻る
    print(f"\033[{STATUS_PANEL_LINES}B", end="")


def init_dynamic_status_panel() -> None:
    print("=== Battle Status ===")
    print("Hunter [--------------------] 0/0")
    print("Monster[--------------------] 0/0")
    print("Event: -")
    print("=====================")
    # パネル先頭を保存
    print(f"\033[{STATUS_PANEL_LINES}A", end="")
    print("\033[s", end="")
    print(f"\033[{STATUS_PANEL_LINES}B", end="")


def read_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        if raw.lower() == "help":
            print_help()
            continue
        try:
            return int(raw)
        except ValueError:
            print("整数を入力してください。例: 100")


def read_hunter_action(turn: int) -> str:
    while True:
        raw = input(f"Turn {turn} Hunter action: ").strip()
        if not raw:
            print("行動を入力してください。")
            continue
        if raw.lower() == "help":
            print_help()
            continue

        parts = raw.split()
        action = canon(parts[0])

        if action not in {"Attack", "Potion", "Guard", "Buff"}:
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


def choose_monster_action(
    hunter_hp: int,
    guard_active: bool,
    anger_turns: int,
    buffs: list,
    breath_cooldown: int,
) -> str:
    # 1) Buffが強い時だけ優先的に解除（毎回即解除はしない）
    #    さらにBreath後はクールダウン中は使えない
    if buffs:
        total_bonus = sum(b["bonus"] for b in buffs)
        max_bonus = max(b["bonus"] for b in buffs)
        if breath_cooldown == 0 and (total_bonus >= 80 or max_bonus >= 50 or len(buffs) >= 3):
            return "Breath"

    # 2) Guard中でAttackが無効なら、怒り準備か様子見
    if guard_active:
        if anger_turns == 0:
            return "Anger"
        return "Intimidate"

    # 3) まだ怒っていないなら怒り状態へ
    if anger_turns == 0 and hunter_hp > 80:
        return "Anger"

    # 4) 基本は攻撃
    return "Attack"


def solve() -> None:
    print_title()
    if not select_start_or_cancel():
        print("ゲームを終了しました。")
        return

    print("入力中はいつでも 'help' で使い方を表示できます。")
    print("A (Hunter初期HP):")
    max_hunter_hp = read_int("> ")

    print("B (Monster初期HP):")
    max_monster_hp = read_int("> ")

    print("N (合計行動回数):")
    n = read_int("> ")

    hunter_hp = max_hunter_hp
    monster_hp = max_monster_hp

    guard_active = False
    anger_turns = 0
    breath_cooldown = 0
    buffs = []  # {"remaining": int, "bonus": int}

    winner = None
    use_dynamic_status = sys.stdout.isatty()

    print("戦闘開始！先手はHunterです。")
    if use_dynamic_status:
        init_dynamic_status_panel()
        render_dynamic_status(
            hunter_hp,
            max_hunter_hp,
            monster_hp,
            max_monster_hp,
            "Battle start",
        )
    else:
        print_hp_status(hunter_hp, max_hunter_hp, monster_hp, max_monster_hp)

    for i in range(n):
        is_hunter_turn = (i % 2 == 0)
        turn_no = i + 1
        event_text = ""

        if is_hunter_turn:
            guard_active = False
            old_buffs_count = len(buffs)

            action_raw = read_hunter_action(turn_no)
            parts = action_raw.split()
            action = parts[0]

            if action == "Attack":
                bonus = sum(b["bonus"] for b in buffs)
                damage = 40 + bonus
                monster_hp = max(0, monster_hp - damage)
                event_text = f"Hunter uses Attack -> Monster -{damage}"

            elif action == "Potion":
                before = hunter_hp
                hunter_hp = min(max_hunter_hp, hunter_hp + 80)
                event_text = f"Hunter uses Potion -> Hunter +{hunter_hp - before}"

            elif action == "Guard":
                guard_active = True
                event_text = "Hunter uses Guard"

            elif action == "Buff":
                x = int(parts[1])
                y = int(parts[2])
                buffs.append({"remaining": x, "bonus": y})
                event_text = f"Hunter uses Buff ({x}, +{y})"

            for idx in range(old_buffs_count):
                buffs[idx]["remaining"] -= 1
            buffs = [b for b in buffs if b["remaining"] > 0]

        else:
            action = choose_monster_action(
                hunter_hp, guard_active, anger_turns, buffs, breath_cooldown
            )
            angry_now = anger_turns > 0

            if action == "Attack":
                damage = 80 if angry_now else 40
                if guard_active:
                    damage = 0
                hunter_hp = max(0, hunter_hp - damage)
                event_text = f"Monster uses Attack -> Hunter -{damage}"

            elif action == "Anger":
                anger_turns = 3
                event_text = "Monster uses Anger"

            elif action == "Breath":
                if buffs:
                    target = max(buffs, key=lambda b: (b["bonus"], b["remaining"]))
                    buffs.remove(target)
                event_text = "Monster uses Breath"
                breath_cooldown = 2

            else:
                event_text = "Monster uses Intimidate"

            if action != "Anger" and anger_turns > 0:
                anger_turns -= 1
            if action != "Breath" and breath_cooldown > 0:
                breath_cooldown -= 1

        if use_dynamic_status:
            render_dynamic_status(
                hunter_hp,
                max_hunter_hp,
                monster_hp,
                max_monster_hp,
                event_text,
            )
        else:
            if event_text:
                print(event_text)
            print_hp_status(hunter_hp, max_hunter_hp, monster_hp, max_monster_hp)

        if monster_hp == 0:
            winner = "Hunter"
            break
        if hunter_hp == 0:
            winner = "Monster"
            break

    if winner is None:
        winner = "Monster"

    hp = hunter_hp if winner == "Hunter" else monster_hp
    if use_dynamic_status:
        print()
    print(f"Result: {winner} {hp}")


if __name__ == "__main__":
    solve()
