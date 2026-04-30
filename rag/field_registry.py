"""
字段注册表 (Field Registry)
===========================
每次写入数据时注册 metadata 字段名，查询时读取完整字段清单。
确保 LLM 能"看到"所有可过滤字段，避免 limit 采样遗漏。

存储后端：
- 默认: JSON 文件持久化
- 可选: Redis（通过 FIELD_REGISTRY_BACKEND 环境变量切换）
"""
from rag.milvus_schema import FIXED_FILTERABLE_FIELDS
import os
import sys
import json
from typing import Dict
from threading import Lock
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==============================================================================
# 配置
# ==============================================================================
REGISTRY_BACKEND = os.getenv(
    "FIELD_REGISTRY_BACKEND", "json").lower()  # "json" or "redis"
REGISTRY_JSON_PATH = os.getenv(
    "FIELD_REGISTRY_PATH",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "field_registry.json")
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_KEY = "autoweb:field_registry"


# ==============================================================================
# JSON 后端
# ==============================================================================
class JsonFieldRegistry:
    """基于 JSON 文件的字段注册表"""

    def __init__(self, path: str = REGISTRY_JSON_PATH):
        self._path = path
        self._lock = Lock()
        self._data: Dict = self._load()

    def _load(self) -> Dict:
        """从文件加载"""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"dynamic_fields": {}}

    def _save(self):
        """持久化到文件"""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _infer_type(value) -> str:
        """根据值推断字段类型"""
        if isinstance(value, (int, float)):
            return "number"
        return "string"

    def register(self, fields):
        """
        注册字段（跳过固定字段）

        Args:
            fields: {field_name: sample_value} 字典，或 [field_name, ...] 列表（兼容旧接口）
        """
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            changed = False

            # 兼容旧接口：列表 → 字典
            if isinstance(fields, list):
                fields = {name: "" for name in fields}

            for name, value in fields.items():
                # 跳过固定字段和内部字段
                if name in FIXED_FILTERABLE_FIELDS or name in ("text", "pk", "vector"):
                    continue

                inferred_type = self._infer_type(value)

                if name not in self._data["dynamic_fields"]:
                    self._data["dynamic_fields"][name] = {
                        "first_seen": today,
                        "count": 1,
                        "type": inferred_type
                    }
                    changed = True
                else:
                    self._data["dynamic_fields"][name]["count"] += 1
                    # 如果之前是 string 但新值是 number，升级类型
                    if inferred_type == "number":
                        self._data["dynamic_fields"][name]["type"] = "number"
                    changed = True

            if changed:
                self._save()

    def get_all_fields(self) -> Dict:
        """
        返回所有可过滤字段

        Returns:
            {
                "fixed_fields": ["source", "title", ...],
                "dynamic_fields": {"director": {"first_seen": ..., "count": ...}, ...}
            }
        """
        return {
            "fixed_fields": list(FIXED_FILTERABLE_FIELDS),
            "dynamic_fields": dict(self._data.get("dynamic_fields", {}))
        }


# ==============================================================================
# Redis 后端
# ==============================================================================
class RedisFieldRegistry:
    """基于 Redis 的字段注册表"""

    def __init__(self, redis_url: str = REDIS_URL, key: str = REDIS_KEY):
        self._key = key
        self._redis = None
        self._redis_url = redis_url

    def _get_redis(self):
        if self._redis is None:
            import redis
            self._redis = redis.from_url(
                self._redis_url, decode_responses=True)
        return self._redis

    def register(self, fields):
        """
        注册字段到 Redis Hash

        Args:
            fields: {field_name: sample_value} 字典，或 [field_name, ...] 列表（兼容旧接口）
        """
        r = self._get_redis()
        today = datetime.now().strftime("%Y-%m-%d")

        if isinstance(fields, list):
            fields = {name: "" for name in fields}

        for name, value in fields.items():
            if name in FIXED_FILTERABLE_FIELDS or name in ("text", "pk", "vector"):
                continue

            inferred_type = JsonFieldRegistry._infer_type(value)

            existing = r.hget(self._key, name)
            if existing:
                data = json.loads(existing)
                data["count"] += 1
                if inferred_type == "number":
                    data["type"] = "number"
            else:
                data = {"first_seen": today, "count": 1, "type": inferred_type}

            r.hset(self._key, name, json.dumps(data, ensure_ascii=False))

    def get_all_fields(self) -> Dict:
        """返回所有可过滤字段"""
        r = self._get_redis()
        raw = r.hgetall(self._key)

        dynamic = {}
        for name, val in raw.items():
            try:
                dynamic[name] = json.loads(val)
            except json.JSONDecodeError:
                dynamic[name] = {"first_seen": "unknown", "count": 0}

        return {
            "fixed_fields": list(FIXED_FILTERABLE_FIELDS),
            "dynamic_fields": dynamic
        }


# ==============================================================================
# 工厂函数 + 全局单例
# ==============================================================================
def _create_registry():
    """根据环境变量选择后端"""
    if REGISTRY_BACKEND == "redis":
        print("📋 [FieldRegistry] 使用 Redis 后端")
        return RedisFieldRegistry()
    else:
        print(f"📋 [FieldRegistry] 使用 JSON 后端: {REGISTRY_JSON_PATH}")
        return JsonFieldRegistry()


# 全局单例
field_registry = _create_registry()


def register_fields(fields):
    """
    注册字段（便捷函数）

    Args:
        fields: {field_name: sample_value} 字典，或 [field_name, ...] 列表
    """
    field_registry.register(fields)


def get_all_filterable_fields() -> Dict:
    """获取所有可过滤字段（便捷函数）"""
    return field_registry.get_all_fields()


def format_fields_for_prompt() -> str:
    """
    将字段清单格式化为可注入 LLM Prompt 的文本

    Returns:
        类似：
        固定字段（高频，已建索引）：source, title, category, data_type, platform, crawled_at
        动态字段（低频）：director (出现 45 次), rating (出现 120 次), ...
    """
    fields = get_all_filterable_fields()

    lines = []
    lines.append(f"固定字段（高频，已建索引）：{', '.join(fields['fixed_fields'])}")

    if fields["dynamic_fields"]:
        dynamic_parts = []
        for name, info in sorted(fields["dynamic_fields"].items(), key=lambda x: x[1].get("count", 0), reverse=True):
            field_type = info.get("type", "string")
            type_label = "数值" if field_type == "number" else "文本"
            dynamic_parts.append(
                f"{name} ({type_label}, 出现 {info.get('count', 0)} 次)")
        lines.append(f"动态字段（低频）：{', '.join(dynamic_parts)}")
    else:
        lines.append("动态字段：暂无")

    return "\n".join(lines)
