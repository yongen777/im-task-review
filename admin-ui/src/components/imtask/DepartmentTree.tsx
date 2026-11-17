import React, { useState } from 'react';
import { Building2, Check, ChevronRight } from 'lucide-react';

export interface DeptTreeNode {
  name: string;
  path: string;
  count: number;
  children: DeptTreeNode[];
}

export function DepartmentTree({ nodes, selected, onSelect }: {
  nodes: DeptTreeNode[];
  selected?: string;
  onSelect?: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(() => new Set(nodes.flatMap(function ancestors(node): string[] {
    return selected?.startsWith(`${node.path}/`) ? [node.path, ...node.children.flatMap(ancestors)] : [];
  })));

  const render = (branch: DeptTreeNode[]): React.ReactNode => branch.map((node) => {
    const hasChildren = node.children.length > 0;
    const isExpanded = expanded.has(node.path);
    const active = selected === node.path;
    const rowClass = `flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs ${active ? 'bg-blue-50 text-blue-700' : ''}`;
    const label = <>
      {hasChildren && <ChevronRight aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 ${isExpanded ? 'rotate-90' : ''}`} />}
      <Building2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-blue-500" />
      <span className="min-w-0 flex-1 break-words" title={node.path}>{node.name}</span>
      <span className="shrink-0 text-slate-500 tabular-nums">{node.count} 人</span>
    </>;
    return (
      <li key={node.path}>
        <div className="flex items-center gap-1">
          {hasChildren ? (
            <button type="button" aria-expanded={isExpanded} className={`${rowClass} hover:bg-slate-50 cursor-pointer`}
              onClick={() => setExpanded((current) => {
                const next = new Set(current);
                if (next.has(node.path)) next.delete(node.path);
                else next.add(node.path);
                return next;
              })}>
              {label}
            </button>
          ) : onSelect ? (
            <button type="button" aria-pressed={active} onClick={() => onSelect(node.path)} className={`${rowClass} hover:bg-blue-50 cursor-pointer`}>
              {label}{active && <Check className="h-3.5 w-3.5 shrink-0" />}
            </button>
          ) : <div className={rowClass}>{label}</div>}
          {hasChildren && onSelect && (
            <button type="button" aria-label={`选择${node.name}`} aria-pressed={active} onClick={() => onSelect(node.path)}
              className="shrink-0 rounded-lg px-2 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 cursor-pointer">
              {active ? '已选' : '选择'}
            </button>
          )}
        </div>
        {isExpanded && hasChildren && <ul className="ml-4 border-l border-slate-100 pl-2">{render(node.children)}</ul>}
      </li>
    );
  });
  return <ul>{render(nodes)}</ul>;
}
