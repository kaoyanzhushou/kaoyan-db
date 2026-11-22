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
import difflib
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
MAX_ITEMS = 40
ROWS_PER_GROUP = 40
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
# 各来源被引计数可信度系数：正式文献索引源可信；元数据/中文库被引口径偏弱，排序时封顶
SOURCE_CITE_CRED = {"pubmed": 1.0, "epmc": 1.0, "crossref": 0.6, "openalex-zh": 0.6}
SCRIPT_VERSION = "stage4-zh-1.0"
HEALTH_FILE = "fetch-health.json"

# ---------- 阶段2：多源拉取配置 ----------
PUBMED_RETMAX = 40        # PubMed 每组返回条数
EPMC_PAGESIZE = 40        # Europe PMC 每组返回条数
ABSTRACT_MAX = 300       # 摘要片段最大字符数
MAX_AUTHORS = 5           # 入库作者数上限（前端仅渲染前两位）

# ---------- 阶段4：OpenAlex 中文源配置 ----------
ZH_SOURCE = "openalex-zh"
ZH_RECENT_DAYS = 365      # 中文新文稀疏，窗口放宽到近一年
ZH_PER_GROUP = 30          # 每组返回条数
ZH_MIN_ITEMS = 8          # 混合结果中为中文预留的最少名额
ZH_MIN_YEAR = 2020        # 中文条目最早年份
OPENALEX_BASE = "https://api.openalex.org/works"

# 主方向：运动心血管生物学（权重高）
CARDIO_KW = {
    "heart failure": 5, "myocardial": 5, "myocardial infarction": 5, "mitochondri": 5,
    "mitophagy": 5, "autophagy": 4, "cardioprotect": 5, "cardiac": 4, "cardiovascular": 4,
    "vagal": 4, "vagal tone": 3, "heart rate variability": 3, "baroreflex": 3,
    "autonomic": 3, "autonomic nervous": 3, "enos": 3, "nlrp3": 3, "vascular": 3,
    "ischemia": 3, "reperfusion": 3, "hypertension": 2, "blood pressure": 2,
    "exercise training": 2, "aerobic exercise": 2, "exercise": 1, "endothelial": 2,
    "endothelial progenitor": 3, "ferroptosis": 3, "macrophage": 3, "remodeling": 3,
    "diastolic": 2, "systolic": 2, "vo2": 2, "cardiopulmonary": 2, "hfpef": 4,
    "cardiac rehabilitation": 3, "nitric oxide": 2, "nrf2": 2, "ampk": 2, "pgc-1": 2,
    # 心血管专科同义词（复核被拒误杀条目后补入）
    "cardiomyopathy": 5, "coronary artery": 4, "coronary bypass": 4, "bypass graft": 4,
    "right ventricular": 3, "right ventricle": 3, "tetralogy": 3, "septal ablation": 4,
    "hemodynamic": 2, "circulatory power": 3, "stress echocardiography": 2,
    "exercise ecg": 2, "cardiorespiratory fitness": 3, "cardiorespiratory": 2,
}
# 辅方向：运动医学 / 康复
MED_KW = {
    "sports medicine": 3, "return to sport": 3, "rehabilitation": 2, "physical therapy": 2,
    "rehab": 2, "injury": 2, "orthopaedic": 1, "ligament": 1, "tendon": 1,
    "knee": 1, "shoulder": 1, "muscle": 1, "exercise capacity": 2,
    "six-minute walk": 2, "physical activity": 2, "cardiac rehabilitation": 3,
}
# 英文别名 → 标准词（词边界替换，避免子串误伤），打分前归一
ALIAS_EN = [
    (re.compile(r"\bchf\b", re.I), "heart failure"),
    (re.compile(r"\bhf\b", re.I), "heart failure"),
    (re.compile(r"\bmi\b", re.I), "myocardial infarction"),
]
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

# 子方向补充查询组：线粒体自噬/铁死亡、血管内皮/高血压、自主神经/迷走、缺血再灌注/巨噬细胞
CARDIO_MITO_QUERY = ("mitophagy autophagy ferroptosis mitochondria myocardial apoptosis oxidative stress exercise")
CARDIO_VASC_QUERY = ("endothelial function eNOS atherosclerosis vascular hypertension blood pressure exercise training")
CARDIO_NEURO_QUERY = ("vagal heart rate variability autonomic nervous baroreflex exercise")
CARDIO_ISCH_QUERY = ("ischemia reperfusion macrophage NLRP3 inflammation myocardial exercise")

