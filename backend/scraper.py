"""闲鱼（goofish.com）数据抓取模块。

设计思路：
1. 用 Playwright 以「持久化上下文 + 有头模式」启动 Chromium，用户手动扫码登录一次，
   登录态保存在 ./browser_profile 目录中，之后搜索直接复用，无需重复登录。
2. 不依赖易变的 DOM 结构，而是通过 page.on("response") 拦截闲鱼前端自己调用的
   mtop 接口（搜索 / 详情 / 卖家主页），直接拿到结构化 JSON，页面前端改版时更稳健。
3. 全部解析做容错：字段缺失记 None，交给评分模块降级处理；
   完全抓不到数据时抛 ScrapeError，由上层自动回退到演示数据，保证演示一定能跑通。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://www.goofish.com"
PROFILE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "browser_profile"))

SEARCH_BUDGET_SECONDS = 240     # 一次搜索的总时间预算
DETAIL_PAGE_LIMIT = 14          # 对前 N 个商品抓详情 / 卖家主页
SCROLL_ROUNDS = 6               # 搜索页滚动轮数，尽量多加载结果


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
    return u


def _is_search_item(d: dict) -> bool:
    return isinstance(d.get("title"), str) and any(
        k in d for k in ("price", "priceWap", "priceString", "salePrice")
    )


def _is_detail(d: dict) -> bool:
    desc = _first(d, ["desc", "description", "itemDesc"])
    return isinstance(desc, str) and len(desc) >= 10 and any(
        k in d for k in ("pics", "images", "picList", "photos")
    )


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
        """通过闲鱼自己的登录态接口判断（未登录返回 SESSION_EXPIRED），
        比看页面 URL 可靠——personal_page 未登录时返回 404 而非跳登录页。"""
        if self._context is None:
            return {"running": False, "logged_in": False}
        page = self._page
        captured: list[dict] = []

        def on_resp(resp):
            try:
                j = resp.json()
                if isinstance(j, dict):
                    captured.append(j)
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3500)
            for j in captured:
                api = str(j.get("api", ""))
                if "loginuser" in api or "user.page.nav" in api:
                    ret = ",".join(j.get("ret") or [])
                    if "SUCCESS" in ret:
                        nick = None
                        data = j.get("data") or {}
                        for k in ("nick", "nickName", "userNick"):
                            if isinstance(data, dict) and data.get(k):
                                nick = str(data[k])
                                break
                        return {"running": True, "logged_in": True, "nick": nick}
                    if "SESSION_EXPIRED" in ret or "TOKEN" in ret:
                        return {"running": True, "logged_in": False}
        except Exception:
            pass
        return {"running": True, "logged_in": False}

    # ---- 搜索 ----

    def search(self, keyword: str) -> list[dict]:
        """搜索关键词，返回归一化的商品列表（可能为空，空则上层抛错回退）。"""
        if self._context is None:
            raise ScrapeError("浏览器未启动")
        deadline = time.time() + SEARCH_BUDGET_SECONDS
        page = self._context.new_page()
        captured: list[dict] = []  # {"url":..., "json":...}

        def on_response(resp):
            try:
                u = resp.url
                if "mtop" not in u and "search" not in u:
                    return
                data = resp.json()
                if isinstance(data, dict):
                    captured.append({"url": u, "json": data})
            except Exception:
                pass

        page.on("response", on_response)
        items: list[dict] = []
        try:
            page.goto(
                f"{BASE}/search?q={keyword}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            # 滚动加载更多
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

        # 详情 + 卖家主页补充
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
        for cap in captured:
            raw_list: list[dict] = []
            _walk(cap["json"], _is_search_item, raw_list, limit=300)
            for raw in raw_list:
                iid = str(_first(raw, ["item_id", "itemId", "id"]) or raw.get("title"))
                if iid in seen:
                    continue
                seen.add(iid)
                items.append({
                    "item_id": iid,
                    "title": str(raw.get("title", "")).strip(),
                    "price": _parse_price(_first(raw, ["price", "priceWap", "priceString", "salePrice"])),
                    "desc": "",
                    "image_count": 0,
                    "image_url": _fix_url(str(_first(raw, ["picUrl", "pic", "img", "image", "coverUrl"]) or "") or None),
                    "url": f"{BASE}/item?id={_first(raw, ['item_id', 'itemId', 'id']) or iid}",
                    "seller": {
                        "id": str(_first(raw, ["userId", "sellerId", "user_id", "seller_id", "ownerId"]) or "") or None,
                        "nick": None, "good_count": None, "bad_count": None,
                        "item_count": None, "categories": None, "last_online_days": None,
                    },
                    "pickup": {"is_pickup": False, "location_text": None},
                    "area": _first(raw, ["area", "city", "location", "areaName"]),
                })
        return items

    def _fetch_detail(self, item: dict, deadline: float) -> dict:
        """打开商品详情页与卖家主页，抓文案、图片、卖家数据。"""
        out: dict = {}
        page = self._context.new_page()
        captured: list[dict] = []

        def on_response(resp):
            try:
                data = resp.json()
                if isinstance(data, dict):
                    captured.append({"url": resp.url, "json": data})
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            detail_raw: list[dict] = []
            for cap in captured:
                _walk(cap["json"], _is_detail, detail_raw, limit=20)
            if detail_raw:
                raw = max(detail_raw, key=lambda d: len(str(d.get("desc", ""))))
                desc = str(_first(raw, ["desc", "description", "itemDesc"]) or "")
                pics = _first(raw, ["pics", "images", "picList", "photos"]) or []
                pic_urls = [_fix_url(p if isinstance(p, str) else str(_first(p, ["url", "picUrl", "img"]) or "")) for p in pics]
                out["desc"] = desc
                out["image_count"] = len(pics)
                if pic_urls and not item.get("image_url"):
                    out["image_url"] = pic_urls[0]
                sid = _first(raw, ["userId", "sellerId", "user_id", "seller_id"])
                if sid:
                    item["seller"]["id"] = str(sid)
                if not item.get("area"):
                    out["area"] = _first(raw, ["area", "city", "location"])
            else:
                # 兜底：尝试页面内 __INIT_DATA__
                try:
                    html = page.content()
                    m = re.search(r"__INIT_DATA__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
                    if m:
                        data = json.loads(m.group(1))
                        raw_list: list[dict] = []
                        _walk(data, _is_detail, raw_list, limit=10)
                        if raw_list:
                            raw = raw_list[0]
                            out["desc"] = str(_first(raw, ["desc", "description", "itemDesc"]) or "")
                except Exception:
                    pass

            # 卖家主页
            sid = item["seller"].get("id")
            if sid and time.time() < deadline:
                captured.clear()
                try:
                    page.goto(f"{BASE}/personal_page?id={sid}", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                    self._parse_seller(page, captured, item)
                except Exception:
                    pass
        finally:
            page.close()
        return out

    @staticmethod
    def _parse_seller(page, captured: list[dict], item: dict) -> None:
        seller = item["seller"]
        # 在售商品数与类目
        raw_items: list[dict] = []
        for cap in captured:
            _walk(cap["json"], _is_search_item, raw_items, limit=200)
        if raw_items:
            seller["item_count"] = len(raw_items)
            cats = []
            for r in raw_items:
                c = _first(r, ["category", "catName", "categoryName"])
                if c:
                    cats.append(str(c))
            seller["categories"] = cats or None
            if not seller.get("nick"):
                for r in raw_items[:3]:
                    n = _first(r, ["userNick", "sellerNick", "nick", "userName"])
                    if n:
                        seller["nick"] = str(n)
                        break
        # 最近上线时间：从页面文本解析「x天前来过 / 今天来过 / x小时前」
        try:
            text = page.inner_text("body")
            m = re.search(r"(\d+)\s*天前\s*来过", text)
            if m:
                seller["last_online_days"] = float(m.group(1))
            elif re.search(r"今天来过|昨天来过|小时前|分钟前|刚刚", text):
                seller["last_online_days"] = 0.0
            else:
                m2 = re.search(r"(\d+)\s*周前\s*来过", text)
                if m2:
                    seller["last_online_days"] = float(m2.group(1)) * 7
        except Exception:
            pass


session = XianyuSession()
