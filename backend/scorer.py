"""评分与筛选模块。

把归一化的商品数据按用户定的规则打分（百分制），再折算成五星推荐，
并生成中文推荐理由。规则全部来自需求确认：

- 同款商品价格横向比较（明显偏高的降权）
- 品相完好程度（从文案关键词解析：全新/99新/95新/…/瑕疵暗病）
- 卖家信誉（好评率 + 评价数量；网页版拿不到评价数时用芝麻信用 + 已售数代理）
- 个人卖家优先（主页商品少、类目杂 = 个人闲置；已售数量大 / 专卖同一类目 = 商家降权）
- 文案质量（文案越长、图片越多越加分）
- 需私聊问价的商品：单独标记，价格不计入比价
- 自提商品：从文案提取位置，按同城/同区粗估距离与运费
- 卖家超过 10 天未上线：不淘汰，但标记「不推荐」并固定排在最后

权重（百分制）：
    价格 30 / 品相 20 / 信誉 15 / 个人卖家 10 / 文案 10 / 自提距离 10 / 上线时间 5
"""
from __future__ import annotations

import difflib
import re

# ------------------------------------------------------------------ 权重
W_PRICE = 30
W_CONDITION = 20
W_REPUTATION = 15
W_PERSONAL = 10
W_DESC = 10
W_PICKUP = 10
W_ONLINE = 5

STALE_DAYS = 10  # 超过 N 天未上线 → 标记不推荐

# 品相关键词 → (分数, 中文标签)
CONDITION_RULES = [
    (r"全新|仅拆封|未拆封|未使用|未开封|未穿|未戴", 1.00, "全新"),
    (r"99新|九九新|充新|媲美全新", 0.95, "99新"),
    (r"95新|九五新", 0.90, "95新"),
    (r"9成新|九成新|9新", 0.80, "9成新"),
    (r"85新|八五新|8成新|八成新|8新", 0.65, "8成新"),
    (r"战损|伊拉克|成色差|有瑕疵|瑕疵|暗病|维修过|拆修|进水|摔过|磕碰严重|有问题", 0.30, "成色差"),
]

ASK_PRICE_RE = re.compile(r"私聊问价|问价私|价格私|询价|详询|价格面议|面议|带价来|有意私|价私")

SHIPPING_NOTE = "快递发货，运费以下单页为准"


# ------------------------------------------------------------------ 工具

def parse_condition(title: str, desc: str) -> tuple[float, str, bool]:
    """返回 (品相分 0~1, 中文标签, 是否从文案明确识别)。"""
    text = f"{title} {desc}"
    for pattern, score, label in CONDITION_RULES:
        if re.search(pattern, text):
            return score, label, True
    return 0.60, "未标注品相", False


def detect_ask_price(price: float | None, desc: str, title: str) -> bool:
    if price is None:
        return True
    return bool(ASK_PRICE_RE.search(f"{title} {desc}"))


def estimate_pickup(pickup: dict, city: str, district: str) -> dict:
    """自提距离粗估：同区 ~3km，同城 ~15km，跨城 ~40km。"""
    info = {"distance_km": None, "note": None, "fee_note": SHIPPING_NOTE}
    if not pickup.get("is_pickup"):
        return info
    text = pickup.get("location_text") or ""
    if district and district in text:
        info["distance_km"] = 3.0
        info["note"] = f"自提 · 同区（{district}）"
        info["fee_note"] = "上门自提，免运费"
    elif city and city in text:
        info["distance_km"] = 15.0
        info["note"] = f"自提 · 同城（{city}）"
        info["fee_note"] = "同城自提，需考虑往返交通成本"
    else:
        info["distance_km"] = 40.0
        info["note"] = "自提 · 跨城（自提位置较远）"
        info["fee_note"] = "自提位置较远，交通成本可能超过运费"
    return info


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def cluster_prices(items: list[dict]) -> dict[int, list[float]]:
    """把有实际价格的商品按标题相似度聚成「同款」组，返回 下标→组内价格列表。"""
    priced = [(i, it) for i, it in enumerate(items) if it.get("price")]
    clusters: list[list[int]] = []
    for i, it in priced:
        placed = False
        for cl in clusters:
            if any(_similar(it["title"], items[j]["title"]) > 0.55 for j in cl):
                cl.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    mapping: dict[int, list[float]] = {}
    for cl in clusters:
        prices = [items[j]["price"] for j in cl]
        for j in cl:
            mapping[j] = prices
    return mapping


# ------------------------------------------------------------------ 主入口

