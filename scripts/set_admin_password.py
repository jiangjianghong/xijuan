"""停服期间交互设置管理员；密码不出现在命令行、日志或终端回显中。"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from argon2 import PasswordHasher
from service.access_control_service import AccessControlService


def main() -> None:
    parser = argparse.ArgumentParser(description="设置析卷 AI 管理员，写入 config.yaml 的 Argon2id 哈希")
    parser.add_argument("--username", default="admin", help="管理员账号，默认 admin")
    args = parser.parse_args()
    print("请先停止服务再修改密码，避免与运行中的设置保存互相覆盖。")
    password = getpass.getpass("新密码（6–15 个字符）: ")
    if not 6 <= len(password) <= 15:
        parser.error("密码长度必须为 6–15 个字符")
    if password != getpass.getpass("再次输入新密码: "):
        parser.error("两次输入不一致")
    service = AccessControlService()
    with service._write_lock:
        document = service._load_document()
        settings = document.setdefault("settings", {})
        settings["username"] = args.username
        settings["password_hash"] = PasswordHasher().hash(password)
        settings.pop("password", None)
        service._commit(document)
    print("管理员配置已保存。请重启服务使正在运行的进程加载新密码。")


if __name__ == "__main__":
    main()
