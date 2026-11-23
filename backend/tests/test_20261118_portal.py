from datetime import timedelta

import pytest

from app.portal import PortalService
from app.state import BizError
from test_submit_feedback_dedupe import _store, _instance
from test_20261117_manual_mark_submitted import ctx
from app.api.events import handle_inbound_text
from app.api.h5 import process_h5_task_info
from app.portal import run_portal_tasks
from app.config import format_dt


@pytest.fixture
def portal(ctx, monkeypatch):
    s, scheduler, notifier, admin, owner, sub, tpl, inst = ctx
    rid = tpl['role_ids'][0]
    p = PortalService(s)
    ident = {'phone': inst['phone'], 'platform': 'zdxlz'}
    # 2026-11-23：授权/选人改走本地名册 dept_path（零远程通讯录调用）——
    # fixture 只把「已导入成员的真实部门」写进名册，不再提供 organization 替身。
    for phone, dept in ((inst['phone'], '本部门'), ('13900009402', '本部门/下级部门'), ('13900009403', '跨部门')):
        if phone != inst['phone']:
            s.add_role_member(rid, phone, '范围成员', owner_id=tpl['owner_id'])
            s.upsert_delivery_target(phone, phone)
        s.conn.execute('UPDATE member SET dept_path=? WHERE role_id=? AND phone=?', (dept, rid, phone))
    s.conn.commit()
    p.save_grant('zdxlz', tpl['owner_id'], inst['phone'], {'enabled': True}, 'tester')
    return p, ident, ctx


def publish(p, ident, **extra):
    return p.create_task(ident, {'name': '工作台任务', 'description': '请提交现场情况',
        'phones': [ident['phone']], 'upload_modules': ['text', 'image'], 'request_id': 'request-00001', 'mode': 'once', **extra})


def test_portal_token_seven_days_and_purpose():
    s = _store()
    inst = _instance(s, 'zdxlz')
    p = PortalService(s)
    raw = p.issue(inst['phone'], 'zdxlz')
    assert p.authenticate(raw)['phone'] == inst['phone']
    start = s.now()
    s._now_fn = lambda: start + timedelta(hours=168)
    with pytest.raises(BizError) as err:
        p.authenticate(raw)
    assert err.value.status_code == 401


def test_portal_unauthorized_create():
    s = _store()
    inst = _instance(s, 'zdxlz')
    p = PortalService(s)
    with pytest.raises(BizError) as err:
        p.create_task({'phone': inst['phone'], 'platform': 'zdxlz'}, {})
    assert err.value.status_code == 403


@pytest.mark.parametrize('text', ['你好', '【待办】', '【菜单】', '1', '我的任务', '未完成'])
def test_all_texts_only_portal_and_callback_retry_once(portal, text):
    p, ident, (s, scheduler, notifier, *_) = portal
    sent = []
    for _ in range(3):
        code, body = handle_inbound_text(state=s, user_id='test_recipient', msg_text=text,
            send=lambda message, tag: sent.append(message), platform='zdxlz', base_url='https://test/prefix', event_id='event-01')
        assert code == 200
    assert len(sent) == 1
    assert sent[0].splitlines()[0] == '【德阳量密助手】您好，请点击下方链接进入个人任务工作台（含待办处理、自主上报、已交回看及授权建任务）：'
    assert len(sent[0].splitlines()) == 2
    assert sent[0].splitlines()[1].startswith('👉 点击进入：https://test/prefix/h5/portal#t=')
    assert '【菜单】' not in sent[0] and '【待办】' not in sent[0]
    assert s.conn.execute('SELECT COUNT(*) FROM h5_token WHERE purpose=?', ('portal',)).fetchone()[0] == 1


