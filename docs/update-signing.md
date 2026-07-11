# 自动更新的 Ed25519 签名 — 启用步骤

当前状态(v0.57):**签名验证代码完整但休眠**。`app/services/update_service.py`
里 `UPDATE_PUBLIC_KEY_B64 = ""`(空)→ `signature_required()` 为 False → 更新只校验
大小 + GitHub 提供的 SHA-256。这挡不住「发布账号被盗、恶意包连同摘要一起被替换」
的威胁。启用签名后,客户端只安装能用你的私钥验证的 zip。

## 一次性启用(3 步)

1. **生成密钥对**(任何机器,建议在你的发布机上):

   ```bash
   python scripts/gen_update_keys.py
   ```

   产物:`secrets/update_private_key.pem`(私钥,**绝不入 git**,`/secrets/` 已在
   .gitignore)+ `secrets/update_public_key.b64`(32 字节公钥的 base64)。

2. **把公钥粘进代码**:打开 `app/services/update_service.py`,把
   `UPDATE_PUBLIC_KEY_B64 = ""` 改成脚本打印的那行。提交这个改动。

3. **备份私钥**到密码管理器/离线介质。私钥丢失 = 以后所有装了新公钥的客户端
   拒绝任何未签名更新,只能让用户手动重装。

## 之后每次发布

`scripts/build_windows.ps1` 会自动:找到 `secrets/update_private_key.pem`
(或 env `SPECIMEN_UPDATE_PRIVATE_KEY` 指定的路径)→ 生成 `<zip>.sig`。
把 **zip 和 .sig 一起**上传到 GitHub Release。忘传 .sig = 客户端拒绝该次更新
(故意的:防「去掉签名」降级攻击)。

## 临时覆盖(测试用)

客户端可用 env `SPECIMEN_UPDATE_PUBKEY` 覆盖内嵌公钥;正式发布不要依赖它。

## 注意

- 启用公钥的那个版本发布**之后**,旧版本客户端(无公钥)仍照常更新——公钥只约束
  装了它的新客户端。
- 千万不要为了"让构建通过"把私钥提交进仓库;build 脚本在缺钥时只是跳过签名并
  打 WARNING,构建不会失败。
