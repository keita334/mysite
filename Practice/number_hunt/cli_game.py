import random


def ask_int(prompt: str, min_value: int, max_value: int) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw.isdigit():
            print("数字で入力してください。")
            continue
        value = int(raw)
        if value < min_value or value > max_value:
            print(f"{min_value}〜{max_value}の範囲で入力してください。")
            continue
        return value


def play_once() -> None:
    print("\n=== Number Hunt ===")
    print("1〜20の数字を当ててください。チャンスは5回です。")

    target = random.randint(1, 20)
    max_try = 5

    for turn in range(1, max_try + 1):
        guess = ask_int(f"[{turn}/{max_try}] 予想: ", 1, 20)

        if guess == target:
            print("正解！おめでとう！")
            return

        if guess < target:
            print("もっと大きいです。")
        else:
            print("もっと小さいです。")

    print(f"残念！正解は {target} でした。")


def ask_replay() -> bool:
    while True:
        answer = input("もう一度遊びますか？ (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("y か n で入力してください。")


def main() -> None:
    print("CLIゲームへようこそ！")
    while True:
        play_once()
        if not ask_replay():
            print("遊んでくれてありがとう！")
            break


if __name__ == "__main__":
    main()