def score_items(items: list[dict], city: str, district: str) -> list[dict]:
    for idx, it in enumerate(items):
        it.setdefault("seller", {})
        it.setdefault("pickup", {"is_pickup": False, "location_text": None})
        it["flags"] = {}
        it["reasons"] = []

    price_map = cluster_prices(items)

    for idx, it in enumerate(items):
        seller = it["seller"]
        title, desc = it.get("title", ""), it.get("desc", "")
        price = it.get("price")
        reasons: list[dict] = []
        flags = it["flags"]

        # ---- 私聊问价 ----
        ask = detect_ask_price(price, desc, title)
        flags["ask_price"] = ask

        # ---- 品相 ----
        c_score, c_label, c_explicit = parse_condition(title, desc)
        flags["condition"] = c_label
        flags["condition_score"] = c_score
        flags["condition_explicit"] = c_explicit
        if c_score >= 0.9:
            reasons.append({"type": "plus", "text": f"品相优秀（{c_label}）"})
        elif c_score <= 0.35:
            reasons.append({"type": "minus", "text": f"品相较差（{c_label}），需仔细验货"})
        elif not c_explicit:
            reasons.append({"type": "note", "text": "卖家未标注品相，建议私聊确认"})

        # ---- 价格（同款横向比较） ----
        if ask:
            p_score = 0.0
            reasons.append({"type": "note", "text": "需私聊问价，未计入比价"})
        else:
            group = price_map.get(idx, [price])
            if len(group) >= 2:
                med = sorted(group)[len(group) // 2]
                ratio = price / med if med else 1.0
                if ratio <= 0.8:
                    p_score, msg = 1.0, f"价格 ¥{price:g}，明显低于同款均价（¥{med:g}）"
                    reasons.append({"type": "plus", "text": msg})
                elif ratio <= 1.0:
                    p_score = 0.9
                    reasons.append({"type": "plus", "text": f"价格 ¥{price:g}，不高于同款均价（¥{med:g}）"})
                elif ratio <= 1.2:
                    p_score = 0.6
                    reasons.append({"type": "minus", "text": f"价格 ¥{price:g}，略高于同款均价（¥{med:g}）"})
                else:
                    p_score = 0.3
                    reasons.append({"type": "minus", "text": f"价格 ¥{price:g}，明显高于同款均价（¥{med:g}）"})
            else:
                p_score = 0.7
                reasons.append({"type": "note", "text": "同款样本少，价格仅供参考"})

        # ---- 卖家信誉 ----
        good, bad = seller.get("good_count"), seller.get("bad_count")
        if good is not None and bad is not None and (good + bad) > 0:
            rate = good / (good + bad)
            import math
            volume = min(1.0, 0.5 + math.log10(good + 1) / 2.5)
            r_score = rate * volume
            pct = f"{rate * 100:.0f}%"
            if rate >= 0.98 and good >= 10:
                reasons.append({"type": "plus", "text": f"卖家好评率 {pct}（{good} 个好评）"})
            elif rate < 0.9:
                reasons.append({"type": "minus", "text": f"卖家好评率仅 {pct}（差评 {bad} 条），谨慎交易"})
            else:
                reasons.append({"type": "note", "text": f"卖家好评率 {pct}"})
        else:
            # 网页版拿不到好差评数时，用芝麻信用认证 + 已售出数量做信誉代理
            zhima = seller.get("zhima_auth")
            sold = seller.get("sold_count") or 0
            if zhima:
                r_score = min(0.9, 0.7 + sold / 200)
                reasons.append({"type": "plus", "text": f"芝麻信用认证，已售出 {sold} 件" +
                                (f"，24h 回复率 {seller['reply_ratio']}" if seller.get("reply_ratio") else "")})
            else:
                r_score = 0.6
                reasons.append({"type": "note", "text": "卖家评价数据不可见，建议先看主页评价"})

        # ---- 个人卖家 or 商家 ----
        count = seller.get("item_count")
        cats = seller.get("categories")
        sold = seller.get("sold_count") or 0
        diversity = len(set(cats)) if cats else None
        div_ok = diversity is not None
        # 已售数量是最强的商家信号（sellerItems 只返回第一页，在售数会低估）
        if sold >= 50 or (count is not None and ((count >= 50 and (not cats or diversity <= 2))
                                                   or (count >= 30 and div_ok and diversity <= 1))):
            per_score = 0.2
            flags["merchant"] = True
            if sold >= 100:
                reasons.append({"type": "minus", "text": f"已售出 {sold} 件，疑似职业商家"})
            elif sold >= 50:
                reasons.append({"type": "minus", "text": f"已售出 {sold} 件，偏向商家"})
            else:
                reasons.append({"type": "minus", "text": f"主页在售 {count} 件且品类单一，疑似职业商家"})
        elif count is not None and (count <= 15 or (div_ok and diversity >= 3)):
            per_score = 1.0
            flags["personal"] = True
            reasons.append({"type": "plus", "text": f"个人卖家（主页在售 {count} 件，多为个人闲置）"})
        elif count is None:
            per_score = 0.6
            reasons.append({"type": "note", "text": "无法判断卖家类型"})
        else:
            per_score = 0.6
            flags["maybe_merchant"] = True
            reasons.append({"type": "note", "text": f"卖家在售 {count} 件，可能半职业"})

        # ---- 文案质量 ----
        d_len = len(desc or "")
        img_n = it.get("image_count") or 0
        d_part = 1.0 if d_len >= 150 else 0.8 if d_len >= 80 else 0.5 if d_len >= 30 else 0.3
        i_part = 1.0 if img_n >= 6 else 0.8 if img_n >= 3 else 0.5 if img_n >= 1 else 0.2
        desc_score = (d_part + i_part) / 2
        if desc_score >= 0.9:
            reasons.append({"type": "plus", "text": f"介绍详细（{d_len} 字 / {img_n} 图）"})
        elif img_n == 0:
            reasons.append({"type": "minus", "text": "无实物图片，真实性存疑"})

        # ---- 自提 / 距离 ----
        pickup_info = estimate_pickup(it["pickup"], city, district)
        flags["pickup"] = pickup_info
        if it["pickup"].get("is_pickup"):
            d = pickup_info["distance_km"]
            if d is not None and d <= 5:
                pk_score = 1.0
                reasons.append({"type": "plus", "text": pickup_info["fee_note"]})
            elif d is not None and d <= 20:
                pk_score = 0.6
                reasons.append({"type": "note", "text": pickup_info["fee_note"]})
            else:
                pk_score = 0.2
                reasons.append({"type": "minus", "text": pickup_info["fee_note"]})
        else:
            # 普通快递：有运费数据时按费用高低微调
            fee = it.get("shipping_fee")
            if fee is None:
                pk_score = 0.9
            elif fee <= 0.5:
                pk_score = 1.0
                reasons.append({"type": "plus", "text": "卖家包邮"})
            elif fee <= 8:
                pk_score = 1.0
                reasons.append({"type": "plus", "text": f"运费约 ¥{fee:g}，较低"})
            elif fee <= 15:
                pk_score = 0.8
                reasons.append({"type": "note", "text": f"运费约 ¥{fee:g}"})
            else:
                pk_score = 0.6
                reasons.append({"type": "minus", "text": f"运费约 ¥{fee:g}，偏高"})

        # ---- 上线时间 ----
        days = seller.get("last_online_days")
        if days is None:
            o_score = 0.6
            reasons.append({"type": "note", "text": "上线时间未知"})
        elif days <= 1:
            o_score = 1.0
        elif days <= 3:
            o_score = 0.8
        elif days <= 7:
            o_score = 0.5
            reasons.append({"type": "note", "text": f"卖家 {days:.0f} 天前上线，回复可能较慢"})
        elif days <= STALE_DAYS:
            o_score = 0.3
            reasons.append({"type": "minus", "text": f"卖家 {days:.0f} 天前上线，可能已停售"})
        else:
            o_score = 0.0
            flags["stale"] = True
            reasons.append({"type": "minus", "text": f"卖家超过 {STALE_DAYS} 天未上线（{days:.0f} 天），标记为不推荐"})

        # ---- 汇总 ----
        total = (p_score * W_PRICE + c_score * W_CONDITION + r_score * W_REPUTATION
                 + per_score * W_PERSONAL + desc_score * W_DESC + pk_score * W_PICKUP
                 + o_score * W_ONLINE)
        it["score"] = round(total, 1)
        it["stars"] = max(0.5, round(total / 20 * 2) / 2)
        it["reasons"] = reasons

        # 排序键（用 10**12 代替 inf：JSON 不兼容 inf）
        it["sort_price"] = price if price is not None else 10 ** 12
        it["sort_distance"] = pickup_info["distance_km"] if pickup_info["distance_km"] is not None else 10 ** 6
        it["sort_condition"] = c_score
        it["sort_value"] = total

    return items


def sort_items(items: list[dict], mode: str) -> list[dict]:
    """排序：超过 10 天未上线的固定排在最后（用户明确要求的规则）。"""
    key_map = {
        "value": lambda it: -it["sort_value"],
        "price": lambda it: it["sort_price"],
        "distance": lambda it: it["sort_distance"],
        "condition": lambda it: -it["sort_condition"],
    }
    key = key_map.get(mode, key_map["value"])
    active = [it for it in items if not it["flags"].get("stale")]
    stale = [it for it in items if it["flags"].get("stale")]
    active.sort(key=key)
    stale.sort(key=key)
    return active + stale
