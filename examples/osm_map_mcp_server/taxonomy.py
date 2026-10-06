"""Small, deterministic query vocabulary for common OSM categories."""

from __future__ import annotations


CATEGORY_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("amenity", "restaurant"): ("餐厅", "餐馆", "饭店", "restaurant"),
    ("amenity", "cafe"): ("咖啡", "咖啡馆", "cafe", "coffee"),
    ("amenity", "fast_food"): ("快餐", "fast food"),
    ("amenity", "hospital"): ("医院", "hospital"),
    ("amenity", "clinic"): ("诊所", "clinic"),
    ("amenity", "pharmacy"): ("药店", "药房", "pharmacy"),
    ("amenity", "school"): ("学校", "school"),
    ("amenity", "kindergarten"): ("幼儿园", "kindergarten"),
    ("amenity", "university"): ("大学", "高校", "university"),
    ("amenity", "college"): ("学院", "college"),
    ("amenity", "bank"): ("银行", "bank"),
    ("amenity", "atm"): ("取款机", "ATM", "atm"),
    ("amenity", "fuel"): ("加油站", "fuel", "gas station"),
    ("amenity", "charging_station"): ("充电站", "充电桩", "charging"),
    ("amenity", "parking"): ("停车场", "parking"),
    ("amenity", "toilets"): ("厕所", "洗手间", "toilets"),
    ("amenity", "police"): ("派出所", "公安", "police"),
    ("amenity", "post_office"): ("邮局", "邮政", "post office"),
    ("shop", "convenience"): ("便利店", "便利", "convenience"),
    ("shop", "greengrocer"): (
        "水果店",
        "水果",
        "果蔬",
        "果蔬店",
        "生鲜",
        "greengrocer",
    ),
    ("shop", "supermarket"): ("超市", "supermarket"),
    ("shop", "mall"): ("商场", "购物中心", "mall"),
    ("shop", "bakery"): ("面包店", "烘焙", "bakery"),
    ("shop", "books"): ("书店", "books"),
    ("tourism", "hotel"): ("酒店", "宾馆", "hotel"),
    ("tourism", "attraction"): ("景点", "旅游景点", "attraction"),
    ("tourism", "museum"): ("博物馆", "museum"),
    ("leisure", "park"): ("公园", "park"),
    ("leisure", "sports_centre"): ("体育馆", "体育中心", "sports centre"),
    ("public_transport", "station"): ("车站", "公交站", "station"),
    ("railway", "station"): ("火车站", "地铁站", "railway station"),
    ("aeroway", "aerodrome"): ("机场", "airport"),
}


def category_terms(category: str, subcategory: str) -> tuple[str, ...]:
    """Return stable bilingual search terms for one OSM category."""

    return CATEGORY_ALIASES.get((category, subcategory), ())


def category_inventory() -> list[dict[str, object]]:
    """Return the public category vocabulary in a JSON friendly shape."""

    return [
        {
            "category": category,
            "subcategory": subcategory,
            "aliases": list(aliases),
        }
        for (category, subcategory), aliases in sorted(CATEGORY_ALIASES.items())
    ]
