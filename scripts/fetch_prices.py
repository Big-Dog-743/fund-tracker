#!/usr/bin/env python3
"""保有銘柄の価格履歴を取得して docs/data/prices.json に書き出す。

- 投資信託: 投資信託協会「投信総合検索ライブラリー」の基準価額CSV
  (分配金が出た場合は再投資したものとして補正 = トータルリターン)
- 株式・為替: Yahoo Finance chart API (調整後終値)
- 外貨建て株式は USD/JPY で円換算した系列も作る

取得に失敗した銘柄は、前回の prices.json に残っている系列をそのまま使う
(1日だけ取れなかった銘柄がグラフから消えないようにするため)。
標準ライブラリのみで動く。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "holdings.json"
OUT = ROOT / "docs" / "data" / "prices.json"

KEEP_DAYS = 365 * 5 + 30  # 5年分 + 余裕
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def http_get(url: str, retries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def cutoff_date() -> str:
    return (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()


# ---------------------------------------------------------------- 投資信託
DATE_RE = re.compile(r"(\d{4})\D(\d{1,2})\D(\d{1,2})")


def to_float(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_fund(isin: str, assoc: str) -> tuple[list[str], list[float]]:
    url = (
        "https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download?"
        + urllib.parse.urlencode({"isinCd": isin, "associFundCd": assoc})
    )
    raw = http_get(url)
    text = None
    for enc in ("cp932", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError("CSV decode failed")

    rows: list[tuple[str, float, float]] = []  # (date, nav, distribution)
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        m = DATE_RE.search(row[0])
        nav = to_float(row[1])
        if not m or nav is None or nav <= 0:
            continue  # ヘッダ行など
        d = dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        dist = to_float(row[3]) if len(row) > 3 else None
        rows.append((d, nav, dist or 0.0))

    if not rows:
        head = text[:200].replace("\n", " | ")
        raise RuntimeError(f"no rows parsed (head: {head!r})")
    rows.sort()

    # 分配金再投資ベースに補正 (分配落ち日の基準価額 + 分配金 で再投資したとみなす)
    dates, values = [], []
    factor = 1.0
    for d, nav, dist in rows:
        if dist > 0:
            factor *= (nav + dist) / nav
        dates.append(d)
        values.append(round(nav * factor, 4))
    return dates, values


# ---------------------------------------------------------------- 株式・為替
def fetch_yahoo(symbol: str) -> tuple[list[str], list[float]]:
    q = urllib.parse.urlencode({"range": "10y", "interval": "1d", "events": "div,split"})
    err: Exception | None = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{q}"
            data = json.loads(http_get(url))
            res = data["chart"]["result"][0]
            ts = res["timestamp"]
            offset = res.get("meta", {}).get("gmtoffset", 0)
            ind = res["indicators"]
            closes = None
            if ind.get("adjclose"):
                closes = ind["adjclose"][0].get("adjclose")
            if not closes:
                closes = ind["quote"][0]["close"]
            by_date: dict[str, float] = {}
            for t, c in zip(ts, closes):
                if c is None or (isinstance(c, float) and math.isnan(c)):
                    continue
                d = dt.datetime.fromtimestamp(t + offset, dt.timezone.utc).date().isoformat()
                by_date[d] = float(c)
            if not by_date:
                raise RuntimeError("empty series")
            dates = sorted(by_date)
            return dates, [round(by_date[d], 4) for d in dates]
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"yahoo {symbol}: {err}")


def convert_to_jpy(dates, values, fx_dates, fx_values):
    """各日付について、その日以前で最新の USD/JPY を掛ける。"""
    out_d, out_v = [], []
    j = -1
    for d, v in zip(dates, values):
        while j + 1 < len(fx_dates) and fx_dates[j + 1] <= d:
            j += 1
        if j < 0:
            continue
        out_d.append(d)
        out_v.append(round(v * fx_values[j], 4))
    return out_d, out_v


# ---------------------------------------------------------------- main
def trim(dates, values, since):
    pairs = [(d, v) for d, v in zip(dates, values) if d >= since]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    previous = {}
    if OUT.exists():
        try:
            previous = {s["id"]: s for s in json.loads(OUT.read_text(encoding="utf-8"))["series"]}
        except Exception:  # noqa: BLE001
            previous = {}

    since = cutoff_date()
    errors: list[str] = []
    fx = None
    if any(h.get("type") == "stock" and h.get("currency", "JPY") != "JPY" for h in cfg["holdings"]):
        try:
            fx = fetch_yahoo("JPY=X")
            print(f"[ok] USD/JPY: {len(fx[0])} rows, last {fx[0][-1]} {fx[1][-1]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"USD/JPY: {e}")
            print(f"[NG] USD/JPY: {e}", file=sys.stderr)

    series = []
    for h in cfg["holdings"]:
        base = {k: h[k] for k in ("id", "name", "short", "type")}
        try:
            if h["type"] == "fund":
                d, v = fetch_fund(h["isin"], h["assoc"])
                s = {**base, "currency": "JPY", "source": "投資信託協会(分配金再投資ベース)"}
                s["dates"], s["values"] = trim(d, v, since)
            else:
                d, v = fetch_yahoo(h["symbol"])
                cur = h.get("currency", "JPY")
                s = {**base, "currency": cur, "source": "Yahoo Finance(調整後終値)"}
                if cur == "JPY":
                    s["dates"], s["values"] = trim(d, v, since)
                else:
                    if fx is None:
                        raise RuntimeError("USD/JPY unavailable for conversion")
                    jd, jv = convert_to_jpy(d, v, *fx)
                    s["dates"], s["values"] = trim(jd, jv, since)
                    s["local_dates"], s["local_values"] = trim(d, v, since)
            if not s["dates"]:
                raise RuntimeError("no data in range")
            s["stale"] = False
            print(f"[ok] {h['short']}: {len(s['dates'])} rows, last {s['dates'][-1]} {s['values'][-1]}")
            series.append(s)
        except Exception as e:  # noqa: BLE001
            msg = f"{h['short']}: {e}"
            errors.append(msg)
            print(f"[NG] {msg}", file=sys.stderr)
            if h["id"] in previous:
                old = previous[h["id"]]
                old.update(base)
                old["stale"] = True
                series.append(old)

    if not series:
        print("全銘柄の取得に失敗しました", file=sys.stderr)
        return 1

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "errors": errors,
        "series": series,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({len(series)} series, {len(errors)} errors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
