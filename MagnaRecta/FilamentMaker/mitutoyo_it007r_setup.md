# Mitutoyo ID-C0512NX + IT-007R 計測データ取得メモ

## 目的

デジタルインジケータ `ID-C0512NX` の測定値を、`IT-007R` 経由でPCへ取り込み、CSV保存する。

## 構成

- デジタルインジケータ: `Mitutoyo ID-C0512NX`
- 入力ツール: `Mitutoyo IT-007R`
- PC: シリアル受信可能な端末

## 配線

1. `ID-C0512NX` を Digimaticケーブルで `IT-007R` に接続
2. `IT-007R` の D-sub 9pin を PC 側シリアルへ接続
3. PC側がUSBしかない場合は USB-Serial 変換器を使用

## 通信条件（IT-007Rマニュアル準拠）

- 速度: `2400 bps`
- データ長: `8 bit`
- パリティ: `None`
- ストップビット: `1 bit`
- フロー制御: `None`
- 方式: 全二重
- ポジション: DCE

## 取得トリガ

- `IT-007R` 本体の DATA ボタンでも取り込み可能
- PCから要求信号を送る場合は、サンプルプログラム上 `"1"` を送信

## 受信データ形式（概要）

- 正常時: 先頭固定 `01A` + 符号 + 数値 + `CR`
- 例: `01A+0000.1234\r`
- エラー時: エラーコードを返す形式あり（`1`: データなし、`2`: フォーマット外）

## 運用上の注意

- 取得間隔は `1秒以上` を推奨
- 高速連続要求はエラーの原因になり得る
- IT-007RはRS-232C前提なので、変換器やケーブル相性の確認が必要

## 参照仕様書

- `Mitutoyo_IT-007R_InputTool_User'sManual.pdf`
- `Mitutoyo_ID-C0512NX_QuickStartGuide.pdf`
- `EMC_DISCLAIMER_DIGILENT_DEVELOPMENT_AND_EVALUATION_KITS.pdf`
