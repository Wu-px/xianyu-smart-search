"""演示数据（降级模式）。

闲鱼反爬较严，真实抓取失败时自动切换到这里的仿真数据，保证演示一定能跑通。
数据由关键词确定性生成（同样的关键词结果稳定），字段结构与真实抓取完全一致，
覆盖需求里的全部典型场景：

- 个人卖家 / 疑似商家（主页 200 件同品类）
- 全新 / 99新 / 95新 / 9成新 / 8成新 / 战损
- 明显低于 / 持平 / 明显高于同款均价
- 需私聊问价
- 同区自提 / 同城自提 / 跨城自提 / 普通快递
- 刚上线 / 3 天前 / 8 天前 / 15 天前（超过 10 天 → 不推荐）
- 文案详细多图 / 文案简略无图
"""
from __future__ import annotations

import hashlib
import random

GOOFISH_SEARCH = "https://www.goofish.com/search?q={kw}"


def _rng(keyword: str) -> random.Random:
    seed = int(hashlib.md5(keyword.encode("utf-8")).hexdigest()[:8], 16)
    return random.Random(seed)


def _img(seed: str) -> str:
    return f"https://picsum.photos/seed/{seed}/400/400"


def _seller(rng: random.Random, nick: str, good: int, bad: int,
            count: int, cats: list[str] | None, days: float | None) -> dict:
    return {"id": f"u{abs(hash(nick)) % 10**8}", "nick": nick,
            "good_count": good, "bad_count": bad,
            "item_count": count, "categories": cats, "last_online_days": days}


