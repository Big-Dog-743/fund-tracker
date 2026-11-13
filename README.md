# 保有銘柄の値動き比較

保有している投資信託・株式の値動きを、期間の始点を 0% として重ねて比較する GitHub Pages サイト。
「どれかを売らなきゃいけない時に、どれを売るか」の判断材料用。

- 期間切り替え: 1ヶ月 / 3ヶ月 / 6ヶ月 / 年初来 / 1年 / 3年 / 5年（←→キーでも切り替え）
- 凡例・表の行クリックで表示/非表示、行にカーソルでその線を強調
- 表: 騰落率・直近1週間・期間高値からの下落率・最大下落・変動率(年率)

## 仕組み

```
GitHub Actions (1日2回)
  └ scripts/fetch_prices.py
       ├ 投資信託 : 投資信託協会 投信総合検索ライブラリーの基準価額CSV（分配金再投資ベースに補正）
       └ 株式・為替: Yahoo Finance（調整後終値、米国株は USD/JPY で円換算）
  └ docs/data/prices.json をコミット
GitHub Pages (main ブランチ /docs) が docs/index.html を配信
```

取得に失敗した銘柄は前回データを残し、画面に「取得失敗」と表示する。

## 初期設定

1. Settings → Pages → Source を **Deploy from a branch**、Branch を **main / /docs** にする
2. Settings → Actions → General → Workflow permissions を **Read and write permissions** にする
3. Actions タブ → **Update prices** → **Run workflow** で初回データ取得

## 銘柄の追加・削除

`holdings.json` を編集して push するだけ（push で自動的にデータ再取得が走る）。

- 投資信託: `isin`（ISINコード）と `assoc`（協会コード）が必要。
  楽天証券のファンドページURLの `ID=JP90C...` が ISIN、モーニングスター/マネックス等で使われる8桁英数字が協会コード。
- 株式: `symbol` に Yahoo Finance のティッカー（東証は `7974.T` のように `.T`）、`currency` に `JPY` か `USD`。
- 並び順がグラフの色の割り当て順。色は8色までなので、9銘柄以上にする場合は index.html の配色の追加が必要。