def test_default_descendants_cross_department_and_role_scope(portal):
    p, ident, ctx = portal
    s, _, _, _, _, _, tpl, _ = ctx
    assert {m['phone'] for m in p.recipients(ident)['members']} == {ident['phone'], '13900009402'}
    with pytest.raises(BizError) as err:
        publish(p, ident, phones=['13900009403'])
    assert err.value.status_code == 403
    p.save_grant('zdxlz', tpl['owner_id'], ident['phone'], {'enabled': True, 'dept_ids': ['跨部门'], 'role_ids': []}, 't')
    assert {m['phone'] for m in p.recipients(ident)['members']} == {'13900009403'}
    p.save_grant('zdxlz', tpl['owner_id'], ident['phone'], {'enabled': True, 'dept_ids': [], 'role_ids': tpl['role_ids']}, 't')
    assert len(p.recipients(ident)['members']) == 3
    with pytest.raises(BizError):
        p.save_grant('zdxlz', tpl['owner_id'], ident['phone'], {'enabled': True, 'dept_ids': ['forged']}, 't')
    with pytest.raises(BizError):
        p.save_grant('zdxlz', tpl['owner_id'], ident['phone'], {'enabled': True, 'dept_ids': [], 'role_ids': ['foreign']}, 't')
    assert not p.grants({'phone': ident['phone'], 'platform': 'wecom'})


def test_publish_once_three_ticks_completion_and_isolation(portal):
    p, ident, ctx = portal
    s, scheduler, notifier, admin, owner, _, tpl, _ = ctx
    result = publish(p, ident, need_review=True, spec={'upload_modules': ['fields']})
    assert publish(p, ident)['task_id'] == result['task_id']
    for _ in range(3):
        scheduler.run_once()
    portal_messages = [m for m in notifier.sent_messages if '工作台任务' in m.text]
    assert len(portal_messages) == 1
    t = p.template(result['task_id'])
    assert t['need_review'] is False
    assert t['spec_json']['upload_modules'] == ['text', 'image']
    assert result['task_id'] not in {t['template_id'] for t in s.list_templates()['items']}
    inst = next(i for i in p.todos(ident) if i['template_id'] == result['task_id'])
    link = p.task_link(ident, inst['id'])['url'].split('#t=')[1]
    assert process_h5_task_info(state=s, raw_token=link)[0] == 200
    s.create_upload_ref(ref='portal_image', phone=ident['phone'], template_id=t['template_id'], file_name='test.png', mime='image/png', size=10, rel_path='test.png')
    response = s.submit_h5_task(raw_token=link, note='真实提交内容', refs=['portal_image'])
    assert response['status'] == 'approved'
    assert inst['id'] not in {i['id'] for i in p.todos(ident)}
    stats = p.statistics(t)
    assert (stats['periods'][0]['expected'], stats['periods'][0]['submitted'], stats['periods'][0]['pending']) == (1, 1, 0)
    assert stats['periods'][0]['members'][0]['submission']['note'] == '真实提交内容'
    assert owner.get('/api/admin/portal-tasks').status_code == 200
    # 2026-11-20 面板重构：清单端点按「发布人 + 任务」两组返回（tasks 取代旧 items）
    assert owner.get('/api/admin/portal-tasks').json()['tasks'][0]['task_id'] == t['template_id']
    # 2026-11-20 需求：超管全局只读监管放行（授权配置仍 403，由下面 grant 端点守住）
    assert admin.get('/api/admin/portal-tasks').status_code == 200
    assert owner.post(f"/api/admin/instances/{inst['id']}/mark-submitted", json={}).status_code == 403
    assert owner.put('/api/admin/templates/' + t['template_id'], json={'name': '代改'}).status_code == 403
    assert owner.put('/api/admin/portal-tasks/' + t['template_id'], json={}).status_code in (404, 405)


def test_expired_cancelled_disabled_remove_todos(portal):
    p, ident, (s, scheduler, *_) = portal
    task = publish(p, ident, deadline_at=format_dt(s.now() + timedelta(minutes=1)))
    run_portal_tasks(scheduler, s.now())
    inst = next(i for i in p.todos(ident) if i['template_id'] == task['task_id'])
    start = s.now()
    s._now_fn = lambda: start + timedelta(minutes=2)
    assert inst['id'] not in {i['id'] for i in p.todos(ident)}
    with pytest.raises(BizError):
        p.task_link(ident, inst['id'])
    s._now_fn = lambda: start
    s.conn.execute("UPDATE task_instance SET status='cancelled' WHERE id=?", (inst['id'],))
    assert inst['id'] not in {i['id'] for i in p.todos(ident)}


