#!/usr/bin/env python3
"""Fetch a hot board snapshot for a given source and append items as JSONL.

Usage: python3 fetch.py {zhihu,bilibili}
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "https://uapis.cn/api/v1/misc/hotboard"
DATA_DIR = Path(__file__).parent / "data"


def fetch_payload(source: str) -> dict:
    url = f"{API_BASE}?type={source}"
    req = urllib.request.Request(url, headers={"User-Agent": "my-zhihu-hot/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def build_record_zhihu(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Convert one zhihu API item into a JSONL record.

    Raw item shape:
        {"index": 1, "title": "...", "url": ".../question/12345",
         "hot_value": "781 万热度",
         "extra": {"desc": "...", "image": "...", "label": "新"}}
    """
    extra = item.get("extra", {})
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
        "desc": extra.get("desc"),
        "image": extra.get("image"),
    }


def build_record_bilibili(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Convert one bilibili API item into a JSONL record.

    Raw item shape (much richer than zhihu):
        {
          "index": 1,
          "title": "...",
          "url": "https://www.bilibili.com/video/BV...",
          "hot_value": "806638播放",
          "extra": {
            "aid": 116515600861789,
            "bvid": "BV129ReB1ExM",
            "owner": {"face": "...", "mid": 383578614, "name": "..."},
            "stat": {"coin": 10128, "danmaku": 1443, "favorite": 10551,
                     "like": 21891, "reply": 1476, "share": 2839, "view": 806638},
            "pic": "...", "pubdate": "2026-05-04T09:47:12.000Z",
            "duration": 1340, "tname": "出行", "desc": "...",
            "short_link": "https://b23.tv/BV...", "rcmd_reason": ""
          }
        }

    Trade-offs to consider — these are *different* from zhihu, write thoughtfully:

    1. `extra.stat` is a goldmine for trend analysis:
         view/like/coin/favorite/share/danmaku/reply
       At hourly cadence you can plot how a video grows. Keep the whole stat dict?
       Just view+like? (Flat columns are easier to query in pandas.)

    2. `extra.bvid` is the natural dedupe key (you said dedupe at analysis time).
       Keep it at top level for `grep BV129ReB1ExM` convenience.

    3. `extra.owner.name` + `extra.owner.mid`:
       enables "which UP主上榜最多" and "which UP主热度涨最快" analyses.

    4. `extra.pubdate`:
       lets you compute "video age when it hit hot list" → cold-start vs viral velocity.

    5. `extra.tname` (category like 出行/游戏/知识):
       useful for category breakdowns. Cheap to keep.

    6. `extra.duration` (seconds): correlates with engagement metrics.

    7. Things probably not worth keeping:
       - `extra.pic`, `extra.owner.face`: image URLs, big and rarely useful for analysis
       - `extra.rcmd_reason`: empirically empty
       - `extra.short_link`: derivable from bvid

    Required: include `fetched_at`, `source_update_time`, `index`, `title`, `url`,
    `hot_value` so the line is self-describing for time-series analysis.
    """
    extra = item.get("extra", {})
    owner = extra.get("owner", {})
    stat = extra.get("stat", {})
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
        "bvid": extra.get("bvid"),
        "owner_name": owner.get("name"),
        "owner_mid": owner.get("mid"),
        "tname": extra.get("tname"),
        "duration": extra.get("duration"),
        "pubdate": extra.get("pubdate"),
        "stat": stat,  # 整个 dict 嵌套保留
        "desc": extra.get("desc"),
    }


def build_record_weibo(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Convert one weibo API item into a JSONL record.

    Raw item shape (sparser than zhihu/bilibili — extra is always {}):
        {"index": 1, "title": "话题名",
         "url": "https://s.weibo.com/weibo?q=%23...%23",
         "hot_value": "13981899", "extra": {}}

    Notes:
    - `hot_value` is a plain numeric string here (no 万/亿 unit). We still keep
      it as a string for cross-source schema consistency; parse at analysis time.
    - `url` contains a URL-encoded #hashtag#; the topic name itself is the
      natural dedupe key (or just use `title`).
    """
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
    }


def build_record_douyin(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Douyin: `extra.view_count` is aggregate views across all videos under the
    topic; `extra.video_count` is how many videos are tagged. `extra.cover` is a
    signed CDN URL with `x-expires` — skip it (will be dead in days)."""
    extra = item.get("extra") or {}
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
        "sentence_id": extra.get("sentence_id"),
        "video_count": extra.get("video_count"),
        "view_count": extra.get("view_count"),
        "label": extra.get("label"),
    }


def build_record_kuaishou(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Kuaishou's `extra` is literally `null`, not `{}` — `item.get("extra", {})`
    would still return None in that case, hence the `or {}` guard.
    `hot_value` format: `"1,116.2万"` (with thousands separator + 万 unit)."""
    extra = item.get("extra") or {}  # noqa: F841 — kept for symmetry & future-proofing
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
    }


def build_record_baidu(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Baidu often returns 51 items because `is_top: true` entries are pinned
    above the regular top-50. Keep `is_top` so analysis can filter sticky items."""
    extra = item.get("extra") or {}
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
        "desc": extra.get("desc"),
        "is_top": extra.get("is_top"),
    }


def build_record_v2ex(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """V2EX exposes no hot score (`hot_value` is always `""`); ranking is just
    whatever order V2EX's hot list gives. `extra.id` is the thread ID — natural
    dedupe key for analysis."""
    extra = item.get("extra") or {}
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],  # always ""
        "thread_id": extra.get("id"),
        "author": extra.get("author"),
    }


def build_record_huxiu(item: dict, fetched_at: str, source_update_time: str) -> dict:
    """Huxiu's `hot_value` is a composite string `"收藏:N 评论:M"` — the same
    numbers are already structured in `extra.favorite_num`/`extra.comment_num`.
    Keep both: structured for analysis, raw for fidelity."""
    extra = item.get("extra") or {}
    return {
        "fetched_at": fetched_at,
        "source_update_time": source_update_time,
        "index": item["index"],
        "title": item["title"],
        "url": item["url"],
        "hot_value": item["hot_value"],
        "article_id": extra.get("id"),
        "channel": extra.get("channel"),
        "type": extra.get("type"),
        "favorite_num": extra.get("favorite_num"),
        "comment_num": extra.get("comment_num"),
        "summary": extra.get("summary"),
    }


BUILDERS = {
    "zhihu": build_record_zhihu,
    "bilibili": build_record_bilibili,
    "weibo": build_record_weibo,
    "douyin": build_record_douyin,
    "kuaishou": build_record_kuaishou,
    "baidu": build_record_baidu,
    "v2ex": build_record_v2ex,
    "huxiu": build_record_huxiu,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=sorted(BUILDERS.keys()))
    args = parser.parse_args()

    try:
        payload = fetch_payload(args.source)
    except Exception as e:
        print(f"[fetch error: {args.source}] {e}", file=sys.stderr)
        return 1

    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    source_update_time = payload.get("update_time", "")
    items = payload.get("list", [])
    if not items:
        print(
            f"[{fetched_at}] {args.source}: empty list, skipping write", file=sys.stderr
        )
        return 1

    out_file = DATA_DIR / f"{args.source}_hot.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    builder = BUILDERS[args.source]
    with out_file.open("a", encoding="utf-8") as f:
        for item in items:
            record = builder(item, fetched_at, source_update_time)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[{fetched_at}] {args.source}: appended {len(items)} records → {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
