"""数据结构定义。

商品统一用 dict 表示（从闲鱼抓取或演示数据两条来源归一化到同一结构），
评分模块只依赖这里的字段，不关心数据来源。
"""
from __future__ import annotations

# 商品字段约定：
# {
#   "item_id": str,
#   "title": str,
#   "price": float | None,          # None 表示需私聊问价
#   "desc": str,                    # 商品文案
#   "image_count": int,
#   "image_url": str | None,        # 主图
#   "url": str,                     # 闲鱼商品链接
#   "seller": {
#       "id": str | None,
#       "nick": str | None,
#       "good_count": int | None,   # 好评数
#       "bad_count": int | None,    # 差评数
#       "item_count": int | None,   # 主页在售商品数
#       "categories": [str] | None, # 主页商品类目（用于判断个人卖家）
#       "last_online_days": float | None,  # 最近上线距今天数
#   },
#   "pickup": {
#       "is_pickup": bool,          # 是否仅自提
#       "location_text": str | None # 文案中提取的自提位置
#   },
#   "area": str | None,             # 商品发布地
# }

SEARCH_REQUEST = {"keyword": str, "city": str, "district": str}
