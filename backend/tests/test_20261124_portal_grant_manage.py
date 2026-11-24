# -*- coding: utf-8 -*-
"""2026-11-24 授权成员管理闭环专项（对应「授权成员发布任务」页四项交互重构）。

覆盖：
  ① GET /api/admin/portal-grants 已授权清单：姓名/手机号/部门/管辖范围摘要/已发布任务数，
     支持按姓名与手机号搜索；名册停用成员 in_roster/deliverable 标记；
  ② GET /api/admin/portal-grant-candidates 可授权成员搜索：仅本管理员名下已启用且
     已激活可投递成员（与 save_grant 名册校验同源），越搜不到越界数据；
  ③ 取消授权闭环：PUT enabled=false → H5 新建 403、已发周期任务调度跳过、
     历史任务与统计在监管面板完整保留可导出；超管写/读授权管理接口一律 403；
  ④ 归属隔离：别的 sub_admin 在清单/候选里都看不到、改不了本管理员的授权。
"""
from datetime import datetime

import pytest

from app.portal import PortalService, run_portal_tasks
from app.state import BizError
from test_20261117_manual_mark_submitted import ctx
from test_20261120_portal_panel import MEMBER_B, publish

NOW = datetime(2026, 11, 17, 10, 0)


@pytest.fixture
def grants(ctx):
    """owner 名下 3 名成员：领导（授权）、B（授权+已发任务）、未授权者。"""
    s, scheduler, notifier, admin, owner, sub, tpl, inst = ctx
    p = PortalService(s)
    leader = {'phone': inst['phone'], 'platform': 'zdxlz'}
    outsider_phone = '13900009403'
    s.add_role_member(tpl['role_ids'][0], MEMBER_B, '范围甲', owner_id=tpl['owner_id'])
    s.upsert_delivery_target(MEMBER_B, MEMBER_B)
    s.add_role_member(tpl['role_ids'][0], outsider_phone, '未授权成员', owner_id=tpl['owner_id'])
    s.upsert_delivery_target(outsider_phone, outsider_phone)
    for phone, dept in ((inst['phone'], '本部门'), (MEMBER_B, '本部门/下级部门'), (outsider_phone, '跨部门')):
        s.conn.execute('UPDATE member SET dept_path=? WHERE role_id=? AND phone=?',
                       (dept, tpl['role_ids'][0], phone))
    s.conn.commit()
    p.save_grant('zdxlz', tpl['owner_id'], leader['phone'],
                 {'enabled': True, 'dept_ids': ['本部门'], 'role_ids': []}, 't')
    p.save_grant('zdxlz', tpl['owner_id'], MEMBER_B,
                 {'enabled': True, 'dept_ids': [], 'role_ids': tpl['role_ids']}, 't')
    return p, leader, outsider_phone, ctx


def test_grant_list_shape_and_search(grants):
    p, leader, outsider, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = grants
    data = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).json()
    rows = {row['phone']: row for row in data['members']}
    assert set(rows) == {inst['phone'], MEMBER_B}
    me = rows[inst['phone']]
    assert (me['name'], me['dept_path'], me['scope_summary']) == ('测试成员', '本部门', '本部门')
    assert me['scope_dept_ids'] == ['本部门'] and me['deliverable'] is True
    assert me['task_count'] == 0
    b = rows[MEMBER_B]
    assert b['scope_dept_ids'] == [] and b['role_names'] == ['代交组']
    assert b['scope_summary'] == ''  # 只勾角色时部门摘要为空

    # 发布任务后 task_count 上涨；搜索：按姓名 / 按手机号 / 无结果
    publish(p, leader, request_id='grant-list-0001')
    data = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).json()
    assert next(r for r in data['members'] if r['phone'] == inst['phone'])['task_count'] == 1
    by_name = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz', 'q': '测试成员'}).json()['members']
    by_phone = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz', 'q': MEMBER_B}).json()['members']
    assert [r['phone'] for r in by_name] == [inst['phone']]
    assert [r['phone'] for r in by_phone] == [MEMBER_B]
    assert owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz', 'q': '不存在的人'}).json()['members'] == []

    # 名册停用：in_roster 仍 True（档案在），deliverable 转 False（提示可取消授权）
    s.update_role_member(tpl['role_ids'][0], MEMBER_B, enabled=False)
    data = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).json()
    row = next(r for r in data['members'] if r['phone'] == MEMBER_B)
    assert row['in_roster'] is True and row['deliverable'] is False
    s.update_role_member(tpl['role_ids'][0], MEMBER_B, enabled=True)


