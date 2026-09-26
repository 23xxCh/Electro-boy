# -*- coding: utf-8 -*-
"""_pack.py —— 把 VE 或 Controller_LP 打包成 exe（onedir 文件夹）。

自动收集 conda numpy/scipy 依赖的 Intel MKL DLL（Library\\bin 下的 mkl*.dll /
libiomp5md.dll 等），否则 exe 启动会报 "Cannot load mkl_intel_thread.2.dll"。

由 build_exe_VE.bat / build_exe_ControllerLP.bat 调用：
    python _pack.py VE
    python _pack.py Controller_LP
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)

MKL_PATTERNS = ("mkl*.dll", "libiomp5md.dll", "libifcoremd.dll", "svml_dispmd.dll")


def collect_mkl() -> list[str]:
    lib_bin = os.path.join(sys.prefix, "Library", "bin")
    found = []
    for pat in MKL_PATTERNS:
        found += glob.glob(os.path.join(lib_bin, pat))
    return found


def build(target: str) -> None:
    target_dir = os.path.join(WS, target)
    dlls = collect_mkl()

    cmd = [sys.executable, "-m", "PyInstaller", "-D", "--clean", "--noconfirm",
           "--paths", target_dir,
           # onnxruntime 自带可选的 torch 集成子模块（transformers/quantization 等），
           # 静态分析会顺着这些 import 把 torch 打包进去；显式排除，release 不需要 torch。
           "--exclude-module", "torch",
           "--exclude-module", "onnxruntime.transformers",
           "--exclude-module", "onnxruntime.quantization",
           "--exclude-module", "onnxruntime.training"]
    for d in dlls:
        cmd += ["--add-binary", f"{d};."]

    if target == "VE":
        cmd += ["-n", "VE_Anker",
                "--add-data", f"{os.path.join(target_dir, 'web')};web",
                os.path.join(target_dir, "run.py")]
    else:  # Controller_LP
        cmd += ["-n", "Controller_LP",
                "--add-data",
                f"{os.path.join(target_dir, 'Weather_Pv_Predicter')};Weather_Pv_Predicter",
                "--add-data",
                f"{os.path.join(target_dir, 'Load_Predicter')};Load_Predicter",
                os.path.join(target_dir, "node.py")]

    print(f"打包 {target}（收集 {len(dlls)} 个 MKL/运行时 DLL，onedir）...")
    subprocess.run(cmd, cwd=target_dir, check=True)
    print(f"完成：{os.path.join(target_dir, 'dist')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", choices=["VE", "Controller_LP"])
    build(ap.parse_args().target)


if __name__ == "__main__":
    main()
