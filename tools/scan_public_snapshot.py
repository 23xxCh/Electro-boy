"""Scan the Git publish candidate without printing credential values."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TEXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".ps1", ".cmd", ".bat", ".html", ".js", ".mjs", ".cjs", ".ts", ".tsx"}
FORMATS = {
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b"),
    "provider_key": re.compile(r"\b(?:ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})\b"),
    "personal_open_id": re.compile(r"\bou_[0-9a-f]{24,}\b"),
}


def auth_literals(text):
    result = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return result
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key_node, value_node in zip(node.keys, node.values):
                if (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
                        and re.search(r"(?:token|secret|password|api_?key)$", key_node.value, re.I)
                        and isinstance(value_node, ast.Constant) and isinstance(value_node.value, str) and value_node.value.strip()):
                    result.append((key_node.value, value_node.value, value_node.lineno))
        key = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            key, value = node.target.id, node.value
        elif isinstance(node, ast.keyword):
            key, value = node.arg, node.value
        if key and re.search(r"(?:token|secret|password|api_?key)$", key, re.I) and isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
            result.append((key, value.value, node.lineno))
    return result


def main():
    staged = subprocess.check_output(["git", "--no-optional-locks", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    new = subprocess.check_output(["git", "--no-optional-locks", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    paths = sorted(set(staged + new) - {""})
    failures, fixture_candidates, manifest = [], [], []
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            failures.append({"path": name, "category": "missing_index_path"})
            continue
        if path.is_symlink() or any(part in {"node_modules", ".git", "__pycache__", ".storage", ".scratch", "ha_lovelace_preview"} for part in Path(name).parts):
            failures.append({"path": name, "category": "private_or_generated_path"})
        if (path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".dpapi", ".log", ".zip", ".mp4", ".exe"}
                or re.search(r"(?i)(?:^|/)(?:ha_token\.txt|secrets?\.ya?ml|\.env(?:\..*)?)$", name)):
            failures.append({"path": name, "category": "private_or_archive_file"})
        data = path.read_bytes()
        if len(data) >= 100 * 1024 * 1024:
            failures.append({"path": name, "category": "oversize_blob"})
        if name not in {"docs/delivery/2026-10-17/github-baseline/candidate-manifest.json", "docs/delivery/2026-10-17/github-baseline/secret-scan.json"}:
            manifest.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if path.suffix.lower() not in TEXT or len(data) > 4 * 1024 * 1024:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            continue
        for category, pattern in FORMATS.items():
            if pattern.search(text):
                failures.append({"path": name, "category": category})
        if path.suffix == ".py":
            for key, value, line in auth_literals(text):
                fixture = ("checks/" in name or path.name.startswith("test_")) and (
                    value.lower().startswith(("fixture", "synthetic", "qa-", "qa_", "test", "ready", "fake"))
                    or value.lower() in {"secret", "token", "password", "app_secret", "app_id", "new", "old"})
                item = {"path": name, "line": line, "field": key, "category": "synthetic_auth_fixture" if fixture else "auth_literal_requires_review"}
                (fixture_candidates if fixture else failures).append(item)
    deleted = subprocess.check_output(["git", "--no-optional-locks", "diff", "--name-only", "--diff-filter=D", "origin/main"], cwd=ROOT).decode().splitlines()
    if deleted:
        failures.extend({"path": name, "category": "remote_file_deleted"} for name in deleted)
    report = ROOT / "docs/delivery/2026-10-17/github-baseline"
    report.mkdir(parents=True, exist_ok=True)
    # Reports intentionally carry paths, categories and hashes only, never matched values.
    (report / "candidate-manifest.json").write_text(json.dumps({"files": manifest, "filesCount": len(manifest), "bytes": sum(row["bytes"] for row in manifest),
        "selfReferentialReportsExcluded": ["candidate-manifest.json", "secret-scan.json"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"status": "pass" if not failures else "review_required", "filesScanned": len(paths), "failures": failures,
              "syntheticFixtureCandidates": fixture_candidates, "valuesPrinted": False,
              "limits": "Pattern/AST scan and file allowlist, not a guarantee about opaque PDF/model/image binary contents."}
    (report / "secret-scan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "filesScanned": len(paths), "blockingCandidates": len(failures), "syntheticFixtures": len(fixture_candidates)}))
    return 0 if not failures else 1


if __name__ == "__main__":
    assert FORMATS["provider_key"].search("ghp_" + "A" * 30)
    assert not FORMATS["provider_key"].search("private config contains no provider value")
    assert auth_literals('api_key = ""') == []
    assert auth_literals('config = {"appSecret": "synthetic-secret"}')[0][0] == "appSecret"
    raise SystemExit(main())
