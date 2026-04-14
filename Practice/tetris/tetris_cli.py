import select
import sys
import termios
import time
import tty
import random
import json
import os

WIDTH = 10
HEIGHT = 20
FRAME_SEC = 0.05
BASE_DROP_SEC = 0.70
MIN_DROP_SEC = 0.10
SCORE_FILE = os.path.join(os.path.dirname(__file__), "tetris_scores.json")

SHAPES = {
    "I": [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
    ],
    "O": [
        [(1, 0), (2, 0), (1, 1), (2, 1)],
    ],
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}


def new_piece() -> dict:
    p_type = random.choice(list(SHAPES.keys()))
    return {"type": p_type, "rot": 0, "x": 3, "y": 0}


def cells(piece: dict):
    shape = SHAPES[piece["type"]][piece["rot"]]
    return [(piece["x"] + dx, piece["y"] + dy) for dx, dy in shape]


def collides(board, piece: dict) -> bool:
    for x, y in cells(piece):
        if x < 0 or x >= WIDTH or y < 0 or y >= HEIGHT:
            return True
        if board[y][x] != " ":
            return True
    return False


def place_piece(board, piece: dict) -> None:
    for x, y in cells(piece):
        if 0 <= y < HEIGHT and 0 <= x < WIDTH:
            board[y][x] = "#"


def clear_lines(board) -> int:
    kept = [row for row in board if any(c == " " for c in row)]
    cleared = HEIGHT - len(kept)
    for _ in range(cleared):
        kept.insert(0, [" "] * WIDTH)
    board[:] = kept
    return cleared


