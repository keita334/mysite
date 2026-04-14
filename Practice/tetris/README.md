# Tetris CLI

このフォルダには、Pythonで動くリアルタイムCLIテトリスが入っています。

## 1. リポジトリをクローン

```bash
git clone git@github.com:keita334/mysite.git
cd mysite
```

HTTPSでクローンする場合:

```bash
git clone https://github.com/keita334/mysite.git
cd mysite
```

## 2. Pythonバージョン確認

```bash
python3 --version
```

Python 3.9 以上を推奨します。

## 3. テトリスを起動

```bash
python3 Practice/tetris/tetris_cli.py
```

## 4. 操作方法

- `a` or `←`: 左移動
- `d` or `→`: 右移動
- `w` or `↑`: 回転
- `s` or `↓`: ソフトドロップ
- `x` or `space`: ハードドロップ
- `q`: 終了

## 5. スコア保存

プレイ結果は次のファイルに保存されます。

- `Practice/tetris/tetris_scores.json`

起動時と終了時にランキング（スコア順）が表示されます。

## 注意

このゲームはリアルタイム入力を使うため、パイプ実行ではなくターミナルで直接実行してください。