EPMC_MITO = ('(TITLE_ABS:mitophagy OR TITLE_ABS:autophagy OR TITLE_ABS:ferroptosis OR TITLE_ABS:mitochondria) '
             'AND (TITLE_ABS:myocardial OR TITLE_ABS:exercise)')
EPMC_VASC = ('(TITLE_ABS:endothelial OR TITLE_ABS:eNOS OR TITLE_ABS:atherosclerosis OR TITLE_ABS:hypertension) '
             'AND (TITLE_ABS:vascular OR TITLE_ABS:exercise)')
EPMC_NEURO = ('(TITLE_ABS:vagal OR TITLE_ABS:"heart rate variability" OR TITLE_ABS:autonomic) AND TITLE_ABS:exercise')
EPMC_ISCH = ('(TITLE_ABS:ischemia OR TITLE_ABS:reperfusion OR TITLE_ABS:macrophage OR TITLE_ABS:NLRP3) '
             'AND TITLE_ABS:myocardial')

# 心脏强词：命中即视为靶部位，保留
STRONG_CARDIO = ["myocardial", "cardiac", "heart failure", "heart", "cardiomyocyte",
                 "ventricular", "myocardium", "cardioprotect", "cardiomyopathy", "coronary"]
# 硬离靶：其他脏器 / 材料 / 给药 / 皮肤 / 骨密度 / 置换手术，命中即剔除（不被心脏强词豁免）
OFFTOPIC_HARD = ["hepatic", "liver", "ovarian", "ovary", "testicular", "testis",
                 "cerebral", "brain", "hippocamp", "pancreatic", "knee arthroplasty",
                 "popliteal", "bone density", "skin", "gastric", "intestinal", "lung injury",
                 "epidermal growth factor", "nanovesicle", "inhalable", "dermatolog",
                 "renal", "kidney", "nephro", "posttraumatic stress", "ptsd",
                 "psychotherap", "parkinson", "dance"]
# 软离靶：运动医学部位（损伤/康复方向对口），仅在未命中心脏强词时剔除
OFFTOPIC_SOFT = ["skeletal muscle", "tendon", "ligament"]

REVIEW_RE = re.compile(r"\b(review|meta-analysis|systematic review|scoping review)\b", re.I)

# ---------- 阶段4：中文（OpenAlex）关键词词典 ----------
# 靶部位 / 机制强词：命中加权
ZH_CARDIO_KW = {
    "心脏": 5, "心肌": 5, "心肌梗死": 5, "心力衰竭": 5, "心衰": 5, "梗死": 5, "心室": 4,
    "心室重构": 4, "线粒体": 5, "自噬": 4, "心血管": 4, "血管内皮": 4,
    "血管内皮功能": 4, "迷走": 4, "迷走神经张力": 4,
    "自主神经": 3, "自主神经功能": 3, "高血压": 3, "血压": 2, "血管": 2, "运动耐力": 3,
    "心脏康复": 4, "心功能": 3, "心源性": 3, "冠脉": 3, "冠状动脉": 3, "射血分数": 3,
    "舒张功能": 3, "收缩功能": 2, "房颤": 2, "动脉粥样硬化": 3,
    "内皮祖细胞": 3, "eNOS": 2, "nlrp3": 2, "纤维化": 3, "铁死亡": 3, "巨噬细胞": 3,
    "心肺运动试验": 3, "六分钟步行": 2, "峰值摄氧量": 2, "抗炎": 2, "有氧运动": 3, "运动干预": 2,
    # 复核中文召回后补入的同义词
    "心肺适能": 3, "心肺耐力": 3, "心率变异性": 3, "脉搏波": 3, "动脉弹性": 3,
    "心肌重构": 4, "运动康复": 4, "炎症因子": 2, "心律失常": 2, "冠心病": 3,
    "介入术后": 2, "血管重构": 3, "慢性心衰": 5, "峰值氧耗": 2,
}
# 运动 / 干预词：与心血管词双命中视为对口
ZH_EXERCISE_KW = ["运动", "有氧", "有氧运动", "训练", "康复", "锻炼", "体力活动", "运动处方",
                  "运动干预", "康复训练", "运动康复", "抗阻训练", "力量训练",
                  "步行", "有氧训练"]
