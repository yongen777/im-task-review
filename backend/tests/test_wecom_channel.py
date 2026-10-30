# -*- coding: utf-8 -*-
"""企业微信自建应用通道（迁移脚手架）的单元测试。

只测「我们容易写错、写错又不容易发现」的地方：
1. access_token 必须缓存，不能每条消息都 gettoken（会被频率拦截 + 多进程互踢）
2. 42001 时强制刷新并只重试一次
3. invaliduser / unlicenseduser 部分失败时 errcode 仍是 0 —— 必须解析出来
4. agentid 必须是整数、幂等参数要带上

⛔ 不测回调解密（尚未实现，等管理员配好回调再补）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.channels.wecom import WecomClient, WecomNotifier, _split_ids


class FakeResponse:
    def __init__(self, data: Dict[str, Any], status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self) -> Dict[str, Any]:
        return self._data


class FakeHttp:
    """按顺序吐预设响应，并记录每次调用，用于断言调用次数与参数。"""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: List[tuple] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"没有更多预设响应，但收到了 {method} {url}")
        return FakeResponse(self.responses.pop(0))

    def close(self) -> None:
        pass

    def paths(self) -> List[str]:
        return [c[1].split("qyapi.weixin.qq.com")[-1].split("?")[0] for c in self.calls]


def _client(responses: List[Dict[str, Any]]) -> tuple:
    http = FakeHttp(responses)
    c = WecomClient("corp_x", "1000002", "secret_x", http_client=http)
    return c, http


TOKEN_OK = {"errcode": 0, "errmsg": "ok", "access_token": "TOKEN_A", "expires_in": 7200}
SEND_OK = {"errcode": 0, "errmsg": "ok", "invaliduser": "", "unlicenseduser": "", "msgid": "m1"}


# ── 1. token 缓存 ────────────────────────────────────────────────────
def test_token_is_cached_across_messages() -> None:
    c, http = _client([TOKEN_OK, SEND_OK, SEND_OK])
    assert c.send_text("zhangsan", "第一条").ok is True
    assert c.send_text("zhangsan", "第二条").ok is True
    # 两次发送只换一次 token
    assert http.paths() == ["/cgi-bin/gettoken", "/cgi-bin/message/send", "/cgi-bin/message/send"]


def test_token_refreshed_when_close_to_expiry() -> None:
    now = [1000.0]
    http = FakeHttp([TOKEN_OK, SEND_OK, TOKEN_OK, SEND_OK])
    c = WecomClient("corp_x", "1000002", "secret_x", http_client=http, now_fn=lambda: now[0])
    c.send_text("u1", "a")
    now[0] += 7200 - 60  # 距过期只剩 60 秒（小于 300 秒安全窗）→ 应重新取
    c.send_text("u1", "b")
    assert http.paths().count("/cgi-bin/gettoken") == 2


# ── 2. token 失效自动重取（只一次）──────────────────────────────────
def test_token_invalid_triggers_single_retry() -> None:
    c, http = _client([
        TOKEN_OK,
        {"errcode": 42001, "errmsg": "access_token expired"},
        TOKEN_OK,
        SEND_OK,
    ])
    res = c.send_text("u1", "内容")
    assert res.ok is True
    assert http.paths() == [
        "/cgi-bin/gettoken", "/cgi-bin/message/send", "/cgi-bin/gettoken", "/cgi-bin/message/send",
    ]


def test_token_invalid_retry_only_once_then_fail() -> None:
    c, http = _client([
        TOKEN_OK,
        {"errcode": 42001, "errmsg": "expired"},
        TOKEN_OK,
        {"errcode": 42001, "errmsg": "still expired"},
    ])
    res = c.send_text("u1", "内容")
    assert res.ok is False
    assert "42001" in (res.error or "")
    assert http.paths().count("/cgi-bin/message/send") == 2


# ── 3. 部分失败必须解析出来 ─────────────────────────────────────────
def test_invalid_user_reported_as_failure() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 0, "errmsg": "ok", "invaliduser": "zhangsan", "unlicenseduser": ""}])
    res = c.send_text("zhangsan", "内容")
    assert res.ok is False
    assert res.result == "invalid_user"
    assert "可见范围" in (res.error or "")


def test_unlicensed_user_reported_as_failure() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 0, "errmsg": "ok", "invaliduser": "", "unlicenseduser": "lisi"}])
    res = c.send_text("lisi", "内容")
    assert res.ok is False
    assert res.result == "unlicensed"
    assert "基础接口许可" in (res.error or "")


def test_other_users_invalid_does_not_fail_this_one() -> None:
    """别人无效不代表我没送达 —— 不能因为返回里带了 invaliduser 就把所有人都记失败。"""
    c, _ = _client([TOKEN_OK, {"errcode": 0, "errmsg": "ok", "invaliduser": "wangwu", "unlicenseduser": ""}])
    res = c.send_text("zhangsan", "内容")
    assert res.ok is True


def test_userid_compare_is_case_insensitive() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 0, "errmsg": "ok", "invaliduser": "ZhangSan", "unlicenseduser": ""}])
    assert c.send_text("zhangsan", "内容").ok is False


# ── 4. 请求体正确性 ─────────────────────────────────────────────────
def test_message_payload_contains_agentid_and_duplicate_check() -> None:
    c, http = _client([TOKEN_OK, SEND_OK])
    c.send_text("zhangsan", "hello\nworld", idem_key="k1")
    send_kwargs = [k for (m, u, k) in http.calls if u.endswith("/cgi-bin/message/send")][0]
    body = send_kwargs["json"]
    assert body["touser"] == "zhangsan"
    assert body["msgtype"] == "text"
    assert body["agentid"] == 1000002
    assert body["text"]["content"] == "hello\nworld"
    assert body["enable_duplicate_check"] == 1
    assert 0 <= body["duplicate_check_interval"] <= 4 * 3600


def test_agentid_must_be_integer() -> None:
    c, _ = _client([TOKEN_OK, SEND_OK])
    c.agentid_raw = "not-a-number"
    with pytest.raises(ValueError):
        c.send_text("zhangsan", "x")


def test_empty_userid_is_rejected_without_calling_api() -> None:
    c, http = _client([])
    res = c.send_text("  ", "x")
    assert res.ok is False and res.error == "EMPTY_USERID"
    assert http.calls == []


# ── 5. 错误码语义 ───────────────────────────────────────────────────
def test_ip_not_whitelisted_is_not_retryable() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 60020, "errmsg": "not allow to access from your ip"}])
    res = c.send_text("u1", "x")
    assert res.ok is False and res.retryable is False


def test_rate_limited_is_not_retryable() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 45009, "errmsg": "api freq out of limit"}])
    assert c.send_text("u1", "x").retryable is False


def test_all_recipients_invalid_is_not_retryable() -> None:
    c, _ = _client([TOKEN_OK, {"errcode": 81013, "errmsg": "user & party & tag all invalid"}])
    assert c.send_text("u1", "x").retryable is False


# ── 6. notifier 适配层 ──────────────────────────────────────────────
class _Cfg:
    wecom_corpid = "corp_x"
    wecom_agentid = "1000002"
    wecom_secret = "secret_x"


def test_notifier_returns_failure_instead_of_raising() -> None:
    http = FakeHttp([TOKEN_OK, {"errcode": 45009, "errmsg": "freq"}])
    client = WecomClient("corp_x", "1000002", "secret_x", http_client=http)
    n = WecomNotifier(_Cfg(), client=client)
    res = n.send_text("u1", "x", "idem")
    assert res.ok is False and res.result == "failed"


def test_notifier_marks_incomplete_config() -> None:
    class _BadCfg:
        wecom_corpid = "corp_x"
        wecom_agentid = ""
        wecom_secret = ""

    n = WecomNotifier(_BadCfg(), client=WecomClient("corp_x", "", "", http_client=FakeHttp([])))
    assert n.client.is_configured() is False


# ── 7. 工具函数 ─────────────────────────────────────────────────────
def test_split_ids_handles_pipe_and_none() -> None:
    assert _split_ids(None) == set()
    assert _split_ids("") == set()
    assert _split_ids("A|b| C ") == {"a", "b", "c"}
