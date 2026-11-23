"""2026-11-20 授权成员发布任务：只读监管面板重构 + 领导周期任务对齐管理员。

覆盖：
  ① 按领导（发布人）筛选任务列表与任务元数据（canonical 归属周期）；
  ② sub_admin 交叉隔离 / super_admin 全局只读 / pt_ 任务绝不进入管理员三大面板（红线）；
  ③ 监管面板明细（全部/已完成/未完成 + 搜索）与 .xlsx 导出（部门汇总口径 + 成员明细）；
  ④ 领导周期任务：周容器 canonical 键、同执行日幂等、跨执行日只催未完成、待办实时联动、
     免审直通与提交记录回看；
  ⑤ 撤销授权：新建 403、调度跳过、历史任务与统计在监管面板完整保留可导出。
"""
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.config import format_dt
from app.portal import PortalService, run_portal_tasks
from app.state import BizError
from test_submit_feedback_dedupe import _store, _instance
from test_20261117_manual_mark_submitted import ctx

NOW = datetime(2026, 11, 17, 10, 0)  # 周二 · ISO 2026-W47
WED = datetime(2026, 11, 18, 10, 0)  # 周三（第二执行日）
MEMBER_B = '13900009402'


@pytest.fixture
def panel(ctx, monkeypatch):
    s, scheduler, notifier, admin, owner, sub, tpl, inst = ctx
    p = PortalService(s)
    leader = {'phone': inst['phone'], 'platform': 'zdxlz'}
    ident_b = {'phone': MEMBER_B, 'platform': 'zdxlz'}
    # 2026-11-23：授权弹窗与领导选人全部改走本地名册 dept_path（零远程通讯录调用），
    # 因此 fixture 只需把成员的真实部门写进名册，不再需要 organization/department_path 替身。
    s.add_role_member(tpl['role_ids'][0], MEMBER_B, '范围甲', owner_id=tpl['owner_id'])
    s.upsert_delivery_target(MEMBER_B, MEMBER_B)
    # 监管面板的部门维度口径：成员名册部门（leader 归「本部门」，B 归「本部门/下级部门」）
    s.conn.execute('UPDATE member SET dept_path=? WHERE role_id=? AND phone=?',
                   ('本部门', tpl['role_ids'][0], inst['phone']))
    s.conn.execute('UPDATE member SET dept_path=?, dept=? WHERE role_id=? AND phone=?',
                   ('本部门/下级部门', '下级部门', tpl['role_ids'][0], MEMBER_B))
    s.conn.commit()
    p.save_grant('zdxlz', tpl['owner_id'], leader['phone'], {'enabled': True}, 't')
    p.save_grant('zdxlz', tpl['owner_id'], MEMBER_B, {'enabled': True}, 't')
    return p, leader, ident_b, ctx


def publish(p, ident, **extra):
    return p.create_task(ident, {'name': '工作台任务', 'description': '请提交现场情况',
        'phones': [ident['phone']], 'upload_modules': ['text', 'image'],
        'request_id': 'req-00001', 'mode': 'once', **extra})


def publish_weekly(p, leader):
    """周容器任务：周二+周三执行 · 本周日 18:00 截止（canonical kind=week）。"""
    return p.create_task(leader, {
        'name': '每周情况收集', 'description': '请补充本周情况',
        'phones': [leader['phone'], MEMBER_B], 'upload_modules': ['text', 'image'],
        'request_id': 'req-weekly-01', 'mode': 'day', 'remind_at': '10:00',
        'exec_weekdays': [2, 3], 'has_deadline': True,
        'deadline_type': 'weekend', 'deadline_time': '18:00',
    })


def submit(p, s, phone, instance):
    ident = {'phone': phone, 'platform': 'zdxlz'}
    link = p.task_link(ident, instance['id'])['url'].split('#t=')[1]
    s.create_upload_ref(ref='panel_image', phone=phone, template_id=instance['template_id'],
                        file_name='shot.png', mime='image/png', size=10, rel_path='shot.png')
    return s.submit_h5_task(raw_token=link, note='现场已核实', refs=['panel_image'])


# ── ① 发布人维度与监管面板结构 ────────────────────────────────────────────