# 心脏强词：命中即视为靶部位，保留
ZH_STRONG = ["心脏", "心肌", "心肌梗死", "心力衰竭", "心衰", "心室", "梗死", "心梗"]
# 中文硬离靶：非心血管/非运动方向，命中即剔除（不被心脏强词豁免）
ZH_OFFTOPIC_HARD = [
    "相对论", "量子", "物理框架", "统一场", "抑郁", "焦虑", "心理", "疼痛", "肌骨",
    "脑", "海马", "肝", "卵巢", "睾丸", "皮肤",
    "胃", "肠", "肺损伤", "骨密度", "肿瘤", "癌", "妊娠", "胎儿", "胎", "八段锦",
    "瑜伽", "太极", "舞蹈", "游泳", "排球", "篮球", "足球", "田径",
    # 工程/工业/游戏/他科明确离靶（标题命中即剔，不被心脏强词豁免）
    "液压", "离心机", "机械装置", "发动机", "电网", "集控站", "方舱", "安防",
    "废气", "二氧化硫", "电竞", "电子竞技", "跳绳", "卧鱼", "警校", "装置改良",
    "数字化转型", "房地产", "地铁", "注意缺陷", "ADHD", "执行功能",
]
# 中文软离靶：运动医学部位 / 泛教育泛转型，仅在未命中心脏强词时剔除
ZH_OFFTOPIC_SOFT = ["韧带", "肌腱", "膝", "肩",
                    "课程", "教学", "教材", "文化传播", "转型", "模式重构", "麻醉"]
# 明确运动/康复干预词（标题级）：命中视为有干预主体，豁免纯护理/技术/中医剔除
ZH_INTERVENE = ["运动", "训练", "锻炼", "运动处方", "运动干预", "抗阻", "力量训练",
                "步行", "有氧训练", "康复训练", "运动康复", "心脏康复", "康复", "有氧"]
# 纯护理管理 / 健康教育 / 防护类：标题命中且无运动康复干预词时，判为非干预偏弱条目
ZH_NONINTERVENE_CARE = ["护理", "健康教育", "健康宣教", "宣教", "职业暴露", "防护策略",
                        "分级管控", "家庭医生", "服药", "用药指导", "血压监测",
                        "管理要点", "管理策略"]
# AI / 技术诊断 / 建模类
ZH_NONINTERVENE_TECH = ["机器学习", "人工智能", "3D扫描", "影像组学", "分类模型", "诊断模型",
                       "体检", "心电图", "心脏超声", "磁共振", "方案生成系统",
                       "追踪分析工具", "分析工具", "数据整合", "二元应对"]
# 中医 / 中药非运动类
ZH_NONINTERVENE_TCM = ["中药", "穴位", "敷贴", "刮痧", "药线", "针灸", "推拿", "拔罐",
                       "体质辨识", "入心经", "中成药", "方剂"]
ZH_NONINTERVENE = ZH_NONINTERVENE_CARE + ZH_NONINTERVENE_TECH + ZH_NONINTERVENE_TCM
# 中文综述/进展判定标题词
ZH_REVIEW_RE = re.compile(r"(综述|研究进展|进展|Meta分析|Meta 分析|系统评价|系统综述)")


def http_get_json(url, timeout=20):
    """带退避重试的 GET JSON；4xx 不重试，5xx/网络/解析错误重试 RETRY_MAX 次。"""
    last_err = None
    for attempt in range(RETRY_MAX + 1):
        bo = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code != 429 and 400 <= e.code < 500:
                raise  # 4xx 属参数/权限问题，重试无意义；429 限流可恢复，走退避重试
            if e.code == 429:
                ra = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait = min(max(float(ra), 5), 30) if ra else bo
                except (TypeError, ValueError):
                    wait = bo
            else:
                wait = bo
        except (urllib.error.URLError, ValueError) as e:
            last_err = e
            wait = bo
        if attempt < RETRY_MAX:
            time.sleep(wait)
    raise last_err


