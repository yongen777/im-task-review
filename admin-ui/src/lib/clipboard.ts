/* ================================================================== */
/* 剪贴板复制（2026-10-30）                                             */
/* ------------------------------------------------------------------ */
/* 踩过的坑：后台用 http://内网IP:9013 访问，属于「非安全上下文」，       */
/* navigator.clipboard 直接是 undefined，复制按钮点了只会报「不允许」。   */
/* 这里做两级降级：                                                     */
/*   1) 安全上下文（https / localhost）→ navigator.clipboard            */
/*   2) 其它（内网 http）→ 隐藏 textarea + document.execCommand('copy') */
/* ================================================================== */

export async function copyText(text: string): Promise<boolean> {
  const val = text || '';
  if (!val.trim()) return false;

  // ① 现代 API（要求 secure context）
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(val);
      return true;
    }
  } catch {
    /* 权限被拒 → 继续走降级 */
  }

  // ② 兼容方案：iOS Safari / 内网 http 都能用
  try {
    const ta = document.createElement('textarea');
    ta.value = val;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, val.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
