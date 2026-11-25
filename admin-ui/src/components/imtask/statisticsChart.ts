interface DepartmentCounts {
  name: string;
  covered: number;
  done: number;
}

interface StatisticsChart {
  task: string;
  period: string;
  department: string;
  receivedAt: Date;
  isReceipt: boolean;
  total: number;
  done: number;
  children: DepartmentCounts[];
}

const WIDTH = 1224;
const ROW_HEIGHT = 48;
const ROWS_PER_PAGE = 34;
const COLORS = {
  paper: '#F1F5FA', navy: '#112A46', muted: '#47617C', blue: '#2462CF',
  green: '#10785F', orange: '#A64818', border: '#DFE8F2', track: '#E3EBF6',
  stripe: '#F5F8FC', direct: '#EDF3FA',
};
const FONTS = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
const formatCount = new Intl.NumberFormat('zh-CN');
const formatDate = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
});

function completionRate(done: number, covered: number) {
  return covered ? done / covered : 0;
}

function renderPng(chart: StatisticsChart, rows: Array<DepartmentCounts & { rank: number | null }>,
  page: number, pages: number): Promise<Blob> {
  const canvas = document.createElement('canvas');
  const height = 294 + rows.length * ROW_HEIGHT;
  canvas.width = WIDTH * 2;
  canvas.height = height * 2;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('当前浏览器无法生成统计图。');
  const ctx = context;
  ctx.scale(2, 2);
  ctx.textBaseline = 'middle';

  function label(text: string, x: number, y: number, options: {
    color?: string; size?: number; bold?: boolean; align?: CanvasTextAlign; maxWidth?: number;
  } = {}) {
    ctx.fillStyle = options.color ?? COLORS.navy;
    ctx.font = (options.bold ? '700 ' : '400 ') + (options.size ?? 15) + 'px ' + FONTS;
    ctx.textAlign = options.align ?? 'left';
    ctx.fillText(text, x, y, options.maxWidth ?? WIDTH);
  }

  function panel(x: number, y: number, width: number, blockHeight: number) {
    ctx.beginPath();
    ctx.roundRect(x, y, width, blockHeight, 14);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();
    ctx.strokeStyle = COLORS.border;
    ctx.stroke();
  }

  const labels = chart.isReceipt
    ? { expected: '应回复', done: '已回复', pending: '待回复', rate: '回复率' }
    : { expected: '应完成', done: '已完成', pending: '未完成', rate: '完成率' };
  const rate = completionRate(chart.done, chart.total);
  ctx.fillStyle = COLORS.paper;
  ctx.fillRect(0, 0, WIDTH, height);
  label(chart.period + '  /  ' + chart.task, 54, 35, { size: 16, bold: true, color: COLORS.blue, maxWidth: 1116 });
  label(chart.department + ' · 部门' + labels.rate, 54, 81, { size: 35, bold: true, maxWidth: 840 });
  label('更新于 ' + formatDate.format(chart.receivedAt), 1170, 81, { color: COLORS.muted, align: 'right' });

  const summaries = [
    { name: labels.expected + '人数', value: formatCount.format(chart.total), color: COLORS.navy },
    { name: labels.done + '人数', value: formatCount.format(chart.done), color: COLORS.green },
    { name: labels.pending + '人数', value: formatCount.format(chart.total - chart.done), color: COLORS.orange },
    { name: '整体' + labels.rate, value: (rate * 100).toFixed(1) + '%', color: COLORS.blue },
  ];
  summaries.forEach(({ name, value, color }, index) => {
    const x = 54 + index * 284;
    panel(x, 117, 264, 78);
    label(name, x + 19, 141, { color: COLORS.muted, bold: true });
    label(value, x + 19, 170, { size: 28, color, bold: true });
  });

  panel(54, 215, 1116, 50 + rows.length * ROW_HEIGHT);
  const heading = { color: COLORS.muted, bold: true };
  label('排名', 98, 237, { ...heading, align: 'center' });
  label('部门', 136, 237, heading);
  label(labels.rate, 492, 237, heading);
  label(labels.expected, 941, 237, { ...heading, align: 'right' });
  label(labels.done, 1038, 237, { ...heading, align: 'right' });
  label(labels.pending, 1135, 237, { ...heading, align: 'right' });

  rows.forEach((row, index) => {
    const top = 257 + index * ROW_HEIGHT;
    const y = top + ROW_HEIGHT / 2;
    if (row.rank === null || index % 2 === 0) {
      ctx.fillStyle = row.rank === null ? COLORS.direct : COLORS.stripe;
      ctx.fillRect(66, top, 1092, ROW_HEIGHT);
    }
    label(row.rank === null ? '单列' : String(row.rank), 98, y, { color: COLORS.muted, bold: true, align: 'center' });
    const name = { bold: true, size: 16, maxWidth: 335 };
    ctx.font = '700 16px ' + FONTS;
    if (ctx.measureText(row.name).width <= name.maxWidth) {
      label(row.name, 136, y, name);
    } else {
      const chars = Array.from(row.name);
      let split = 1;
      while (split < chars.length && ctx.measureText(chars.slice(0, split + 1).join('')).width <= name.maxWidth) split++;
      label(chars.slice(0, split).join(''), 136, y - 10, name);
      label(chars.slice(split).join(''), 136, y + 10, name);
    }
    const rate = completionRate(row.done, row.covered);
    const color = rate === 1 ? COLORS.green : COLORS.blue;
    ctx.fillStyle = COLORS.track;
    ctx.fillRect(492, y - 7, 285, 14);
    ctx.fillStyle = color;
    ctx.fillRect(492, y - 7, 285 * rate, 14);
    label((rate * 100).toFixed(1) + '%', 851, y, { color, align: 'right', bold: true });
    label(formatCount.format(row.covered), 941, y, { align: 'right' });
    label(formatCount.format(row.done), 1038, y, { color: COLORS.green, align: 'right', bold: true });
    label(formatCount.format(row.covered - row.done), 1135, y, { color: COLORS.orange, align: 'right', bold: true });
  });

  label('IMTask 任务数据统计' + (pages > 1 ? ' · ' + page + ' / ' + pages : ''), 1170, height - 15,
    { color: COLORS.muted, size: 12, align: 'right' });
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      canvas.width = canvas.height = 0;
      if (blob) resolve(blob);
      else reject(new Error('统计图生成失败，请重试。'));
    }, 'image/png');
  });
}