@pytest.mark.parametrize('mode,extra', [('day', {}), ('week', {'anchor_weekday': 2}), ('month', {'anchor_dom': 17})])
def test_periodic_dynamic_deadline_three_ticks(portal, mode, extra):
    p, ident, (s, scheduler, notifier, *_) = portal
    task = publish(p, ident, mode=mode, remind_at='10:00', has_deadline=True,
                   deadline_type='month_end', deadline_time='18:00', **extra)
    for _ in range(3):
        scheduler.run_once()
    inst = next(i for i in p.todos(ident) if i['template_id'] == task['task_id'])
    assert inst['deadline_at'] == '2026-11-30 18:00:00'
    assert len([m for m in notifier.sent_messages if '工作台任务' in m.text]) == 1


def test_http_auth_purpose_and_revocation(portal):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.h5 import create_h5_router
    p, ident, (s, scheduler, _, _, _, _, tpl, inst) = portal
    app = FastAPI()
    app.include_router(create_h5_router(s, s.data_dir / 'uploads', scheduler=scheduler))
    client = TestClient(app)
    assert client.get('/h5/portal').status_code == 200
    assert client.post('/api/h5/portal/info', json={}).status_code == 401

    token = p.issue(ident['phone'], ident['platform'])
    client.headers['Authorization'] = 'Bearer ' + token
    assert client.post('/api/h5/portal/info', json={}).json()['can_create'] is True
    p.save_grant('zdxlz', tpl['owner_id'], ident['phone'], {'enabled': False}, 't')
    assert client.post('/api/h5/portal/create', json={}).status_code == 403
    assert client.post('/api/h5/portal/recipients', json={}).status_code == 403
    client.headers['Authorization'] = 'Bearer ' + s.issue_h5_token(inst['phone'], inst['template_id'], inst['period_key'], inst['id'])
    assert client.post('/api/h5/portal/info', json={}).status_code == 401


def test_admin_scope_platform_and_readonly_boundaries(portal):
    p, ident, (s, scheduler, notifier, admin, owner, sub, tpl, inst) = portal
    foreign_id, foreign = sub('portal_foreign')
    foreign_role = s.create_role('外部管理员分组', owner_id=foreign_id)['role_id']
    # 同手机号同时属于其他管理员、其他平台，才能检出漏过滤。
    s.add_role_member(foreign_role, ident['phone'], '测试成员', owner_id=foreign_id)
    wecom_role = s.create_role('企微同名册', platform='wecom', owner_id=tpl['owner_id'])['role_id']
    s.add_role_member(wecom_role, ident['phone'], '测试成员', owner_id=tpl['owner_id'])
    s.upsert_delivery_target(ident['phone'], 'wecom_recipient', platform='wecom')
    for role in (foreign_role, wecom_role):
        response = owner.put('/api/admin/portal-grants/' + ident['phone'], json={
            'enabled': True, 'dept_ids': [], 'role_ids': [role]})
        assert response.status_code == 403, response.text
    assert admin.put('/api/admin/portal-grants/' + ident['phone'], json={'enabled': True}).status_code == 403
    task = publish(p, ident)
    run_portal_tasks(scheduler, s.now())
    assert foreign.get('/api/admin/portal-tasks').json()['tasks'] == []
    assert owner.get('/api/admin/portal-tasks?platform=wecom').json()['tasks'] == []
    assert not p.grants({'phone': ident['phone'], 'platform': 'wecom'})
    # 停用当前管理范围的成员行，不允许另一管理员或平台的同手机号绕过。
    s.update_role_member(tpl['role_ids'][0], ident['phone'], enabled=False)
    t = p.template(task['task_id'])
    assert not p.can_submit({'phone': ident['phone']}, t)


