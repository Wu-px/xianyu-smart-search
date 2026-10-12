"""闲鱼（goofish.com）数据抓取模块。

设计思路：
1. 用 Playwright 以「持久化上下文 + 有头模式」启动 Chromium，用户手动扫码登录一次，
   登录态保存在 ./browser_profile 目录中，之后搜索直接复用，无需重复登录。
   （实测无头模式会被闲鱼风控直接拦截，必须真人登录建立信任态。）
2. 不依赖易变的 DOM 结构，而是通过 page.on("response") 拦截闲鱼前端自己调用的
   mtop 接口（搜索 / 详情），直接拿结构化 JSON，页面前端改版时更稳健。
3. 卖家信息（昵称 / 最近上线 / 在售数 / 芝麻认证 / 已售数）直接来自详情接口的
   sellerDO 字段，不再逐个访问卖家主页，速度快一个数量级且更稳定。
4. 全部解析做容错：字段缺失记 None，交给评分模块降级处理；
   完全抓不到数据时抛 ScrapeError，由上层自动回退到演示数据，保证演示一定能跑通。
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://www.goofish.com"
PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "browser_profile"))

SEARCH_BUDGET_SECONDS = 180     # 一次搜索的总时间预算
DETAIL_PAGE_LIMIT = 14          # 对前 N 个商品抓详情
SCROLL_ROUNDS = 6               # 搜索页滚动轮数，尽量多加载结果

# 登录后闲鱼域名下必然存在的 Cookie 名（用于登录态兜底判断）
LOGIN_COOKIE_NAMES = {"unb", "cookie2", "_tb_token_", "sgcookie"}


class ScrapeError(RuntimeError):
    """抓取失败（未登录 / 被反爬 / 页面结构变化），上层应回退演示数据。"""


# ---------------------------------------------------------------- 工具函数

def _walk(obj: Any, pred, out: list, limit: int = 400) -> None:
    """递归遍历 JSON，收集满足 pred 的 dict。"""
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        try:
            if pred(obj):
                out.append(obj)
        except Exception:
            pass
        for v in obj.values():
            _walk(v, pred, out, limit)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk(v, pred, out, limit)


def _first(d: dict, keys: list[str]):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _parse_price(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def _fix_url(u: str | None) -> str | None:
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://"):]
    return u


def _parse_last_visit(text: str) -> float | None:
    """解析 sellerDO.lastVisitTime，如 「13分钟前来过」「昨天来过」「3天前来过」→ 天数。"""
    if not text:
        return None
    if re.search(r"分钟|小时|刚刚", text) or "今天" in text:
        return 0.0
    if "昨天" in text:
        return 1.0
    m = re.search(r"(\d+)\s*天", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*周", text)
    if m:
        return float(m.group(1)) * 7
    m = re.search(r"(\d+)\s*个月", text)
    if m:
        return float(m.group(1)) * 30
    return None


def _is_search_card(d: dict) -> bool:
    """搜索结果商品卡片：必有 itemId + title，价格可能是富文本 spans 或在 detailParams 里。"""
    if not (isinstance(d.get("title"), str) and d.get("itemId")):
        return False
    return "price" in d or "soldPrice" in (d.get("detailParams") or {})


def _card_price(raw: dict) -> float | None:
    p = _parse_price((raw.get("detailParams") or {}).get("soldPrice"))
    if p is not None:
        return p
    spans = raw.get("price")
    if isinstance(spans, list):  # 富文本价格：[{'text':'¥'},{'text':'18'}]
        text = "".join(str(s.get("text", "")) for s in spans if isinstance(s, dict))
        return _parse_price(text)
    return _parse_price(spans)


def _safe_json(resp) -> dict | None:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- 会话管理

class XianyuSession:
    """全局唯一的浏览器会话（FastAPI 线程里串行使用）。"""

    def __init__(self) -> None:
        self._pw = None
        self._context = None
        self._page = None
        self.lock = threading.Lock()

    # ---- 生命周期 ----

    def start(self, headless: bool = False) -> dict:
        with self.lock:
            if self._context is not None:
                return {"ok": True, "already": True}
            try:
                self._pw = sync_playwright().start()
                os.makedirs(PROFILE_DIR, exist_ok=True)
                self._context = self._pw.chromium.launch_persistent_context(
                    PROFILE_DIR,
                    headless=headless,
                    viewport={"width": 1280, "height": 820},
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:  # 浏览器缺失等
                self._cleanup()
                raise ScrapeError(f"浏览器启动失败：{exc}") from exc
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            try:
                self._page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
            except PWTimeout:
                pass  # 首页加载慢不影响登录
            return {"ok": True, "already": False}

    def stop(self) -> dict:
        with self.lock:
            self._cleanup()
            return {"ok": True}

    def _cleanup(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._pw = None
        self._page = None

    def is_running(self) -> bool:
        return self._context is not None

    # ---- 登录状态 ----

    def login_status(self) -> dict:
        """优先看闲鱼登录态接口的返回，不确定时兜底检查登录 Cookie。"""
        if self._context is None:
            return {"running": False, "logged_in": False}
        page = self._page
        captured: list[dict] = []

        def on_resp(resp):
            j = _safe_json(resp)
            if j is not None:
                captured.append(j)

        page.on("response", on_resp)
        try:
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3500)
            saw_login_api = False
            for j in captured:
                api = str(j.get("api", ""))
                if "loginuser" in api or "user.page.nav" in api:
                    saw_login_api = True
                    ret = ",".join(j.get("ret") or [])
                    if "SUCCESS" in ret:
                        nick = None
                        data = j.get("data") or {}
                        for k in ("nick", "nickName", "userNick"):
                            if isinstance(data, dict) and data.get(k):
                                nick = str(data[k])
                                break
                        return {"running": True, "logged_in": True, "nick": nick}
            # 接口未明确返回（token 过期等常见）：看登录 Cookie 兜底
            cookies = self._context.cookies(BASE)
            names = {c.get("name") for c in cookies}
            logged = bool(names & LOGIN_COOKIE_NAMES)
            return {"running": True, "logged_in": logged}
        except Exception:
            return {"running": True, "logged_in": False}

    # ---- 搜索 ----

    def search(self, keyword: str) -> list[dict]:
        """搜索关键词，返回归一化的商品列表（可能为空，空则上层抛错回退）。"""
        if self._context is None:
            raise ScrapeError("浏览器未启动")
        deadline = time.time() + SEARCH_BUDGET_SECONDS
        page = self._context.new_page()
        captured: list[dict] = []

        def on_response(resp):
            j = _safe_json(resp)
            if j is not None and ("mtop" in resp.url or "search" in resp.url):
                captured.append(j)

        page.on("response", on_response)
        items: list[dict] = []
        try:
            page.goto(
                f"{BASE}/search?q={keyword}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            for _ in range(SCROLL_ROUNDS):
                if time.time() > deadline:
                    break
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(1200)
            items = self._extract_search_items(captured)
        except PWTimeout as exc:
            raise ScrapeError(f"搜索页加载超时：{exc}") from exc
        finally:
            page.close()

        if not items:
            raise ScrapeError("未能从闲鱼获取搜索结果（可能未登录或触发反爬验证）")

        detailed = []
        for it in items[:DETAIL_PAGE_LIMIT]:
            if time.time() > deadline:
                break
            try:
                detail = self._fetch_detail(it, deadline)
                it.update(detail)
            except Exception:
                pass
            detailed.append(it)
        return detailed

    # ---- 解析 ----

    @staticmethod
    def _extract_search_items(captured: list[dict]) -> list[dict]:
        seen: set[str] = set()
        items: list[dict] = []
        for cap_json in captured:
            raw_list: list[dict] = []
            _walk(cap_json, _is_search_card, raw_list, limit=300)
            for raw in raw_list:
                iid = str(raw.get("itemId"))
                if iid in seen:
                    continue
                seen.add(iid)
                items.append({
                    "item_id": iid,
                    "title": str(raw.get("title", "")).strip(),
                    "price": _card_price(raw),
                    "desc": "",
                    "image_count": 0,
                    "image_url": _fix_url(str(raw.get("picUrl") or "") or None),
                    "url": f"{BASE}/item?id={iid}",
                    "seller": {
                        "id": None,
                        "nick": raw.get("userNickName") or None,
                        "good_count": None, "bad_count": None,
                        "item_count": None, "categories": None, "last_online_days": None,
                    },
                    "pickup": {"is_pickup": False, "location_text": None},
                    "area": raw.get("area"),
                })
        return items

    def _fetch_detail(self, item: dict, deadline: float) -> dict:
        """打开商品详情页，从 mtop.taobao.idle.pc.detail 拿文案/图片/卖家数据。"""
        out: dict = {}
        page = self._context.new_page()
        captured: list[dict] = []

        def on_response(resp):
            j = _safe_json(resp)
            if j is not None:
                captured.append(j)

        page.on("response", on_response)
        try:
            page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            for j in captured:
                if j.get("api") != "mtop.taobao.idle.pc.detail":
                    continue
                data = j.get("data") or {}
                item_do = data.get("itemDO") or {}
                seller_do = data.get("sellerDO") or {}

                # 文案与图片
                desc = str(item_do.get("desc") or "")
                if desc:
                    out["desc"] = desc
                infos = item_do.get("imageInfos") or []
                urls = [str(i.get("url") or "") for i in infos if isinstance(i, dict)]
                if urls:
                    out["image_count"] = len(urls)
                    if not item.get("image_url"):
                        out["image_url"] = _fix_url(urls[0])

                # 价格兜底（卡片价缺失时）
                if item.get("price") is None:
                    p = _parse_price(item_do.get("soldPrice"))
                    if p is not None:
                        out["price"] = p

                # 运费
                fee = _parse_price(item_do.get("transportFee"))
                if fee is not None:
                    out["shipping_fee"] = fee

                # 卖家数据（sellerDO 一站拿全，不再访问卖家主页）
                seller = item["seller"]
                if not seller.get("id") and seller_do.get("sellerId"):
                    seller["id"] = str(seller_do["sellerId"])
                if not seller.get("nick") and seller_do.get("nick"):
                    seller["nick"] = str(seller_do["nick"])
                s_items = seller_do.get("sellerItems") or []
                if s_items:
                    seller["item_count"] = len(s_items)
                sold = seller_do.get("hasSoldNumInteger")
                seller["sold_count"] = int(sold) if str(sold or "").isdigit() else None
                seller["zhima_auth"] = bool(seller_do.get("zhimaAuth"))
                seller["reply_ratio"] = seller_do.get("replyRatio24h")
                seller["last_online_days"] = _parse_last_visit(str(seller_do.get("lastVisitTime") or ""))

                if not item.get("area") and seller_do.get("city"):
                    out["area"] = seller_do["city"]

                # 自提识别：文案提到自提 → 用卖家城市作为自提位置
                text = f"{item['title']} {desc}"
                if not item["pickup"].get("is_pickup"):
                    strong = re.search(r"仅.{0,6}自提|只(支持)?.{0,3}自提|自提.{0,4}优先|"
                                       r"不(包)?邮(寄)?|仅限.{0,10}自提|不支持邮寄|同城自提", text)
                    weak = "自提" in text and not re.search(r"快递|邮寄|包邮|到付|可邮", text)
                    if strong or weak:
                        out["pickup"] = {"is_pickup": True,
                                         "location_text": str(seller_do.get("city") or item.get("area") or "")}
                break
        finally:
            page.close()
        return out


session = XianyuSession()
