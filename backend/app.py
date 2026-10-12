"""FastAPI 入口：提供静态页面与 REST API。

API 一览：
    GET  /api/status          浏览器会话与登录状态
    POST /api/login/start     打开有头浏览器访问闲鱼（用户手动登录）
    POST /api/login/stop      关闭浏览器
    POST /api/search          搜索 + 评分 + 排序（真实抓取失败自动回退演示数据）
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import mock_data, scraper
from .scorer import score_items, sort_items

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("xianyu")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="闲鱼智能搜索助手", docs_url=None, redoc_url=None)
executor = ThreadPoolExecutor(max_workers=1)  # 浏览器操作全局串行


class SearchReq(BaseModel):
    keyword: str
    city: str = ""
    district: str = ""


# ---------------------------------------------------------------- API

@app.get("/api/status")
def api_status():
    try:
        return executor.submit(scraper.session.login_status).result(timeout=45)
    except Exception:
        return {"running": scraper.session.is_running(), "logged_in": False}


@app.post("/api/login/start")
def api_login_start():
    headless = os.environ.get("XIANYU_HEADLESS", "") == "1"
    try:
        return executor.submit(scraper.session.start, headless).result(timeout=90)
    except scraper.ScrapeError as exc:
        raise HTTPException(500, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"浏览器启动失败：{exc}")


@app.post("/api/login/stop")
def api_login_stop():
    return executor.submit(scraper.session.stop).result(timeout=30)


@app.post("/api/search")
def api_search(req: SearchReq):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(400, "关键词不能为空")
    city, district = req.city.strip(), req.district.strip()

    source, note = "xianyu", None
    try:
        raw = executor.submit(scraper.session.search, keyword).result(
            timeout=scraper.SEARCH_BUDGET_SECONDS + 30)
        if not raw:
            raise scraper.ScrapeError("未获取到任何商品")
    except scraper.ScrapeError as exc:
        log.warning("真实抓取失败，回退演示数据：%s", exc)
        source, note = "demo", str(exc)
        raw = mock_data.generate(keyword, city, district)
    except Exception as exc:
        log.warning("真实抓取异常，回退演示数据：%s", exc)
        source, note = "demo", f"真实抓取失败：{exc}"
        raw = mock_data.generate(keyword, city, district)

    scored = score_items(raw, city, district)
    # 求购/收购帖：对方是买家，价格无比较意义，整体排除（仅计数提示）
    excluded_wtb = sum(1 for it in scored if it["flags"].get("want_to_buy"))
    scored = [it for it in scored if not it["flags"].get("want_to_buy")]
    result = sort_items(scored, "value")
    return {"source": source, "note": note, "keyword": keyword,
            "city": city, "district": district, "excluded_wtb": excluded_wtb,
            "items": result}


# ---------------------------------------------------------------- 静态页面（挂到最后，避免挡住 /api）

if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
