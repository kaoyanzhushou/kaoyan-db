# -*- coding: utf-8 -*-
"""
每日科研前沿 · 论文自动抓取脚本（GitHub Actions 定时运行，纯 Python 标准库）

数据源：
- 英文：Crossref REST API、PubMed E-utilities、Europe PMC REST（开放元数据，无需 Key）；
- 中文：OpenAlex Works API（language=zh，标题保留中文原文，无需 Key）。
产出：仓库根目录 daily-frontier.json，供前端「每日科研前沿」拉取。

边界（与人工维护的 rnc-updates.json 区分）：
- 本脚本抓期刊论文元数据；中文核心、会议、课题、特刊征稿仍由人工核实后录入 rnc-updates.json；
- 中文开放源覆盖有限，仅收录 OpenAlex 可免登获取的中文期刊论文，缺 DOI/期刊/作者时按兜底字段入库。

质量规则：
- 英文近 RECENT_DAYS 天、中文近 ZH_RECENT_DAYS 天内发表；剔除空标题、过短标题、未来年份、过早年份；
- 标题/期刊关键词命中打分，低于 MIN_SCORE 剔除；DOI 去重；
- 按匹配度 → 被引数 → 发表日期排序，取前 MAX_ITEMS，并为中文条目预留 ZH_MIN_ITEMS 名额。

可用性保护（本轮加固）：
- 接口失败自动重试 2 次（4xx 不重试）；
- 本次抓取为空时不覆盖上一份有效 JSON；无历史文件时以非 0 退出让 CI 标红；
- 避免一次限流/抖动把线上数据清空成空列表。

本地自测：
  python scripts/fetch_daily.py --dry-run
  python scripts/fetch_daily.py --out daily-frontier.json
"""

import argparse
import datetime as dt
import html
import json
import math
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------- 可调配置 ----------
RECENT_DAYS = 120
MAX_ITEMS = 20
ROWS_PER_GROUP = 25
MIN_SCORE = 4
MAILTO = "research@example.com"
USER_AGENT = "kaoyan-db-daily-fetch/1.0 (mailto:%s)" % MAILTO
RETRY_MAX = 2
RETRY_BACKOFF = (2, 5)  # 秒，按次退避

# ---------- 排序与时效配置（阶段1：时间公平 + 复合分） ----------
RECENT_BUCKET_DAYS = 7      # 近 N 天在线新文进入置顶桶
TIME_HALFLIFE = 30.0        # 时间衰减半衰期（天）
W_REL = 0.50                # 相关性权重
W_TIME = 0.25               # 时间新鲜度权重
W_CITE = 0.15               # 被引权重（对数压缩）
W_JOURNAL = 0.10            # 对口期刊权重
SCRIPT_VERSION = "stage4-zh-1.0"
HEALTH_FILE = "fetch-health.json"

# ---------- 阶段2：多源拉取配置 ----------
PUBMED_RETMAX = 25        # PubMed 每组返回条数
EPMC_PAGESIZE = 25        # Europe PMC 每组返回条数
ABSTRACT_MAX = 300       # 摘要片段最大字符数
MAX_AUTHORS = 5           # 入库作者数上限（前端仅渲染前两位）

# ---------- 阶段4：OpenAlex 中文源配置 ----------
ZH_SOURCE = "openalex-zh"
ZH_RECENT_DAYS = 365      # 中文新文稀疏，窗口放宽到近一年
ZH_PER_GROUP = 25          # 每组返回条数
ZH_MIN_ITEMS = 4          # 混合结果中为中文预留的最少名额
ZH_MIN_YEAR = 2020        # 中文条目最早年份
OPENALEX_BASE = "https://api.openalex.org/works"

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

# 阶段2：PubMed / Europe PMC 查询式
PUBMED_CARDIO = ('(heart failure OR myocardial OR cardiac OR mitophagy OR mitochondria OR vagal '
                 'OR autonomic) AND (exercise OR aerobic OR "exercise training")')
PUBMED_MED = ('(sports medicine OR rehabilitation OR "return to sport" OR physical therapy) '
              'AND (exercise OR injury)')
