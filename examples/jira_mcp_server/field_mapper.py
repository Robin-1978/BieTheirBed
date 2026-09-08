"""Dynamic Jira field metadata mapping, noise filtering and domain signal extraction."""

from __future__ import annotations

import re
from typing import Any

BUILTIN_FIELD_ALIASES: dict[str, str] = {
    "customfield_10708": "机器SN",
    "customfield_10602": "客户名称",
    "customfield_10707": "归属产品",
    "customfield_10720": "客户所在区域",
    "customfield_10702": "上位机固件版本",
    "customfield_10905": "责任部门",
    "customfield_12300": "合同客户",
    "customfield_12403": "客户组等级",
    "customfield_12404": "问题等级",
    "customfield_13182": "发生时间",
    "customfield_13010": "一级故障现象分类",
    "customfield_13031": "云平台事件编码",
    "customfield_13710": "缺陷一级分类",
    "customfield_11118": "现场临时处置方案",
    "customfield_13572": "技改发布关联",
}

NAME_ALIASES: dict[str, str] = {
    "产品ID": "机器SN",
}

NOISE_VALUES = frozenset(
    {
        "",
        "{}",
        "null",
        "None",
        "待填写",
        "待确认",
        "默认值",
        "否",
        "不涉及",
        "不需要盲测",
        "常规机器",
        "无",
        "未驳回过",
        "未闭环",
        "原因不明",
        "待办",
        "待检查",
        "待二线填写",
        "待漏出部门复核确认",
        "维持",
        "0",
        "-",
    }
)

_DEFAULT_VALUE_PATTERN = re.compile(
    r"^(非\w+|不需要\w+|待\w+|未\w+|待确认[,，\s]*待确认)$"
)

ELEVATOR_KEYWORDS = ["梯控", "乘梯", "电梯", "lora"]
MOTION_PLAN_PATTERNS = [
    r"pnc",
    r"运动规划",
    r"轨迹",
    r"路径",
    r"避障",
    r"绕障",
    r"走s弯",
]
MISSING_DATA_PATTERNS = [
    r"补充数据",
    r"请补充",
    r"数据一直请求中",
    r"检查机器网络",
    r"补日志",
    r"提供日志",
]
ESCALATION_PATTERNS = [
    r"转pnc",
    r"请pnc",
    r"转研发",
    r"请研发",
    r"转开发",
    r"请开发",
    r"转感知",
    r"转算法",
    r"升级",
    r"请.+看下",
]


def resolve_field_display_name(
    field_id: str, field_names: dict[str, str] | None = None
) -> str:
    """Resolve field ID to business human-readable name."""
    if field_id in BUILTIN_FIELD_ALIASES:
        return BUILTIN_FIELD_ALIASES[field_id]
    if field_names and field_id in field_names:
        raw_name = field_names[field_id]
        return NAME_ALIASES.get(raw_name, raw_name)
    return field_id


def is_noise_value(value: Any) -> bool:
    """Check whether a field value is meaningless noise or placeholder."""
    if value is None:
        return True
    if isinstance(value, (dict, list)) and not value:
        return True
    val_str = str(value).strip()
    if val_str in NOISE_VALUES:
        return True
    if _DEFAULT_VALUE_PATTERN.match(val_str):
        return True
    return False


def clean_custom_fields(
    raw_fields: dict[str, Any],
    field_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Map customfield_xxx to readable names and strip noise values."""
    cleaned: dict[str, Any] = {}
    for key, value in raw_fields.items():
        if not key.startswith("customfield_"):
            continue
        if is_noise_value(value):
            continue
        display_name = resolve_field_display_name(key, field_names)
        if isinstance(value, dict) and "value" in value:
            cleaned[display_name] = value["value"]
        elif isinstance(value, dict) and "name" in value:
            cleaned[display_name] = value["name"]
        elif isinstance(value, list):
            items = []
            for item in value:
                if isinstance(item, dict) and "value" in item:
                    items.append(item["value"])
                elif isinstance(item, dict) and "name" in item:
                    items.append(item["name"])
                elif isinstance(item, str) and not is_noise_value(item):
                    items.append(item)
            if items:
                cleaned[display_name] = items
        else:
            cleaned[display_name] = value
    return cleaned


def extract_domain_signals(texts: list[str]) -> dict[str, bool]:
    """Extract industrial robotics business signals from issue texts."""
    combined = " ".join(texts).lower()
    return {
        "elevator_related": any(kw in combined for kw in ELEVATOR_KEYWORDS),
        "motion_planning_related": any(
            re.search(pat, combined) is not None for pat in MOTION_PLAN_PATTERNS
        ),
        "missing_data": any(
            re.search(pat, combined) is not None for pat in MISSING_DATA_PATTERNS
        ),
        "escalation_requested": any(
            re.search(pat, combined) is not None for pat in ESCALATION_PATTERNS
        ),
    }
