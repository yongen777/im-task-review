# -*- coding: utf-8 -*-
"""通讯录响应信封（/open/v2/*）解密探针 —— 2026-10-27

背景
----
官方《API 调用流程》§5 明确：密文信封为 {sign, timeStamp, nonce, encrypt}，
解密「以对接包或内部工程中的 EncryptionUtils.decryptV2(appId, appSecret, data) 为准」。
但公开交付物（callback-demo Go/Java、quantum-msg-sdk 四个 jar + Go SDK）经核验
**均不含该类**（连 decryptV2 / open/v2 符号都没有），算法未公开。

本脚本用途
----------
1. 抓一份真实的 /open/v2/user/search 密文信封；
2. 用三套假设穷举：回调同构方案、国密 SM3/SM4、以及「密钥材料直接当 key」；
3. 一旦命中就打印明文并返回退出码 0；全部失败返回 1。

拿到官方算法后，用 --custom-key 直接验：
    python scripts/dec_v2_probe.py --custom-key <64位hex或base64>  --iv head|nonce|zero

注意
----
- 凭据只从 .env / 环境变量读，本脚本不落任何密钥到磁盘。
- 密文信封会缓存到 data/env_search.json，便于反复试而不重复请求平台。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import itertools
import json
import pathlib
import sys
from typing import Iterable, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util.Padding import unpad  # noqa: E402

try:  # 国密可选，缺库时自动跳过
    from gmssl import sm3 as gm_sm3
    from gmssl.sm4 import SM4_DECRYPT, CryptSM4
    HAS_GM = True
except Exception:  # pragma: no cover
    HAS_GM = False

import httpx  # noqa: E402

from app.channels.zdxlz import ZdxlzChannel  # noqa: E402
from app.config import Config  # noqa: E402

SEPS = ["", ":", "|", "_", ","]
CACHE = ROOT / "data" / "env_search.json"
MARKERS = ('"users"', '"name"', '"customer_id"', '"code"', "郑")


# ────────────────────────────── 抓信封 ──────────────────────────────
def fetch_envelopes(cfg: Config, name: str, use_cache: bool = True) -> dict:
    if use_cache and CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("name") == name and cached.get("envelopes"):
            print("· 使用缓存信封 %s" % CACHE)
            return cached
    token = ZdxlzChannel(cfg).get_access_token()
    url = cfg.api_base.rstrip("/") + "/open/v2/user/search"
    headers = {"Authorization": "Bearer %s" % token,
               "Content-Type": "application/json; charset=utf-8"}
    envelopes = []
    for _ in range(2):
        resp = httpx.post(url, json={"name": name}, headers=headers, timeout=15)
        body = resp.json()
        envelopes.append({"http": resp.status_code, "code": body.get("code"),
                          "msg": body.get("msg"), "data": body.get("data") or {}})
    payload = {"appId": cfg.app_id, "name": name, "envelopes": envelopes}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("· 已抓取 %d 组信封 → %s" % (len(envelopes), CACHE))
    return payload


# ────────────────────────────── 工具 ──────────────────────────────
def decode(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def ok_key(key: bytes) -> bool:
    return len(key) in (16, 24, 32)


def is_json_like(plain: bytes) -> bool:
    try:
        text = plain.decode("utf-8")
    except UnicodeDecodeError:
        return False
    text = text.rstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10")
    if "{" not in text or "}" not in text:
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable / max(len(text), 1) > 0.95


def materials(cfg: Config) -> dict:
    """所有可能被当作主密钥的材料。"""
    out = {}
    sec = cfg.app_secret.encode()
    out["appSecret"] = sec
    out["sha256(appSecret)"] = hashlib.sha256(sec).digest()
    out["md5(appSecret)"] = hashlib.md5(sec).digest()
    out["sha256(appId+appSecret)"] = hashlib.sha256(
        (cfg.app_id + cfg.app_secret).encode()).digest()
    if cfg.verify_token:
        vt = cfg.verify_token.encode()
        out["verifyToken"] = vt
        out["sha256(verifyToken)"] = hashlib.sha256(vt).digest()
        out["md5(verifyToken)"] = hashlib.md5(vt).digest()
    if cfg.encrypt_key:
        for pad in ("=", ""):
            try:
                raw = base64.b64decode(cfg.encrypt_key + pad)
            except Exception:
                continue
            if ok_key(raw):
                out["encryptKey(b64)"] = raw
                break
    if len(sec) % 4 == 0:
        try:
            raw = base64.b64decode(sec + b"=" * (-len(sec) % 4))
            if ok_key(raw):
                out["appSecret(b64)"] = raw
        except Exception:
            pass
    return {k: v for k, v in out.items() if ok_key(v)}


def derived(app_id: str, secret: str, ts: str, nonce: str) -> Iterable[Tuple[str, bytes]]:
    """token 顺序 × 分隔符 × 摘要算法（sha256/sm3/md5）派生。"""
    values = {"appId": app_id, "appSecret": secret, "ts": ts, "nonce": nonce}
    names = list(values)
    seen = set()
    for length in range(1, 5):
        for order in itertools.permutations(names, length):
            for sep in SEPS:
                text = sep.join(values[t] for t in order)
                algos = [("sha256", hashlib.sha256(text.encode()).digest()),
                         ("md5", hashlib.md5(text.encode()).digest())]
                if HAS_GM:
                    algos.append(("sm3", bytes.fromhex(
                        gm_sm3.sm3_hash(list(text.encode())))))
                for algo, digest in algos:
                    if len(digest) != 32:
                        continue
                    for vname, key in (("raw32", digest), ("head16", digest[:16])):
                        tag = "%s(%s)/%s" % (algo, sep.join(order), vname)
                        if tag not in seen:
                            seen.add(tag)
                            yield tag, key


def iv_candidates(raw: bytes, nonce: str, ts: str) -> List[Tuple[str, bytes]]:
    out = [("head", raw[:16]), ("nonce", bytes.fromhex(nonce)[:16]),
           ("zero", b"\x00" * 16), ("tail", raw[-16:])]
    out.append(("tsascii", ts.encode()[:16].ljust(16, b"\x00")))
    return [(n, v) for n, v in out if len(v) == 16]


# ────────────────────────────── 三个假设 ──────────────────────────────
def stage_sign(app_id: str, secret: str, ts: str, nonce: str, enc: str,
               sign: str, mats: dict) -> Optional[str]:
    """假设一：sign 与回调同构（sha256 / sm3 / hmac），只是密钥材料不同。"""
    values = {"appId": app_id, "appSecret": secret, "ts": ts,
              "nonce": nonce, "encrypt": enc}
    names = list(values)
    tried = 0
    for length in range(1, 6):
        for order in itertools.permutations(names, length):
            for sep in SEPS:
                text = sep.join(values[t] for t in order).encode()
                tried += 1
                if hashlib.sha256(text).hexdigest() == sign.lower():
                    return "sha256(%s)" % sep.join(order)
                if HAS_GM and gm_sm3.sm3_hash(list(text)) == sign.lower():
                    return "sm3(%s)" % sep.join(order)
                for mname, mkey in mats.items():
                    if hmac.new(mkey, text, hashlib.sha256).hexdigest() == sign.lower():
                        return "hmac(%s, %s)" % (mname, sep.join(order))
    print("  sign 反推：✗（试 %d 组）" % tried)
    return None


def stage_decrypt(raw: bytes, keys: Iterable[Tuple[str, bytes]], cfg: Config,
                  ts: str, nonce: str, skip_sm4: bool = False) -> Optional[dict]:
    bodies = [("head16", raw[16:]), ("full", raw)]
    ivs = iv_candidates(raw, nonce, ts)
    count = 0
    for label, key in keys:
        for iv_name, iv in ivs:
            for body_name, body in bodies:
                if len(body) % 16:
                    continue
                count += 1
                for mode in ("CBC", "CFB", "OFB"):
                    try:
                        cipher = {"CBC": lambda: AES.new(key, AES.MODE_CBC, iv),
                                  "CFB": lambda: AES.new(key, AES.MODE_CFB, iv),
                                  "OFB": lambda: AES.new(key, AES.MODE_OFB, iv)}[mode]()
                        plain = cipher.decrypt(body)
                    except Exception:
                        continue
                    if mode == "CBC":
                        try:
                            plain = unpad(plain, 16)
                        except ValueError:
                            continue
                    if is_json_like(plain):
                        return {"alg": "AES-%s" % mode, "key": label,
                                "iv": iv_name, "body": body_name,
                                "plain": plain.decode("utf-8", "ignore"),
                                "tried": count}
                if HAS_GM and not skip_sm4:
                    try:
                        sm4 = CryptSM4()
                        sm4.set_key(key, SM4_DECRYPT)
                        plain = sm4.crypt_cbc(iv, body)
                    except Exception:
                        plain = b""
                    if plain and is_json_like(plain):
                        return {"alg": "SM4-CBC", "key": label, "iv": iv_name,
                                "body": body_name,
                                "plain": plain.decode("utf-8", "ignore"),
                                "tried": count}
    print("  解密穷举：✗（试 %d 组）" % count)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="通讯录响应信封解密探针")
    parser.add_argument("--name", default="张三", help="user/search 的查询名")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新抓信封")
    parser.add_argument("--custom-key", help="已知算法时直接验：64 位 hex")
    parser.add_argument("--custom-iv", default="head",
                        choices=["head", "nonce", "zero", "tail"])
    parser.add_argument("--no-sm4", action="store_true", help="跳过 SM4（更快）")
    args = parser.parse_args()

    cfg = Config.from_env()
    payload = fetch_envelopes(cfg, args.name, use_cache=not args.refresh)
    mats = materials(cfg)
    print("· 密钥材料：%s" % ", ".join(sorted(mats)))
    print("· 国密可用：%s" % ("是" if HAS_GM else "否（缺 gmssl）"))

    for index, env in enumerate(payload["envelopes"], start=1):
        data = env.get("data") or {}
        if not data.get("encrypt"):
            print("\n=== 信封 %d：响应里没有密文（code=%s msg=%s）"
                  % (index, env.get("code"), env.get("msg")))
            continue
        ts, nonce = data["timeStamp"], data["nonce"]
        enc, sign = data["encrypt"], data["sign"]
        print("\n=== 信封 %d（ts=%s，密文 %d 字符）===" % (index, ts, len(enc)))

        if stage_sign(cfg.app_id, cfg.app_secret, ts, nonce, enc, sign, mats):
            print("  ⚠️ sign 命中，说明信封与回调同构，可据此收窄解密候选")

        raw = decode(enc)
        if args.custom_key:
            key = bytes.fromhex(args.custom_key) if len(args.custom_key.rstrip("=")) == 64 \
                and all(c in "0123456789abcdefABCDEF" for c in args.custom_key) \
                else base64.b64decode(args.custom_key + "=" * (-len(args.custom_key) % 4))
            iv = dict(iv_candidates(raw, nonce, ts))[args.custom_iv]
            hit = stage_decrypt(raw, [("custom", key)], cfg, ts, nonce,
                                skip_sm4=args.no_sm4)
            if hit:
                print("✅ 命中！%s key=%s iv=%s" % (hit["alg"], hit["key"], hit["iv"]))
                print(hit["plain"][:600])
                return 0
            print("  custom key 未命中")
            continue

        keys = list(materials(cfg).items()) + list(
            derived(cfg.app_id, cfg.app_secret, ts, nonce))
        hit = stage_decrypt(raw, keys, cfg, ts, nonce, skip_sm4=args.no_sm4)
        if hit:
            print("\n✅ 命中！%s  key=%s  iv=%s  body=%s（第 %d 组）"
                  % (hit["alg"], hit["key"], hit["iv"], hit["body"], hit["tried"]))
            print(hit["plain"][:600])
            return 0

    print("\n❌ 全部假设未命中。结论：该信封不是回调同构方案，"
          "标准 KDF（sha256/md5/sm3 × 顺序 × 分隔符）也不成立。")
    print("   下一步：向平台索取 EncryptionUtils.decryptV2 实现，或一组「明文+密文」对照。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
