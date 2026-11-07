# -*- coding: utf-8 -*-
"""
每日科研前沿 · 英文论文自动抓取脚本（GitHub Actions 定时运行，纯 Python 标准库）

数据源：Crossref REST API（开放元数据，无需 Key）。
产出：仓库根目录 daily-frontier.json，供前端「每日科研前沿」拉取。

边界（与人工维护的 rnc-updates.json 区分）：
- 本脚本只抓英文期刊论文元数据；
- 中文核心、会议、课题、特刊征稿由人工核实后录入 rnc-updates.json，不在此自动抓取。

质量规则：
- 近 RECENT_DAYS 天内发表；剔除空标题、过短标题、未来年份、过早年份；
- 标题/期刊关键词命中打分，低于 MIN_SCORE 剔除；DOI 去重；
- 按匹配度 → 被引数 → 发表日期排序，取前 MAX_ITEMS。

本地自测：
  python scripts/fetch_daily.py --dry-run
  python scripts/fetch_daily.py --out daily-frontier.json
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------- 可调配置 ----------
RECENT_DAYS = 120
MAX_ITEMS = 20
ROWS_PER_GROUP = 25
MIN_SCORE = 4
MAILTO = "research@example.com"
USER_AGENT = "kaoyan-db-daily-fetch/1.0 (mailto:%s)" % MAILTO

# 主方向：运动心血管生物学（权重高）
CARDIO_KW = {
    "heart failure": 5, "myocardial": 5, "mitochondri": 5, "mitophagy": 5,
    "cardioprotect": 5, "cardiac": 4, "cardiovascular": 4, "vagal": 4,
    "autonomic": 3, "enos": 3, "nlrp3": 3, "vascular": 3, "ischemia": 3,
    "reperfusion": 3, "hypertension": 2, "blood pressure": 2, "autonomic nervous": 3,
    "exercise training": 2, "aerobic exercise": 2, "exercise": 1, "endothelial": 2,
}
# 辅方向：运动医学 / 康复
MED_KW = {
    "sports medicine": 3, "return to sport": 3, "rehabilitation": 2, "physical therapy": 2,
    "rehab": 2, "injury": 2, "orthopaedic": 1, "ligament": 1, "tendon": 1,
    "knee": 1, "shoulder": 1, "muscle": 1,
}
# 对口期刊加权（标题未命中但期刊对口时加分）
JOURNAL_BOOST = [
    "british journal of sports medicine", "american journal of sports medicine",
    "journal of sport and health science", "medicine and science in sports",
    "circulation", "journal of the american college of cardiology", "european journal of heart failure",
    "journal of physiology", "international journal of molecular sciences",
    "frontiers in cardiovascular medicine", "hypertension", "journal of molecular and cellular cardiology",
    "basic research in cardiology", "european journal of preventive cardiology",
]

CARDIO_QUERY = ("exercise cardiovascular heart failure myocardial mitochondria mitophagy "
                "vagal autonomic eNOS NLRP3 vascular ischemia reperfusion exercise training cardiac")
MED_QUERY = ("sports medicine exercise rehabilitation injury return to sport physical therapy "
             "orthopaedic ligament tendon knee shoulder")

# 心脏强词：命中即视为靶部位，保留
STRONG_CARDIO = ["myocardial", "cardiac", "heart failure", "heart", "cardiomyocyte",
                 "ventricular", "myocardium", "cardioprotect"]
# 非靶部位：未命中心脏强词时，命中这些部位视为偏离，剔除
OFFTOPIC_SITE = ["hepatic", "liver", "ovarian", "ovary", "testicular", "testis",
                 "cerebral", "brain", "hippocamp", "pancreatic", "knee arthroplasty",
                 "popliteal", "bone density", "skin", "gastric", "intestinal", "lung injury",
                 "skeletal muscle", "tendon", "ligament"]

REVIEW_RE = re.compile(r"\b(review|meta-analysis|systematic review|scoping review)\b", re.I)


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def crossref_group(query, from_date):
    params = urllib.parse.urlencode({
        "query.bibliographic": query,
        "filter": "from-pub-date:%s,type:journal-article" % from_date,
        "rows": ROWS_PER_GROUP,
        "sort": "issued",
        "order": "desc",
        "select": "title,container-title,issued,DOI,is-referenced-by-count,type",
        "mailto": MAILTO,
    })
    url = "https://api.crossref.org/works?" + params
    data = http_get_json(url)
    return data.get("message", {}).get("items", []) or []


def parse_pub(item):
    """返回 (year:int|None, published:str)。"""
    issued = item.get("issued", {}).get("date-parts", [[None]])
    parts = issued[0] if issued else [None]
    year = parts[0] if parts else None
    if not isinstance(year, int):
        return None, ""
    month = parts[1] if len(parts) > 1 and isinstance(parts[1], int) else 1
    day = parts[2] if len(parts) > 2 and isinstance(parts[2], int) else 1
    return year, "%04d-%02d-%02d" % (year, month, day)


def score_item(title, journal):
    t = (title or "").lower()
    j = (journal or "").lower()
    score = 0
    topics = []
    strong = any(k in t for k in STRONG_CARDIO)
    cardio_hit = strong
    for kw, w in CARDIO_KW.items():
        if kw in t:
            score += w
            cardio_hit = True
    med_hit = False
    for kw, w in MED_KW.items():
        if kw in t:
            score += w
            med_hit = True
    for jname in JOURNAL_BOOST:
        if jname in j:
            score += 3
            cardio_hit = cardio_hit or ("cardio" in jname or "heart" in jname or "circulation" in jname)
            med_hit = med_hit or ("sports" in jname or "medicine" in jname)
            break
    if cardio_hit:
        topics.append("cardio")
    if med_hit:
        topics.append("med")
    # 部位偏离：未命中心脏强词、且属于非靶部位即剔除（不被 ischemia/线粒体等泛词豁免）
    offtopic = (not strong) and any(s in t for s in OFFTOPIC_SITE)
    return min(score, 100), topics, strong, offtopic


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="输出 JSON 路径，默认仓库根 daily-frontier.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = ap.parse_args()

    today = dt.date.today()
    from_date = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()

    raw = []
    for q in (CARDIO_QUERY, MED_QUERY):
        try:
            raw.extend(crossref_group(q, from_date))
            time.sleep(2)  # 礼貌间隔，避免触发 Crossref 限流
        except Exception as e:  # 单组失败不致命，另一组仍可用
            print("WARN: group fetch failed: %r" % e, file=sys.stderr)

    seen_doi = set()
    papers = []
    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    for it in raw:
        title = (it.get("title") or [""])[0].strip()
        journal = (it.get("container-title") or [""])[0].strip()
        doi = (it.get("DOI") or "").strip()
        if not title or len(title) < 15:
            continue
        year, published = parse_pub(it)
        # 允许下一年在线先发（卷期年为次年）；只剔除脏未来年份（如 2109）与过早文献
        if year is None or year > today.year + 1 or year < 2015:
            continue
        doi_key = doi.lower()
        if doi_key and doi_key in seen_doi:
            continue
        score, topics, strong, offtopic = score_item(title, journal)
        if offtopic:
            continue
        med_hit = "med" in topics
        # 低分且既非心脏强词、也非运动医学/康复方向的弱相关条目剔除
        if score < 6 and not strong and not med_hit:
            continue
        if score < MIN_SCORE:
            continue
        if doi_key:
            seen_doi.add(doi_key)
        citations = it.get("is-referenced-by-count", 0) or 0
        papers.append({
            "id": ("doi-" + doi_key) if doi_key else "title-" + str(len(papers)),
            "title": title,
            "journal": journal or "未知期刊",
            "year": year,
            "published": published,
            "doi": doi,
            "url": ("https://doi.org/" + urllib.parse.quote(doi)) if doi else "",
            "citations": citations,
            "isReview": bool(REVIEW_RE.search(title)),
            "matchScore": score,
            "topics": topics,
        })

    # 排序：匹配度 → 被引 → 日期新
    papers.sort(key=lambda p: (p["matchScore"], p["citations"], p["published"]), reverse=True)
    papers = papers[:MAX_ITEMS]

    payload = {
        "version": today.isoformat(),
        "meta": {
            "generatedAt": now_iso,
            "source": "Crossref REST API",
            "count": len(papers),
            "windowDays": RECENT_DAYS,
            "note": "英文期刊论文由 GitHub Actions 每日自动抓取；中文核心、会议、课题、特刊征稿见 rnc-updates.json（人工核实）。",
        },
        "items": papers,
    }

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n=== 入选 %d 篇 ===" % len(papers))
        for p in papers:
            print("  [%d|cites=%s|%s] %s" % (p["matchScore"], p["citations"], p["year"], p["title"][:64]))
        return

    out = pathlib.Path(args.out) if args.out else pathlib.Path(__file__).resolve().parent.parent / "daily-frontier.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # 无 BOM、纯 LF
    out.write_text(text, encoding="utf-8", newline="\n")
    print("written %s (%d papers)" % (out, len(papers)))


if __name__ == "__main__":
    main()
