"""用私钥对更新 zip 生成 Ed25519 分离签名（.sig）。

用法：
    python scripts/sign_release.py <zip 路径> [--key 私钥.pem] [--out 输出.sig]

默认私钥路径 secrets/update_private_key.pem；默认输出 <zip>.sig。
把生成的 <zip>.sig 和 zip 一起上传到 GitHub Release——应用会下载 .sig 并用内嵌
公钥校验，验证通过才安装。

也可用环境变量 SPECIMEN_UPDATE_PRIVATE_KEY 指定私钥 PEM 路径（CI 场景）。
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"不是 Ed25519 私钥：{path}")
    return key


def sign_file(zip_path: Path, private_key: Ed25519PrivateKey) -> str:
    signature = private_key.sign(zip_path.read_bytes())
    return base64.b64encode(signature).decode("ascii")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sign an update zip with Ed25519")
    parser.add_argument("zip", type=Path, help="更新包 zip 路径")
    parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="私钥 PEM 路径（默认 secrets/update_private_key.pem 或 env SPECIMEN_UPDATE_PRIVATE_KEY）",
    )
    parser.add_argument("--out", type=Path, default=None, help="输出 .sig 路径")
    args = parser.parse_args(argv[1:])

    zip_path: Path = args.zip
    if not zip_path.is_file():
        raise SystemExit(f"找不到 zip：{zip_path}")

    key_path = args.key
    if key_path is None:
        env_key = os.environ.get("SPECIMEN_UPDATE_PRIVATE_KEY", "").strip()
        key_path = Path(env_key) if env_key else Path("secrets/update_private_key.pem")
    if not key_path.is_file():
        raise SystemExit(f"找不到私钥：{key_path}（先运行 gen_update_keys.py）")

    out_path: Path = args.out or Path(str(zip_path) + ".sig")
    private_key = load_private_key(key_path)
    sig_b64 = sign_file(zip_path, private_key)
    out_path.write_text(sig_b64 + "\n", encoding="utf-8")

    print(f"已签名：{zip_path}")
    print(f"签名文件：{out_path}")
    print("请把该 .sig 与 zip 一起上传到 GitHub Release。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