EPMC_CARDIO = ('(TITLE_ABS:"heart failure" OR TITLE_ABS:myocardial OR TITLE_ABS:cardiac '
               'OR TITLE_ABS:mitophagy) AND (TITLE_ABS:exercise OR TITLE_ABS:aerobic)')
EPMC_MED = ('(TITLE_ABS:"sports medicine" OR TITLE_ABS:rehabilitation OR TITLE_ABS:"return to sport") '
            'AND (TITLE_ABS:exercise OR TITLE_ABS:injury)')

# 心脏强词：命中即视为靶部位，保留
STRONG_CARDIO = ["myocardial", "cardiac", "heart failure", "heart", "cardiomyocyte",
                 "ventricular", "myocardium", "cardioprotect"]
# 非靶部位：未命中心脏强词时，命中这些部位视为偏离，剔除
OFFTOPIC_SITE = ["hepatic", "liver", "ovarian", "ovary", "testicular", "testis",
                 "cerebral", "brain", "hippocamp", "pancreatic", "knee arthroplasty",
                 "popliteal", "bone density", "skin", "gastric", "intestinal", "lung injury",
                 "skeletal muscle", "tendon", "ligament",
                 # 加强离靶：肿瘤表皮生长因子、吸入给药纳米囊等非心血管方向
                 "epidermal growth factor", "nanovesicle", "inhalable", "dermatolog"]

REVIEW_RE = re.compile(r"\b(review|meta-analysis|systematic review|scoping review)\b", re.I)

# ---------- 阶段4：中文（OpenAlex）关键词词典 ----------
# 靶部位 / 机制强词：命中加权
ZH_CARDIO_KW = {
    "心脏": 5, "心肌": 5, "心力衰竭": 5, "心衰": 5, "梗死": 5, "心室": 4,
    "重构": 4, "线粒体": 5, "自噬": 4, "心血管": 4, "血管内皮": 4, "迷走": 4,
    "自主神经": 3, "高血压": 3, "血压": 2, "血管": 2, "运动耐力": 3, "心脏康复": 4,
    "心功能": 3, "冠脉": 3, "冠状动脉": 3, "射血分数": 3, "房颤": 2, "动脉粥样硬化": 3,
    "内皮祖细胞": 3, "eNOS": 2, "nlrp3": 2,
}
# 运动 / 干预词：与心血管词双命中视为对口
ZH_EXERCISE_KW = ["运动", "有氧", "训练", "康复", "锻炼", "体力活动", "运动处方"]
# 心脏强词：命中即视为靶部位，保留
ZH_STRONG = ["心脏", "心肌", "心力衰竭", "心衰", "心室", "梗死", "心梗"]
# 中文离靶词：未命中强词时，命中这些视为偏离，剔除
ZH_OFFTOPIC = [
    "相对论", "量子", "物理框架", "统一场", "抑郁", "焦虑", "心理", "疼痛", "肌骨",
    "韧带", "肌腱", "膝", "肩", "脑", "海马", "肝", "卵巢", "睾丸", "皮肤",
    "胃", "肠", "肺损伤", "骨密度", "肿瘤", "癌", "妊娠", "胎儿", "胎", "八段锦",
    "瑜伽", "太极", "舞蹈", "游泳", "排球", "篮球", "足球", "田径",
]
# 中文综述/进展判定标题词
ZH_REVIEW_RE = re.compile(r"(综述|研究进展|进展|Meta分析|Meta 分析|系统评价|系统综述)")