def test_publisher_dimension_and_task_meta(panel):
    p, leader, ident_b, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task_a = publish(p, leader, request_id='req-once-0001')
    task_b = publish_weekly(p, leader)
    task_c = publish(p, ident_b, name='甲的任务', request_id='req-once-0003')

    data = owner.get('/api/admin/portal-tasks').json()
    assert [x['task_id'] for x in data['tasks']] == [task_a['task_id'], task_b['task_id'], task_c['task_id']]
    pubs = {x['phone']: x for x in data['publishers']}
    assert (pubs[leader['phone']]['name'], pubs[leader['phone']]['task_count']) == ('测试成员', 2)
    assert (pubs[MEMBER_B]['name'], pubs[MEMBER_B]['task_count']) == ('范围甲', 1)
    assert 'dept_path' in pubs[leader['phone']]

    # 按发布人筛选：任务与发布人联动收窄
    filtered = owner.get('/api/admin/portal-tasks', params={'creator_phone': MEMBER_B}).json()
    assert [x['task_id'] for x in filtered['tasks']] == [task_c['task_id']]
    assert [x['phone'] for x in filtered['publishers']] == [MEMBER_B]

    meta = {x['task_id']: x for x in data['tasks']}
    assert meta[task_a['task_id']]['current_period_key'] == 'once'
    assert meta[task_b['task_id']]['statistics_period'] == 'week'
    assert meta[task_b['task_id']]['current_period_key'] == '2026-W47'
    assert meta[task_c['task_id']]['creator_phone'] == MEMBER_B

    # 跑一轮调度后：周容器任务按归属周期建实例（整周一个键，不再是执行日日键）
    run_portal_tasks(scheduler, NOW)
    rows = s.conn.execute('SELECT period_key, deadline_at FROM task_instance WHERE template_id=?',
                          (task_b['task_id'],)).fetchall()
    assert {r['period_key'] for r in rows} == {'2026-W47'}
    assert rows[0]['deadline_at'] == '2026-11-22 18:00:00'
    refreshed = owner.get('/api/admin/portal-tasks').json()
    assert refreshed['tasks'][1]['period_keys'] == ['2026-W47']

    # 监管概览与任务数据统计同口径 KPI
    overview = owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/overview",
                         params={'period_key': '2026-W47'}).json()
    assert (overview['kpis']['total'], overview['kpis']['not_done']) == (2, 2)


# ── ② 监管隔离与数据红线 ───────────────────────────────────────────────────

def test_sub_isolation_super_readonly_and_redline(panel):
    p, leader, ident_b, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task_a = publish(p, leader, request_id='req-once-0001')
    task_b = publish_weekly(p, leader)
    run_portal_tasks(scheduler, NOW)

    # 交叉管理员：别的 sub_admin 看不到本管理员的授权任务（列表空 + 详情 403）
    _, outsider = sub('portal_panel_other')
    assert outsider.get('/api/admin/portal-tasks').json()['tasks'] == []
    assert outsider.get(f"/api/admin/portal-tasks/{task_a['task_id']}/overview",
                        params={'period_key': 'once'}).status_code == 403
    assert outsider.get(f"/api/admin/portal-tasks/{task_a['task_id']}/export",
                        params={'period_key': 'once'}).status_code == 403

    # 超管：全局只读放行（监管面板），但任务数据统计面板仍 403（既有口径不动）
    super_tasks = {x['task_id'] for x in admin.get('/api/admin/portal-tasks').json()['tasks']}
    assert super_tasks == {task_a['task_id'], task_b['task_id']}
    assert admin.get(f"/api/admin/portal-tasks/{task_b['task_id']}/overview",
                     params={'period_key': '2026-W47'}).json()['kpis']['total'] == 2
    assert admin.get('/api/admin/stats-panel/tasks').status_code == 403
    # 授权配置仍仅限归属分级管理员：超管保持只读
    assert admin.get(f"/api/admin/portal-grants/{leader['phone']}").status_code == 403

    # ⛔ 红线：pt_ 任务绝不进入管理员的模板 / 实例 / 任务数据统计三大面板
    assert owner.get('/api/admin/instances', params={'template_id': task_a['task_id']}).json()['total'] == 0
    assert owner.get('/api/admin/stats-panel/overview',
                     params={'template_id': task_a['task_id'], 'period_key': 'once'}).status_code == 404
    assert owner.get('/api/admin/stats-panel/export',
                     params={'template_id': task_a['task_id'], 'period_key': 'once'}).status_code == 404
    listed = owner.get('/api/admin/stats-panel/tasks').json()['items']
    assert all(not item['template_id'].startswith('pt_') for item in listed)


# ── ③ 明细切换 / 搜索 / 导出 ───────────────────────────────────────────────