def test_grant_candidates_only_deliverable_own_members(grants):
    p, leader, outsider, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = grants
    data = owner.get('/api/admin/portal-grant-candidates', params={'platform': 'zdxlz'}).json()
    phones = {row['phone'] for row in data['members']}
    # 名册全部 3 人都是已启用已激活 → 候选含全部（授权状态在 UI 层标注，不再排除）
    assert phones == {inst['phone'], MEMBER_B, outsider}
    assert all(row['dept_path'] for row in data['members'])
    # 按姓名/手机号过滤
    by_name = owner.get('/api/admin/portal-grant-candidates', params={'platform': 'zdxlz', 'q': '范围'}).json()['members']
    assert [r['phone'] for r in by_name] == [MEMBER_B]
    # 停用成员从候选消失（save_grant 同口径会 403）
    s.update_role_member(tpl['role_ids'][0], outsider, enabled=False)
    phones = {row['phone'] for row in owner.get(
        '/api/admin/portal-grant-candidates', params={'platform': 'zdxlz'}).json()['members']}
    assert outsider not in phones
    with pytest.raises(BizError):
        p.save_grant('zdxlz', tpl['owner_id'], outsider, {'enabled': True}, 't')
    s.update_role_member(tpl['role_ids'][0], outsider, enabled=True)


def test_cancel_grant_full_lifecycle(grants):
    p, leader, outsider, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = grants
    task = publish(p, leader, request_id='cancel-0001')
    run_portal_tasks(scheduler, NOW)

    # 取消授权（PUT enabled=false，二次确认后的落库动作）
    response = owner.put(f'/api/admin/portal-grants/{leader["phone"]}',
                         params={'platform': 'zdxlz'}, json={'enabled': False})
    assert response.status_code == 200, response.text
    # H5 端立即失效
    with pytest.raises(BizError):
        p.create_task(leader, {'name': 'x', 'description': 'y', 'phones': [MEMBER_B],
                               'upload_modules': ['text'], 'request_id': 'after-cancel', 'mode': 'once'})
    # 调度跳过已撤授权领导（不再补发/新建实例）
    before = s.conn.execute('SELECT COUNT(*) FROM task_instance WHERE template_id=?',
                            (task['task_id'],)).fetchone()[0]
    run_portal_tasks(scheduler, NOW)
    after = s.conn.execute('SELECT COUNT(*) FROM task_instance WHERE template_id=?',
                           (task['task_id'],)).fetchone()[0]
    assert before == after
    # 监管面板：历史任务与已发实例完整保留
    data = owner.get('/api/admin/portal-tasks', params={'platform': 'zdxlz'}).json()
    assert task['task_id'] in {t['task_id'] for t in data['tasks']}
    assert data['grant_count'] == 1  # 还剩 B
    members = owner.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).json()['members']
    assert {m['phone'] for m in members} == {MEMBER_B}
    # 重新授权后恢复（修改闭环：改回 enabled 并调整范围到「跨部门」子树）
    response = owner.put(f'/api/admin/portal-grants/{leader["phone"]}',
                         params={'platform': 'zdxlz'},
                         json={'enabled': True, 'dept_ids': ['跨部门'], 'role_ids': []})
    assert response.status_code == 200
    scope = p.recipients(leader)
    # 选择范围 =「跨部门」子树（含 outsider）；领导本人属「本部门」不在可选中，属正确口径
    assert {m['phone'] for m in scope['members']} == {outsider}
    assert [d['dept_id'] for d in scope['departments']] == ['跨部门']


def test_super_readonly_and_cross_admin_isolation(grants):
    p, leader, outsider, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = grants
    # 超管：授权管理读写一律 403（SUPER_ADMIN_READONLY，保持纯只读）
    assert admin.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).status_code == 403
    assert admin.get('/api/admin/portal-grant-candidates', params={'platform': 'zdxlz'}).status_code == 403
    assert admin.put(f'/api/admin/portal-grants/{leader["phone"]}',
                     params={'platform': 'zdxlz'}, json={'enabled': False}).status_code == 403
    # 交叉管理员：清单/候选均为空，改不了别人的授权
    _, others = sub('grant_manage_outsider')
    assert others.get('/api/admin/portal-grants', params={'platform': 'zdxlz'}).json()['members'] == []
    assert others.get('/api/admin/portal-grant-candidates', params={'platform': 'zdxlz'}).json()['members'] == []
    assert others.put(f'/api/admin/portal-grants/{leader["phone"]}',
                      params={'platform': 'zdxlz'}, json={'enabled': False}).status_code == 403
    # 本管理员授权未被破坏
    assert p.grants(leader)