def http_get_json(url, timeout=20):
    """带退避重试的 GET JSON；4xx 不重试，5xx/网络/解析错误重试 RETRY_MAX 次。"""
    last_err = None
    for attempt in range(RETRY_MAX + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last_err = e
            if 400 <= e.code < 500:
                raise  # 4xx 属参数/权限问题，重试无意义
        except (urllib.error.URLError, ValueError) as e:
            last_err = e
        if attempt < RETRY_MAX:
            time.sleep(RETRY_BACKOFF[attempt])
    raise last_err


def crossref_group(query, from_date):
    params = urllib.parse.urlencode({
        "query.bibliographic": query,
        "filter": "from-pub-date:%s,type:journal-article" % from_date,
        "rows": ROWS_PER_GROUP,
        "sort": "issued",
        "order": "desc",
        "select": "title,container-title,issued,DOI,is-referenced-by-count,type,author",
        "mailto": MAILTO,
    })
    url = "https://api.crossref.org/works?" + params
    data = http_get_json(url)
    return data.get("message", {}).get("items", []) or []


def pubmed_group(term, retmax=PUBMED_RETMAX):
    """PubMed E-utilities：esearch 取 id，再 esummary 取元数据。"""
    q = urllib.parse.urlencode({"db": "pubmed", "term": term, "retmax": retmax,
                               "sort": "date", "retmode": "json"})
    es = http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + q)
    ids = es.get("esearchresult", {}).get("idlist", []) or []
    if not ids:
        return []
    q2 = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
    sm = http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + q2)
    res = sm.get("result", {})
    return [res[i] for i in ids if i in res]


def epmc_group(query, pagesize=EPMC_PAGESIZE):
    """Europe PMC REST：core 结果，含摘要 / OA / 被引 / 作者。"""
    q = urllib.parse.urlencode({"query": query, "format": "json",
                               "pageSize": pagesize, "resultType": "core"})
    ep = http_get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + q)
    return ep.get("resultList", {}).get("result", []) or []


# 中文标题 AND 检索组：运动 × 心血管子类，多组拉取后跨组去重
ZH_QUERY_GROUPS = [
    "title.search:运动,title.search:心脏",
    "title.search:运动,title.search:心肌",
    "title.search:运动,title.search:心力衰竭",
    "title.search:有氧,title.search:血管",
    "title.search:运动,title.search:高血压",
    "title.search:运动,title.search:线粒体",
]


def openalex_zh_group(expr, from_date, to_date, per_page=ZH_PER_GROUP):
    """OpenAlex Works：language=zh 中文文献，标题保留原文；免 Key 开放元数据。"""
    filt = "language:zh,from_publication_date:%s,to_publication_date:%s,%s" % (from_date, to_date, expr)
    q = urllib.parse.urlencode({"filter": filt, "per-page": per_page, "mailto": MAILTO})
    ox = http_get_json(OPENALEX_BASE + "?" + q)
    return ox.get("results", []) or []


def norm_title(t):
    """规范化标题：小写、去标点与多余空白，用于跨源去重。"""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", (t or "").lower())).strip()


def parse_pubmed(item):
    title = html.unescape(item.get("title") or "").strip()
    journal = html.unescape(item.get("fulljournalname") or item.get("source") or "").strip()
    doi = ""
    pmid = str(item.get("uid") or "").strip()
    for aid in item.get("articleids", []) or []:
        if aid.get("idtype") == "doi":
            doi = str(aid.get("value") or "").strip()
    authors = [a.get("name", "") for a in item.get("authors", []) or [] if a.get("name")]
    pubdate = item.get("pubdate") or ""
    year = None
    published = ""
    m = re.match(r"(\d{4})", pubdate)
    if m:
        year = int(m.group(1))
    for fmt in ("%Y %b %d", "%Y %b"):
        try:
            published = dt.datetime.strptime(pubdate, fmt).date().isoformat()
            break
        except ValueError:
            continue
    pubtype = " ".join(item.get("pubtype", []) or [])
    return {
        "title": title, "journal": journal, "doi": doi, "pmid": pmid, "pmcid": "",
        "year": year, "published": published, "citations": 0,
        "authors": authors[:MAX_AUTHORS], "abstractSnippet": "", "oa": False,
        "source": "pubmed", "isReview": "Review" in pubtype,
    }


