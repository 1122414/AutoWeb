from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from config import (
    DPCLI_BATCH_TIMEOUT_SECONDS,
    DPCLI_CWD,
    DPCLI_HEADLESS,
    DPCLI_PYTHON,
    DPCLI_SESSION,
    DPCLI_TIMEOUT_SECONDS,
)
from skills.logger import logger, trace_log, save_dpcli_code_log


class DPCLIExecutor:
    """Controlled adapter around `python -m dp_cli`."""

    def __init__(
        self,
        session: str = DPCLI_SESSION,
        headless: bool = DPCLI_HEADLESS,
        python_executable: str = DPCLI_PYTHON,
        cwd: str = DPCLI_CWD,
        timeout_seconds: float = DPCLI_TIMEOUT_SECONDS,
        batch_timeout_seconds: float = DPCLI_BATCH_TIMEOUT_SECONDS,
    ) -> None:
        self.session = session
        self.headless = headless
        self.python_executable = python_executable
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.batch_timeout_seconds = batch_timeout_seconds
        self._active_request_id: Optional[str] = None
        trace_log(f"DPCLIExecutor 初始化: session={session}, headless={headless}")

    def open(self, url: str, wait_time: Optional[float] = None) -> Dict[str, Any]:
        return self._run("open", url, *self._wait_args(wait_time))

    def snapshot(
        self,
        mode: str = "agent_summary",
        ref: Optional[str] = None,
        depth: Optional[int] = None,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args: List[str] = ["snapshot"]
        if ref:
            args.append(ref)
        args.extend(["--mode", mode or "agent_summary"])
        if depth is not None:
            args.extend(["--depth", str(depth)])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def wait(self, seconds: float = 1.0) -> Dict[str, Any]:
        """Wait through dp_cli and return refreshed page evidence."""
        result = self.snapshot(mode="agent_summary", wait_time=max(0.0, float(seconds)))
        if isinstance(result, dict):
            result = dict(result)
            result["action"] = "wait"
        return result

    def find(
        self,
        text: Optional[str] = None,
        locator: Optional[str] = None,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = ["find"]
        if text:
            args.extend(["--text", text])
        if locator:
            args.extend(["--locator", locator])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def click(
        self,
        ref: Optional[str] = None,
        locator: Optional[str] = None,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = ["click"]
        if ref:
            args.extend(["--ref", ref])
        if locator:
            args.extend(["--locator", locator])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def type_text(
        self,
        text: str,
        ref: Optional[str] = None,
        locator: Optional[str] = None,
        submit: bool = False,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = ["type"]
        if ref:
            args.extend(["--ref", ref])
        if locator:
            args.extend(["--locator", locator])
        args.extend(["--text", text])
        if submit:
            args.append("--submit")
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def scroll(
        self,
        direction: str = "down",
        amount: int = 900,
        to: Optional[str] = None,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = [
            "scroll",
            "--direction",
            str(direction or "down"),
            "--amount",
            str(max(1, int(amount))),
        ]
        if to:
            args.extend(["--to", str(to)])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def expand(
        self,
        ref: str,
        depth: int = 2,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._run("expand", ref, "--depth", str(depth), *self._wait_args(wait_time))

    def list_items(
        self,
        group_ref: str,
        sample_size: int = 5,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._run(
            "list-items",
            group_ref,
            "--sample-size",
            str(sample_size),
            *self._wait_args(wait_time),
        )

    def extract(
        self,
        target_ref: str,
        schema: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
        wait_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = ["extract", target_ref]
        schema_items = [str(item) for item in (schema or []) if str(item).strip()]
        if schema_items:
            args.extend(["--schema", *schema_items])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

    def resolve_locator(self, ref: str, wait_time: Optional[float] = None) -> Dict[str, Any]:
        return self._run("resolve-locator", "--ref", ref, *self._wait_args(wait_time))

    def eval_js(self, js: str, wait_time: Optional[float] = None) -> Dict[str, Any]:
        return self._run("eval", js, *self._wait_args(wait_time))

    def session_inspect(self, wait_time: Optional[float] = None) -> Dict[str, Any]:
        return self._run("session", "inspect", *self._wait_args(wait_time))

    def session_close(self) -> Dict[str, Any]:
        return self._run("session", "close")

    def batch_detail_extract(
        self,
        items: List[Dict[str, Any]],
        source_url: Optional[str] = None,
        target_pages: Optional[int] = None,
        list_pages_extracted: Optional[int] = None,
        limit: Optional[int] = None,
        schema: Optional[Iterable[str]] = None,
        extractor: str = "legacy-js",
        navigation_mode: str = "click",
        fallback_mode: str = "direct",
        wait_time: Optional[float] = None,
        wait_jitter: Optional[float] = None,
        max_retries: Optional[int] = None,
        item_timeout: Optional[float] = None,
        ai_timeout: Optional[float] = None,
        output_file: Optional[str] = None,
        progress_file: Optional[str] = None,
        command_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        items = self._filter_detail_items(items)
        if not items:
            return self._invalid_action(
                "batch-detail-extract requires at least one valid http(s) item URL.",
                skill="batch-detail-extract",
            )
        args = ["batch-detail-extract", "--items-json", json.dumps(items, ensure_ascii=False)]
        if source_url:
            args.extend(["--source-url", source_url])
        if target_pages is not None:
            args.extend(["--target-pages", str(target_pages)])
        if list_pages_extracted is not None:
            args.extend(["--list-pages-extracted", str(list_pages_extracted)])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        schema_items = [str(item) for item in (schema or []) if str(item).strip()]
        if schema_items:
            args.extend(["--schema", *schema_items])
        args.extend(["--extractor", extractor])
        args.extend(["--navigation-mode", navigation_mode])
        args.extend(["--fallback-mode", fallback_mode])
        args.extend(self._wait_args(wait_time))
        if wait_jitter is not None:
            args.extend(["--wait-jitter", str(wait_jitter)])
        if max_retries is not None:
            args.extend(["--max-retries", str(max_retries)])
        if item_timeout is not None:
            args.extend(["--item-timeout", str(item_timeout)])
        if ai_timeout is not None:
            args.extend(["--ai-timeout", str(ai_timeout)])
        if output_file:
            args.extend(["--output-file", str(Path(output_file).resolve())])
        if progress_file:
            args.extend(["--progress-file", str(Path(progress_file).resolve())])
        return self._run(
            *args,
            timeout=command_timeout if command_timeout is not None else self.batch_timeout_seconds,
        )

    @staticmethod
    def _filter_detail_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        seen_urls = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            url = str(
                item.get("detail_url") or item.get("url") or item.get("href") or ""
            ).strip()
            if not url:
                continue
            try:
                parsed = urlparse(url)
            except Exception:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            normalized = url.rstrip("/")
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            filtered.append(item)
        return filtered

    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(action, dict):
            return self._invalid_action("Action must be a JSON object.")
        skill = str(action.get("skill") or "").strip()
        params = action.get("params") or {}
        if not isinstance(params, dict):
            return self._invalid_action("Action params must be a JSON object.", skill=skill)

        trace_log(f"execute_action: skill={skill}")
        self._active_request_id = str(action.get("request_id") or "").strip() or None

        if "target_ref" in params and "ref" not in params:
            params = dict(params)
            params["ref"] = params["target_ref"]

        try:
            if skill == "open":
                return self.open(str(params["url"]), wait_time=params.get("wait_time"))
            if skill == "snapshot":
                return self.snapshot(
                    mode=params.get("mode", "agent_summary"),
                    ref=params.get("ref"),
                    depth=params.get("depth"),
                    wait_time=params.get("wait_time"),
                )
            if skill == "wait":
                seconds = params.get("seconds")
                if seconds is None and params.get("timeout_ms") is not None:
                    seconds = float(params["timeout_ms"]) / 1000.0
                return self.wait(float(seconds if seconds is not None else 1.0))
            if skill == "find":
                return self.find(
                    text=params.get("text"),
                    locator=params.get("locator"),
                    wait_time=params.get("wait_time"),
                )
            if skill == "click":
                return self.click(
                    ref=params.get("ref"),
                    locator=params.get("locator"),
                    wait_time=params.get("wait_time"),
                )
            if skill == "type":
                return self.type_text(
                    text=str(params["text"]),
                    ref=params.get("ref"),
                    locator=params.get("locator"),
                    submit=bool(params.get("submit", False)),
                    wait_time=params.get("wait_time"),
                )
            if skill == "scroll":
                return self.scroll(
                    direction=str(params.get("direction") or "down"),
                    amount=int(params.get("amount") or 900),
                    to=params.get("to"),
                    wait_time=params.get("wait_time"),
                )
            if skill == "expand":
                return self.expand(
                    ref=str(params["ref"]),
                    depth=int(params.get("depth", 2)),
                    wait_time=params.get("wait_time"),
                )
            if skill == "list-items":
                return self.list_items(
                    group_ref=str(
                        params.get("group_ref")
                        or params.get("ref")
                        or params["target_ref"]
                    ),
                    sample_size=int(params.get("sample_size", 5)),
                    wait_time=params.get("wait_time"),
                )
            if skill == "extract":
                return self.extract(
                    target_ref=str(params.get("target_ref") or params["ref"]),
                    schema=params.get("schema"),
                    limit=params.get("limit"),
                    wait_time=params.get("wait_time"),
                )
            if skill == "resolve-locator":
                return self.resolve_locator(str(params["ref"]), wait_time=params.get("wait_time"))
            if skill in {"session.inspect", "session_inspect"}:
                return self.session_inspect(wait_time=params.get("wait_time"))
            if skill in {"session.close", "session_close"}:
                return self.session_close()
            if skill == "batch-detail-extract":
                return self.batch_detail_extract(**params)
            if skill == "eval":
                return self.eval_js(str(params["js"]), wait_time=params.get("wait_time"))
        except KeyError as exc:
            return self._invalid_action(f"Missing required param: {exc.args[0]}", skill=skill)
        except (TypeError, ValueError) as exc:
            return self._invalid_action(str(exc), skill=skill)

        return self._invalid_action(f"Unsupported dp_cli skill: {skill}", skill=skill)

    def _run(self, *args: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        raw = self._run_raw(list(args), timeout=timeout)
        if raw.get("timed_out"):
            return self._error_payload(
                action=self._action_name(args),
                code="timeout",
                message=f"dp_cli command timed out after {raw.get('timeout')}s.",
                details={"timeout": raw.get("timeout"), "stderr": raw.get("stderr") or ""},
            )

        parsed = self._parse_json(raw.get("stdout") or "")
        if parsed is not None:
            if isinstance(parsed, dict):
                return parsed
            return self._error_payload(
                action=self._action_name(args),
                code="invalid_json",
                message="dp_cli stdout JSON was not an object.",
                details={"stdout": raw.get("stdout") or ""},
            )

        return self._error_payload(
            action=self._action_name(args),
            code="invalid_json" if raw.get("returncode") == 0 else "process_error",
            message="dp_cli did not return parseable JSON.",
            details={
                "returncode": raw.get("returncode"),
                "stdout": raw.get("stdout") or "",
                "stderr": raw.get("stderr") or "",
            },
        )

    def _run_raw(self, args: List[str], timeout: Optional[float] = None) -> Dict[str, Any]:
        import time as _time
        cmd = [self.python_executable, "-m", "dp_cli", *args]
        accepts_headless = list(args[:2]) != ["session", "close"]
        if self.headless and accepts_headless and "--headless" not in cmd:
            cmd.append("--headless")
        if "--session" not in cmd:
            cmd.extend(["--session", self.session])
        if (
            self._active_request_id
            and accepts_headless
            and "--request-id" not in cmd
        ):
            cmd.extend(["--request-id", self._active_request_id])

        trace_log(f"dp_cli run: {' '.join(cmd)}")

        run_timeout = self.timeout_seconds if timeout is None else timeout
        t0 = _time.time()
        try:
            completed = subprocess.run(
                cmd,
                cwd=self.cwd or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=run_timeout,
            )
            elapsed = _time.time() - t0
            trace_log(f"dp_cli done: rc={completed.returncode}, stdout={len(completed.stdout)}B, {elapsed:.2f}s")

            result = {
                "cmd": cmd,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            self._save_dpcli_log(cmd, completed.stdout, completed.stderr,
                                 completed.returncode, elapsed)
            return result
        except subprocess.TimeoutExpired as exc:
            elapsed = _time.time() - t0
            logger.warning(f"   ⏱️  [DPCLIExecutor] 超时: {run_timeout}s")
            result = {
                "cmd": cmd,
                "returncode": None,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "timeout": run_timeout,
                "timed_out": True,
            }
            self._save_dpcli_log(cmd, exc.stdout or "", exc.stderr or "",
                                 None, elapsed, timed_out=True)
            return result
        except OSError as exc:
            elapsed = _time.time() - t0
            logger.error(f"   ❌ [DPCLIExecutor] OS错误: {exc}")
            result = {
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
            }
            self._save_dpcli_log(cmd, "", str(exc), None, elapsed)
            return result
        except Exception as exc:
            elapsed = _time.time() - t0
            self._save_dpcli_log(cmd, "", f"{type(exc).__name__}: {exc}", None, elapsed)
            raise

    def _save_dpcli_log(self, cmd, stdout, stderr, returncode, elapsed,
                        timed_out=False):
        log_path = save_dpcli_code_log(
            cmd=cmd,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            elapsed=elapsed,
            extra_info=f"session={self.session}",
        )
        if log_path:
            logger.info(f"   📄 [DPCLIExecutor] dp_cli log saved to: {log_path}")

    @staticmethod
    def _wait_args(wait_time: Optional[float]) -> List[str]:
        if wait_time is None:
            return []
        try:
            value = float(wait_time)
        except (TypeError, ValueError):
            return []
        return ["--wait-time", str(value)] if value > 0 else []

    @staticmethod
    def _parse_json(stdout: str) -> Optional[Any]:
        text = (stdout or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _action_name(args: Iterable[str]) -> str:
        return next(iter(args), "unknown")

    def _invalid_action(self, message: str, skill: str = "action") -> Dict[str, Any]:
        return self._error_payload(
            action=skill or "action",
            code="invalid_action",
            message=message,
            details={},
        )

    def _error_payload(
        self,
        action: str,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "ok": False,
            "session": self.session,
            "action": action,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        }
