# -*- coding: utf-8 -*-
"""企业微信回调加解密（官方 WXBizMsgCrypt 的 Python3 移植）。

⚠️ 来源说明
  算法、类名、方法签名、错误码全部取自**企业微信官方 Python 加解密库**
  （官方文档「加解密库下载与返回码」https://developer.work.weixin.qq.com/document/path/90307
   列出的 WXBizMsgCrypt.py，Copyright (c) 1998-2014 Tencent Inc.）。
  官方 zip 需要企业微信扫码登录才能下载，这里是把官方源码**逐行等价移植到 Python3**
  （官方原版是 Python2 语法：reload(sys) / except Exception,e / str 与 bytes 混用）。

  ⛔ 不是自己发明算法：
     · 签名 = sha1( sort([token, timestamp, nonce, encrypt]).join("") )
     · AES-256-CBC，IV = key[:16]，key = base64decode(EncodingAESKey + "=")
     · 明文 = random(16B) + netlen(4B, htonl) + msg + receiveid
     · **PKCS7 块大小是 32**（企微特有，不是常见的 16 —— 这里差一位就会解不出来且不报错）

  如果之后拿到官方 zip，可把官方 WXBizMsgCrypt.py 原样放进本目录替换本文件，
  对外接口（VerifyURL / DecryptMsg / EncryptMsg、返回 (ret, data)）保持完全一致。

  Python3 移植的两处必要改动（不影响协议）：
   1. 全程用 bytes，不再依赖隐式编码；
   2. 签名比较改用 hmac.compare_digest（官方原版用 ==，存在时序侧信道）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import random
import socket
import string
import struct
import time
from typing import Optional, Tuple

from Crypto.Cipher import AES

# ── 官方错误码（ierror.py）──────────────────────────────────────────
WXBizMsgCrypt_OK = 0
WXBizMsgCrypt_ValidateSignature_Error = -40001  # 签名验证错误
WXBizMsgCrypt_ParseXml_Error = -40002           # xml 解析失败
WXBizMsgCrypt_ComputeSignature_Error = -40003   # sha 加密生成签名失败
WXBizMsgCrypt_IllegalAesKey = -40004            # AESKey 非法
WXBizMsgCrypt_ValidateCorpid_Error = -40005     # ReceiveId 校验错误
WXBizMsgCrypt_EncryptAES_Error = -40006         # AES 加密失败
WXBizMsgCrypt_DecryptAES_Error = -40007         # AES 解密失败
WXBizMsgCrypt_IllegalBuffer = -40008            # 解密后得到的 buffer 非法
WXBizMsgCrypt_EncodeBase64_Error = -40009       # base64 编码失败
WXBizMsgCrypt_DecodeBase64_Error = -40010       # base64 解码失败
WXBizMsgCrypt_GenReturnXml_Error = -40011       # 生成 xml 失败

_ERROR_TEXT = {
    WXBizMsgCrypt_OK: "ok",
    WXBizMsgCrypt_ValidateSignature_Error: "签名验证错误",
    WXBizMsgCrypt_ParseXml_Error: "xml 解析失败",
    WXBizMsgCrypt_ComputeSignature_Error: "sha 加密生成签名失败",
    WXBizMsgCrypt_IllegalAesKey: "EncodingAESKey 非法",
    WXBizMsgCrypt_ValidateCorpid_Error: "ReceiveId 校验错误",
    WXBizMsgCrypt_EncryptAES_Error: "AES 加密失败",
    WXBizMsgCrypt_DecryptAES_Error: "AES 解密失败",
    WXBizMsgCrypt_IllegalBuffer: "解密后得到的 buffer 非法",
    WXBizMsgCrypt_EncodeBase64_Error: "base64 编码失败",
    WXBizMsgCrypt_DecodeBase64_Error: "base64 解码失败",
    WXBizMsgCrypt_GenReturnXml_Error: "生成 xml 失败",
}


def error_text(code: int) -> str:
    return _ERROR_TEXT.get(code, f"未知错误 {code}")


class FormatException(Exception):
    """参数格式错误（官方同名异常）。"""


def _to_bytes(value) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


class PKCS7Encoder:
    """官方 PKCS7 填充。⚠️ block_size = 32（企微特有）。"""

    block_size = 32

    def encode(self, text: bytes) -> bytes:
        text_length = len(text)
        amount_to_pad = self.block_size - (text_length % self.block_size)
        if amount_to_pad == 0:
            amount_to_pad = self.block_size
        return text + bytes([amount_to_pad]) * amount_to_pad

    def decode(self, decrypted: bytes) -> bytes:
        pad = decrypted[-1]
        if pad < 1 or pad > 32:
            pad = 0
        return decrypted[:-pad] if pad else decrypted


class Prpcrypt:
    """AES-256-CBC 加解密（官方实现）。"""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.mode = AES.MODE_CBC

    def encrypt(self, text: bytes, receiveid: bytes) -> Tuple[int, Optional[bytes]]:
        # 16 字节随机串 + 4 字节网络序长度 + 明文 + receiveid
        text = self.get_random_str() + struct.pack("I", socket.htonl(len(text))) + text + receiveid
        text = PKCS7Encoder().encode(text)
        cryptor = AES.new(self.key, self.mode, self.key[:16])
        try:
            ciphertext = cryptor.encrypt(text)
        except Exception:  # noqa: BLE001
            return WXBizMsgCrypt_EncryptAES_Error, None
        return WXBizMsgCrypt_OK, base64.b64encode(ciphertext)

    def decrypt(self, text: bytes, receiveid: bytes) -> Tuple[int, Optional[bytes]]:
        try:
            cryptor = AES.new(self.key, self.mode, self.key[:16])
            plain_text = cryptor.decrypt(base64.b64decode(text))
        except Exception:  # noqa: BLE001
            return WXBizMsgCrypt_DecryptAES_Error, None
        try:
            pad = plain_text[-1]
            if not (1 <= pad <= 32):  # 官方原版这里会静默切错，明确挡掉
                return WXBizMsgCrypt_IllegalBuffer, None
            content = plain_text[16:-pad]
            xml_len = socket.ntohl(struct.unpack("I", content[:4])[0])
            xml_content = content[4: xml_len + 4]
            from_receiveid = content[xml_len + 4:]
        except Exception:  # noqa: BLE001
            return WXBizMsgCrypt_IllegalBuffer, None
        if from_receiveid != receiveid:
            return WXBizMsgCrypt_ValidateCorpid_Error, None
        return WXBizMsgCrypt_OK, xml_content

    def get_random_str(self) -> bytes:
        rule = string.ascii_letters + string.digits
        return "".join(random.sample(rule, 16)).encode("utf-8")


class SHA1:
    """官方签名算法：sort([token, timestamp, nonce, encrypt]) 后 sha1。"""

    def getSHA1(self, token: str, timestamp: str, nonce: str, encrypt: str) -> Tuple[int, Optional[str]]:
        try:
            sortlist = [token, timestamp, nonce, encrypt]
            sortlist.sort()
            sha = hashlib.sha1()
            sha.update("".join(sortlist).encode("utf-8"))
            return WXBizMsgCrypt_OK, sha.hexdigest()
        except Exception:  # noqa: BLE001
            return WXBizMsgCrypt_ComputeSignature_Error, None


class XMLParse:
    """官方 XML 提取 / 组包。"""

    AES_TEXT_RESPONSE_TEMPLATE = (
        "<xml>\n"
        "<Encrypt><![CDATA[%(msg_encrypt)s]]></Encrypt>\n"
        "<MsgSignature><![CDATA[%(msg_signaturet)s]]></MsgSignature>\n"
        "<TimeStamp>%(timestamp)s</TimeStamp>\n"
        "<Nonce><![CDATA[%(nonce)s]]></Nonce>\n"
        "</xml>"
    )

    def extract(self, xmltext) -> Tuple[int, Optional[str]]:
        import xml.etree.ElementTree as ET

        try:
            xml_tree = ET.fromstring(_to_bytes(xmltext))
            encrypt = xml_tree.find("Encrypt")
            if encrypt is None or encrypt.text is None:
                return WXBizMsgCrypt_ParseXml_Error, None
            return WXBizMsgCrypt_OK, encrypt.text
        except Exception:  # noqa: BLE001
            return WXBizMsgCrypt_ParseXml_Error, None

    def generate(self, encrypt: str, signature: str, timestamp: str, nonce: str) -> str:
        return self.AES_TEXT_RESPONSE_TEMPLATE % {
            "msg_encrypt": encrypt,
            "msg_signaturet": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        }


class WXBizMsgCrypt:
    """官方接口类：VerifyURL / DecryptMsg / EncryptMsg，返回 (ret, data)。

    ⚠️ receiveid：企业自建应用填 **corpid**（不是 agentid，也不是 userid）。
    """

    def __init__(self, sToken: str, sEncodingAESKey: str, sReceiveId: str) -> None:
        try:
            key = base64.b64decode((sEncodingAESKey or "") + "=")
            if len(key) != 32:
                raise ValueError("AES key must be 32 bytes")
        except Exception:  # noqa: BLE001
            raise FormatException("[error]: EncodingAESKey unvalid !")
        self.key = key
        self.m_sToken = sToken or ""
        self.m_sReceiveId = _to_bytes(sReceiveId or "")

    def VerifyURL(self, sMsgSignature: str, sTimeStamp: str, sNonce: str, sEchoStr: str):
        """回调 URL 验证：验签 → 解密 echostr → 返回明文（调用方须在 1 秒内原样返回）。"""
        ret, signature = SHA1().getSHA1(self.m_sToken, sTimeStamp, sNonce, sEchoStr)
        if ret != WXBizMsgCrypt_OK:
            return ret, None
        if not hmac.compare_digest((signature or ""), (sMsgSignature or "")):
            return WXBizMsgCrypt_ValidateSignature_Error, None
        ret, reply = Prpcrypt(self.key).decrypt(_to_bytes(sEchoStr), self.m_sReceiveId)
        return ret, reply

    def DecryptMsg(self, sPostData, sMsgSignature: str, sTimeStamp: str, sNonce: str):
        """业务回调：从 XML 取 Encrypt → 验签 → 解密 → 返回明文字节。"""
        ret, encrypt = XMLParse().extract(sPostData)
        if ret != WXBizMsgCrypt_OK:
            return ret, None
        ret, signature = SHA1().getSHA1(self.m_sToken, sTimeStamp, sNonce, encrypt)
        if ret != WXBizMsgCrypt_OK:
            return ret, None
        if not hmac.compare_digest((signature or ""), (sMsgSignature or "")):
            return WXBizMsgCrypt_ValidateSignature_Error, None
        return Prpcrypt(self.key).decrypt(_to_bytes(encrypt), self.m_sReceiveId)

    def EncryptMsg(self, sReplyMsg, sNonce: str, timestamp: Optional[str] = None):
        """被动回复：加密 + 生成 XML 包（本轮暂未用到，保留以对齐官方接口）。"""
        ret, encrypt = Prpcrypt(self.key).encrypt(_to_bytes(sReplyMsg), self.m_sReceiveId)
        if ret != WXBizMsgCrypt_OK:
            return ret, None
        ts = timestamp or str(int(time.time()))
        ret, signature = SHA1().getSHA1(self.m_sToken, ts, sNonce, encrypt.decode("utf-8"))
        if ret != WXBizMsgCrypt_OK:
            return ret, None
        return ret, XMLParse().generate(encrypt.decode("utf-8"), signature, ts, sNonce)
