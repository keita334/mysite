# WaveSlicer App

Blenderに依存しないGUIスライサーです。

## 実装済み
- OpenGL 3Dビュー（回転: 左ドラッグ / パン: 右ドラッグ / ズーム: ホイール）
- 複数周回（Perimeters）
- インフィル（ハッチ）
- サポート（単純オーバーハング検出）
- プロファイル保存（Printer / Filament）

## 起動
```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 構成
- `main.py`: アプリ起動
- `src/waveslicer/core/slicer.py`: Blender非依存スライスロジック
- `src/waveslicer/ui/main_window.py`: メインGUI
- `src/waveslicer/ui/gl_view.py`: OpenGL 3Dビュー
- `profiles/printers/*.json`: プリンタプロファイル
- `profiles/filaments/*.json`: フィラメントプロファイル

## 注意
サポートは初期版のため、商用スライサーほど高機能ではありません。順次改善できます。
