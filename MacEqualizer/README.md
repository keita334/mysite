# MacEqualizer

SwiftUI + AVAudioEngine で作った、macOS 用の 10 バンド・グラフィックイコライザー付き音楽プレーヤーです。

## 機能

- 音声ファイル (mp3 / m4a / wav / aiff / flac など) の再生・一時停止・停止・シーク
- 10 バンドイコライザー (32Hz〜16kHz, ±12dB, 0.5dB 刻み)
- プリアンプ (全体ゲイン) — ブーストで音割れする場合はマイナスに
- 周波数特性カーブのリアルタイム表示
- プリセット (フラット / 低音強調 / ボーカル / ロック / ジャズ など 11 種)
- EQ バイパス (オン/オフ比較)
- ドラッグ&ドロップでファイルを開く
- EQ 設定は自動保存され、次回起動時に復元
- キーボード: `Space` 再生/一時停止、`⌘O` 開く、`⌘.` 停止、スライダーをダブルクリックで 0dB

## 動作環境

- macOS 14 以降
- Xcode 16 以降

## ビルド方法

1. `MacEqualizer.xcodeproj` を Xcode で開く
2. ターゲットに `My Mac` を選び、`⌘R` で実行

署名は「Sign to Run Locally」設定なので、Apple Developer アカウントなしでローカル実行できます。
配布したい場合は *Signing & Capabilities* で Team を設定してください。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `MacEqualizerApp.swift` | アプリのエントリーポイント |
| `AudioEngine.swift` | 再生と EQ 処理 (`AVAudioPlayerNode → AVAudioUnitEQ → 出力`) |
| `ContentView.swift` | メイン画面 (再生コントロール・EQ スライダー・プリセット) |
| `VerticalSlider.swift` | EQ 用の縦型スライダー |
| `EQCurveView.swift` | 周波数特性カーブの描画 |
| `EQPreset.swift` | プリセット定義 |

## 仕組み

`AVAudioUnitEQ` の 10 個のバンドをそれぞれピーキング (parametric) フィルタ、帯域幅 1 オクターブに設定し、
スライダーの値をそのまま各バンドの `gain` に反映しています。

## 拡張のヒント

- **システム全体の音にEQをかけたい場合**: Spotify や YouTube など他アプリの音を処理するには、
  仮想オーディオデバイス (例: BlackHole) から入力を受けて EQ 後に実スピーカーへ出力する構成や、
  Core Audio の Process Tap (macOS 14.2+) を使う必要があります。このアプリはその前段となる基本実装です。
- ユーザー定義プリセットの保存、スペクトラムアナライザー表示 (`installTap` で FFT) なども追加しやすい構造になっています。