export async function downloadStatisticsChart(chart: StatisticsChart) {
  const covered = chart.children.reduce((sum, row) => sum + row.covered, 0);
  const done = chart.children.reduce((sum, row) => sum + row.done, 0);
  const direct = { name: '本级直属', covered: chart.total - covered, done: chart.done - done };
  if (direct.covered < 0 || direct.done < 0 || direct.done > direct.covered) {
    throw new Error('部门统计合计异常，请刷新后重试。');
  }

  const rows: Array<DepartmentCounts & { rank: number | null }> = chart.children.map((row, index) => ({
    ...row, rank: index + 1,
  }));
  if (!rows.length) rows.push({ ...direct, name: chart.department, rank: 1 });
  else if (direct.covered) rows.push({ ...direct, rank: null });

  const filename = [chart.task, chart.period, chart.department, '统计图'].join('_').replace(/[<>:"/\\|?*]/g, '_');
  const pages = Math.ceil(rows.length / ROWS_PER_PAGE);
  let blob: Blob;
  let extension: string;
  if (pages === 1) {
    blob = await renderPng(chart, rows, 1, pages);
    extension = '.png';
  } else {
    const { default: JSZip } = await import('jszip');
    const zip = new JSZip();
    for (let index = 0; index < pages; index++) {
      const png = await renderPng(chart, rows.slice(index * ROWS_PER_PAGE, (index + 1) * ROWS_PER_PAGE), index + 1, pages);
      zip.file(filename + '_' + (index + 1) + '.png', await png.arrayBuffer());
    }
    blob = await zip.generateAsync({ type: 'blob' });
    extension = '.zip';
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename + extension;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
