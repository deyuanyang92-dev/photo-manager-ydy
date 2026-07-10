"""生成软件更新签名用的 Ed25519 密钥对。

用法：
    python scripts/gen_update_keys.py [输出目录]

生成两个文件（默认写到 ./secrets/，该目录应加入 .gitignore，切勿提交私钥）：
    update_private_key.pem   —— 私钥，**只保存在发布者手里**，用于 sign_release.py 签名
    update_public_key.b64    —— 公钥（base64，32 字节），粘贴到
                                app/services/update_service.py 的 UPDATE_PUBLIC_KEY_B64

一旦在源码里配置了公钥，应用就会强制校验每个更新包的签名。
"""

from __future__ import annotations

import base64
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("secrets")
    out_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode("ascii")

    priv_path = out_dir / "update_private_key.pem"
    pub_path = out_dir / "update_public_key.b64"
    priv_path.write_bytes(priv_pem)
    pub_path.write_text(pub_b64 + "\n", encoding="utf-8")

    print("已生成更新签名密钥对：")
    print(f"  私钥（保密，勿提交）: {priv_path}")
    print(f"  公钥（base64）      : {pub_path}")
    print()
    print("请把下面这行公钥粘贴到 app/services/update_service.py：")
    print(f'  UPDATE_PUBLIC_KEY_B64 = "{pub_b64}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
