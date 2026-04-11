# niitFes Directory Structure

- `web/`
  - `index.html`: HOME (商品写真のみ)
  - `pages/`: HOME以外の各ページ
  - `assets/css/styles.css`: 共通スタイル
  - `assets/js/app.js`: 共通ロジック（スライドショー/非同期読込）
  - `assets/data/data.json`: 表示データ
  - `assets/images/home/`: HOME用画像（`home-01.jpg` など）
  - `assets/images/home/source/`: 元画像の保管
  - `assets/images/products/`: 商品画像（拡張用）
- `models/`
  - `stl/`: STLデータ
  - `source/`: CAD元データ
  - `export/`: 変換後データ

## Preview

```bash
cd web
python3 -m http.server
```

- HOME: `http://localhost:8000/`
- その他: `http://localhost:8000/pages/`
