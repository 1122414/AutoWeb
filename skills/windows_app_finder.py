"""Windows application and shortcut discovery utilities.

The finder is intentionally deterministic and small-output: it searches the
Windows shell locations that users perceive as "desktop/start menu" before
falling back to installed-app registry entries. This avoids expensive broad
filesystem scans and catches shortcuts placed on the public desktop.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass
class AppCandidate:
    name: str
    path: str
    source: str
    kind: str
    score: float
    target_path: str = ""
    arguments: str = ""
    working_directory: str = ""


def _norm(text: str) -> str:
    text = (text or "").lower()
    table = str.maketrans({
        "：": ":",
        "（": "(",
        "）": ")",
        "　": " ",
        "·": "",
        "《": "",
        "》": "",
    })
    text = text.translate(table)
    text = re.sub(r"\.(lnk|url|exe)$", "", text, flags=re.IGNORECASE)
    return re.sub(r"[\s_\-.:()（）【】\[\]{}]+", "", text)


def _query_parts(query: str) -> List[str]:
    query = query or ""
    compact = _norm(query)
    parts = [compact] if compact else []
    ascii_words = re.findall(r"[a-zA-Z0-9]+", query)
    parts.extend(_norm(w) for w in ascii_words)
    # For Chinese names, short substrings matter more than whitespace tokens.
    if len(compact) >= 4:
        parts.extend({compact[:2], compact[-2:]})
    return [p for p in dict.fromkeys(parts) if p]


def score_name(query: str, name: str, path: str = "") -> float:
    q = _norm(query)
    n = _norm(Path(name).stem)
    p = _norm(path)
    if not q:
        return 0.0
    if q == n:
        return 100.0
    if q and q in n:
        return 92.0
    if n and n in q:
        return 85.0

    parts = _query_parts(query)
    score = 0.0
    for part in parts:
        if part in n:
            score += 22.0
        elif part in p:
            score += 10.0

    if q and n:
        common = len(set(q) & set(n))
        score += min(35.0, common * 4.0)
    if Path(name).suffix.lower() in (".lnk", ".url"):
        score += 5.0
    return min(score, 99.0)


def shell_search_roots() -> List[Path]:
    roots: List[Path] = []

    def add(value: Optional[str]) -> None:
        if value:
            path = Path(os.path.expandvars(value)).expanduser()
            if path.exists() and path not in roots:
                roots.append(path)

    userprofile = os.environ.get("USERPROFILE")
    public = os.environ.get("PUBLIC")
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("ProgramData")
    onedrive_values = [
        os.environ.get("OneDrive"),
        os.environ.get("OneDriveConsumer"),
        os.environ.get("OneDriveCommercial"),
    ]

    add(str(Path(userprofile) / "Desktop") if userprofile else None)
    add(str(Path(public) / "Desktop") if public else None)
    for od in onedrive_values:
        add(str(Path(od) / "Desktop") if od else None)
    add(str(Path(appdata) / "Microsoft/Windows/Start Menu/Programs") if appdata else None)
    add(str(Path(programdata) / "Microsoft/Windows/Start Menu/Programs") if programdata else None)
    return roots


def _iter_shortcuts(roots: Sequence[Path]) -> Iterable[Path]:
    for root in roots:
        recursive = "start menu" in str(root).lower()
        pattern = "**/*" if recursive else "*"
        try:
            for path in root.glob(pattern):
                if path.is_file() and path.suffix.lower() in (".lnk", ".url", ".exe"):
                    yield path
        except OSError:
            continue


def _resolve_lnk(path: Path) -> Dict[str, str]:
    if path.suffix.lower() != ".lnk":
        return {}
    path_literal = str(path).replace("'", "''")
    script = (
        f"$p = '{path_literal}';"
        "$ws = New-Object -ComObject WScript.Shell;"
        "$sc = $ws.CreateShortcut($p);"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::UTF8;"
        "[pscustomobject]@{target=$sc.TargetPath;arguments=$sc.Arguments;"
        "working_directory=$sc.WorkingDirectory} | ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if cp.returncode == 0 and cp.stdout.strip():
            data = json.loads(cp.stdout)
            return {
                "target_path": str(data.get("target") or ""),
                "arguments": str(data.get("arguments") or ""),
                "working_directory": str(data.get("working_directory") or ""),
            }
    except Exception:
        return {}
    return {}


def _registry_candidates(query: str) -> List[AppCandidate]:
    try:
        import winreg
    except ImportError:
        return []

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    result: List[AppCandidate] = []
    for hive, subkey in roots:
        try:
            root = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    app_key = winreg.OpenKey(root, winreg.EnumKey(root, i))
                    name = str(winreg.QueryValueEx(app_key, "DisplayName")[0])
                except OSError:
                    continue
                install = _read_reg_value(app_key, "InstallLocation")
                icon = _read_reg_value(app_key, "DisplayIcon")
                path = install or icon
                score = score_name(query, name, path)
                if score >= 35:
                    result.append(AppCandidate(name, path, "registry", "installed_app", score))
        finally:
            try:
                winreg.CloseKey(root)
            except OSError:
                pass
    return result


def _read_reg_value(key: object, value: str) -> str:
    try:
        import winreg
        return str(winreg.QueryValueEx(key, value)[0])
    except OSError:
        return ""


def find_windows_app(query: str, limit: int = 5, resolve: bool = True) -> List[AppCandidate]:
    candidates: List[AppCandidate] = []
    roots = shell_search_roots()
    for path in _iter_shortcuts(roots):
        score = score_name(query, path.name, str(path))
        if score < 35:
            continue
        root_label = "shell"
        lowered = str(path).lower()
        if "\\public\\desktop" in lowered:
            root_label = "public_desktop"
        elif "\\desktop" in lowered:
            root_label = "user_desktop"
        elif "start menu" in lowered:
            root_label = "start_menu"
        extra = _resolve_lnk(path) if resolve and path.suffix.lower() == ".lnk" else {}
        candidates.append(AppCandidate(
            name=path.name,
            path=str(path),
            source=root_label,
            kind=path.suffix.lower().lstrip(".") or "file",
            score=score,
            **extra,
        ))

    candidates.extend(_registry_candidates(query))
    candidates.sort(key=lambda c: (c.score, c.source == "public_desktop"), reverse=True)

    deduped: List[AppCandidate] = []
    seen = set()
    for c in candidates:
        key = (_norm(c.name), _norm(c.path))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
        if len(deduped) >= limit:
            break
    return deduped


def open_candidate(candidate: AppCandidate) -> None:
    target = candidate.path
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        raise RuntimeError("open_candidate is only supported on Windows")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Find Windows apps/shortcuts with shell-aware search.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-resolve", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_first")
    args = parser.parse_args(argv)

    matches = find_windows_app(args.query, limit=args.limit, resolve=not args.no_resolve)
    payload = [asdict(m) for m in matches]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.open_first and matches:
        open_candidate(matches[0])
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