def test_local_tree_zero_remote_calls_and_directory_failure(portal):
    """2026-11-23：授权弹窗与领导选人完全走本地名册 dept_path —— 0 次远程通讯录调用；
    历史空授权 + 名册无部门时按 503 拒绝（不再远程解析）。"""
    p, ident, (s, _, _, _, _, _, tpl, _) = portal

    class ExplodingContacts:
        def __getattr__(self, name):
            raise AssertionError('授权/选人不允许访问远程通讯录：' + name)

    s.contacts = ExplodingContacts()
    options = p.grant_options('zdxlz', tpl['owner_id'], ident['phone'])
    assert [d['dept_id'] for d in options['departments']] == ['本部门', '本部门/下级部门', '跨部门']
    assert [d['dept_id'] for d in options['selected_departments']] == ['本部门']
    assert options['directory_notice'] == ''
    assert {m['phone'] for m in p.recipients(ident)['members']} == {ident['phone'], '13900009402'}
    with pytest.raises(BizError) as err:
        p.grant_options('zdxlz', tpl['owner_id'], ident['phone'], parent='无此部门')
    assert err.value.status_code == 403
    # 历史空授权 + 名册缺部门：无法定位默认部门 → 503（口径与 2026-11-20 一致）
    s.conn.execute("UPDATE portal_grant SET scope_json=? WHERE phone=? AND platform=?",
                   ('{"dept_ids": [], "role_ids": []}', ident['phone'], 'zdxlz'))
    s.conn.execute('UPDATE member SET dept_path=NULL WHERE role_id=? AND phone=?',
                   (tpl['role_ids'][0], ident['phone']))
    s.conn.commit()
    with pytest.raises(BizError) as err:
        p.recipients(ident)
    assert err.value.status_code == 503


def test_failed_delivery_and_concurrent_ticks_never_duplicate(portal):
    from concurrent.futures import ThreadPoolExecutor
    p, ident, (s, scheduler, notifier, *_) = portal
    result = publish(p, ident)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: run_portal_tasks(scheduler, s.now()), range(3)))
    assert len([m for m in notifier.sent_messages if '工作台任务' in m.text]) == 1
    assert len(s.list_reminder_logs(template_id=result['task_id'])) == 1
    notifier.fail_receive_ids.add('test_recipient')
    result2 = publish(p, ident, request_id='request-failed-02')
    for _ in range(3):
        run_portal_tasks(scheduler, s.now())
    logs = s.list_reminder_logs(template_id=result2['task_id'])
    assert len(logs) == 1 and logs[0]['result'] == 'failed'


def test_local_tree_default_department():
    """2026-11-23：默认部门直接取名册 dept_path——全路径节点、含下级、零远程调用。"""
    s = _store()
    try:
        inst = _instance(s, 'zdxlz')
        p = PortalService(s)
        rid = s.conn.execute('SELECT role_id FROM member WHERE phone=?', (inst['phone'],)).fetchone()[0]
        s.conn.execute('UPDATE member SET dept_path=? WHERE role_id=? AND phone=?',
                       ('官方根部门/官方当前部门', rid, inst['phone']))
        s.conn.commit()
        tree = p.local_dept_tree('zdxlz', 1)
        assert [d['dept_id'] for d in tree['departments']] == ['官方根部门', '官方根部门/官方当前部门']
        assert [d['member_count'] for d in tree['departments']] == [1, 1]
        assert p.local_default_department('zdxlz', 1, inst['phone'])['dept_id'] == '官方根部门/官方当前部门'
        options = p.grant_options('zdxlz', 1, inst['phone'])
        assert options['default_dept_ids'] == ['官方根部门/官方当前部门']
        children = p.grant_options('zdxlz', 1, inst['phone'], parent='官方根部门')['departments']
        assert [d['dept_id'] for d in children] == ['官方根部门/官方当前部门']
    finally:
        s.close()