def generate(keyword: str, city: str, district: str) -> list[dict]:
    rng = _rng(keyword)
    kw = keyword
    base = rng.choice([199, 299, 399, 499, 599, 899, 1299])
    mock_id = abs(hash(keyword)) % 10000
    img = lambda s: _img(f"xy{mock_id}{s}")

    items = [
        {   # 1. 全能优质：个人卖家 + 99新 + 低于均价 + 详细文案多图
            "item_id": f"m{mock_id}1", "title": f"{kw} 个人自用 99新 箱说全",
            "price": round(base * 0.82, 0), "desc":
                f"个人闲置，{kw} 购入后使用极少，一直贴膜戴套，无拆无修无暗病。"
                "箱说配件齐全，支持当面验货。因换新出给有需要的朋友，"
                "图片全部实拍，细节都在图上，指哪拍哪，售出非质量问题不退。",
            "image_count": 9, "image_url": img("a"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "闲置小达人", 86, 1, 9, ["数码", "家居", "服饰"], 0.2),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 2. 商家低价（疑假/翻新风险）
            "item_id": f"m{mock_id}2", "title": f"全新未拆封 {kw} 厂家直销 量大从优",
            "price": round(base * 0.75, 0), "desc":
                f"全新未拆封 {kw}，本店专营，批发零售均可，好评如潮，当天发货，全国包邮。",
            "image_count": 3, "image_url": img("b"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "品牌优选店", 1200, 18, 230, ["数码"], 0.0),
            "pickup": {"is_pickup": False, "location_text": None}, "area": rng.choice(["深圳", "广州"]),
        },
        {   # 3. 高于均价 + 8成新
            "item_id": f"m{mock_id}3", "title": f"{kw} 8成新 正常使用痕迹",
            "price": round(base * 1.35, 0), "desc":
                f"{kw} 用了一年多，功能一切正常，边角有正常使用痕迹，介意勿拍。",
            "image_count": 2, "image_url": img("c"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "断舍离中", 23, 0, 12, ["数码", "图书", "运动"], 1.5),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 4. 私聊问价
            "item_id": f"m{mock_id}4", "title": f"{kw} 成色极好 价格私聊",
            "price": None, "desc":
                f"{kw} 成色极好，用得很少，价格私聊问价，合适就出，记者勿扰。",
            "image_count": 5, "image_url": img("d"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "随缘出", 12, 0, 6, ["数码", "母婴"], 2.0),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 5. 同区自提 + 95新
            "item_id": f"m{mock_id}5", "title": f"{kw} 95新 仅限{city}{district}自提",
            "price": round(base * 0.95, 0), "desc":
                f"{kw} 95新，购买半年使用不多。体积较大不邮寄，仅限{city}{district}附近自提，"
                "当面验货当面交易，放心省心。",
            "image_count": 7, "image_url": img("e"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "搬家清物", 45, 2, 15, ["家居", "数码", "厨具"], 0.5),
            "pickup": {"is_pickup": True, "location_text": f"{city}{district}"}, "area": city,
        },
        {   # 6. 同城自提
            "item_id": f"m{mock_id}6", "title": f"{kw} 9成新 {city}自提",
            "price": round(base * 1.05, 0), "desc":
                f"{kw} 9成新，功能完好，{city}同城自提优先，也可协商快递。",
            "image_count": 4, "image_url": img("f"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, f"{city}老王", 67, 1, 21, None, 3.0),
            "pickup": {"is_pickup": True, "location_text": city}, "area": city,
        },
        {   # 7. 跨城自提（交通成本警示）
            "item_id": f"m{mock_id}7", "title": f"{kw} 全新仅拆封 外地自提",
            "price": round(base * 0.88, 0), "desc":
                f"{kw} 全新仅拆封验机，大件物流贵且易损，仅支持武汉自提。",
            "image_count": 6, "image_url": img("g"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "武汉出闲置", 34, 0, 11, ["数码", "家居"], 0.8),
            "pickup": {"is_pickup": True, "location_text": "武汉"}, "area": "武汉",
        },
        {   # 8. 超过 10 天未上线
            "item_id": f"m{mock_id}8", "title": f"{kw} 95新 闲置转让",
            "price": round(base * 0.9, 0), "desc":
                f"{kw} 95新，无拆无修，配件齐全，诚心要可小刀。",
            "image_count": 5, "image_url": img("h"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "很久没上了", 56, 3, 18, None, 15.0),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 9. 差评多
            "item_id": f"m{mock_id}9", "title": f"{kw} 便宜出",
            "price": round(base * 0.7, 0), "desc": f"{kw} 便宜出，看图，不退不换。",
            "image_count": 1, "image_url": img("i"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "阿强数码", 40, 9, 45, ["数码"], 0.1),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 10. 战损成色
            "item_id": f"m{mock_id}10", "title": f"{kw} 战损成色 能正常使用",
            "price": round(base * 0.4, 0), "desc":
                f"{kw} 外观战损，屏幕有划痕，功能正常无暗病，当配件价出，会折腾的来。",
            "image_count": 8, "image_url": img("j"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "垃圾佬之家", 210, 4, 30, ["数码", "配件", "工具"], 0.3),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 11. 未标注品相、文案简略无图
            "item_id": f"m{mock_id}11", "title": f"{kw} 出",
            "price": round(base * 0.98, 0), "desc": f"出{kw}，私聊。",
            "image_count": 0, "image_url": None,
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "神秘卖家", None, None, None, None, None),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
        {   # 12. 另一件同款（拉低均价样本，价格适中）
            "item_id": f"m{mock_id}12", "title": f"{kw} 个人闲置 箱说全 95新",
            "price": round(base * 1.0, 0), "desc":
                f"{kw} 购入一年，使用爱惜，95新，箱说全，无拆修。同城可自提，外地发顺丰到付。",
            "image_count": 6, "image_url": img("k"),
            "url": GOOFISH_SEARCH.format(kw=kw),
            "seller": _seller(rng, "理性剁手党", 156, 2, 14, ["数码", "家电"], 1.0),
            "pickup": {"is_pickup": False, "location_text": None}, "area": city,
        },
    ]
    # 轻微扰动让不同关键词的结果价格分布略有差异
    for it in items:
        if it["price"] is not None:
            it["price"] = max(9.0, round(it["price"] * rng.uniform(0.97, 1.03), 0))
    return items
