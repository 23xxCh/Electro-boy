# -*- coding: utf-8 -*-
"""install_ha_config.py —— 把 VE 的设备配置 YAML 安装到官方 HA 插件 config 目录。

官方插件通过 PN(0x8000) 的加盐 SHA256 哈希定位 config/<hash>.yaml。
VE 上报 PN="DMWHVESIM"，对应哈希文件已打包在本目录。把该文件复制到
ha-anker-solix-official 的 custom_components/anker_solix_official/config/ 下即可。

用法：
    python install_ha_config.py --plugin-dir <ha 插件 config 目录>

（若 HA 装在官方环境机上，把 --plugin-dir 指向其 config/custom_components/
  anker_solix_official/config 目录。）
"""
from __future__ import annotations

import argparse
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
HASH_FILE = "6ff7f6dd1fada1bebc0d7e18c02c01ee69ce977f91a2ab29d171b251a4e0c445.yaml"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-dir", required=True,
                    help="官方插件 custom_components/anker_solix_official/config 目录")
    args = ap.parse_args()
    src = os.path.join(HERE, HASH_FILE)
    if not os.path.exists(src):
        raise SystemExit(f"未找到 {src}")
    dst = os.path.join(args.plugin_dir, HASH_FILE)
    os.makedirs(args.plugin_dir, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"已安装 VE 设备配置 -> {dst}")
    print("重启 HA 后，在 HA 中添加 3 台设备：IP=<VE主机>，端口分别 1502/1503/1504。")


if __name__ == "__main__":
    main()
