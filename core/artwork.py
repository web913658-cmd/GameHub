"""封面 / 宣传图解析。

封面来源，按游戏类型分两条链路：

**Steam 游戏**（检测时就有 appid）
    appid ──拼接──> https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg
    library_600x900.jpg 是 Steam 库里的官方竖版海报，发布商已把 Logo 与标题
    合成进去，正是「带文字的官方宣传图」。整条链路不需要任何 API 调用。

**非 Steam 游戏**（Epic / GOG / 本地 / 独立游戏）
    这些平台的本地清单里没有图片地址（Epic 的 VaultThumbnailUrl 是空字符串），
    必须先把游戏名解析成 Steam appid，再复用上面的 CDN。解析依次尝试：
      1. 本地 appid 全量表（离线，命中即秒回）
      2. Steam 商店搜索接口 storesearch
      3. Steam 社区 SearchApps 接口
    都拿不到时，退到 SteamGridDB 社区图库（需用户自填 API Key）。

**最后兜底**：提取 exe 内嵌图标，前端用专属渐变 + 图标合成卡片。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from .models import Game
from . import icon_extract

CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{name}"
STORE_SEARCH = "https://store.steampowered.com/api/storesearch/?term={term}&cc={cc}&l={lang}"
COMMUNITY_SEARCH = "https://steamcommunity.com/actions/SearchApps/{term}"
SGDB_BASE = "https://www.steamgriddb.com/api/v2"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameHub/1.0"

# Steam CDN 上可用的官方美术资源
ART_FILES = {
    "cover": ["library_600x900.jpg", "library_600x900_2x.jpg", "header.jpg"],
    "hero": ["library_hero.jpg", "page_bg_raw.jpg"],
    "logo": ["logo.png"],
    "capsule": ["capsule_616x353.jpg", "header.jpg"],
}

_TRADEMARK = re.compile(r"[™®©]")
_NOISE = re.compile(
    r"\b(edition|definitive|deluxe|ultimate|complete|remastered|remake|goty|"
    r"game of the year|standard|premium|gold|enhanced|hd|vr)\b", re.I)
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 :'\-\.&!]*")


# --------------------------------------------------------------------------
# 名称归一化与相似度
# --------------------------------------------------------------------------

def _norm(name: str) -> str:
    s = _TRADEMARK.sub("", name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = _NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    """轻量相似度：token 覆盖率 + 前缀加成 ± 多余词处理。

    多余词的位置很关键：
      · 都在查询词之前 -> 通常是发布商/人名前缀，同一款游戏
        （"Sid Meier's Civilization VI" vs "Civilization VI"）-> 加分
      · 出现在之后 -> 往往是实质区分，是另一款游戏
        （"Minecraft Dungeons" vs "Minecraft"）-> 扣分，避免张冠李戴
    """
    na, nb = _norm(a), _norm(b)
    ta_list, tb_list = na.split(), nb.split()
    ta, tb = set(ta_list), set(tb_list)
    if not ta or not tb:
        return 0.0

    score = len(ta & tb) / max(len(ta), len(tb))
    if na and nb and (na.startswith(nb) or nb.startswith(na)):
        score += 0.25

    extra = tb - ta
    if extra:
        first_q = next((i for i, t in enumerate(tb_list) if t in ta), len(tb_list))
        if set(tb_list[:first_q]) >= extra:
            score += 0.15                       # 前置的发布商/人名
        else:
            score -= 0.3 * len(extra)           # 后置的实质区分词
    return score


_BRACKET_RE = re.compile(r"[（(\[【《「]([^）)\]】》」]*)[）)\]】》」]")
# 注意不能用 \b：中文字符在 Python 里也算 \w，中文与字母之间没有词边界
_VERSION_RE = re.compile(r"(?<![A-Za-z])v?\d+(?:\.\d+){1,3}[a-z]?(?![A-Za-z0-9])", re.I)
_MARKER_RE = re.compile(
    r"(整合|汉化|中文|免安装|绿色|豪华|典藏|终极|完全|重制|高清|修复|更新|"
    r"mod|mods|repack|fitgirl|dodi|xatab|elamigos|update|patch|build)\S*", re.I)
# 发布组 / 打包标记：出现在方括号里时不是游戏名
_RELEASE_TAG_RE = re.compile(
    r"(fitgirl|dodi|xatab|r\.?\s?g\.?\s?mechanics|elamigos|corepack|codex|plaza|"
    r"rune|empress|skidrow|reloaded|prophet|tinyiso|repack|proper|multi\d*|iso|"
    r"免安装|绿色|汉化|整合|中文)", re.I)


def search_terms(name: str) -> List[str]:
    """为一个游戏名生成多个候选检索词，按信息量从高到低排列。

    本地安装的游戏名往往带很多噪声（版本号、整合/汉化标记、方括号），
    直接拿去搜命中率很低。例如「【逃离鸭科夫】最新v1.2.5 整合mod版」
    真正有用的只是括号里的「逃离鸭科夫」；但「[FitGirl Repack]」这种
    括号内容反而是噪声，要剔除。
    """
    raw = (name or "").strip()
    out: List[str] = []

    def add(s: str):
        s = re.sub(r"\s+", " ", s or "").strip(" -_·|[]【】《》「」()（）")
        if len(s) >= 2 and s not in out:
            out.append(s)

    # 1) 方括号里的内容 —— 往往是真正的游戏名，但纯发布组标记要跳过
    for m in _BRACKET_RE.finditer(raw):
        inner = m.group(1)
        if _RELEASE_TAG_RE.sub(" ", inner).strip():
            add(inner)

    # 2) 去掉括号内容、版本号、整合/汉化等修饰词后的主体
    stripped = _VERSION_RE.sub(" ", _MARKER_RE.sub(" ", raw))
    add(_BRACKET_RE.sub(" ", stripped))
    add(stripped)

    # 3) 纯拉丁字母段（中文名混排时用得上）
    add(" ".join(m.group(0) for m in _LATIN.finditer(raw)))

    # 4) 原文
    add(raw)

    return out[:4]


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------

def _openers():
    """请求器链：先直连，失败再走系统代理。

    国内环境下 Steam 图片 CDN、SteamGridDB、Epic 的域名都是直连可达的，
    而 store/api.steampowered.com 只有挂代理才通。先直连能避免
    「代理开着但已经断了」时白白等超时；代理作为兜底保证开 VPN 时也能用。
    """
    global _OPENERS
    if _OPENERS is None:
        _OPENERS = [
            urllib.request.build_opener(urllib.request.ProxyHandler({})),  # 直连
            urllib.request.build_opener(),                                 # 系统/环境代理
        ]
    return _OPENERS


_OPENERS: Optional[list] = None


def _http_json(url: str, headers: Optional[dict] = None, timeout: int = 8,
               attempts: int = 2, retry_delay: float = 0.3,
               data: Optional[bytes] = None, method: str = "GET"):
    """带重试与直连/代理回退的 JSON 请求。

    attempts 次尝试会依次轮换 直连 -> 代理 -> 直连 …
    """
    last: Optional[Exception] = None
    openers = _openers()
    for i in range(max(1, attempts)):
        opener = openers[i % len(openers)]
        try:
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={"User-Agent": UA, **(headers or {})})
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(retry_delay)
    raise last if last else RuntimeError("request failed")


def fetch_bytes(url: str, timeout: int = 15, attempts: int = 2) -> Tuple[bytes, str]:
    """下载二进制内容（图片等），带直连/代理回退。

    返回 (数据, Content-Type)。全部失败时抛异常。
    """
    last: Optional[Exception] = None
    openers = _openers()
    for i in range(max(1, attempts)):
        opener = openers[i % len(openers)]
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://store.steampowered.com/",
            })
            with opener.open(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "image/jpeg")
        except Exception as exc:
            last = exc
    raise last if last else RuntimeError("download failed")


class _Breaker:
    """连续失败熔断器。

    在线接口偶尔不可达或限流，若不处理，每个游戏都要等满超时，
    一轮扫描能拖到几分钟。连续失败达阈值就整轮跳过。

    阈值不能太低：本机走不稳定代理时，单次失败很常见，
    阈值太小会导致整轮扫描都放弃封面。因此配合重试一起用。
    """

    def __init__(self, name: str, threshold: int = 4):
        self.name = name
        self.threshold = threshold
        self.fails = 0
        self.tripped = False
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.fails = 0
            self.tripped = False

    def call(self, fn, *a, **kw):
        with self._lock:
            if self.tripped:
                return None
        try:
            r = fn(*a, **kw)
        except Exception:
            with self._lock:
                self.fails += 1
                if self.fails >= self.threshold and not self.tripped:
                    self.tripped = True
                    print(f"[artwork] {self.name} 连续失败 {self.fails} 次，本轮扫描跳过该来源")
            return None
        with self._lock:
            self.fails = 0
        return r


_store_breaker = _Breaker("Steam 商店搜索", 4)
_community_breaker = _Breaker("Steam 社区搜索", 4)
_sgdb_breaker = _Breaker("SteamGridDB", 4)
_epic_breaker = _Breaker("Epic 官方接口", 4)

# 数据目录（离线全量表放在这里），由 resolve() 注入
_data_dir = ""


def reset_breakers() -> None:
    _store_breaker.reset()
    _community_breaker.reset()
    _sgdb_breaker.reset()
    _epic_breaker.reset()


# --------------------------------------------------------------------------
# 游戏名 -> Steam appid
# --------------------------------------------------------------------------

def _search_storesearch(term: str, cc: str, lang: str) -> List[dict]:
    data = _store_breaker.call(
        _http_json, STORE_SEARCH.format(term=urllib.parse.quote(term), cc=cc, lang=lang),
        None, 4, 2)          # 超时 4s，失败重试 2 次
    if not data:
        return []
    return [{"id": str(it.get("id", "")), "name": it.get("name", "")}
            for it in (data.get("items") or []) if it.get("type") == "app"]


def _search_community(term: str) -> List[dict]:
    data = _community_breaker.call(
        _http_json, COMMUNITY_SEARCH.format(term=urllib.parse.quote(term)),
        None, 4, 2)
    if not isinstance(data, list):
        return []
    return [{"id": str(it.get("appid", "")), "name": it.get("name", "")} for it in data]


def steam_search_appid(name: str) -> str:
    """把游戏名解析成 Steam appid。

    先走在线搜索（快且准），都拿不到时才启用本地全量表兜底。
    这样正常情况下完全不会把十几 MB 的全量表读进内存。
    """
    aid = _online_search_appid(name)
    if aid:
        return aid
    # 离线兜底：未下载过全量表时直接返回空，不做任何隐式下载
    try:
        from . import steam_applist
        return steam_applist.lookup(name, _data_dir)
    except Exception:
        return ""


def _online_search_appid(name: str) -> str:
    has_cjk = bool(_CJK.search(name or ""))
    locales = [("cn", "schinese"), ("us", "en")] if has_cjk else [("us", "en")]
    best_id, best_score = "", 0.5

    # 商店搜索接口
    for cc, lang in locales:
        for term in (search_terms(name) or [name])[:2]:
            for item in _search_storesearch(term, cc, lang):
                score = max(_similarity(name, item["name"]),
                            _similarity(term, item["name"]))
                if score > best_score:
                    best_score, best_id = score, item["id"]
            if best_id:
                return best_id

    # 社区接口（域名不同，可能与商店接口一个通一个不通）
    for term in (search_terms(name) or [name])[:2]:
        for item in _search_community(term):
            score = _similarity(name, item["name"])
            if score > best_score:
                best_score, best_id = score, item["id"]
        if best_id:
            break
    return best_id


# --------------------------------------------------------------------------
# SteamGridDB（社区图库，覆盖非 Steam 游戏）
# --------------------------------------------------------------------------

def _sgdb_get(path: str, key: str, timeout: int = 6):
    def _do():
        return _http_json(f"{SGDB_BASE}{path}",
                          {"Authorization": f"Bearer {key}"}, timeout, 2)
    return _sgdb_breaker.call(_do)


def _first_url(payload, prefer: Tuple[int, int] = (0, 0)) -> str:
    """从 SteamGridDB 的 {success,data:[...]} 里取图片地址。

    格式在不同端点/版本间略有差异，这里做防御性解析。
    """
    if not isinstance(payload, dict) or not payload.get("success"):
        return ""
    data = payload.get("data")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        return ""

    items = [d for d in data if isinstance(d, dict) and d.get("url")]
    if not items:
        return ""

    # 指定了期望尺寸时，优先挑尺寸最接近的
    if prefer != (0, 0):
        pw, ph = prefer
        def dist(d):
            w = d.get("width") or 0
            h = d.get("height") or 0
            if not w or not h:
                return 10 ** 6
            return abs(w - pw) + abs(h - ph)
        items.sort(key=dist)
    return str(items[0].get("url") or "")


def steamgriddb_find(name: str, key: str) -> Tuple[str, str]:
    """按名称在 SteamGridDB 里找游戏，返回 (game_id, 官方名)。"""
    for term in (search_terms(name) or [name])[:2]:
        payload = _sgdb_get(f"/search/autocomplete/{urllib.parse.quote(term)}", key)
        if not isinstance(payload, dict) or not payload.get("success"):
            continue
        data = payload.get("data") or []
        if not isinstance(data, list) or not data:
            continue
        best, best_score = None, 0.5
        for it in data:
            if not isinstance(it, dict):
                continue
            s = max(_similarity(name, it.get("name", "")),
                    _similarity(term, it.get("name", "")))
            if s > best_score:
                best_score, best = s, it
        if best:
            return str(best.get("id", "")), str(best.get("name", ""))
    return "", ""


def steamgriddb_lookup(name: str, key: str) -> dict:
    """取 SteamGridDB 的网格图 / 大图 / Logo / 图标。"""
    if not key:
        return {}
    gid, matched = steamgriddb_find(name, key)
    if not gid:
        return {}

    out: dict = {"_matched": matched}
    # 网格图：先要 600x900 竖版（与卡片 2:3 比例一致），没有就不限尺寸
    grid = _first_url(_sgdb_get(f"/grids/game/{gid}?dimensions=600x900", key), (600, 900))
    if not grid:
        grid = _first_url(_sgdb_get(f"/grids/game/{gid}", key), (600, 900))
    if grid:
        out["grid"] = grid

    for kind, field in (("heroes", "hero"), ("logos", "logo"), ("icons", "icon")):
        url = _first_url(_sgdb_get(f"/{kind}/game/{gid}", key))
        if url:
            out[field] = url
    return out


def steamgriddb_test(key: str) -> Tuple[bool, str]:
    """验证 API Key 是否可用（供设置界面「测试连接」用）。"""
    if not key or not key.strip():
        return False, "请先填入 API Key"
    try:
        payload = _http_json(
            f"{SGDB_BASE}/search/autocomplete/Monument%20Valley",
            {"Authorization": f"Bearer {key.strip()}"}, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Key 无效（服务端返回 401）"
        return False, f"请求失败：HTTP {e.code}"
    except Exception as e:
        return False, f"网络不可达：{type(e).__name__}"

    if isinstance(payload, dict) and payload.get("success"):
        n = len(payload.get("data") or [])
        return True, f"连接正常（测试检索返回 {n} 条结果）"
    errs = ""
    if isinstance(payload, dict):
        errs = "；".join(payload.get("errors") or [])
    return False, f"Key 无效或额度用尽{('：' + errs) if errs else ''}"


# --------------------------------------------------------------------------
# Epic 官方封面（GraphQL，无需 API Key，国内直连可达）
# --------------------------------------------------------------------------

EPIC_GRAPHQL = "https://store.epicgames.com/graphql"

_EPIC_QUERY = (
    "query searchStoreQuery($keywords: String!, $country: String!, $locale: String!) {"
    " Catalog { searchStore(keywords: $keywords, country: $country, locale: $locale,"
    " count: 5) { elements { title id namespace keyImages { type url } } } } }"
)

# 图片类型优先级：竖版海报最适合卡片
_EPIC_IMG_PRIORITY = ("OfferImageTall", "DieselStoreFrontTall", "Thumbnail",
                      "OfferImageWide", "DieselStoreFrontWide", "featuredMedia")


def _epic_pick_image(images: List[dict]) -> Tuple[str, str]:
    """按优先级挑竖版图，返回 (竖版, 横版)。"""
    by_type: Dict[str, str] = {}
    for img in images or []:
        t, u = img.get("type"), img.get("url")
        if t and u and t not in by_type:
            by_type[t] = u
    tall = next((by_type[t] for t in _EPIC_IMG_PRIORITY[:3] if t in by_type), "")
    wide = next((by_type[t] for t in _EPIC_IMG_PRIORITY[3:] if t in by_type), "")
    if not tall:
        tall = wide
    return tall, wide


def epic_lookup(name: str, country: str = "CN", locale: str = "zh-CN") -> dict:
    """用 Epic 官方 GraphQL 按名称查游戏封面。

    不需要 API Key，而且国内直连可达 —— 是 Epic 游戏封面的首选来源，
    比「拿名字去 Steam 反查」可靠得多（后者依赖被墙的商店接口）。
    """
    for term in (search_terms(name) or [name])[:2]:
        body = json.dumps({
            "query": _EPIC_QUERY,
            "variables": {"keywords": term, "country": country, "locale": locale},
        }).encode("utf-8")

        def _do(_body=body):
            return _http_json(
                EPIC_GRAPHQL,
                {"Content-Type": "application/json",
                 "Origin": "https://store.epicgames.com"},
                8, 2, 0.3, _body, "POST")

        payload = _epic_breaker.call(_do)
        if not isinstance(payload, dict):
            continue
        try:
            elements = payload["data"]["Catalog"]["searchStore"]["elements"]
        except (KeyError, TypeError):
            continue
        if not isinstance(elements, list) or not elements:
            continue

        best, best_score = None, 0.5
        for el in elements:
            if not isinstance(el, dict):
                continue
            s = max(_similarity(name, el.get("title", "")),
                    _similarity(term, el.get("title", "")))
            if s > best_score:
                best_score, best = s, el
        if best:
            tall, wide = _epic_pick_image(best.get("keyImages") or [])
            if tall:
                return {"cover": tall, "wide": wide, "_matched": best.get("title", "")}
    return {}


# --------------------------------------------------------------------------
# Steam CDN
# --------------------------------------------------------------------------

def _cdn_set(appid: str) -> dict:
    return {
        "cover": CDN.format(appid=appid, name=ART_FILES["cover"][0]),
        "cover_alt": [CDN.format(appid=appid, name=n) for n in ART_FILES["cover"][1:]],
        "hero": CDN.format(appid=appid, name=ART_FILES["hero"][0]),
        "logo": CDN.format(appid=appid, name=ART_FILES["logo"][0]),
        "capsule": CDN.format(appid=appid, name=ART_FILES["capsule"][0]),
        "appid": appid,
    }


# --------------------------------------------------------------------------
# exe 图标提取（兜底）
#
# 用纯 Python 解析 PE 资源段，取程序自带的最高分辨率图标（通常 256×256）。
# 不调用 PowerShell：既快很多，也不会在程序本身没有图标资源时
# 返回一个通用的「应用程序」图标（那种反而比字母占位更难看）。
# --------------------------------------------------------------------------

def extract_exe_icon(exe: str, out_dir: str, key: str) -> str:
    """提取 exe 图标并落盘，返回可直接给 <img> 用的 URL；失败返回空串。"""
    if not exe or not os.path.isfile(exe):
        return ""
    try:
        ext, path = icon_extract.save_icon(exe, out_dir, key)
    except Exception:
        return ""
    if not path or not os.path.isfile(path):
        return ""
    return f"/api/icon/{key}.{ext}"


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------

class ArtCache:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, dict] = {}
        self._dirty = False
        self._lock = threading.Lock()
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
        except Exception:
            self.data = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: dict):
        with self._lock:
            self.data[key] = value
            self._dirty = True

    def save(self):
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            self._dirty = False
        except Exception:
            pass


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def _resolve_one(g: Game, cache: ArtCache, icon_dir: str, sgdb_key: str,
                 now: int) -> None:
    """解析单个游戏的封面，就地写回 g。"""
    key = f"{g.platform}:{g.app_id or _norm(g.name)}"
    cached = cache.get(key)

    # 命中缓存直接复用。两种情况例外：
    #   · 上次解析时数据源正好不可用（retry 标记）—— 这次重试
    #   · 空结果超过一天 —— 网络可能已恢复
    if cached and not cached.get("retry"):
        age = now - int(cached.get("ts", 0) or 0)
        if cached.get("cover") or cached.get("icon") or age < 86400:
            g.cover = cached.get("cover", "")
            g.hero = cached.get("hero", "")
            g.logo = cached.get("logo", "")
            g.icon = cached.get("icon", "")
            return

    info: dict = {}

    # ---- 1. Steam 游戏：直接用 appid 拼 CDN 地址（不需要任何 API）----
    if g.platform == "steam" and g.app_id.isdigit():
        info = _cdn_set(g.app_id)

    # ---- 2. Epic 官方 GraphQL：按名字检索，不限平台 ----
    # 官方图、无需 Key，国内直连可达（实测 0.7s）。
    # Epic 游戏用 AppName 检索；本地游戏也走这条 —— 只要该作在 Epic
    # 上架过就能拿到竖版海报，比拿名字去 Steam 商店反查可靠得多
    # （store.steampowered.com 在国内无法直连）。
    if not info.get("cover") and g.platform != "steam":
        ep = epic_lookup(g.name)
        if ep.get("cover"):
            info = {
                "cover": ep["cover"],
                "hero": ep.get("wide", ""),
                "matched_by": "epic-official",
            }

    # ---- 3. 其它非 Steam 游戏：先把名字解析成 Steam appid，再拼 CDN ----
    if not info.get("cover"):
        appid = steam_search_appid(g.name)
        if appid:
            info = _cdn_set(appid)
            info["matched_by"] = "steam-appid"

    # ---- 4. SteamGridDB：以上都拿不到时用社区图库补上 ----
    if not info.get("cover") and sgdb_key:
        extra = steamgriddb_lookup(g.name, sgdb_key)
        if extra.get("grid"):
            info["cover"] = extra["grid"]
            info["matched_by"] = "steamgriddb"
        if extra.get("hero"):
            info["hero"] = extra["hero"]
        if extra.get("logo"):
            info["logo"] = extra["logo"]
        if extra.get("icon"):
            info["icon"] = extra["icon"]

    # ---- 5. 兜底：提取 exe 图标 ----
    icon_local = ""
    if g.exe and os.path.isfile(g.exe):
        icon_local = extract_exe_icon(g.exe, icon_dir, g.id)

    g.cover = info.get("cover", "")
    g.hero = info.get("hero", "")
    g.logo = info.get("logo", "")
    g.icon = info.get("icon", "") or icon_local

    # 没拿到封面、且当时有数据源处于熔断状态 -> 标记下次重试
    tripped = (_store_breaker.tripped or _community_breaker.tripped
               or _sgdb_breaker.tripped or _epic_breaker.tripped)
    cache.set(key, {
        "cover": g.cover, "hero": g.hero, "logo": g.logo, "icon": g.icon,
        "matched_by": info.get("matched_by", ""), "ts": now,
        "retry": bool(tripped and not g.cover),
    })


def resolve(games: List[Game], cache: ArtCache, icon_dir: str,
            sgdb_key: str = "", progress=None, workers: int = 6) -> None:
    """就地填充每个 Game 的 cover / hero / logo / icon 字段。

    这一步几乎全是网络等待，因此并发处理；本机若走不稳定代理，
    并发也能把「等超时」的墙钟时间压下来。
    """
    global _data_dir
    _data_dir = os.path.dirname(os.path.abspath(icon_dir)) or _data_dir

    reset_breakers()
    total = len(games)
    now = int(time.time())
    done = [0]
    lock = threading.Lock()

    def work(g: Game):
        try:
            _resolve_one(g, cache, icon_dir, sgdb_key, now)
        except Exception as exc:
            print(f"[artwork] 解析失败 {g.name}: {exc}")
        finally:
            with lock:
                done[0] += 1
                if progress:
                    progress(g.name, done[0], total)

    if total == 1 or workers <= 1:
        for g in games:
            work(g)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
            list(pool.map(work, games))

    cache.save()
