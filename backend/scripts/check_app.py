# -*- coding: utf-8 -*-
"""app/ 布局落地自检（只读，不改任何文件）。

用法（在 D:\\im-task 下）：
    .\\.venv\\Scripts\\python scripts\\check_app.py

做什么：
  1) 按依赖顺序逐个 import app 包下的模块，报出**第一个缺失的模块名**
  2) 若 app.config 可用，读 .env 并把端口 / 对外地址 / 通道与回调密钥状态打出来（密钥只显示是否已填）
  3) 任一模块失败 -> 退出码 1；全通 -> 0

为什么需要它：AI 是分批交付的，每批落盘后如果只看 "import 报错"，
只能看到最外层那一个缺失模块，看不出还差几个。这个脚本一次把缺口全列出来。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 依赖顺序：前面的模块是后面的前置
MODULES = [
    "app.config",
    "app.db",
    "app.channels.zdxlz",
    "app.api.admin",
    "app.state",
    "app.scheduler",
    "app.api.events",
    "app.api.h5",
    "app.main",
]


def main() -> int:
    print("== 模块检查 ==")
    missing = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            target = str(exc).split("'")[1] if "'" in str(exc) else str(exc)
            third_party = not target.startswith("app.")
            print("FAIL %-22s 缺: %s%s" % (
                name, target, "（第三方依赖，先 pip install）" if third_party else ""))
            missing.append(target)
        except Exception as exc:  # 其它异常说明代码本身有问题，不是缺文件
            print("ERR  %-22s %s: %s" % (name, type(exc).__name__, exc))
            missing.append("%s(%s)" % (name, type(exc).__name__))
        else:
            print("OK   %s" % name)

    print()
    print("== 配置检查 ==")
    try:
        from app.config import Config  # type: ignore
    except Exception:
        print("跳过：app.config 还不可用")
    else:
        try:
            cfg = Config.from_env()
        except SystemExit as exc:
            print("配置拒绝启动：%s" % exc)
            return 1
        print("业务端口      : %s" % cfg.public_port)
        print("后台端口      : %s" % cfg.admin_port)
        print("对外基址      : %s" % cfg.public_base_url)
        print("数据目录      : %s" % cfg.data_dir)
        print("通道凭证      : %s" % cfg.channel_status)
        print("回调密钥      : %s%s" % (
            cfg.crypto_status,
            "" if cfg.event_crypto_configured else
            "  <- 未填 IMT_ENCRYPT_KEY / IMT_VERIFY_TOKEN，真实平台事件解不了密"))
        if cfg.is_loopback_public_url:
            print("注意          : 对外基址仍是 127.0.0.1/localhost，H5 链接只能本机打开")

    print()
    if missing:
        print("还缺 %d 项，服务无法启动。" % len(missing))
        return 1
    print("全部模块可导入，可以起服务了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
