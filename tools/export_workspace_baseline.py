"""Export this reviewed source snapshot; never copy private HA/runtime state."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def sha(data):
    return hashlib.sha256(data).hexdigest()


def selected(source):
    base = Path("Anker_ChargingAndEnergyStorage")
    files = set()

    def add(pattern):
        files.update(path.relative_to(source) for path in source.glob(pattern) if path.is_file())

    for module in ("assistant", "channels", "energy_runtime"):
        add(f"{base}/{module}/*.py")
        add(f"{base}/{module}/README.md")
    add(f"{base}/channels/checks/test_*.py")
    for folder in ("AnkerProject_DJXZ_Debug", "AnkerProject_DJXZ_Release", "DJXZ/AnkerProject_DJXZ_Release"):
        add(f"{base}/{folder}/Simulator/*.py")
    for folder in ("src", "public", "licenses"):
        add(f"{base}/frontend/{folder}/**/*")
    for name in (".gitignore", "index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "server.py", "THIRD_PARTY_NOTICES.md", "DESIGN.md", "CHANGELOG.md"):
        add(f"{base}/frontend/{name}")
    for name in ("fixtures.py", "offline_adapter.py", "offline_baseline.py", "offline_frontend.mjs"):
        add(f"{base}/checks/environment-pipeline/{name}")
    for name in ("test_core_contract.py", "test_physical_oracle.py", "physical_oracle.py"):
        add(f"{base}/checks/runtime/{name}")
    debug = base / "AnkerProject_DJXZ_Debug"
    required = (
        "Controller/__init__.py",
        "Controller/Controller_LP_costmin/__init__.py",
        "Controller/Controller_LP_costmin/Controller.py",
        "Controller/Controller_LP_costmin/model_lp.py",
        "Controller/Controller_LP_costmin/predictor_config.py",
        "Controller/Controller_LP_costmin/site_params.py",
        "Controller/Controller_LP_costmin/lstm_predictor.py",
        "Controller/Controller_LP_costminTree/__init__.py",
        "Controller/Controller_LP_costminTree/Controller.py",
        "Controller/Controller_LP_costminTree/household_plan.py",
        "Controller/Controller_LP_costminTree/model_lp.py",
        "Controller/Controller_LP_costminTree/predictor_config.py",
        "Controller/Controller_LP_costminTree/site_params.py",
        "Controller/Controller_LP_costmin/Predictor/PV_Predictor/Train.py",
        "Controller/Controller_LP_costmin/Predictor/PV_Predictor/Trainer.py",
        "Controller/Controller_LP_costmin/Predictor/PV_Predictor/Downstream_model/pv_predictor_site1.pt",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/Train_downstream_lstm.py",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/um_embedding.py",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/station_embedding.py",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/Downstream_model/load_predictor_site1.pt",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/Upstream_model/window_embedding_time.pt",
        "Controller/Controller_LP_costmin/Predictor/Load_Predictor/Upstream_model/window_embedding_freq.pt",
        "Tools/value_comparison/artifacts/comparison.v1.json",
    )
    for name in required:
        path = debug / name
        if not (source / path).is_file():
            raise SystemExit(f"Required source missing: {path}")
        files.add(path)
    add(f"{debug}/Controller/Controller_LP_costmin/Predictor/Load_Predictor/signal_features/*.py")
    for site in (1, 2, 3):
        add(f"{debug}/Controller/Controller_LP_costminTree/report/4/site_{site}/slot_*.xml")
        add(f"{debug}/DataSet/DataSet_mocksampling_15mpt/dataset_mock_1011_10160249/contest_simulation_site{site}.csv")
    add("ha_host/*.py")
    add("ha_host/*.ps1")
    add("ha_host/config.example.json")
    add("ha_host/local_ha_service/*")
    for folder, names in {
        "环境机管线接入": ("spec.md", "contract.md", "plan.md", "tasks.md", "diff-review.md"),
        "家庭能源世界": ("spec.md", "scene-contract.md"),
        "飞书储能助手": ("CH03-spec.md", "GW1-interface.md"),
    }.items():
        for name in names:
            add(f"docs/specs/{folder}/{name}")
    for name in ("README.md", "lux3d-house.json"):
        add(f"docs/design/energy-island/asset-manifest/{name}")
    return sorted(files, key=lambda path: path.as_posix())


def export(source, destination):
    source, destination = source.resolve(), destination.resolve()
    if source == destination or not (destination / ".git").is_file():
        raise SystemExit("Destination must be a separate existing Git worktree")
    rows = []
    for relative in selected(source):
        if any(part in {".git", "node_modules", "__pycache__", ".storage"} for part in relative.parts):
            raise SystemExit("Private/generated path rejected")
        origin = source / relative
        if origin.is_symlink():
            raise SystemExit("Source symlink rejected")
        data = origin.read_bytes()
        sanitized = relative.name == "config.py" and relative.parent.name == "Simulator"
        if sanitized:
            text, count = re.subn(r'(\b(?:ha_token|sim_user|sim_password)\s*:\s*str\s*=\s*)["\'][^"\'\r\n]*["\']', r'\1""', data.decode("utf-8-sig"))
            if count != 3:
                raise SystemExit("Unexpected Simulator credential declaration")
            data = text.encode("utf-8")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({"path": relative.as_posix(), "sourceSha256": sha(origin.read_bytes()),
                     "publishedSha256": sha(data), "bytes": len(data), "sanitizedAuthDefaults": sanitized})
    for row in rows:
        if sha((source / row["path"]).read_bytes()) != row["sourceSha256"]:
            raise SystemExit("Source changed while exporting; review the snapshot")
    report = destination / "docs/delivery/2026-10-17/github-baseline"
    report.mkdir(parents=True, exist_ok=True)
    (report / "source-manifest.json").write_text(json.dumps({"kind": "reviewed_workspace_source_snapshot", "files": rows,
        "excluded": ["private HA state/configuration", "tokens and credential files", "personal databases and logs", "raw execution receipts", "training datasets and duplicate models", "archives and video", "node_modules/build output", "nested Git repositories"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exportedFiles": len(rows), "bytes": sum(row["bytes"] for row in rows),
                      "sanitizedConfigFiles": sum(row["sanitizedAuthDefaults"] for row in rows)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    export(args.source, args.destination)
