#!/usr/bin/env python3
"""
Apple整備済製品の絞り込みページを見張り、前回チェックから「新しく出た製品」があれば
iPhone（ntfy）に通知する。GitHub Actionsで定期実行する前提（Macは不要）。

  python3 watch.py          # チェックして、差分があれば通知
  python3 watch.py --test   # テスト通知
  python3 watch.py --list   # 今ページに出ている製品を表示するだけ
"""

import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

# ===================== 設定 =====================

# 見張るページ（いくつでも追加できる）
WATCH_URLS = [
    "https://www.apple.com/jp/shop/refurbished/mac/2tb-14%E3%82%A4%E3%83%B3%E3%83%81-macbook-pro-48gb",
]

# 製品名でさらに絞る（空のリスト [] にすると、ページに新しく出たものをすべて通知）
TITLE_MUST = ["14インチ", "M5 Pro", "18コアCPUと20コアGPU"]

# 製品ページで確認するメモリ容量とSSD容量（ここに合うものだけ通知）
MEMORY_OK = ["48GB", "64GB"]
STORAGE_OK = ["2TB"]

# Nano-texture版も通知するか
ALLOW_NANO = True

# =================================================

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # GitHubのSecretsから渡す
STATE_FILE = pathlib.Path(__file__).with_name("state.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15")
PRODUCT_RE = re.compile(r"/jp/shop/product/([a-z0-9]{6})/a/([^\"'?#\s<>\\]+)", re.I)


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja-JP,ja;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def products_on(page):
    page = page.replace("\\u002F", "/").replace("\\/", "/")
    items = {}
    for code, slug in PRODUCT_RE.findall(page):
        code = code.lower()
        if code not in items:
            items[code] = {
                "title": urllib.parse.unquote(slug).replace("-", " "),
                "url": f"https://www.apple.com/jp/shop/product/{code}/a/{slug}",
            }
    return items


def wanted(title):
    if TITLE_MUST and not all(w in title for w in TITLE_MUST):
        return False
    if not ALLOW_NANO and "Nano" in title:
        return False
    return True


def read_specs(url):
    """製品ページからメモリ・SSD・価格を読み取る"""
    text = html.unescape(fetch(url))
    mem = re.search(r"(\d+)\s*GB\s*ユニファイドメモリ", text)
    ssd = re.search(r"(\d+)\s*TB\s*SSD", text)
    price = re.search(r"([\d,]{6,})\s*円", text)
    return (
        f"{mem.group(1)}GB" if mem else None,
        f"{ssd.group(1)}TB" if ssd else None,
        f"{price.group(1)}円" if price else "価格不明",
    )


def notify(title, message, url=None):
    if not NTFY_TOPIC:
        log("NTFY_TOPICが未設定のため通知できません")
        return
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message, "priority": 5, "tags": ["computer"]}
    if url:
        payload["click"] = url
        payload["actions"] = [{"action": "view", "label": "Appleストアを開く", "url": url}]
    req = urllib.request.Request("https://ntfy.sh", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def main():
    args = sys.argv[1:]
    if "--test" in args:
        notify("テスト通知", "整備済製品ウォッチャー（クラウド版）の通知テストです",
               "https://www.apple.com/jp/shop/refurbished/mac")
        log("テスト通知を送りました")
        return

    current = {}
    for url in WATCH_URLS:
        try:
            current.update(products_on(fetch(url)))
        except Exception as e:
            log(f"取得に失敗: {url} : {e}")
            return  # 取得失敗時は状態を更新しない（誤って再通知しないため）

    if "--list" in args:
        log(f"ページ上の製品: {len(current)}件")
        for code, v in current.items():
            mark = "★" if wanted(v["title"]) else "  "
            log(f"{mark} {code}  {v['title']}")
        return

    try:
        previous = json.loads(STATE_FILE.read_text())
    except Exception:
        previous = {}

    new_codes = [c for c in current if c not in previous]
    state = {c: previous[c] for c in current if c in previous}  # 消えたものは忘れる→再入荷で再通知

    for code in new_codes:
        v = current[code]
        entry = {"title": v["title"]}
        if not wanted(v["title"]):
            log(f"新着だが製品名が条件外: {code} {v['title']}")
        else:
            try:
                mem, ssd, price = read_specs(v["url"])
            except Exception as e:
                log(f"製品ページの取得に失敗（次回再確認）: {code} : {e}")
                continue  # 状態に入れない → 次回もう一度確認する
            entry.update({"mem": mem, "ssd": ssd})
            if mem in MEMORY_OK and ssd in STORAGE_OK:
                nano = "（Nano-texture）" if "Nano" in v["title"] else ""
                notify(f"入荷！ 14インチ M5 Pro {mem}/{ssd}{nano}", f"{price}　{v['title'][:70]}", v["url"])
                log(f"通知: {code} {mem} {ssd} {price}")
            else:
                log(f"新着だが仕様が条件外: {code} {mem} {ssd} {v['title']}")
        state[code] = entry

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    log(f"チェック完了（掲載{len(current)}件、新着{len(new_codes)}件）")


if __name__ == "__main__":
    main()