def parse_epmc(h):
    title = html.unescape(h.get("title") or "").strip()
    ji = h.get("journalInfo") or {}
    journal = html.unescape((ji.get("journal") or {}).get("title") or h.get("journalTitle") or "").strip()
    doi = str(h.get("doi") or "").strip()
    pmid = str(h.get("pmid") or "").strip()
    pmcid = str(h.get("pmcid") or "").strip()
    year = h.get("pubYear")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    published = h.get("firstPublicationDate") or h.get("electronicPublicationDate") or ""
    citations = h.get("citedByCount", 0) or 0
    authors = [a.strip() for a in (h.get("authorString") or "").split(",") if a.strip()]
    abstract = (h.get("abstractText") or "").strip()
    oa = str(h.get("isOpenAccess", "N")).upper() == "Y"
    pt = (h.get("pubTypeList") or {}).get("pubType", []) or []
    is_review = any("review" in str(x).lower() for x in pt)
    return {
        "title": title, "journal": journal, "doi": doi, "pmid": pmid, "pmcid": pmcid,
        "year": year, "published": published, "citations": citations,
        "authors": authors[:MAX_AUTHORS], "abstractSnippet": abstract[:ABSTRACT_MAX],
        "oa": oa, "source": "epmc", "isReview": is_review,
    }


