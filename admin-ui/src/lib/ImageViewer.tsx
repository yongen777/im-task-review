import React, { useCallback, useEffect, useRef, useState } from 'react';

/* 2026-11-20 需求 C：后台弹窗统一的全屏看图层（贴微信/系统原生手感）。
   纯黑全屏居中；单击（未放大时）/遮罩/Esc 关闭；双击 1x⇄2x；
   滚轮缩放 1x~4x；放大后鼠标/单指拖拽（触屏双指捏合同步支持）。 */
export interface ViewerSource {
  url: string;
  alt?: string;
}

export const ImageViewer: React.FC<{
  source: ViewerSource | null;
  onClose: () => void;
}> = ({ source, onClose }) => {
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const lastTap = useRef(0);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const pinch = useRef<{ d: number; scale: number } | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (source) {
      setScale(1);
      setOffset({ x: 0, y: 0 });
    }
  }, [source?.url]);

  const clamp = useCallback((nextScale: number, off: { x: number; y: number }) => {
    if (nextScale <= 1) return { x: 0, y: 0 };
    const img = imgRef.current;
    if (!img) return off;
    const maxX = Math.max(0, (img.clientWidth * nextScale - window.innerWidth) / 2);
    const maxY = Math.max(0, (img.clientHeight * nextScale - window.innerHeight) / 2);
    return {
      x: Math.min(maxX, Math.max(-maxX, off.x)),
      y: Math.min(maxY, Math.max(-maxY, off.y)),
    };
  }, []);

  useEffect(() => {
    if (!source) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [source, onClose]);

  if (!source) return null;

  const applyScale = (next: number, keepOffset = false) => {
    const clamped = Math.min(4, Math.max(1, next));
    setScale(clamped);
    if (!keepOffset || clamped <= 1) setOffset(clamped <= 1 ? { x: 0, y: 0 } : offset);
  };

  const toggle = () => {
    if (scale > 1) {
      setScale(1);
      setOffset({ x: 0, y: 0 });
    } else {
      applyScale(2);
    }
  };

  const onImageClick = () => {
    const now = Date.now();
    if (now - lastTap.current < 300) {
      toggle();
      lastTap.current = 0;
      return;
    }
    lastTap.current = now;
    if (scale <= 1) {
      onClose();
      return;
    }
    setScale(1);
    setOffset({ x: 0, y: 0 });
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black select-none"
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      onClick={event => {
        if (event.target === event.currentTarget) onClose();
      }}
      onWheel={event => {
        event.preventDefault();
        applyScale(scale * (event.deltaY < 0 ? 1.2 : 1 / 1.2), true);
      }}
    >
      <img
        ref={imgRef}
        src={source.url}
        alt={source.alt || ''}
        draggable={false}
        className="max-h-screen max-w-screen object-contain"
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          transformOrigin: 'center center',
        }}
        onClick={onImageClick}
        onTouchStart={event => {
          if (event.touches.length === 2) {
            const [a, b] = [event.touches.item(0)!, event.touches.item(1)!];
            pinch.current = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), scale };
            drag.current = null;
          } else if (event.touches.length === 1 && scale > 1) {
            const t = event.touches.item(0)!;
            drag.current = { x: t.clientX, y: t.clientY, ox: offset.x, oy: offset.y };
          } else {
            drag.current = null;
          }
        }}
        onTouchMove={event => {
          if (pinch.current && event.touches.length === 2) {
            event.preventDefault();
            const [a, b] = [event.touches.item(0)!, event.touches.item(1)!];
            const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
            const next = Math.min(4, Math.max(1, pinch.current.scale * (d / pinch.current.d)));
            setScale(next);
            setOffset(next <= 1 ? { x: 0, y: 0 } : offset);
          } else if (drag.current && event.touches.length === 1) {
            event.preventDefault();
            const t = event.touches.item(0)!;
            setOffset(clamp(scale, {
              x: drag.current.ox + (t.clientX - drag.current.x),
              y: drag.current.oy + (t.clientY - drag.current.y),
            }));
          }
        }}
        onTouchEnd={event => {
          if (pinch.current && event.touches.length < 2) pinch.current = null;
          if (drag.current && event.touches.length === 0) drag.current = null;
        }}
        onMouseDown={event => {
          if (scale > 1) {
            event.preventDefault();
            drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y };
            const move = (e: MouseEvent) => {
              if (!drag.current) return;
              setOffset(clamp(scale, {
                x: drag.current.ox + (e.clientX - drag.current.x),
                y: drag.current.oy + (e.clientY - drag.current.y),
              }));
            };
            const up = () => {
              drag.current = null;
              window.removeEventListener('mousemove', move);
              window.removeEventListener('mouseup', up);
            };
            window.addEventListener('mousemove', move);
            window.addEventListener('mouseup', up);
          }
        }}
      />
    </div>
  );
};