def test_instances_tabs_search_and_export(panel):
    p, leader, ident_b, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task_b = publish_weekly(p, leader)
    run_portal_tasks(scheduler, NOW)
    inst_leader = next(i for i in p.todos(leader) if i['template_id'] == task_b['task_id'])
    assert submit(p, s, leader['phone'], inst_leader)['status'] == 'approved'  # 免审直通

    base = f"/api/admin/portal-tasks/{task_b['task_id']}/instances"
    params = {'period_key': '2026-W47'}
    assert owner.get(base, params=params).json()['total'] == 2
    assert owner.get(base, params={**params, 'status_group': 'done'}).json()['total'] == 1
    assert owner.get(base, params={**params, 'status_group': 'todo'}).json()['total'] == 1
    assert owner.get(base, params={**params, 'q': '范围甲'}).json()['total'] == 1
    rows = owner.get(base, params=params).json()['items']
    done_row = next(r for r in rows if r['phone'] == leader['phone'])
    assert done_row['status'] == 'approved'
    assert done_row['content']['note'] == '现场已核实'
    assert done_row['content']['attachments'][0]['url'].startswith('/api/admin/uploads/')

    # 部门下钻：选「本部门」子树 = 两人（leader 已完成）；「下级部门」= 仅 B（未完成）
    overview = owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/overview",
                         params={**params, 'dept_path': '本部门'}).json()
    assert (overview['kpis']['total'], overview['kpis']['done']) == (2, 1)
    overview_sub = owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/overview",
                             params={**params, 'dept_path': '本部门/下级部门'}).json()
    assert (overview_sub['kpis']['total'], overview_sub['kpis']['done']) == (1, 0)

    # 导出 .xlsx：成员明细 + 完成状态 + 提交内容（stats_export 同一出口）
    exported = owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/export", params=params)
    assert exported.status_code == 200
    assert exported.headers['Content-Disposition'].startswith('attachment')
    values = list(load_workbook(BytesIO(exported.content), read_only=True).active.values)
    header = values[0]
    assert list(header[:7]) == ['序号', '姓名', '手机号', '所属部门（末级）', '完整部门路径', '完成状态', '提交时间']
    assert '补充说明' in header and '上报图片' in header
    leader_row = next(r for r in values if r and str(r[2]) == leader['phone'])
    assert '已完成' in str(leader_row[5]) and leader_row[3]
    todo_row = next(r for r in values if r and str(r[2]) == MEMBER_B)
    assert todo_row[5] == '待上传'


# ── ④ 周期对齐：canonical 键 / 幂等 / 只催未完成 / 待办联动 ─────────────────

def test_weekly_task_canonical_idempotent_and_todo_sync(panel):
    p, leader, ident_b, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task_b = publish_weekly(p, leader)
    original_now = s._now_fn

    # 执行日 1（周二）：建实例并发送 1 条；同日第三次 tick 不刷屏（幂等红线）
    for _ in range(3):
        run_portal_tasks(scheduler, NOW)
    # FakeNotifier 按 receive_id 记录（leader 的绑定 external_id = test_recipient）
    def portal_texts(recv):
        return [m for m in notifier.sent_messages if '每周情况收集' in m.text and m.receive_id == recv]
    assert len(portal_texts('test_recipient')) == 1
    assert len(portal_texts(MEMBER_B)) == 1

    # 工作台待办实时出现 → 提交即 approved（免审直通）→ 待办立即移除、进入提交记录
    inst_leader = next(i for i in p.todos(leader) if i['template_id'] == task_b['task_id'])
    assert submit(p, s, leader['phone'], inst_leader)['status'] == 'approved'
    assert all(i['template_id'] != task_b['task_id'] for i in p.todos(leader))
    history = p.submission_history(leader)
    assert any(h['template_id'] == task_b['task_id'] for h in history)

    # 截止前未完成成员可在下一执行日被再催、已完成成员不再被催（方案 A 对齐）
    s._now_fn = scheduler._now_fn = (lambda: WED)
    run_portal_tasks(scheduler, WED)
    assert len(portal_texts('test_recipient')) == 1       # 已完成：跳过
    assert len(portal_texts(MEMBER_B)) == 2               # 仍未完成：再催一次
    # 周期内仍只有一个实例（同周期按人去重）
    rows = s.conn.execute('SELECT phone, COUNT(*) c FROM task_instance WHERE template_id=? GROUP BY phone',
                          (task_b['task_id'],)).fetchall()
    assert all(r['c'] == 1 for r in rows)

    # H5「我发布的任务」按归属周期按人去重展示
    stats = p.statistics(p.template(task_b['task_id']))
    assert stats['periods'][0]['period_key'] == '2026-W47'
    assert (stats['periods'][0]['expected'], stats['periods'][0]['submitted']) == (2, 1)
    s._now_fn = scheduler._now_fn = original_now


# ── ⑤ 撤销授权：新建拦截 · 调度跳过 · 历史保留 ─────────────────────────────

def test_revoke_grant_blocks_new_and_schedule_keeps_history(panel):
    p, leader, ident_b, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = panel
    task_a = publish(p, leader, request_id='req-once-0001')
    task_b = publish_weekly(p, leader)
    run_portal_tasks(scheduler, NOW)

    p.save_grant('zdxlz', tpl['owner_id'], leader['phone'], {'enabled': False}, 't')
    with pytest.raises(BizError) as err:
        publish(p, leader, request_id='req-once-0002')
    assert err.value.status_code == 403

    s._now_fn = scheduler._now_fn = (lambda: WED)
    sent_before = len(notifier.sent_messages)
    run_portal_tasks(scheduler, WED)
    assert len(notifier.sent_messages) == sent_before  # 定时调度器跳过其后续周期推送

    # 历史任务与统计数据在监管面板完整保留：可查、可下钻、可导出
    data = owner.get('/api/admin/portal-tasks').json()
    assert {x['task_id'] for x in data['tasks']} >= {task_a['task_id'], task_b['task_id']}
    overview = owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/overview",
                         params={'period_key': '2026-W47'}).json()
    assert overview['kpis']['total'] == 2
    assert owner.get(f"/api/admin/portal-tasks/{task_b['task_id']}/export",
                     params={'period_key': '2026-W47'}).status_code == 200
