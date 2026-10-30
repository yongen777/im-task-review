# -*- coding: utf-8 -*-
"""企业微信「自建应用」通道（IMTask 迁移脚手架）。

2026-10-30 起，IMTask 从量子密信迁向通用办公平台，第一步是企微自建应用。
本模块只做**发消息**这一层，接口严格按官方文档：

- 获取 access_token：https://developer.work.weixin.qq.com/document/path/91039
- 发送应用消息：    https://developer.work.weixin.qq.com/document/path/90372
- 上传临时素材：    https://developer.work.weixin.qq.com/document/path/90253

⛔ 尚未实现（等管理员配好回调 URL / Token / EncodingAESKey 再补）：
   POST 回调的验签与 AES 解密。加解密**必须用官方加解密库**，自己写 padding 差一位就解不出来且不报错。

三个必须踩准的点（都写在方法注释里）：
1. access_token 必须单例缓存 + 提前刷新 + 遇 42001 强制重取 —— 多进程各自 gettoken 会互相踢掉
2. 部分接收人无效时接口**仍返回 errcode=0**，失败信息只在 invaliduser / unlicenseduser 里
3. media_id 只有 3 天有效期，不能提前批量传好存库
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:  # httpx 与其它通道保持一致；测试可注入假 client
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .zdxlz import SendResult

logger = logging.getLogger(__name__)

# access_token 失效相关返回码：42001 过期、40014 不合法、42007 用户凭证变更
_TOKEN_INVALID_CODES = {40014, 42001, 42007}


class WecomError(Exception):
    """调用企微接口失败（带 errcode，便于上层判断是否重试）。"""

    def __init__(self, errcode: int, errmsg: str) -> None:
        super().__init__(f"[{errcode}] {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg


class WecomClient:
    """企业微信 API 客户端（只实现当前需要的能力，不追求全覆盖）。"""

    DEFAULT_API_BASE = "https://qyapi.weixin.qq.com"
    # 官方说有效期 7200s，且可能被提前失效 —— 提前 5 分钟刷新，别卡在边界上
    TOKEN_SAFETY_SECONDS = 300

    def __init__(
        self,
        corpid: str,
        agentid: str,
        secret: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        http_client: Optional[Any] = None,
        timeout: float = 10.0,
        now_fn=time.time,
    ) -> None:
        self.corpid = (corpid or "").strip()
        self.agentid_raw = (agentid or "").strip()
        self.secret = (secret or "").strip()
        self.api_base = (api_base or self.DEFAULT_API_BASE).rstrip("/")
        self._now = now_fn
        self._lock = threading.Lock()
        self._token: str = ""
        self._expires_at: float = 0.0
        self._client = http_client or (httpx.Client(timeout=timeout) if httpx is not None else None)

    # ── 基础 ────────────────────────────────────────────────────────
    @property
    def agentid(self) -> int:
        try:
            return int(self.agentid_raw)
        except (TypeError, ValueError):
            # agentid 在 message/send 里是整型；配错就尽早炸，别等线上发不出去
            raise ValueError(f"企业微信 agentid 必须是整数，当前为 {self.agentid_raw!r}")

    def is_configured(self) -> bool:
        return bool(self.corpid and self.agentid_raw and self.secret)

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        if self._client is None:  # pragma: no cover
            raise WecomError(-1, "httpx 未安装，无法调用企业微信接口")
        url = f"{self.api_base}{path}"
        resp = self._client.request(method, url, **kwargs)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise WecomError(-1, f"返回非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}")
        return data

    # ── access_token ───────────────────────────────────────────────
    def get_token(self, *, force: bool = False) -> str:
        """取 access_token（带缓存）。

        ⚠️ 官方原文两条硬要求，这里都满足了：
        1. 「需要缓存 access_token，不能频繁调用 gettoken 接口，否则会受到频率拦截」
        2. 「企业微信可能会出于运营需要提前使 access_token 失效，应实现失效时重新获取」
        所以：双检锁（多线程只放一个出去）+ 提前 5 分钟过期 + 上层遇 42001 时 force=True 重取。
        """
        now = self._now()
        with self._lock:
            if not force and self._token and now < self._expires_at - self.TOKEN_SAFETY_SECONDS:
                return self._token
            data = self._request(
                "GET",
                "/cgi-bin/gettoken",
                params={"corpid": self.corpid, "corpsecret": self.secret},
            )
            errcode = int(data.get("errcode") or 0)
            if errcode != 0:
                raise WecomError(errcode, str(data.get("errmsg") or "gettoken failed"))
            token = str(data.get("access_token") or "")
            if not token:
                raise WecomError(-1, "gettoken 返回空 access_token")
            self._token = token
            # expires_in 官方说明通常 7200；缺省按 7200 兜底
            self._expires_at = now + float(data.get("expires_in") or 7200)
            return token

    # ── 发消息 ─────────────────────────────────────────────────────
    def send_text(
        self,
        userid: str,
        content: str,
        *,
        idem_key: str = "",
        enable_duplicate_check: bool = True,
        duplicate_check_interval: int = 1800,
        safe: bool = False,
    ) -> SendResult:
        """给单个成员发文本消息。

        与量子密信最大的差别：**不需要用户先发过消息**。只要该 userid 在应用可见范围内、
        且有基础接口许可，就能直接推 —— 迁移后「待归纳池 → 绑定」那条链路可以整条删掉。

        ⚠️ 部分接收人无效时企微**仍返回 errcode=0**，失败只在 invaliduser / unlicenseduser 里，
        所以必须解析这两个字段，否则会把「没送达」记成成功。
        """
        target = (userid or "").strip()
        if not target:
            return SendResult(ok=False, result="failed", retryable=False, error="EMPTY_USERID")
        body: Dict[str, Any] = {
            "touser": target,
            "msgtype": "text",
            "agentid": self.agentid,
            "text": {"content": content},
            "safe": 1 if safe else 0,
        }
        if enable_duplicate_check:
            # 企微侧兜底去重：默认 30 分钟内同样请求不重复下发（我们自己的 idem_key 仍照记）
            body["enable_duplicate_check"] = 1
            body["duplicate_check_interval"] = max(0, min(int(duplicate_check_interval), 4 * 3600))

        data = self._post_message(body)
        errcode = int(data.get("errcode") or 0)

        # token 失效：强制刷新后重试一次（只重试一次，避免死循环）
        if errcode in _TOKEN_INVALID_CODES:
            self.get_token(force=True)
            data = self._post_message(body)
            errcode = int(data.get("errcode") or 0)

        if errcode != 0:
            errmsg = str(data.get("errmsg") or "")
            # 60020 = IP 不在白名单；45009 = 接口调用超限；两者都不是「重试能好」的
            retryable = errcode not in (60020, 45009, 81013)
            return SendResult(
                ok=False, result="failed", status_code=200, retryable=retryable,
                error=f"{errcode}: {errmsg}",
            )

        invalids = _split_ids(data.get("invaliduser"))
        unlicensed = _split_ids(data.get("unlicenseduser"))
        low = target.lower()
        if low in invalids:
            return SendResult(ok=False, result="invalid_user", status_code=200,
                              retryable=False, error="INVALIDUSER: 该成员不在应用可见范围内")
        if low in unlicensed:
            return SendResult(ok=False, result="unlicensed", status_code=200,
                              retryable=False, error="UNLICENSED: 该成员没有基础接口许可")
        # 即使返回了别人无效，只要本条目标不在其中，就算送达
        if invalids or unlicensed:
            logger.warning("企业微信部分接收人无效 invalid=%s unlicensed=%s", invalids, unlicensed)
        return SendResult(ok=True, result="sent", status_code=200)

    def _post_message(self, body: Dict[str, Any]) -> Dict[str, Any]:
        token = self.get_token()
        return self._request(
            "POST",
            "/cgi-bin/message/send",
            params={"access_token": token},
            json=body,
        )

    # ── 网页授权（P3 预留）─────────────────────────────────────────
    def get_userid_by_code(self, code: str) -> Optional[str]:
        """用 OAuth2 的 code 换成员 userid（静默授权，不弹窗）。

        ⚠️ 本轮只提供能力，不接前端；返回 None 表示凭证无效或成员不在可见范围。
        """
        clean = (code or "").strip()
        if not clean:
            return None
        data = self._request(
            "GET",
            "/cgi-bin/auth/getuserinfo",
            params={"access_token": self.get_token(), "code": clean},
        )
        if int(data.get("errcode") or 0) != 0:
            logger.warning("code 换 userid 失败: %s %s", data.get("errcode"), data.get("errmsg"))
            return None
        userid = str(data.get("userid") or "").strip()
        return userid or None

    # ── 图片素材 ───────────────────────────────────────────────────
    def upload_image(self, file_path: str) -> str:
        """上传图片拿 media_id。⚠️ media_id 只有 3 天有效，必须现发现传，不能存库复用。"""
        path = Path(file_path)
        if not path.is_file():
            raise WecomError(-1, f"图片不存在: {file_path}")
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            raise WecomError(-1, "企业微信图片仅支持 JPG / PNG")
        token = self.get_token()
        with path.open("rb") as fh:
            data = self._request(
                "POST",
                "/cgi-bin/media/upload",
                params={"access_token": token, "type": "image"},
                files={"media": (path.name, fh, "application/octet-stream")},
            )
        errcode = int(data.get("errcode") or 0)
        if errcode != 0:
            raise WecomError(errcode, str(data.get("errmsg") or "media/upload failed"))
        media_id = str(data.get("media_id") or "")
        if not media_id:
            raise WecomError(-1, "media/upload 未返回 media_id")
        return media_id

    def close(self) -> None:
        try:
            if self._client is not None and hasattr(self._client, "close"):
                self._client.close()
        except Exception:  # noqa: BLE001
            pass


class WecomNotifier:
    """把 WecomClient 适配成 scheduler 需要的 NotifierProtocol。

    receive_id 语义变化：
    - 量子密信：会话 external_id（用户必须先发消息才有）
    - 企业微信：成员 userid（管理员导入 / OAuth2 静默获取，不需要用户先说话）
    """

    def __init__(self, config: Any, *, client: Optional[WecomClient] = None) -> None:
        corpid = getattr(config, "wecom_corpid", "")
        agentid = getattr(config, "wecom_agentid", "")
        secret = getattr(config, "wecom_secret", "")
        self.client = client or WecomClient(corpid, agentid, secret)
        if not self.client.is_configured():
            # 配置不全时不要静默失败：启动期就喊出来，避免以为是「发了但没收到」
            logger.error(
                "企业微信通道配置不完整（corpid/agentid/secret 有空值），发送会全部失败。"
                " 需要在 .env 里补 IMT_WECOM_CORPID / IMT_WECOM_AGENTID / IMT_WECOM_SECRET"
            )

    def send_text(self, receive_id: str, text: str, idem_key: str) -> SendResult:
        try:
            return self.client.send_text(receive_id, text, idem_key=idem_key)
        except WecomError as exc:
            logger.warning("企业微信发送失败 to=%s: %s", receive_id, exc)
            # 42001 之外的配置类错误都不值得重试
            return SendResult(ok=False, result="failed", retryable=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("企业微信发送异常 to=%s: %s", receive_id, exc)
            return SendResult(ok=False, result="failed", retryable=True, error=f"EXCEPTION: {exc}")


def build_oauth_url(config: Any, *, state: str = "") -> Optional[str]:
    """构造网页授权链接（snsapi_base 静默授权，直接拿 userid）。

    ⚠️ P3 阶段才启用，本轮只预留构造器，不接前端：
       前置条件是管理后台「网页授权及 JS-SDK」里配置了**可信域名**（需域名归属验证），
       没配之前跳转会直接被拦，且报错信息不直观。

    授权回调 `redirect_uri?code=CODE` 后，用 code 换 userid：
       GET https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo?access_token=xx&code=CODE
    （见 WecomClient.get_userid_by_code）
    """
    corpid = str(getattr(config, "wecom_corpid", "") or "").strip()
    agentid = str(getattr(config, "wecom_agentid", "") or "").strip()
    redirect = str(getattr(config, "wecom_oauth_redirect", "") or "").strip()
    if not (corpid and agentid and redirect):
        return None
    from urllib.parse import quote

    return (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={quote(corpid, safe='')}"
        f"&redirect_uri={quote(redirect, safe='')}"
        "&response_type=code"
        "&scope=snsapi_base"
        f"&state={quote(state or '', safe='')}"
        f"&agentid={quote(agentid, safe='')}"
        "#wechat_redirect"
    )


def _split_ids(raw: Any) -> set:
    """企微返回的 invaliduser/unlicenseduser 是 'a|b|c' 形式。"""
    if not raw:
        return set()
    if isinstance(raw, (list, tuple)):
        return {str(x).strip().lower() for x in raw if str(x).strip()}
    return {part.strip().lower() for part in str(raw).split("|") if part.strip()}