def test_multiple_admin_grants_do_not_mix_task_ownership(portal):
    p, ident, (s, _, _, _, _, sub, tpl, _) = portal
    foreign_id, foreign = sub('portal_second_owner')
    rid = s.create_role('另一个授权范围', owner_id=foreign_id)['role_id']
    for phone in (ident['phone'], '13900009404'):
        s.add_role_member(rid, phone, '测试成员' if phone == ident['phone'] else '另一范围成员', owner_id=foreign_id)
        if phone != ident['phone']:
            s.upsert_delivery_target(phone, 'recipient_foreign')
    p.save_grant('zdxlz', foreign_id, ident['phone'], {'enabled': True, 'dept_ids': [], 'role_ids': [rid]}, 't')
    with pytest.raises(BizError) as err:
        publish(p, ident, phones=['13900009402', '13900009404'])
    assert err.value.code == 'OWNER_SCOPE_MISMATCH'
    result = publish(p, ident, phones=['13900009404'])
    assert p.template(result['task_id'])['owner_id'] == foreign_id
    assert foreign.get('/api/admin/portal-tasks').json()['tasks'][0]['task_id'] == result['task_id']


def test_http_publish_upload_submit_and_private_attachment(portal):
    from io import BytesIO
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from PIL import Image
    from app.api.h5 import create_h5_router
    p, ident, (s, scheduler, notifier, _, owner, _, _, _) = portal
    app = FastAPI()
    app.include_router(create_h5_router(s, s.data_dir / 'uploads', scheduler=scheduler, notifier=notifier))
    client = TestClient(app)
    headers = {'Authorization': 'Bearer ' + p.issue(ident['phone'], ident['platform'])}
    response = client.post('/api/h5/portal/create', headers=headers, json={
        'name': '图片采集', 'description': '请提交现场照片', 'request_id': 'http-publish-001',
        'phones': [ident['phone']], 'mode': 'once', 'upload_modules': ['text', 'image', 'file']})
    assert response.status_code == 200, response.text
    task_id = response.json()['task_id']
    items = client.post('/api/h5/portal/info', headers=headers, json={}).json()['todos']
    inst = next(i for i in items if i['template_id'] == task_id)
    opened = client.post('/api/h5/portal/open', headers=headers, json={'instance_id': inst['id']})
    raw = opened.json()['url'].split('#t=')[1]
    stream = BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(stream, format='PNG')
    uploaded = client.post('/api/h5/task/upload', data={'token': raw}, files={'file': ('photo.png', stream.getvalue(), 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    ref = uploaded.json()['ref']
    document = b'%PDF-1.4\n'
    uploaded_file = client.post('/api/h5/task/upload', data={'token': raw}, files={'file': ('交接.pdf', document, 'application/pdf')})
    assert uploaded_file.status_code == 200, uploaded_file.text
    file_ref = uploaded_file.json()['ref']
    submitted = client.post('/api/h5/task/submit', json={'token': raw, 'note': '现场正常', 'refs': [ref, file_ref]})
    assert submitted.status_code == 200 and submitted.json()['status'] == 'approved', submitted.text
    attachment = client.post('/api/h5/portal/attachment', headers=headers, json={'task_id': task_id, 'ref': ref})
    assert attachment.status_code == 200 and attachment.content == stream.getvalue()
    download = client.post('/api/h5/portal/attachment', headers=headers, json={'task_id': task_id, 'ref': file_ref})
    assert download.status_code == 200 and download.content == document
    assert download.headers['content-type'] == 'application/pdf'
    assert download.headers['content-disposition'] == "inline; filename*=utf-8''%E4%BA%A4%E6%8E%A5.pdf"
    assert owner.get(f'/api/admin/portal-tasks/{task_id}/attachments/{ref}').content == stream.getvalue()
    assert client.post('/api/h5/portal/attachment', headers=headers, json={'task_id': task_id, 'ref': 'foreign_ref'}).status_code == 404
    assert client.post('/api/h5/portal/attachment', headers=headers, json={'task_id': 'pt_foreign', 'ref': ref}).status_code == 403


def test_once_ignores_hidden_periodic_fields_and_checks_absolute_deadline(portal):
    p, ident, (s, *_) = portal
    deadline = format_dt(s.now() + timedelta(hours=1))
    result = publish(p, ident, remind_at='20:00', has_deadline=True, deadline_time='18:00', deadline_at=deadline)
    assert p.template(result['task_id'])['once_deadline'] == deadline
    with pytest.raises(BizError) as err:
        publish(p, ident, request_id='expired-deadline', deadline_at=format_dt(s.now() - timedelta(seconds=1)))
    assert err.value.code == 'INVALID_DEADLINE'
