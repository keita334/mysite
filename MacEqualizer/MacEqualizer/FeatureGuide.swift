import SwiftUI

/// 機能の説明。アプリの「説明」ウィンドウと、リポジトリの discription.md の両方をここから作る (内容がずれないように)
struct GuideSection: Identifiable {
    /// アプリで使うアイコン (SF Symbols)
    let symbol: String
    /// discription.md で使うシンボル
    let emoji: String
    let title: String
    var badge: String?
    let color: Color
    /// 1 行ずつの説明。**太字** などの Markdown を使える
    let items: [String]
    /// アプリでは「権限の設定を開く」ボタンも出す
    var showsPrivacySettingsButton = false

    var id: String { title }
}

enum FeatureGuide {
    static let intro = """
        Apple Music・Spotify・ブラウザなど、**Mac 全体の音**に EQ・エコー・ホールなどの響きをかけるアプリです。\
        アプリを起動している間だけ効き、ウィンドウを閉じるとアプリが終了して元の音に戻ります。
        """

    /// 「音の流れ」の各段階
    static let flowSteps: [String] = [
        "**① 取り込み**: macOS の Process Tap で、このアプリ以外のすべてのアプリの音を取り込みます。元の音はミュートされ、加工後の音だけが聞こえます",
        "**② 加工**: EQ → エコー → 環境 (響き) の順にかけます。オーディオ専用のスレッドで動くので、画面を操作しても音が途切れません",
        "**③ 出力**: いま選ばれている出力デバイス (スピーカー・ヘッドホン・オーディオインターフェース) へ出します。出力先を切り替えると自動で追従します",
        "**④ 表示**: 出力した音を FFT で分析し、EQ のグラフの後ろ (アナライザー) とビジュアル表示に使います",
        "このアプリ自身の音は取り込みません。取り込むと、自分の出した音をまた加工してループするためです",
    ]

    static let sections: [GuideSection] = [
        GuideSection(symbol: "slider.horizontal.3", emoji: "🎛️", title: "EQ", color: Theme.bandColors[5], items: [
            "8 バンド: ローカット / ローシェルフ / ピーク ×4 / ハイシェルフ / ハイカット (Logic Pro の Channel EQ と同じ構成)",
            "**グラフの点をドラッグ**: 横で周波数、縦でゲインが変わります。点をダブルクリックするとそのバンドのオン/オフ",
            "**下の数値欄を上下にドラッグ**: 周波数・ゲイン・Q (カットはスロープ) を細かく調整。ダブルクリックで初期値に戻ります",
            "グラフの上の色付きボタンで、バンドごとにオン/オフ",
            "**プリセット** (フラット・低音強調・ボーカルなど 11 種) と **リセット** (EQ だけを初期状態に戻す)",
            "**電源ボタン**: EQ・エコー・環境をまとめてオフにして、元の音と聴き比べ",
            "**出力**: 全体の音量。ブーストで音が割れるときは下げます",
        ]),
        GuideSection(symbol: "chart.bar.xaxis", emoji: "📊", title: "アナライザー", color: Theme.analyzerLabel, items: [
            "EQ・エコー・環境をかけた後の音を、周波数ごとのレベルでグラフの後ろに表示します",
            "左の目盛りがレベル (dBFS)、右の目盛りが EQ のゲイン (dB)",
            "ヘッダーの「アナライザー」で表示のオン/オフ",
        ]),
        GuideSection(symbol: "building.columns", emoji: "🏛️", title: "環境 (空間の響き)", color: Theme.bandColors[6], items: [
            "ヘッダーの「環境」で空間を選びます: **スタジオ** (0.29 秒) / **ライブハウス** (0.95 秒) / **コンサートホール** (1.77 秒) / **大ホール** (2.63 秒) / **アリーナ** (2.76 秒) / **大聖堂** (7.64 秒)。カッコ内は響きが消えるまでの時間 (RT60) の実測値",
            "右の % は響きの量。上下にドラッグで調整、ダブルクリックでその空間のおすすめ値",
            "Apple の残響エフェクト (AUMatrixReverb) を使っています",
        ]),
        GuideSection(symbol: "repeat", emoji: "🔁", title: "エコー", badge: "実験的", color: Theme.bandColors[2], items: [
            "ヘッダーの「エコー」から設定します。プリセット: スラップバック / 標準 / ロング / ピンポン",
            "**間隔** (30〜1500 ms)・**繰り返し** (0〜90%)・**量**・**ピンポン** (左右交互に飛ばす)",
            "繰り返すほど音がこもり、間隔を変えるとテープエコーのように音程が揺れながら追従します",
            "原音に足すので、量や繰り返しを上げると音が大きくなります",
        ]),
        GuideSection(symbol: "sparkles", emoji: "✨", title: "ビジュアル", color: Theme.bandColors[7], items: [
            "ヘッダーの「EQ / ビジュアル」で切り替えると、音に合わせて粒子が動きます",
            "円の上が低音、下が高音 (左右対称)。その帯域が鳴るほど粒子が外へ広がって明るくなります",
            "低音の拍 (キックなど) で全体が脈打って火花が飛び、高音が多いほど粒子が細かく震えます",
            "ダブルクリックでフルスクリーン。表示中は CPU を多めに使います (1 コアの 20〜25% ほど)",
        ]),
        GuideSection(symbol: "wrench.and.screwdriver", emoji: "🛠️", title: "困ったとき", color: Theme.bandColors[0], items: [
            "**アナライザーが動かない / 音が出ない**: システム設定 > プライバシーとセキュリティ > 画面収録とシステムオーディオ録音 で MacEqualizer を許可してください",
            "**音が割れる**: 「出力」を下げてください。EQ のブーストやエコーで音量が上がるためです",
            "**「すでに起動しています」と出る**: MacEqualizer が 2 つ動いています。2 つ同時だと互いの音を取り込んで発振するため、片方を終了してから「再開」を押してください",
            "**ヘッダーが「停止中」**: 出力デバイスの切り替え後に音を取り込み直せなかったときなどに止まります。「再開」を押してください",
            "AirPods などの Bluetooth ヘッドホンでは、まだ動作を確かめていません",
        ], showsPrivacySettingsButton: true),
    ]
}

/// 「画面収録とシステムオーディオ録音」の設定画面を開く
enum PrivacySettings {
    static func openAudioCapture() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }
}