def crossref_group(query, from_date, sort="issued"):
    params = urllib.parse.urlencode({
        "query.bibliographic": query,
        "filter": "from-pub-date:%s,type:journal-article" % from_date,
        "rows": ROWS_PER_GROUP,
        "sort": sort,
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


def epmc_abstract_by_doi(doi):
    """按 DOI 向 Europe PMC 补摘要；失败或无摘要返回空串。纯元数据接口，不调模型。"""
    doi = (doi or "").strip()
    if not doi:
        return ""
    try:
        q = urllib.parse.urlencode({"query": 'DOI:"%s"' % doi, "format": "json",
                                   "resultType": "core", "pageSize": 1})
        ep = http_get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + q)
        res = ep.get("resultList", {}).get("result", []) or []
        if res:
            return _clean_abstract(res[0].get("abstractText") or "")
    except Exception:
        return ""
    return ""


# 中文标题 AND 检索组：运动/康复 × 心血管子类，多组拉取后跨组去重
# 中文检索词组对：顶层 search 跨标题/摘要/关键词全字段召回，词间 AND；相关性由后端打分与离靶门槛兜底。
ZH_QUERY_GROUPS = [
    ("运动", "心脏"),
    ("运动", "心肌"),
    ("运动", "心力衰竭"),
    ("有氧", "血管"),
    ("运动", "高血压"),
    ("运动", "线粒体"),
    ("康复", "心脏"),
    ("康复", "心力衰竭"),
    ("运动", "心功能"),
    ("训练", "心力衰竭"),
    ("运动", "血压"),
    ("康复", "射血分数"),
    ("运动", "心肌重构"),
    ("运动", "动脉粥样硬化"),
    ("运动", "内皮"),
    ("训练", "高血压"),
]


def openalex_zh_group(words, from_date, to_date, per_page=ZH_PER_GROUP):
    """OpenAlex Works：language=zh 中文文献，标题保留原文；免 Key 开放元数据。

    检索走顶层 search（标题/摘要/关键词全字段，词间 AND），filter 仅约束语言与日期窗口。
    """
    filt = "language:zh,from_publication_date:%s,to_publication_date:%s" % (from_date, to_date)
    q = urllib.parse.urlencode({"filter": filt, "search": " ".join(words),
                               "per-page": per_page, "mailto": MAILTO})
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
        "year": year, "published": published, "citations": None,
        "authors": authors[:MAX_AUTHORS], "abstractSnippet": "", "oa": False,
        "source": "pubmed", "isReview": "Review" in pubtype, "citesUnavailable": True,
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
    abstract = _clean_abstract(h.get("abstractText") or "")
    oa = str(h.get("isOpenAccess", "N")).upper() == "Y"
    pt = (h.get("pubTypeList") or {}).get("pubType", []) or []
    is_review = any("review" in str(x).lower() for x in pt)
    return {
        "title": title, "journal": journal, "doi": doi, "pmid": pmid, "pmcid": pmcid,
        "year": year, "published": published, "citations": citations,
        "authors": authors[:MAX_AUTHORS], "abstractSnippet": abstract,
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


def _clean_abstract(text):
    """去 HTML 标签、解码实体、归一空白并截断到 ABSTRACT_MAX；空摘要返回空串。"""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", html.unescape(text))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:ABSTRACT_MAX]


def parse_openalex_zh(w):
    """OpenAlex 中文文献 → 统一 schema；期刊/作者/摘要缺失时回落兜底。"""
    # 仅保留期刊论文；剔除撤稿、数据集、学位论文等非论文类型
    if w.get("type") != "article" or w.get("is_retracted"):
        return None
    title = html.unescape(w.get("title") or "").strip()
    # 语言兜底：OpenAlex 会把中文期刊的英文综述/双语条目标为 zh，标题无汉字即归英文通道，不占中文名额
    if not re.search(r"[一-鿿]", title):
        return None
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
    abstract = _clean_abstract(_oa_abstract(w.get("abstract_inverted_index")))
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


TITLE_SIM_STRONG = 0.90
TITLE_SIM_SOFT = 0.82


def _first_surname(authors):
    """取第一作者姓氏（兼容 'Last, First' 与 'First Last'），用于跨源同题辅助判断。"""
    if not authors:
        return ""
    a = (authors[0] or "").strip()
    if not a:
        return ""
    if "," in a:
        return a.split(",")[0].strip().lower()
    parts = a.split()
    return parts[-1].lower() if parts else ""


def _merge_into(m, c):
    """把同工作条目 c 的字段并入主条目 m（被引取可用值最大、摘要取长、话题并集等）。"""
    if len(c["title"]) > len(m["title"]):
        m["title"] = c["title"]
    if not m["journal"] and c["journal"]:
        m["journal"] = c["journal"]
    for f in ("doi", "pmid", "pmcid", "published"):
        if not m[f] and c[f]:
            m[f] = c[f]
    if m["citations"] is None:
        m["citations"] = c["citations"]
    elif c["citations"] is not None:
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
    m["tags"] = sorted(set(m.get("tags", [])) | set(c.get("tags", [])),
                       key=lambda x: TAG_KEYS.index(x) if x in TAG_KEYS else 99)
    m["journalHit"] = m["journalHit"] or c["journalHit"]


def _similar_work(a, b):
    """近似同题判断：标题高度相似，或同年+同首姓+标题较相似（阈值保守，避免误合并）。"""
    ta, tb = norm_title(a["title"]), norm_title(b["title"])
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    sim = difflib.SequenceMatcher(None, ta, tb).ratio()
    if sim >= TITLE_SIM_STRONG:
        return True
    if sim >= TITLE_SIM_SOFT and a.get("year") and a.get("year") == b.get("year"):
        sa, sb = _first_surname(a.get("authors", [])), _first_surname(b.get("authors", []))
        if sa and sa == sb:
            return True
    return False


def dedup_merge(cands):
    """去重合并：DOI → PMID → 规范化标题精确键；再对近似标题按相似度/作者年份辅助合并。"""
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
        _merge_into(merged[key], c)
    kept = [merged[k] for k in order]
    # 第二阶段：跨近似标题合并（同年同首姓兜底）
    result = []
    for c in kept:
        hit = None
        for m in result:
            if _similar_work(c, m):
                hit = m
                break
        if hit is None:
            result.append(dict(c))
        else:
            _merge_into(hit, c)
    return result


def _norm_en_text(*parts):
    """英文文本归一：合并片段、小写、别名替换为标准词（词边界）。"""
    t = " ".join(p for p in parts if p).lower()
    for rx, repl in ALIAS_EN:
        t = rx.sub(repl, t)
    return t


# 细方向标签：稳定 key，前端映射中文展示；一条可命中多个，按固定顺序输出
TAG_KEYS = ["mechanism", "clinical", "exercise-physio", "metabolic-vascular", "sports-med"]

EN_TAG_KW = {
    "mechanism": ["mitochondri", "apoptosis", "autophagy", "mitophagy", "ferroptosis",
        "oxidative stress", "nrf2", "ampk", "pgc-1", "inflammasome", "nlrp3", "signaling pathway",
        "gene expression", "macrophage", "reactive oxygen", "cardiomyocyte", "molecular",
        "protein", "inflammation", "pathway"],
    "clinical": ["rehabilitation", "clinical", "randomized", "cohort", "patient", "intervention",
        "heart failure", "myocardial infarction", "exercise training", "exercise program",
        "nursing", "feasibility", "adherence", "trial", "therapy"],
    "exercise-physio": ["cardiopulmonary exercise", "cpet", "vo2", "exercise capacity",
        "endurance", "aerobic", "heart rate", "pulse wave", "cardiorespiratory fitness",
        "exercise intensity", "oxygen uptake", "cardiac output", "exercise testing", "exercise ecg"],
    "metabolic-vascular": ["blood pressure", "hypertension", "lipid", "cholesterol", "glucose",
        "insulin", "endothelial", "vascular", "microvascular", "atherosclerosis", "circulation",
        "metabolism", "body composition", "obesity", "hemodynamic"],
    "sports-med": ["acl", "ligament", "tendon", "injury", "return to sport", "orthopaedic",
        "knee", "calf", "shoulder", "musculoskeletal", "sprain", "muscle injury"],
}

ZH_TAG_KW = {
    "mechanism": ["线粒体", "凋亡", "自噬", "铁死亡", "氧化应激", "信号通路", "基因", "蛋白",
        "炎症", "巨噬细胞", "分子机制", "通路", "心肌细胞"],
    "clinical": ["康复", "临床", "随机对照", "队列", "患者", "干预", "护理", "心肌梗死",
        "心力衰竭", "运动训练", "治疗"],
    "exercise-physio": ["心肺运动", "运动耐力", "有氧", "心率", "脉搏波", "体能", "摄氧量",
        "运动强度", "心输出量", "运动能力"],
    "metabolic-vascular": ["血压", "高血压", "血脂", "胆固醇", "血糖", "胰岛素", "内皮", "血管",
        "动脉粥样硬化", "微循环", "代谢", "体成分", "肥胖"],
    "sports-med": ["韧带", "肌腱", "损伤", "重返运动", "骨科", "膝", "小腿", "肌肉损伤", "肌骨"],
}


def detect_tags(title, abstract, lang="en"):
    """按标题+摘要词表打细方向标签；返回固定顺序的稳定 key 列表，命中为空则空表。"""
    kw = ZH_TAG_KW if lang == "zh" else EN_TAG_KW
    text = (title or "") + " " + (abstract or "")
    text = _norm_en_text(text) if lang != "zh" else text.lower()
    return [key for key in TAG_KEYS if any(k in text for k in kw[key])]


# 预印本 / 撤稿识别（纯规则，不调外部接口）
PREPRINT_DOI_PREFIX = ("10.1101/", "10.21203/", "10.31234/")
PREPRINT_NAME_RE = re.compile(r"medrxiv|biorxiv|ssrn|research square|preprint|预印本|预印", re.I)
RETRACT_RE = re.compile(r"retract|撤稿|撤回", re.I)


def _annotate_entry(c):
    """补真实性/类型标注：DOI 与摘要完整性、预印本、撤稿提示、稳定类型标签。"""
    title = c.get("title") or ""
    journal = c.get("journal") or ""
    doi = (c.get("doi") or "").strip()
    blob = title + " " + journal
    preprint = doi.startswith(PREPRINT_DOI_PREFIX) or bool(PREPRINT_NAME_RE.search(blob))
    retracted = bool(RETRACT_RE.search(blob))
    c["hasDoi"] = bool(doi)
    c["hasAbstract"] = bool((c.get("abstractSnippet") or "").strip())
    c["preprint"] = preprint
    c["retracted"] = retracted
    if c.get("isReview"):
        c["pubType"] = "review"
    elif preprint:
        c["pubType"] = "preprint"
    else:
        c["pubType"] = "original"
    return c


def _cite_credibility(source):
    """跨源合并条目取各源最高被引可信度；未知源按偏弱处理。"""
    srcs = [s for s in str(source or "").split("+") if s]
    if not srcs:
        return 0.6
    return max(SOURCE_CITE_CRED.get(s, 0.6) for s in srcs)


def score_item(title, journal, abstract=""):
    title_t = _norm_en_text(title)
    full_t = _norm_en_text(title, abstract)
    j = (journal or "").lower()
    score = 0
    topics = []
    strong = any(k in title_t for k in STRONG_CARDIO)
    cardio_hit = strong
    for kw, w in CARDIO_KW.items():
        if kw in full_t:
            score += w
            cardio_hit = True
    med_hit = False
    for kw, w in MED_KW.items():
        if kw in full_t:
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
    # 部位偏离：只判标题，避免摘要里的其他脏器词误剔；未命中强词且属非靶部位即剔除
    offtopic = any(h in title_t for h in OFFTOPIC_HARD) or ((not strong) and any(s in title_t for s in OFFTOPIC_SOFT))
    return min(score, 100), topics, strong, offtopic, journal_hit


def score_zh_item(title, journal, abstract=""):
    """中文条目打分：心脏/线粒体等强词加权，运动词双命中视为对口；返回与 score_item 同构。"""
    title_t = title or ""
    full_t = (title or "") + " " + (abstract or "")
    score = 0
    topics = []
    strong = any(k in title_t for k in ZH_STRONG)
    cardio_hit = strong
    for kw, w in ZH_CARDIO_KW.items():
        if kw in full_t:
            score += w
            cardio_hit = True
    exercise_hit = any(k in full_t for k in ZH_EXERCISE_KW)
    if cardio_hit:
        topics.append("cardio")
    if exercise_hit:
        topics.append("med")
    offtopic = any(h in title_t for h in ZH_OFFTOPIC_HARD) or ((not strong) and any(s in title_t for s in ZH_OFFTOPIC_SOFT))
    return min(score, 100), topics, strong, offtopic, False, cardio_hit, exercise_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="输出 JSON 路径，默认仓库根 daily-frontier.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = ap.parse_args()

    today = dt.date.today()
    from_date = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    zh_from_date = (today - dt.timedelta(days=ZH_RECENT_DAYS)).isoformat()
    classic_from_date = (today - dt.timedelta(days=1095)).isoformat()  # 高被引经典桶：近三年

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
        # OpenAlex 匿名额度易触发 429，中文组间隔放慢；其余源保持 1s 礼貌间隔
        time.sleep(2.5 if source == ZH_SOURCE else 1)

    run_group("crossref", CARDIO_QUERY, lambda: crossref_group(CARDIO_QUERY, from_date))
    run_group("crossref", MED_QUERY, lambda: crossref_group(MED_QUERY, from_date))
    run_group("pubmed", PUBMED_CARDIO, lambda: pubmed_group(PUBMED_CARDIO))
    run_group("pubmed", PUBMED_MED, lambda: pubmed_group(PUBMED_MED))
    run_group("epmc", EPMC_CARDIO, lambda: epmc_group(EPMC_CARDIO))
    run_group("epmc", EPMC_MED, lambda: epmc_group(EPMC_MED))
    # 子方向补充组
    run_group("crossref", CARDIO_MITO_QUERY, lambda: crossref_group(CARDIO_MITO_QUERY, from_date))
    run_group("crossref", CARDIO_VASC_QUERY, lambda: crossref_group(CARDIO_VASC_QUERY, from_date))
    run_group("crossref", CARDIO_NEURO_QUERY, lambda: crossref_group(CARDIO_NEURO_QUERY, from_date))
    run_group("crossref", CARDIO_ISCH_QUERY, lambda: crossref_group(CARDIO_ISCH_QUERY, from_date))
    run_group("epmc", EPMC_MITO, lambda: epmc_group(EPMC_MITO))
    run_group("epmc", EPMC_VASC, lambda: epmc_group(EPMC_VASC))
    run_group("epmc", EPMC_NEURO, lambda: epmc_group(EPMC_NEURO))
    run_group("epmc", EPMC_ISCH, lambda: epmc_group(EPMC_ISCH))
    # 高被引经典桶：近三年、按被引排序，让高被引旧文可进入主池
    run_group("crossref", CARDIO_QUERY+" highly-cited",
              lambda: crossref_group(CARDIO_QUERY, classic_from_date, sort="is-referenced-by-count"))
    for words in ZH_QUERY_GROUPS:
        run_group(ZH_SOURCE, " ".join(words),
                  lambda w=words: openalex_zh_group(w, zh_from_date, today.isoformat()))

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
        """统一相关性 / 离靶 / 低分过滤，并补打分、话题、期刊命中、年龄天数。返回 (条目, 拒绝原因)。"""
        score, topics, strong, offtopic, journal_hit = score_item(c["title"], c["journal"], c.get("abstractSnippet", ""))
        if offtopic:
            return None, "offtopic"
        med_hit = "med" in topics
        if score < 6 and not strong and not med_hit:
            return None, "low_score"
        if score < MIN_SCORE and not strong:
            return None, "low_score"
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
        return c, None

    def accept_zh(c):
        """中文条目过滤：离靶剔除；强词保留，或标题级心血管×运动双命中，或标题含心血管词且达打分门槛。返回 (条目, 拒绝原因)。"""
        score, topics, strong, offtopic, journal_hit, cardio_hit, exercise_hit = score_zh_item(
            c["title"], c["journal"], c.get("abstractSnippet", ""))
        if offtopic:
            return None, "offtopic"
        title_t = c["title"] or ""
        # 非干预偏弱条目：纯护理 / AI 技术诊断 / 中医中药，标题无明确运动康复干预主体时剔除
        has_interv = any(k in title_t for k in ZH_INTERVENE)
        if any(k in title_t for k in ZH_NONINTERVENE) and not has_interv:
            return None, "low_score"
        title_cardio = strong or any(k in title_t for k in ZH_CARDIO_KW)
        title_exercise = any(k in title_t for k in ZH_EXERCISE_KW)
        # 双命中须标题显式出现心血管词（运动/康复词可来自摘要），避免仅摘要弱词凑出 cardio×exercise；
        # 分数门槛同样要求标题含心血管词
        dual = cardio_hit and exercise_hit and title_cardio
        score_pass = score >= MIN_SCORE and title_cardio
        if not (strong or dual or score_pass):
            return None, "low_score"
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
        return c, None

    candidates = []
    parsers = (("crossref", parse_crossref), ("pubmed", parse_pubmed),
               ("epmc", parse_epmc), (ZH_SOURCE, parse_openalex_zh))
    funnel = {src: {"raw": len(raw_by_source[src]), "parsed": 0, "accepted": 0}
              for src in raw_by_source}
    reject = {"no_title": 0, "short_title": 0, "bad_year": 0, "offtopic": 0, "low_score": 0}
    for src, parser in parsers:
        for it in raw_by_source[src]:
            c = parser(it)
            if not c or not c["title"]:
                reject["no_title"] += 1
                continue
            min_len = 8 if src == ZH_SOURCE else 15
            if len(c["title"]) < min_len:
                reject["short_title"] += 1
                continue
            min_year = ZH_MIN_YEAR if src == ZH_SOURCE else 2015
            if c["year"] is None or c["year"] > today.year + 1 or c["year"] < min_year:
                reject["bad_year"] += 1
                continue
            funnel[src]["parsed"] += 1
            c, reason = (accept_zh(c) if src == ZH_SOURCE else accept(c))
            if c:
                candidates.append(c)
                funnel[src]["accepted"] += 1
            else:
                reject[reason or "low_score"] += 1

    papers = dedup_merge(candidates)
    dedup_total = len(papers)

    # 摘要回填：Crossref / PubMed 等源摘要为空时，按 DOI 向 Europe PMC 补摘要；不重算打分
    for p in papers:
        if not p.get("abstractSnippet") and p.get("doi"):
            ab = epmc_abstract_by_doi(p["doi"])
            if ab:
                p["abstractSnippet"] = ab
            time.sleep(0.2)
        lang = "zh" if p.get("isChinese") else "en"
        p["tags"] = detect_tags(p["title"], p.get("abstractSnippet", ""), lang)
        _annotate_entry(p)

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

    # 批次内被引归一化（对数压缩），再计算复合分与新文置顶标记；PubMed 等无被引源记 None，不参与压分
    cite_vals = [p["citations"] for p in papers if isinstance(p["citations"], int)]
    max_cites = max(cite_vals, default=0)
    cites_denom = math.log10(1 + max_cites) if max_cites > 0 else 0.0
    # 池内被引普遍很低时，被引信号区分度弱，整体压一档，避免个位数被引过度拉开排序
    cite_scale = 1.0 if max_cites >= 5 else 0.7
    for p in papers:
        rel_norm = min(p["matchScore"], 100) / 100.0
        age = p["ageDays"] if p["ageDays"] is not None else RECENT_DAYS
        if age < 0:
            age = 0  # 在线先发（卷期年为次年）按最新处理
        decay = math.exp(-age / TIME_HALFLIFE)
        if p["citations"] is None:
            cites_score = 0.0
        else:
            cites_score = (math.log10(1 + p["citations"]) / cites_denom) if cites_denom > 0 else 0.0
            cites_score *= _cite_credibility(p.get("source", "")) * cite_scale
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
        print("\n=== 漏斗 dedup=%d  reject=%s ===" % (
            dedup_total, json.dumps(reject, ensure_ascii=False)))
        for src, f in funnel.items():
            print("  %s: raw=%d parsed=%d accepted=%d" % (src, f["raw"], f["parsed"], f["accepted"]))
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
        "dedupCount": dedup_total,
        "funnel": funnel,
        "rejectReasons": reject,
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
