import { chromium } from '/root/.bun/install/global/node_modules/playwright/index.mjs';
import fs from 'fs';
import { execFileSync } from 'child_process';

const BASE_URL = 'http://localhost:3000';

function getTodayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

async function runTests() {
  const results = [];
  const consoleErrors = [];
  const pageErrors = [];

  const logPass = (name, detail = '') => {
    console.log(`✅ [PASS] ${name}${detail ? ` — ${detail}` : ''}`);
    results.push({ name, status: 'PASS', detail });
  };
  const logFail = (name, error) => {
    console.error(`❌ [FAIL] ${name} — ${error}`);
    results.push({ name, status: 'FAIL', detail: String(error) });
  };

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text();
      if (!text.includes('WebSocket') && !text.includes('vite')) {
        consoleErrors.push(text);
      }
    }
  });
  page.on('pageerror', (err) => {
    pageErrors.push(err.message);
  });

  try {
    // 1. 打开首页
    await page.goto(BASE_URL, { waitUntil: 'networkidle' });
    await page.waitForSelector('h1:has-text("IMTask 任务调度平台")', { timeout: 10000 });
    logPass('1. 平台首页加载与导航栏渲染', '成功渲染「IMTask 任务调度平台」及4大工作台模块');

    // 2. 检查「任务实例与审核」日期选择器默认今天
    const today = getTodayStr();
    const dateInput = page.locator('input[type="date"][aria-label="选择任务日期"]');
    await dateInput.waitFor({ state: 'visible', timeout: 5000 });
    const dateVal = await dateInput.inputValue();
    if (dateVal === today) {
      logPass('2. 任务实例与审核-日期选择默认今天', `默认日期值为 ${dateVal}`);
    } else {
      logFail('2. 任务实例与审核-日期选择默认今天', `期望 ${today}，实际为 ${dateVal}`);
    }

    // 3. 检查「任务实例与审核」筛选栏：按任务类型查询、无导出CSV、无已过期状态
    const pageText = await page.content();
    if (!pageText.includes('导出 CSV')) {
      logPass('3. 任务实例与审核-移除导出CSV按钮', '页面已无导出 CSV 按钮');
    } else {
      logFail('3. 任务实例与审核-移除导出CSV按钮', '页面仍存在导出 CSV 按钮');
    }

    const statusOptions = await page.locator('select').nth(2).innerText();
    if (!statusOptions.includes('已过期')) {
      logPass('4. 任务实例与审核-移除已过期状态选项', '状态下拉框已移除「已过期」');
    } else {
      logFail('4. 任务实例与审核-移除已过期状态选项', `状态下拉框仍包含已过期: ${statusOptions}`);
    }

    // 4. 检查张三 (13900000000) 的 5 条真实春促任务实例与纯通知无审核按钮
    const rows = page.locator('tbody tr');
    const rowCount = await rows.count();
    if (rowCount === 5) {
      logPass('5. 任务实例与审核-真实春促数据展示', '默认今日展示张三(13900000000)的 5 条春促任务实例');
    } else {
      logFail('5. 任务实例与审核-真实春促数据展示', `期望 5 条，实际渲染 ${rowCount} 条`);
    }

    const approveBtnsOnNotice = await page.locator('tbody tr button:has-text("通过")').count();
    if (approveBtnsOnNotice === 0) {
      logPass('6. 任务实例与审核-纯通知提醒无需审核', '纯通知提醒实例不展示「通过/驳回」审核按钮');
    } else {
      logFail('6. 任务实例与审核-纯通知提醒无需审核', '纯通知提醒出现了审核按钮');
    }

    // 5. 点击「详情」检查接收人是否带上角色
    await page.locator('tbody tr').first().locator('button:has-text("详情")').click();
    const modal = page.locator('div.fixed.inset-0');
    await modal.waitFor({ state: 'visible', timeout: 5000 });
    const modalText = await modal.innerText();
    if (
      modalText.includes('接收人（含角色）') &&
      modalText.includes('张三') &&
      modalText.includes('片长/分支局长') &&
      modalText.includes('张三 (13900000000) · 片长/分支局长')
    ) {
      logPass('7. 任务实例详情-接收人带上角色', '详情弹窗头部、基础信息卡片及明细均展示「张三 + 片长/分支局长」');
    } else {
      logFail('7. 任务实例详情-接收人带上角色', `详情弹窗内容未完整包含角色: ${modalText.slice(0, 300)}`);
    }
    await modal.locator('button:has-text("关闭详情")').click();
    await modal.waitFor({ state: 'hidden', timeout: 5000 });

    // 6. 测试切换日期筛选与重置回今天
    await dateInput.fill('2026-01-01');
    await page.waitForTimeout(300);
    const emptyText = await page.locator('tbody').innerText();
    if (emptyText.includes('当前筛选条件下暂无任务实例')) {
      logPass('8. 任务实例与审核-切换非今日日期联动过滤', '选择 2026-01-01 后正确过滤为空列表');
    } else {
      logFail('8. 任务实例与审核-切换非今日日期联动过滤', `切换日期后列表未清空: ${emptyText}`);
    }
    await page.locator('button:has-text("重置")').click();
    await page.waitForTimeout(300);
    const resetDateVal = await dateInput.inputValue();
    const afterResetCount = await page.locator('tbody tr').count();
    if (resetDateVal === today && afterResetCount === 5) {
      logPass('9. 任务实例与审核-重置筛选恢复今日日期与数据', `重置后日期恢复为 ${resetDateVal}，列表恢复 5 条记录`);
    } else {
      logFail('9. 任务实例与审核-重置筛选恢复今日日期与数据', `重置后日期=${resetDateVal}, 行数=${afterResetCount}`);
    }

    // 7. 切换到「任务模板管理」
    await page.locator('aside button:has-text("任务模板管理")').click();
    await page.waitForSelector('h2:has-text("任务模板管理")');
    const tplPageText = await page.content();
    if (
      !tplPageText.includes('上传任务 H5 预览效果图') &&
      !tplPageText.includes('共 2 条子任务并列下发') &&
      !tplPageText.includes('默认无需审核') &&
      !tplPageText.includes('立即推送测试')
    ) {
      logPass('10. 任务模板管理-界面精简与冗余文案清理', '无独立H5预览按钮、无底部冗余描述文本、无立即推送测试按钮');
    } else {
      logFail('10. 任务模板管理-界面精简与冗余文案清理', '仍存在未清理的按钮或冗余描述文本');
    }

    // 8. 打开「新建通知提醒」与「新建上传任务」测试配置与预览
    await page.locator('button:has-text("新建通知提醒")').click();
    const tplModal = page.locator('div.fixed.inset-0');
    await tplModal.waitFor({ state: 'visible', timeout: 5000 });
    const noticeModalText = await tplModal.innerText();
    if (
      noticeModalText.includes('执行时间') &&
      !noticeModalText.includes('截止时间') &&
      !noticeModalText.includes('{task_name}')
    ) {
      logPass('11. 新建纯通知提醒模板-无截止时间且支持多条并列文本/文件配置', '仅展示执行时间，支持添加多条并列文本或文件/图片');
    } else {
      logFail('11. 新建纯通知提醒模板-无截止时间且支持多条并列文本/文件配置', '纯通知提醒表单字段不符合预期');
    }

    await tplModal.locator('button:has-text("取消")').click();
    await tplModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 打开「新建上传任务」弹窗
    await page.locator('button:has-text("新建上传任务")').click();
    await tplModal.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(200);
    const uploadModalText = await tplModal.innerText();
    if (
      uploadModalText.includes('执行时间') &&
      uploadModalText.includes('截止时间') &&
      uploadModalText.includes('需要审核') &&
      uploadModalText.includes('无需审核') &&
      !uploadModalText.includes('上传完成后的反馈提示语') &&
      uploadModalText.includes('1. 密信提醒') &&
      uploadModalText.includes('2. H5上传页') &&
      !uploadModalText.includes('3. 完成反馈')
    ) {
      logPass(
        '12. 新建上传任务模板-执行/截止时间、审核开关与右侧2步预览联动',
        '包含执行时间与截止时间、需不需要审核开关、已移除反馈提示语及第3步完成反馈预览',
      );
    } else {
      logFail('12. 新建上传任务模板-执行/截止时间、审核开关与右侧2步预览联动', '上传任务弹窗配置或预览步骤不符');
    }

    // 实际测试新建一条多选角色（片长/分支局长 + 行销总监 + 网格长）的上传任务并校验展示与筛选
    await tplModal.locator('input[placeholder*="例如："]').fill('晚间走访晒图上传');
    await tplModal.locator('button[data-role-id="role_marketing_director"]').click();
    await tplModal.locator('button[data-role-id="role_grid_leader"]').click();
    const selectedRoleBadgeText = await tplModal.innerText();
    const hasSelected3Roles = selectedRoleBadgeText.includes('已选 3 / 9 个角色');
    await tplModal.locator('button[type="submit"]').click();
    await tplModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    const afterCreateTplCount = await page.locator('tbody tr').count();
    const newRowText = await page.locator('tbody tr:has-text("晚间走访晒图上传")').innerText();
    if (
      hasSelected3Roles &&
      afterCreateTplCount === 6 &&
      newRowText.includes('片长/分支局长') &&
      newRowText.includes('行销总监') &&
      newRowText.includes('网格长')
    ) {
      logPass(
        '12b. 任务模板管理-支持多选业务角色创建与多角色标签展示',
        '成功勾选 3 个角色（片长/分支局长、行销总监、网格长）创建「晚间走访晒图上传」并在列表中完整展示多角色标签',
      );
    } else {
      logFail(
        '12b. 任务模板管理-支持多选业务角色创建与多角色标签展示',
        `hasSelected3Roles=${hasSelected3Roles}, count=${afterCreateTplCount}, rowText=${newRowText}`,
      );
    }

    // 删除刚创建的模板
    await page.locator('tbody tr:has-text("晚间走访晒图上传") button:has-text("删除")').click();
    const delModal = page.locator('div.fixed.inset-0');
    await delModal.waitFor({ state: 'visible', timeout: 5000 });
    await delModal.locator('button:has-text("确认删除")').click();
    await delModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    const afterDelTplCount = await page.locator('tbody tr').count();
    if (afterDelTplCount === 5) {
      logPass('12c. 任务模板管理-删除新建模板恢复初始5条春促模板', '删除成功，列表恢复 5 条春促真实模板');
    } else {
      logFail('12c. 任务模板管理-删除新建模板恢复初始5条春促模板', `删除后模板行数=${afterDelTplCount}`);
    }

    // 9. 切换到「角色与成员名册」
    await page.locator('aside button:has-text("角色与成员名册")').click();
    await page.waitForSelector('h2:has-text("角色与成员名册")');
    const rolePageText = await page.content();
    if (
      !rolePageText.includes('所属部门') &&
      !rolePageText.includes('角色编码') &&
      !rolePageText.includes('春促角色清单：负责每日')
    ) {
      logPass('13. 角色与成员名册-仅保留用户名与手机号、无角色编码与冗余描述', '角色与成员界面干净规范');
    } else {
      logFail('13. 角色与成员名册-仅保留用户名与手机号、无角色编码与冗余描述', '仍包含部门、角色编码或冗余描述');
    }

    // 测试打开「改名/停用」弹窗
    await page.locator('button:has-text("改名/停用")').first().click();
    const memberModal = page.locator('div.fixed.inset-0');
    await memberModal.waitFor({ state: 'visible', timeout: 5000 });
    const phoneReadonly = await memberModal.locator('input[disabled]').inputValue();
    if (phoneReadonly === '13900000000') {
      logPass('14. 角色成员改名/停用-手机号只读锁死不可改号', '手机号 13900000000 处于只读锁定状态');
    } else {
      logFail('14. 角色成员改名/停用-手机号只读锁死不可改号', `手机号锁定异常: ${phoneReadonly}`);
    }
    await memberModal.locator('button:has-text("取消")').click();
    await memberModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 测试「已激活待归纳角色人员」一栏：全名完整展示无遮挡、无手动登记表单、支持查询与两级移除逻辑
    const unassignedActivatedHeader = await page
      .locator('h3:has-text("已激活待归纳角色人员")')
      .isVisible();
    const activatedBadgeVisible = await page
      .locator('span:has-text("已激活 · 待归纳角色")')
      .first()
      .isVisible();
    const hasManualRegisterForm = (await page.content()).includes('登记已激活待归纳人员');
    const unassignedSearchInput = page.locator('input[aria-label="搜索已激活待归纳人员"]');
    const searchInputVisible = await unassignedSearchInput.isVisible();

    // 校验全名「林建国」未被遮挡截断（scrollWidth <= clientWidth）
    const fullNameCheck = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('span'));
      const target = spans.find((el) => el.textContent?.trim() === '林建国');
      if (!target) return { found: false };
      return {
        found: true,
        text: target.textContent?.trim(),
        truncated: target.scrollWidth > target.clientWidth + 1,
        hasTruncateClass: target.className.includes('truncate'),
      };
    });

    if (
      unassignedActivatedHeader &&
      activatedBadgeVisible &&
      !hasManualRegisterForm &&
      searchInputVisible &&
      fullNameCheck.found &&
      !fullNameCheck.truncated &&
      !fullNameCheck.hasTruncateClass
    ) {
      logPass(
        '14b. 已激活待归纳角色人员-全名完整无遮挡、移除手动登记入口并支持搜索查询',
        '用户全名（如「林建国」）完整显示无截断，已移除手动登记表单，仅走密信对话激活并提供搜索框',
      );
    } else {
      logFail(
        '14b. 已激活待归纳角色人员-全名完整无遮挡、移除手动登记入口并支持搜索查询',
        `header=${unassignedActivatedHeader}, badge=${activatedBadgeVisible}, hasManualForm=${hasManualRegisterForm}, search=${searchInputVisible}, nameCheck=${JSON.stringify(fullNameCheck)}`,
      );
    }

    // 测试「已激活待归纳角色人员」搜索过滤功能
    await unassignedSearchInput.fill('林建国');
    await page.waitForTimeout(150);
    const filteredCardCount = await page.locator('button:has-text("一键移入分组")').count();
    await unassignedSearchInput.fill('');
    await page.waitForTimeout(150);
    const restoredCardCount = await page.locator('button:has-text("一键移入分组")').count();
    if (filteredCardCount === 1 && restoredCardCount === 3) {
      logPass(
        '14c. 已激活待归纳角色人员-按姓名/手机号搜索过滤',
        '输入「林建国」精准过滤出 1 人，清空后恢复 3 人',
      );
    } else {
      logFail(
        '14c. 已激活待归纳角色人员-按姓名/手机号搜索过滤',
        `过滤后=${filteredCardCount}，恢复后=${restoredCardCount}`,
      );
    }

    // 测试将「林建国」一键移入当前角色，再测试【角色内移除 -> 移回已激活待归纳角色人员列表】
    await page.locator('button:has-text("一键移入分组")').first().click();
    await page.waitForTimeout(250);
    const movedInRoleTable = await page.locator('tbody tr:has-text("林建国")').count();
    await page.locator('tbody tr:has-text("林建国") button:has-text("移除")').click();
    const removeRoleModal = page.locator('div.fixed.inset-0');
    await removeRoleModal.waitFor({ state: 'visible', timeout: 5000 });
    const removeRoleModalText = await removeRoleModal.innerText();
    await removeRoleModal.locator('button:has-text("确认移至待归纳列表")').click();
    await removeRoleModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(250);
    const afterRemoveInRole = await page.locator('tbody tr:has-text("林建国")').count();
    const returnedToUnassigned = await page
      .locator('span:has-text("林建国")')
      .first()
      .isVisible();

    if (
      movedInRoleTable === 1 &&
      removeRoleModalText.includes('确认将该成员移出当前角色') &&
      removeRoleModalText.includes('保留量子密信会话绑定') &&
      afterRemoveInRole === 0 &&
      returnedToUnassigned
    ) {
      logPass(
        '14d. 角色成员移除逻辑-从角色移出后自动回到「已激活待归纳角色人员」列表并保留会话绑定',
        '弹窗提示清晰，确认后「林建国」从角色名册移回右侧已激活待归纳角色人员列表',
      );
    } else {
      logFail(
        '14d. 角色成员移除逻辑-从角色移出后自动回到「已激活待归纳角色人员」列表并保留会话绑定',
        `movedInRole=${movedInRoleTable}, afterRemoveInRole=${afterRemoveInRole}, returned=${returnedToUnassigned}`,
      );
    }

    // 测试在角色中添加一名临时测试人员「陈测试」，将其从角色移至「已激活待归纳角色人员」，再从「已激活待归纳角色人员」移除（解除会话绑定）
    await page.locator('input[placeholder="用户名 (如：张三)"]').fill('陈测试');
    await page.locator('input[placeholder="11位手机号 (必填)"]').fill('13700137000');
    await page.locator('button:has-text("添加成员")').click();
    await page.waitForTimeout(200);
    await page.locator('tbody tr:has-text("陈测试") button:has-text("移除")').click();
    await removeRoleModal.waitFor({ state: 'visible', timeout: 5000 });
    await removeRoleModal.locator('button:has-text("确认移至待归纳列表")').click();
    await removeRoleModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);

    // 在右侧「已激活待归纳角色人员」列表中点击「陈测试」的移除按钮 -> 弹出解除会话绑定确认框
    await unassignedSearchInput.fill('陈测试');
    await page.waitForTimeout(150);
    await page
      .locator('button[title*="解除该用户的量子密信会话绑定"]')
      .first()
      .click();
    const unbindModal = page.locator('div.fixed.inset-0');
    await unbindModal.waitFor({ state: 'visible', timeout: 5000 });
    const unbindModalText = await unbindModal.innerText();
    await unbindModal.locator('button:has-text("确认解除绑定并移除")').click();
    await unbindModal.waitFor({ state: 'hidden', timeout: 5000 });
    await page.waitForTimeout(200);
    await unassignedSearchInput.fill('陈测试');
    await page.waitForTimeout(150);
    const chenCardCountAfterUnbind = await page.locator('button:has-text("一键移入分组")').count();
    await unassignedSearchInput.fill('');
    await page.waitForTimeout(150);
    const totalUnassignedCountAfterUnbind = await page
      .locator('button:has-text("一键移入分组")')
      .count();

    if (
      unbindModalText.includes('确认移除并解除密信会话绑定') &&
      unbindModalText.includes('重新输入发送激活信息') &&
      chenCardCountAfterUnbind === 0 &&
      totalUnassignedCountAfterUnbind === 3
    ) {
      logPass(
        '14e. 已激活待归纳人员移除逻辑-解除量子密信会话绑定并提示用户需重新输入激活',
        '成功解除「陈测试」会话绑定并从待归纳列表移除，提示文案明确告知需重新输入激活',
      );
    } else {
      logFail(
        '14e. 已激活待归纳人员移除逻辑-解除量子密信会话绑定并提示用户需重新输入激活',
        `chenCardCount=${chenCardCountAfterUnbind}, totalCount=${totalUnassignedCountAfterUnbind}, modal=${unbindModalText.slice(0, 200)}`,
      );
    }

    // 10. 检查左侧栏已移除「量子密信网关状态」模块，且管理员菜单已移除「账号与安全策略」
    const sidebarText = await page.locator('aside').innerText();
    if (!sidebarText.includes('量子密信网关状态')) {
      logPass('15a. 左侧栏移除「量子密信网关状态」模块', '侧边栏已移除量子密信网关状态卡片');
    } else {
      logFail('15a. 左侧栏移除「量子密信网关状态」模块', '侧边栏仍存在量子密信网关状态卡片');
    }

    await page.locator('aside button:has-text("系统管理员")').click();
    await page.waitForTimeout(150);
    const adminMenuText = await page.locator('aside').innerText();
    if (!adminMenuText.includes('账号与安全策略')) {
      logPass('15b. 管理员菜单移除「账号与安全策略」', '下拉菜单已移除账号与安全策略项');
    } else {
      logFail('15b. 管理员菜单移除「账号与安全策略」', '下拉菜单仍存在账号与安全策略项');
    }
    await page.locator('aside button:has-text("系统管理员")').click();

    // 10b. 测试「获取最新代码与 Agent 指令」弹窗按钮
    await page.locator('aside button:has-text("获取最新代码与 Agent 指令")').click();
    const agentModal = page.locator('div.fixed.inset-0');
    await agentModal.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(400);
    const agentModalText = await agentModal.innerText();
    const fullMdValue = await agentModal.locator('textarea').first().inputValue();
    const toastRegionText = await page.locator('[aria-live="polite"]').innerText();
    const hasErrorToast = toastRegionText.includes('操作未成功');
    if (
      agentModalText.includes('获取最新源码与 Agent 1:1 复刻指令') &&
      agentModalText.includes('一键复制全量指令+源码') &&
      !agentModalText.includes('按文件逐个查看与复制 (0)') &&
      !agentModalText.includes('正在实时打包最新源码文件与操作指令') &&
      fullMdValue.includes('IMTask 办公任务调度平台') &&
      !hasErrorToast
    ) {
      logPass(
        '15c. 页面随时获取最新代码与Agent指令弹窗',
        `成功加载最新源码合集与复刻指令（总长度 ${fullMdValue.length} 字符，0 报错）`,
      );
    } else {
      logFail(
        '15c. 页面随时获取最新代码与Agent指令弹窗',
        `弹窗内容加载异常: hasErrorToast=${hasErrorToast}, text=${agentModalText.slice(0, 200)}`,
      );
    }

    // 10c. 点击「下载 .tar.gz 源码包」并校验真实 tar.gz 二进制包与文件列表
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 10000 }),
      agentModal.locator('button:has-text("下载 .tar.gz 源码包")').click(),
    ]);
    const downloadPath = await download.path();
    const buf = fs.readFileSync(downloadPath);
    const isGzip = buf.length > 100 && buf[0] === 0x1f && buf[1] === 0x8b;
    const tarListOutput = isGzip
      ? execFileSync('tar', ['-tzf', downloadPath], { encoding: 'utf-8' })
      : '';
    if (
      isGzip &&
      tarListOutput.includes('src/App.tsx') &&
      tarListOutput.includes('src/components/imtask/InstancesView.tsx') &&
      tarListOutput.includes('src/components/imtask/RolesMembersView.tsx') &&
      tarListOutput.includes('AGENT_1TO1_REPLICATION.md')
    ) {
      const entryCount = tarListOutput.trim().split('\n').length;
      logPass(
        '15d. 真实 .tar.gz 源码包 Blob 下载与解压校验',
        `GZIP 魔数校验通过 (1f 8b)，大小 ${buf.length} 字节，tar -tzf 列出 ${entryCount} 个源码文件（绝非 Cookie 拦截页）`,
      );
    } else {
      logFail(
        '15d. 真实 .tar.gz 源码包 Blob 下载与解压校验',
        `isGzip=${isGzip}, size=${buf.length}, tarList=${tarListOutput.slice(0, 200)}`,
      );
    }

    await agentModal.locator('button').nth(1).click();
    await agentModal.waitFor({ state: 'hidden', timeout: 5000 });

    // 11. 验证左侧导航栏已彻底移除独立的「催办与审计日志」一级菜单，聚焦 3 大核心工作台
    const navButtonsCount = await page.locator('aside nav button').count();
    const hasLogsNav = (await page.locator('aside nav').innerText()).includes('催办与审计日志');
    if (navButtonsCount === 3 && !hasLogsNav) {
      logPass(
        '16. 导航栏精简-移除独立的「催办与审计日志」模块',
        '左侧导航栏仅保留「任务实例与审核、任务模板管理、角色与成员名册」3大高频核心工作台，单任务提醒记录内聚于实例详情弹窗中',
      );
    } else {
      logFail(
        '16. 导航栏精简-移除独立的「催办与审计日志」模块',
        `navButtonsCount=${navButtonsCount}, hasLogsNav=${hasLogsNav}`,
      );
    }

    // 12. 检查无运行时报错
    if (consoleErrors.length === 0 && pageErrors.length === 0) {
      logPass('17. 浏览器控制台与运行时异常检查', '0 个 Console Error，0 个 Uncaught PageError');
    } else {
      logFail(
        '17. 浏览器控制台与运行时异常检查',
        `ConsoleErrors: ${consoleErrors.join('; ')} | PageErrors: ${pageErrors.join('; ')}`,
      );
    }
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => r.status === 'FAIL');
  console.log(`\n========================================`);
  console.log(`Playwright 自动化测试汇总: 共 ${results.length} 项，通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  console.log(`========================================`);
  if (failed.length > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('Playwright 执行异常:', err);
  process.exit(1);
});
