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
        site_policy=None,
    ) -> None:
        self.session = session
        self.headless = headless
        self.python_executable = python_executable
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.batch_timeout_seconds = batch_timeout_seconds
        self._active_request_id: Optional[str] = None
        self._active_action: Dict[str, Any] = {}
        if site_policy is None:
            from skills.site_policy import site_policy as default_site_policy

            site_policy = default_site_policy
        self.site_policy = site_policy
        self._active_policy_decisions: List[Dict[str, Any]] = []
        trace_log(f"DPCLIExecutor 初始化: session={session}, headless={headless}")

    def open(
        self,
        url: str,
        wait_time: Optional[float] = None,
        navigation_timeout: Optional[float] = 15.0,
    ) -> Dict[str, Any]:
        args: List[str] = ["open", url]
        if navigation_timeout is not None:
            args.extend(["--navigation-timeout", str(max(1.0, float(navigation_timeout)))])
        args.extend(self._wait_args(wait_time))
        return self._run(*args)

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
        """Wait for browser network quiescence instead of blindly sleeping."""
        result = self._run(
            "wait-ready",
            "--condition",
            "network-idle",
            "--timeout",
            str(max(0.0, float(seconds))),
        )
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
        ready_condition: Optional[str] = None,
        ready_locator: Optional[str] = None,
        ready_timeout: Optional[float] = None,
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
        if ready_condition:
            args.extend(["--ready-condition", str(ready_condition)])
        if ready_locator:
            args.extend(["--ready-locator", str(ready_locator)])
        if ready_timeout is not None:
            args.extend(["--ready-timeout", str(ready_timeout)])
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
        result = self._run("eval", js, *self._wait_args(wait_time))
        if result.get("ok") and isinstance(result.get("data"), dict):
            data = dict(result["data"])
            value = data.get("result")
            if isinstance(value, dict):
                data["items"] = [value]
                data["item_count"] = 1
                result = dict(result)
                result["data"] = data
        return result

    def session_inspect(self, wait_time: Optional[float] = None) -> Dict[str, Any]:
        return self._run("session", "inspect", *self._wait_args(wait_time))

    def session_close(self, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """Close a session with a hard deadline on Windows pipe inheritance."""
        raw = self._run_raw_hard(
            ["session", "close"],
            timeout=max(1.0, min(float(timeout_seconds), self.timeout_seconds)),
        )
        return self._parse_raw_result(raw, ("session", "close"))

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
        if skill == "navigate":
            action = {**action, "skill": "open", "params": dict(params)}
            skill = "open"

        trace_log(f"execute_action: skill={skill}")
        self._active_request_id = str(action.get("request_id") or "").strip() or None
        self._active_action = dict(action)
        self._active_policy_decisions = []
        if self.site_policy is not None:
            decisions = self.site_policy.authorize_action(action)
            self._active_policy_decisions = [
                decision.to_dict() for decision in decisions
            ]
            denied = next(
                (decision for decision in decisions if not decision.allowed),
                None,
            )
            if denied is not None:
                return self._error_payload(
                    action=skill or "action",
                    code="site_policy_denied",
                    message=f"Site policy denied access: {denied.reason}",
                    details={
                        "policy_decision": denied.to_dict(),
                    },
                )

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
                    ready_condition=params.get("ready_condition"),
                    ready_locator=params.get("ready_locator"),
                    ready_timeout=params.get("ready_timeout"),
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
        return self._parse_raw_result(raw, args)

    def _parse_raw_result(
        self, raw: Dict[str, Any], args: Iterable[str]
    ) -> Dict[str, Any]:
        if raw.get("timed_out"):
            return self._finalize_result(self._error_payload(
                action=self._action_name(args),
                code="timeout",
                message=f"dp_cli command timed out after {raw.get('timeout')}s.",
                details={"timeout": raw.get("timeout"), "stderr": raw.get("stderr") or ""},
            ))

        parsed = self._parse_json(raw.get("stdout") or "")
        if parsed is not None:
            if isinstance(parsed, dict):
                return self._finalize_result(parsed)
            return self._finalize_result(self._error_payload(
                action=self._action_name(args),
                code="invalid_json",
                message="dp_cli stdout JSON was not an object.",
                details={"stdout": raw.get("stdout") or ""},
            ))

        return self._finalize_result(self._error_payload(
            action=self._action_name(args),
            code="invalid_json" if raw.get("returncode") == 0 else "process_error",
            message="dp_cli did not return parseable JSON.",
            details={
                "returncode": raw.get("returncode"),
                "stdout": raw.get("stdout") or "",
                "stderr": raw.get("stderr") or "",
            },
        ))

    def _finalize_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        finalized = dict(result)
        navigation_denied = self._post_navigation_policy_decision(finalized)
        if self._active_policy_decisions:
            finalized["_site_policy"] = {
                "decisions": list(self._active_policy_decisions),
            }
        if self.site_policy is None:
            return finalized
        if navigation_denied is not None:
            finalized["ok"] = False
            finalized["error"] = {
                "code": "site_policy_denied",
                "message": (
                    "Site policy denied follow-up work after navigation: "
                    f"{navigation_denied.reason}"
                ),
                "details": {"policy_decision": navigation_denied.to_dict()},
            }
            return finalized
        signal = self._response_policy_signal(finalized)
        if not signal.detected:
            signal = self.site_policy.detect_block_signal(finalized)
        if not signal.detected:
            return finalized
        finalized["ok"] = False
        finalized["error"] = {
            "code": "site_blocked",
            "message": f"Site blocking signal detected: {signal.kind}",
            "details": {"blocking_signal": signal.to_dict()},
        }
        finalized.setdefault("_site_policy", {})["blocking_signal"] = (
            signal.to_dict()
        )
        return finalized

    def _post_navigation_policy_decision(self, result: Dict[str, Any]):
        """Prevent extraction after a click/type navigation into a denied URL."""
        if (
            self.site_policy is None
            or not result.get("ok")
            or not hasattr(self.site_policy, "authorize")
        ):
            return None
        skill = str(self._active_action.get("skill") or "").lower()
        if skill not in {"click", "type"}:
            return None
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        page = data.get("page") if isinstance(data.get("page"), dict) else {}
        url = str(page.get("url") or data.get("url") or "").strip()
        if not url:
            return None
        decision = self.site_policy.authorize(url, pace=False)
        self._active_policy_decisions.append(decision.to_dict())
        return decision if not decision.allowed else None

    def _response_policy_signal(self, result: Dict[str, Any]):
        """Forward explicit HTTP restrictions to the durable Site Policy."""
        if not result.get("ok") or not hasattr(self.site_policy, "observe_response"):
            from skills.site_policy import BlockingSignal

            return BlockingSignal(False)
        data = result.get("data")
        data = data if isinstance(data, dict) else {}
        page = data.get("page")
        page = page if isinstance(page, dict) else {}
        status_code = (
            page.get("status_code")
            or page.get("http_status")
            or data.get("status_code")
            or data.get("http_status")
            or result.get("status_code")
        )
        headers = page.get("headers") or data.get("headers") or result.get("headers")
        url = (
            page.get("url")
            or data.get("url")
            or result.get("url")
            or ""
        )
        try:
            return self.site_policy.observe_response(
                str(url),
                status_code=int(status_code) if status_code is not None else None,
                headers=headers if isinstance(headers, dict) else None,
            )
        except (TypeError, ValueError):
            from skills.site_policy import BlockingSignal

            return BlockingSignal(False)

    def _run_raw_hard(self, args: List[str], timeout: float) -> Dict[str, Any]:
        """Run a lifecycle command without post-timeout pipe hangs.

        On Windows, ``subprocess.run(timeout=...)`` may kill the direct CLI
        process and then keep waiting because a grandchild inherited the
        captured stdout/stderr handles.  This path releases those readers after
        the direct process is killed, enforcing the caller's wall-clock limit.
        """
        import time as _time

        cmd = [self.python_executable, "-m", "dp_cli", *args]
        if "--session" not in cmd:
            cmd.extend(["--session", self.session])
        trace_log(f"dp_cli hard-timeout run: {' '.join(cmd)}")
        started = _time.monotonic()
        proc = subprocess.Popen(
            cmd,
            cwd=self.cwd or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            elapsed = _time.monotonic() - started
            self._save_dpcli_log(cmd, stdout, stderr, proc.returncode, elapsed)
            return {
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except subprocess.TimeoutExpired:
            elapsed = _time.monotonic() - started
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            self._save_dpcli_log(cmd, "", "", None, elapsed, timed_out=True)
            return {
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "timeout": timeout,
                "timed_out": True,
            }

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