def _oa_abstract(inv):
    """OpenAlex 摘要为倒排索引 {词:[位置]}，还原为纯文本。"""
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def parse_openalex_zh(w):
    """OpenAlex 中文文献 → 统一 schema；期刊/作者/摘要缺失时回落兜底。"""
    # 仅保留期刊论文；剔除撤稿、数据集、学位论文等非论文类型
    if w.get("type") != "article" or w.get("is_retracted"):
        return None
    title = html.unescape(w.get("title") or "").strip()
    pl = w.get("primary_location") or {}
    src = pl.get("source") or {}
    journal = (src.get("title") or "").strip()
    if not journal:
        for loc in w.get("locations", []) or []:
            ls = loc.get("source") or {}
            if ls.get("title"):
                journal = str(ls["title"]).strip()
                break
    doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
    authors = [a.get("author", {}).get("display_name", "")
               for a in (w.get("authorships") or [])]
    authors = [a for a in authors if a][:MAX_AUTHORS]
    year = w.get("publication_year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    published = w.get("publication_date") or ""
    citations = w.get("cited_by_count", 0) or 0
    abstract = _oa_abstract(w.get("abstract_inverted_index"))[:ABSTRACT_MAX]
    oa = bool((w.get("open_access") or {}).get("is_oa"))
    is_review = bool(ZH_REVIEW_RE.search(title))
    return {
        "title": title, "journal": journal or "未知期刊", "doi": doi,
        "pmid": "", "pmcid": "", "year": year, "published": published,
        "citations": citations, "authors": authors, "abstractSnippet": abstract,
        "oa": oa, "source": ZH_SOURCE, "isReview": is_review, "isChinese": True,
    }


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


def dedup_merge(cands):
    """三级去重合并：DOI → PMID → 规范化标题；同工作字段取并集。"""
    merged = {}
    order = []
    for c in cands:
        if c["doi"]:
            key = "doi:" + c["doi"].lower()
        elif c["pmid"]:
            key = "pmid:" + c["pmid"]
        else:
            key = "ti:" + norm_title(c["title"])
        if key not in merged:
            merged[key] = dict(c)
            order.append(key)
            continue
        m = merged[key]
        if len(c["title"]) > len(m["title"]):
            m["title"] = c["title"]
        if not m["journal"] and c["journal"]:
            m["journal"] = c["journal"]
        for f in ("doi", "pmid", "pmcid", "published"):
            if not m[f] and c[f]:
                m[f] = c[f]
        m["citations"] = max(m["citations"], c["citations"])
        seen_a = set(m["authors"])
        for a in c["authors"]:
            if a not in seen_a:
                m["authors"].append(a)
                seen_a.add(a)
        if len(c["abstractSnippet"]) > len(m["abstractSnippet"]):
            m["abstractSnippet"] = c["abstractSnippet"]
        m["oa"] = m["oa"] or c["oa"]
        m["isReview"] = m["isReview"] or c["isReview"]
        srcs = {s for s in m["source"].split("+") if s}
        srcs.update(s for s in c["source"].split("+") if s)
        m["source"] = "+".join(sorted(srcs))
        m["matchScore"] = max(m["matchScore"], c["matchScore"])
        m["topics"] = sorted(set(m["topics"]) | set(c["topics"]))
        m["journalHit"] = m["journalHit"] or c["journalHit"]
    return [merged[k] for k in order]


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
    journal_hit = False
    for jname in JOURNAL_BOOST:
        if jname in j:
            score += 3
            journal_hit = True
            cardio_hit = cardio_hit or ("cardio" in jname or "heart" in jname or "circulation" in jname)
            med_hit = med_hit or ("sports" in jname or "medicine" in jname)
            break
    if cardio_hit:
        topics.append("cardio")
    if med_hit:
        topics.append("med")
    # 部位偏离：未命中心脏强词、且属于非靶部位即剔除（不被 ischemia/线粒体等泛词豁免）
    offtopic = (not strong) and any(s in t for s in OFFTOPIC_SITE)
    return min(score, 100), topics, strong, offtopic, journal_hit


def score_zh_item(title, journal):
    """中文条目打分：心脏/线粒体等强词加权，运动词双命中视为对口；返回与 score_item 同构。"""
    t = title or ""
    score = 0
    topics = []
    strong = any(k in t for k in ZH_STRONG)
    cardio_hit = strong
    for kw, w in ZH_CARDIO_KW.items():
        if kw in t:
            score += w
            cardio_hit = True
    exercise_hit = any(k in t for k in ZH_EXERCISE_KW)
    if cardio_hit:
        topics.append("cardio")
    if exercise_hit:
        topics.append("med")
    offtopic = (not strong) and any(s in t for s in ZH_OFFTOPIC)
    return min(score, 100), topics, strong, offtopic, False, cardio_hit, exercise_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="输出 JSON 路径，默认仓库根 daily-frontier.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = ap.parse_args()

    today = dt.date.today()
    from_date = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    zh_from_date = (today - dt.timedelta(days=ZH_RECENT_DAYS)).isoformat()

    raw_by_source = {"crossref": [], "pubmed": [], "epmc": [], ZH_SOURCE: []}
    groups = []
    t0 = time.time()

    def run_group(source, query, fetcher):
        g = {"source": source, "query": query[:48], "ok": False, "count": 0,
             "error": "", "elapsedSec": 0}
        t = time.time()
        try:
            items = fetcher()
            raw_by_source[source].extend(items)
            g["ok"] = True
            g["count"] = len(items)
        except Exception as e:  # 单组失败不致命，其他源/组仍可用
            g["error"] = repr(e)[:200]
            print("WARN: %s fetch failed: %r" % (source, e), file=sys.stderr)
        g["elapsedSec"] = round(time.time() - t, 2)
        groups.append(g)
        time.sleep(1)  # 礼貌间隔，避免触发源限流

    run_group("crossref", CARDIO_QUERY, lambda: crossref_group(CARDIO_QUERY, from_date))
    run_group("crossref", MED_QUERY, lambda: crossref_group(MED_QUERY, from_date))
    run_group("pubmed", PUBMED_CARDIO, lambda: pubmed_group(PUBMED_CARDIO))
    run_group("pubmed", PUBMED_MED, lambda: pubmed_group(PUBMED_MED))
    run_group("epmc", EPMC_CARDIO, lambda: epmc_group(EPMC_CARDIO))
    run_group("epmc", EPMC_MED, lambda: epmc_group(EPMC_MED))
    for expr in ZH_QUERY_GROUPS:
        run_group(ZH_SOURCE, expr, lambda e=expr: openalex_zh_group(e, zh_from_date, today.isoformat()))

    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    def parse_crossref(it):
        title = html.unescape((it.get("title") or [""])[0]).strip()
        journal = html.unescape((it.get("container-title") or [""])[0]).strip()
        doi = (it.get("DOI") or "").strip()
        year, published = parse_pub(it)
        citations = it.get("is-referenced-by-count", 0) or 0
        authors = []
        for a in (it.get("author") or []):
            name = ((a.get("given") or "") + " " + (a.get("family") or "")).strip() or (a.get("family") or "")
            if name:
                authors.append(name)
        return {
            "title": title, "journal": journal or "未知期刊", "doi": doi,
            "pmid": "", "pmcid": "", "year": year, "published": published,
            "citations": citations, "authors": authors[:MAX_AUTHORS], "abstractSnippet": "", "oa": False,
            "source": "crossref", "isReview": bool(REVIEW_RE.search(title)),
            "matchScore": 0, "topics": [], "journalHit": False, "ageDays": None,
        }

    def accept(c):
        """统一相关性 / 离靶 / 低分过滤，并补打分、话题、期刊命中、年龄天数。"""
        score, topics, strong, offtopic, journal_hit = score_item(c["title"], c["journal"])
        if offtopic:
            return None
        med_hit = "med" in topics
        if score < 6 and not strong and not med_hit:
            return None
        if score < MIN_SCORE:
            return None
        c["matchScore"] = score
        c["topics"] = topics
        c["journalHit"] = journal_hit
        pub_date = None
        if c["published"]:
            try:
                pub_date = dt.date.fromisoformat(c["published"])
            except ValueError:
                pub_date = None
        c["ageDays"] = (today - pub_date).days if pub_date else None
        return c

    def accept_zh(c):
        """中文条目过滤：离靶剔除；强词保留，或心血管×运动双命中，或达打分门槛。"""
        score, topics, strong, offtopic, journal_hit, cardio_hit, exercise_hit = score_zh_item(
            c["title"], c["journal"])
        if offtopic:
            return None
        if not (strong or (cardio_hit and exercise_hit) or score >= MIN_SCORE):
            return None
        c["matchScore"] = score
        c["topics"] = topics
        c["journalHit"] = journal_hit
        pub_date = None
        if c["published"]:
            try:
                pub_date = dt.date.fromisoformat(c["published"])
            except ValueError:
                pub_date = None
        c["ageDays"] = (today - pub_date).days if pub_date else None
        return c

    candidates = []
    parsers = (("crossref", parse_crossref), ("pubmed", parse_pubmed),
               ("epmc", parse_epmc), (ZH_SOURCE, parse_openalex_zh))
    for src, parser in parsers:
        for it in raw_by_source[src]:
            c = parser(it)
            if not c or not c["title"]:
                continue
            min_len = 8 if src == ZH_SOURCE else 15
            if len(c["title"]) < min_len:
                continue
            min_year = ZH_MIN_YEAR if src == ZH_SOURCE else 2015
            if c["year"] is None or c["year"] > today.year + 1 or c["year"] < min_year:
                continue
            c = accept_zh(c) if src == ZH_SOURCE else accept(c)
            if c:
                candidates.append(c)

    papers = dedup_merge(candidates)

    # 补稳定 id、可点击链接与版本标记（DOI 优先，回落 PMID，再回落标题）
    for idx, p in enumerate(papers):
        if p["doi"]:
            p["id"] = "doi-" + p["doi"].lower()
            p["url"] = "https://doi.org/" + urllib.parse.quote(p["doi"])
        elif p["pmid"]:
            p["id"] = "pmid-" + p["pmid"]
            p["url"] = "https://pubmed.ncbi.nlm.nih.gov/" + p["pmid"] + "/"
        else:
            p["id"] = "title-" + str(idx)
            p["url"] = ""
        p["version"] = "published"
        p["isChinese"] = bool(p.get("isChinese"))

    # 批次内被引归一化（对数压缩），再计算复合分与新文置顶标记
    max_cites = max((p["citations"] for p in papers), default=0)
    cites_denom = math.log10(1 + max_cites) if max_cites > 0 else 0.0
    for p in papers:
        rel_norm = min(p["matchScore"], 100) / 100.0
        age = p["ageDays"] if p["ageDays"] is not None else RECENT_DAYS
        if age < 0:
            age = 0  # 在线先发（卷期年为次年）按最新处理
        decay = math.exp(-age / TIME_HALFLIFE)
        cites_score = (math.log10(1 + p["citations"]) / cites_denom) if cites_denom > 0 else 0.0
        journal_flag = 1.0 if p["journalHit"] else 0.0
        composite = W_REL * rel_norm + W_TIME * decay + W_CITE * cites_score + W_JOURNAL * journal_flag
        p["compositeScore"] = round(composite, 4)
        p["isRecent"] = (p["ageDays"] is not None and 0 <= p["ageDays"] <= RECENT_BUCKET_DAYS)

    # 排序：近 7 天置顶桶优先，桶内按复合分降序
    papers.sort(key=lambda p: (1 if p["isRecent"] else 0, p["compositeScore"]), reverse=True)

    # 中文名额预留：先保底 ZH_MIN_ITEMS 篇达标中文，剩余名额给英文；中文超出保底再按复合分补入
    zh_all = [p for p in papers if p.get("isChinese")]
    en_all = [p for p in papers if not p.get("isChinese")]
    zh_keep = zh_all[:ZH_MIN_ITEMS]
    remain = MAX_ITEMS - len(zh_keep)
    en_keep = en_all[:max(remain, 0)]
    slot = MAX_ITEMS - len(zh_keep) - len(en_keep)
    if slot > 0:
        zh_keep += zh_all[ZH_MIN_ITEMS:ZH_MIN_ITEMS + slot]
    papers = zh_keep + en_keep
    papers.sort(key=lambda p: (1 if p["isRecent"] else 0, p["compositeScore"]), reverse=True)

    zh_count = len([p for p in papers if p.get("isChinese")])
    payload = {
        "version": today.isoformat(),
        "meta": {
            "generatedAt": now_iso,
            "source": "Crossref + PubMed + Europe PMC + OpenAlex(中文)",
            "count": len(papers),
            "zhCount": zh_count,
            "windowDays": RECENT_DAYS,
            "zhWindowDays": ZH_RECENT_DAYS,
            "scriptVersion": SCRIPT_VERSION,
            "recentBucketDays": RECENT_BUCKET_DAYS,
            "weights": {"rel": W_REL, "time": W_TIME, "cite": W_CITE, "journal": W_JOURNAL},
            "note": "英文论文来自 Crossref/PubMed/Europe PMC；中文论文来自 OpenAlex 开放中文源；会议、课题、特刊征稿见 rnc-updates.json（人工核实）。",
        },
        "items": papers,
    }

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n=== 入选 %d 篇 ===" % len(papers))
        for p in papers:
            print("  [rel=%d|comp=%.3f|recent=%s|cites=%s|%s] %s" % (
                p["matchScore"], p["compositeScore"], p["isRecent"], p["citations"], p["year"], p["title"][:60]))
        return

    out = pathlib.Path(args.out) if args.out else pathlib.Path(__file__).resolve().parent.parent / "daily-frontier.json"

    # 健康记录：无论本次是否入选，都落一份抓取状态（dry-run 除外），便于前端与排错观测。
    health = {
        "version": today.isoformat(),
        "scriptVersion": SCRIPT_VERSION,
        "runAt": now_iso,
        "source": "Crossref + PubMed + Europe PMC + OpenAlex(中文)",
        "windowDays": RECENT_DAYS,
        "zhWindowDays": ZH_RECENT_DAYS,
        "groups": groups,
        "rawCount": sum(len(v) for v in raw_by_source.values()),
        "selectedCount": len(papers),
        "zhCount": zh_count,
        "isEmpty": len(papers) == 0,
        "elapsedSec": round(time.time() - t0, 2),
        "note": "每日抓取健康记录；selectedCount=0 表示本次无达标新文献，daily-frontier.json 保留上一版。",
    }
    health_path = out.parent / HEALTH_FILE
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    # 可用性保护：本次抓取为空时，不覆盖上一份有效 JSON；无历史文件则非 0 退出让 CI 标红。
    if not papers:
        if out.exists():
            print("WARN: 本次抓取为空，保留上一份 %s，不覆盖。" % out, file=sys.stderr)
            return
        print("ERROR: 本次抓取为空且无历史 JSON，终止以避免空数据上线。", file=sys.stderr)
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # 无 BOM、纯 LF
    out.write_text(text, encoding="utf-8", newline="\n")
    print("written %s (%d papers)" % (out, len(papers)))


if __name__ == "__main__":
    main()