def calc_level(lines: int) -> int:
    return 1 + (lines // 5)


def calc_drop_sec(level: int) -> float:
    return max(MIN_DROP_SEC, BASE_DROP_SEC - (level - 1) * 0.06)


def load_scores() -> list:
    if not os.path.exists(SCORE_FILE):
        return []
    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_scores(scores: list) -> None:
    with open(SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def sort_scores(scores: list) -> list:
    # score高い順、同点ならlines高い順、さらに短時間優先
    return sorted(
        scores,
        key=lambda s: (-int(s.get("score", 0)), -int(s.get("lines", 0)), int(s.get("time_sec", 10**9))),
    )


def show_ranking(scores: list, top_n: int = 10) -> None:
    print("\n=== Ranking (Top {}) ===".format(top_n))
    ranked = sort_scores(scores)[:top_n]
    if not ranked:
        print("No records yet.")
        return
    for idx, s in enumerate(ranked, start=1):
        mm = int(s.get("time_sec", 0)) // 60
        ss = int(s.get("time_sec", 0)) % 60
        print(
            f"{idx:2d}. {s.get('name', 'Player')}  "
            f"Score:{s.get('score', 0)}  Lines:{s.get('lines', 0)}  "
            f"Level:{s.get('level', 1)}  Time:{mm:02d}:{ss:02d}"
        )


def draw(board, piece: dict, score: int, lines: int, level: int, elapsed_sec: int) -> None:
    temp = [row[:] for row in board]
    for x, y in cells(piece):
        if 0 <= y < HEIGHT and 0 <= x < WIDTH:
            temp[y][x] = "@"

    print("\033[2J\033[H", end="")
    print("=" * 30)
    mm = elapsed_sec // 60
    ss = elapsed_sec % 60
    print(f"TETRIS CLI  Score:{score}  Lines:{lines}  Level:{level}  Time:{mm:02d}:{ss:02d}")
    print("=" * 30)
    print("+" + "-" * WIDTH + "+")
    for row in temp:
        print("|" + "".join(row) + "|")
    print("+" + "-" * WIDTH + "+")
    print("a:left d:right w:rotate s:soft-drop x/space:hard-drop q:quit")


def try_move(board, piece: dict, dx: int, dy: int) -> bool:
    moved = {**piece, "x": piece["x"] + dx, "y": piece["y"] + dy}
    if collides(board, moved):
        return False
    piece.update(moved)
    return True


def try_rotate(board, piece: dict) -> bool:
    max_rot = len(SHAPES[piece["type"]])
    rotated = {**piece, "rot": (piece["rot"] + 1) % max_rot}
    for kick_x in (0, -1, 1, -2, 2):
        test = {**rotated, "x": rotated["x"] + kick_x}
        if not collides(board, test):
            piece.update(test)
            return True
    return False


def lock_and_spawn(board, piece: dict, score: int, lines_total: int):
    place_piece(board, piece)
    cleared = clear_lines(board)
    if cleared:
        score += (cleared * cleared) * 100
        lines_total += cleared
    nxt = new_piece()
    game_over = collides(board, nxt)
    return nxt, score, lines_total, game_over


def read_key_nonblocking() -> str:
    rlist, _, _ = select.select([sys.stdin], [], [], 0)
    if not rlist:
        return ""

    ch = sys.stdin.read(1)
    if ch != "\x1b":
        return ch

    # Arrow keys: ESC [ A/B/C/D
    rlist2, _, _ = select.select([sys.stdin], [], [], 0)
    if not rlist2:
        return ch
    ch2 = sys.stdin.read(1)
    rlist3, _, _ = select.select([sys.stdin], [], [], 0)
    if not rlist3:
        return ch
    ch3 = sys.stdin.read(1)

    seq = ch + ch2 + ch3
    if seq == "\x1b[A":
        return "w"
    if seq == "\x1b[B":
        return "s"
    if seq == "\x1b[C":
        return "d"
    if seq == "\x1b[D":
        return "a"
    return ""


def main() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("リアルタイム版はターミナルで直接実行してください。")
        return

    print("=== TETRIS CLI ===")
    player_name = input("Player name (EnterでPlayer): ").strip() or "Player"
    scores = load_scores()
    show_ranking(scores)
    input("\nEnterでゲーム開始...")

    board = [[" "] * WIDTH for _ in range(HEIGHT)]
    piece = new_piece()
    score = 0
    lines_total = 0
    level = 1
    elapsed_sec = 0

    if collides(board, piece):
        print("Game Over")
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    finish_reason = "quit"

    try:
        tty.setcbreak(fd)
        start_time = time.monotonic()
        last_drop = time.monotonic()
        running = True

        while running:
            elapsed_sec = int(time.monotonic() - start_time)
            level = calc_level(lines_total)
            drop_sec = calc_drop_sec(level)
            draw(board, piece, score, lines_total, level, elapsed_sec)

            # フレーム中にキーを複数処理
            frame_end = time.monotonic() + FRAME_SEC
            while time.monotonic() < frame_end:
                key = read_key_nonblocking().lower()
                if not key:
                    time.sleep(0.005)
                    continue

                if key == "q":
                    finish_reason = "quit"
                    running = False
                    break
                if key == "a":
                    try_move(board, piece, -1, 0)
                elif key == "d":
                    try_move(board, piece, 1, 0)
                elif key == "w":
                    try_rotate(board, piece)
                elif key == "s":
                    if not try_move(board, piece, 0, 1):
                        piece, score, lines_total, over = lock_and_spawn(board, piece, score, lines_total)
                        if over:
                            elapsed_sec = int(time.monotonic() - start_time)
                            level = calc_level(lines_total)
                            draw(board, piece, score, lines_total, level, elapsed_sec)
                            finish_reason = "game_over"
                            running = False
                            break
                    last_drop = time.monotonic()
                elif key in ("x", " "):
                    while try_move(board, piece, 0, 1):
                        pass
                    piece, score, lines_total, over = lock_and_spawn(board, piece, score, lines_total)
                    if over:
                        elapsed_sec = int(time.monotonic() - start_time)
                        level = calc_level(lines_total)
                        draw(board, piece, score, lines_total, level, elapsed_sec)
                        finish_reason = "game_over"
                        running = False
                        break
                    last_drop = time.monotonic()

            if not running:
                break

            # 自動落下
            now = time.monotonic()
            if now - last_drop >= drop_sec:
                if not try_move(board, piece, 0, 1):
                    piece, score, lines_total, over = lock_and_spawn(board, piece, score, lines_total)
                    if over:
                        elapsed_sec = int(time.monotonic() - start_time)
                        level = calc_level(lines_total)
                        draw(board, piece, score, lines_total, level, elapsed_sec)
                        finish_reason = "game_over"
                        break
                last_drop = now

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    if finish_reason == "game_over":
        print("\nGame Over")
    else:
        print("\nゲームを終了しました。")

    scores.append(
        {
            "name": player_name,
            "score": score,
            "lines": lines_total,
            "level": calc_level(lines_total),
            "time_sec": elapsed_sec,
            "result": finish_reason,
            "played_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    scores = sort_scores(scores)
    save_scores(scores)
    show_ranking(scores)


if __name__ == "__main__":
    main()
