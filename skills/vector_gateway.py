import time
from datetime import datetime
from typing import Any, Callable, Iterable, List, Sequence, Tuple
from urllib.parse import urlparse

from pymilvus import connections

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.3


def parse_milvus_uri(uri: str) -> Tuple[str, str]:
    raw = (uri or "").strip()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "localhost"
    port = str(parsed.port or 19530)
    return host, port


def is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    msg = str(exc).lower()
    retryable_patterns = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "connection aborted",
        "unavailable",
        "rpc",
        "channel",
        "socket",
        "deadline exceeded",
    )
    non_retryable_patterns = (
        "schema",
        "field not found",
        "illegal",
        "invalid",
        "multiple values for argument",
        "dimension",
        "param error",
    )
    if any(key in msg for key in non_retryable_patterns):
        return False
    return any(key in msg for key in retryable_patterns)


def run_with_retry(
    operation: str,
    fn: Callable[[], Any],
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    tag: str = "VectorGateway",
) -> Any:
    last_exc: Exception | None = None
    for i in range(1, max(1, attempts) + 1):
        start = time.time()
        try:
            result = fn()
            cost_ms = int((time.time() - start) * 1000)
            if i > 1:
                print(f"✅ [{tag}] {operation} recovered on attempt {i}/{attempts} ({cost_ms}ms)")
            return result
        except Exception as exc:
            cost_ms = int((time.time() - start) * 1000)
            last_exc = exc
            retryable = is_retryable_error(exc)
            print(
                f"⚠️ [{tag}] {operation} failed attempt {i}/{attempts} "
                f"(retryable={retryable}, {cost_ms}ms): {exc}"
            )
            if (not retryable) or i >= attempts:
                break
            sleep_seconds = backoff_seconds * (3 ** (i - 1))
            time.sleep(sleep_seconds)
    assert last_exc is not None
    raise last_exc


def connect_milvus(uri: str, alias: str = "default", tag: str = "VectorGateway") -> None:
    host, port = parse_milvus_uri(uri)
    run_with_retry(
        operation=f"connect_milvus({host}:{port})",
        fn=lambda: connections.connect(alias=alias, host=host, port=port),
        tag=tag,
    )
    print(f"🔗 [{tag}] Milvus connected {host}:{port} (alias={alias})")


def normalize_weights(
    weights: Sequence[float],
    defaults: Sequence[float],
    tag: str = "VectorGateway",
) -> Tuple[float, ...]:
    safe = [max(0.0, float(w)) for w in weights]
    total = sum(safe)
    if total <= 0:
        return tuple(float(x) for x in defaults)
    if abs(total - 1.0) > 1e-6:
        print(f"⚠️ [{tag}] Weight sum={total:.4f}, auto-normalized")
    return tuple(w / total for w in safe)


def read_hit_field(hit: Any, field: str) -> Any:
    value = None
    try:
        value = hit.get(field)
    except Exception:
        pass
    if value is None and hasattr(hit, "entity") and hit.entity is not None:
        try:
            value = hit.entity.get(field)
        except Exception:
            pass
    return value


def hybrid_search(
    collection: Any,
    reqs: List[Any],
    rerank: Any,
    limit: int,
    output_fields: List[str],
    expr: str | None = None,
    tag: str = "VectorGateway",
) -> List[Any]:
    start = time.time()
    try:
        kwargs = {
            "reqs": reqs,
            "rerank": rerank,
            "limit": limit,
            "output_fields": output_fields,
        }
        if expr:
            kwargs["expr"] = expr
        res = run_with_retry(
            operation="hybrid_search",
            fn=lambda: collection.hybrid_search(**kwargs),
            tag=tag,
        )
        cost_ms = int((time.time() - start) * 1000)
        size = len(res[0]) if res else 0
        print(f"📈 [{tag}] hybrid_search done in {cost_ms}ms (hits={size}, limit={limit})")
        return res or []
    except Exception as exc:
        cost_ms = int((time.time() - start) * 1000)
        print(f"❌ [{tag}] hybrid_search failed in {cost_ms}ms: {exc}")
        raise


def insert_and_flush(collection: Any, data: List[Any], tag: str = "VectorGateway") -> None:
    run_with_retry(
        operation="collection.insert",
        fn=lambda: collection.insert(data),
        tag=tag,
    )
    run_with_retry(
        operation="collection.flush",
        fn=lambda: collection.flush(),
        tag=tag,
    )


def add_documents(vector_store: Any, docs: List[Any], tag: str = "VectorGateway") -> Any:
    return run_with_retry(
        operation="vector_store.add_documents",
        fn=lambda: vector_store.add_documents(docs),
        tag=tag,
    )


def filter_not_expired(
    hits: Iterable[Any],
    expire_field: str,
    now_dt: datetime,
    time_format: str = "%Y-%m-%dT%H:%M:%S",
    tag: str = "VectorGateway",
) -> List[Any]:
    kept: List[Any] = []
    dropped = 0
    for hit in hits:
        expire_at = read_hit_field(hit, expire_field) or ""
        try:
            exp_dt = datetime.strptime(expire_at, time_format)
            if exp_dt >= now_dt:
                kept.append(hit)
            else:
                dropped += 1
        except Exception:
            dropped += 1
    if dropped:
        print(f"⏭️ [{tag}] TTL filtered {dropped} expired/invalid hits")
    return kept
