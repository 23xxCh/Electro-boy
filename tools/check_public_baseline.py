"""Focused public-source checks. No sockets, devices, providers or services."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import platform
import struct
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "Anker_ChargingAndEnergyStorage"
sys.path[:0] = [str(ROOT), str(BASE), str(BASE / "channels/checks")]
ATTEMPTS = []


def deny_external(event, _args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        ATTEMPTS.append(event)
        raise RuntimeError("offline_network_or_process_denied")


sys.addaudithook(deny_external)


def main():
    manifest = json.loads((ROOT / "docs/delivery/2026-10-17/github-baseline/source-manifest.json").read_text(encoding="utf-8"))
    parsed = 0
    for row in manifest["files"]:
        path = ROOT / row["path"]
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=row["path"])
            parsed += 1
    modules = (
        "Anker_ChargingAndEnergyStorage.energy_runtime.test_runtime",
        "Anker_ChargingAndEnergyStorage.energy_runtime.test_release",
        "test_ch03",
    )
    results = []
    # The existing ONNX dependency queries platform at import; no subprocess probe is needed.
    with patch.multiple(platform, system=lambda: "Windows", machine=lambda: "AMD64", node=lambda: "offline",
                        release=lambda: "11", version=lambda: "offline", architecture=lambda *_args, **_kwargs: ("64bit", "WindowsPE")):
        import requests  # Its optional import-time socket capability probe is denied and recorded here.
        suites = [(name, unittest.defaultTestLoader.loadTestsFromName(name)) for name in modules]
        import_probes = ATTEMPTS[:]
        ATTEMPTS.clear()
        for name, suite in suites:
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            results.append({"suite": name, "testsRun": result.testsRun, "failures": len(result.failures), "errors": len(result.errors)})
        spec = importlib.util.spec_from_file_location("public_frontend_adapter", BASE / "frontend/server.py")
        frontend = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = frontend
        spec.loader.exec_module(frontend)
        comparison = frontend.value_comparison()
    assert comparison["status"] == "valid" and len(comparison["sites"]) == 3
    debug = BASE / "AnkerProject_DJXZ_Debug"
    xml_counts = {}
    for site in (1, 2, 3):
        files = sorted((debug / f"Controller/Controller_LP_costminTree/report/4/site_{site}").glob("slot_*.xml"))
        assert len(files) == 672
        for path in files:
            ET.parse(path)
        xml_counts[f"site_{site}"] = len(files)
        assert (debug / f"DataSet/DataSet_mocksampling_15mpt/dataset_mock_1011_10160249/contest_simulation_site{site}.csv").is_file()
    checkpoints = [ROOT / row["path"] for row in manifest["files"] if row["path"].endswith(".pt")]
    assert len(checkpoints) == 4 and all(path.stat().st_size > 0 for path in checkpoints)
    glb = BASE / "frontend/public/energy-world/lux3d-house-4166575.glb"
    data = glb.read_bytes()
    magic, version, size = struct.unpack_from("<4sII", data)
    assert (magic, version, size) == (b"glTF", 2, len(data))
    report = {"scope": "public worktree focused offline checks", "pythonAstFiles": parsed, "suites": results,
              "blockedImportProbes": import_probes, "externalAttemptsDuringChecks": ATTEMPTS,
              "frozenComparisonValid": True, "frozenRunId": comparison["runId"], "historicalXmlCounts": xml_counts,
              "checkpointFilesPresent": len(checkpoints), "checkpointLoadedOrInferred": False,
              "glbVersion": version, "glbBytes": len(data), "servicesStarted": 0, "deviceWrites": 0,
              "limits": "Existing installed Python environment; no live HTTP, model inference, HA setup, environment connectivity or browser acceptance."}
    (ROOT / "docs/delivery/2026-10-17/github-baseline/verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert not ATTEMPTS and all(not result["failures"] and not result["errors"] for result in results)
    print(json.dumps({"suites": results, "xmlFilesParsed": sum(xml_counts.values()), "externalAttemptsDuringChecks": len(ATTEMPTS)}))


if __name__ == "__main__":
    main()
