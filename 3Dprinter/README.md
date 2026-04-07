# 3Dprinter

このディレクトリには、`CustomSlicer` 本体と、`CustomSlicer` で生成した `gcode` ファイルをまとめています。

## 構成

- `CustomSlicer/`
  - Blender ファイルで作成したカスタムスライサー
  - `xywave` / `zwave` の設定・バリエーションを含む
- `testprint/`
  - スライサー出力結果の `gcode` ファイル一式
  - `xywave` / `zwave` ごとのテスト出力を保存

## 使い方の想定

1. `CustomSlicer` の `.blend` ファイルを開いてスライス設定を確認・調整する
2. 出力した `gcode` を `testprint` 配下で管理する
3. 3D プリンターでテスト印刷し、必要に応じて再調整する

## 補足

`testprint` には実験用の `gcode` が複数含まれています。ファイル名から波形やバージョン（例: `twist`, `zwave`）を判別できるようにしています。

![IMG_4511](https://github.com/user-attachments/assets/474a242e-9c86-49ed-ad45-7819c88269fd)
