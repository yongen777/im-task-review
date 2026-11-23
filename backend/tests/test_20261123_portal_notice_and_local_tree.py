"""2026-11-23 大需求专项：本地部门树 + 手机端跳转 + 领导 H5 通知任务 + 监管口径。

覆盖：
  ① 授权弹窗部门树 / 领导选人**完全基于本地名册 dept_path**（0 次远程通讯录调用），
     授权范围校验与领导选人 100% 一致，历史 dept_ids 原样兼容；
  ② 手机端跳转：`/h5/task/`、`/h5/portal/`、`/h5/notice-receipt/` 尾斜杠可用；
     portal.html 用 `?from=portal#t=` 标准 query 跳转、portal-create.html 独立建任务页；
     task.html / notice-receipt.html 同时从 location.search 与 location.hash 读参数；
  ③ 领导 H5 通知任务全链路：纯广播（应送达/已送达）、H5 链接回执、文本回复回执，
     投递顺序 正文 → 媒体 → 确认链接，待办联动与回执确认后自动移除；
  ④ 后台监管面板 / .xlsx 导出按通知口径出表（送达明细/汇总、回执明细/汇总）。
"""
from datetime import datetime
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app.api.h5 import create_h5_router
from app.api.notice_receipts import create_notice_receipt_router
from app.api.portal import create_portal_router
from app.notice_media import store_asset
from app.notice_receipts import confirm as confirm_receipt
from app.portal import PortalService, run_portal_tasks
from app.state import BizError
from test_20261117_manual_mark_submitted import ctx
from test_submit_feedback_dedupe import _store, _instance

MEMBER_B = '13900009402'


@pytest.fixture
def leader(ctx):
    """领导（授权发布人）+ 两条本地名册成员：本部门 / 本部门/下级部门。"""
    s, scheduler, notifier, admin, owner, sub, tpl, inst = ctx
    p = PortalService(s)
    rid = tpl['role_ids'][0]
    for phone, dept in ((inst['phone'], '本部门'), (MEMBER_B, '本部门/下级部门')):
        if phone != inst['phone']:
            s.add_role_member(rid, phone, '范围甲', owner_id=tpl['owner_id'])
            s.upsert_delivery_target(phone, phone)
        s.conn.execute('UPDATE member SET dept_path=? WHERE role_id=? AND phone=?', (dept, rid, phone))
    s.conn.commit()
    p.save_grant('zdxlz', tpl['owner_id'], inst['phone'], {'enabled': True}, 't')
    return p, {'phone': inst['phone'], 'platform': 'zdxlz'}, int(tpl['owner_id']), ctx


def notice_payload(**extra):
    data = {'kind': 'notice', 'name': '安全通知', 'description': '请阅读本周安全检查通知',
            'phones': [MEMBER_B], 'mode': 'once', 'request_id': 'notice-00001',
            'notice_items': [], 'notice_receipt': {'enabled': False}}
    data.update(extra)
    return data


def image_asset(s, owner):
    stream = BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(stream, format='PNG')
    return store_asset(s, owner, 'image', '现场.png', BytesIO(stream.getvalue()), len(stream.getvalue()))


def test_local_tree_purely_local_and_consistent(leader):
    """部门树 / 默认部门 / 授权校验 / 领导选人全部本地构建，且两侧范围一致。"""
    p, ident, owner_id, (s, *_) = leader

    class ExplodingContacts:
        def __getattr__(self, name):
            raise AssertionError('授权/选人不允许访问远程通讯录：' + name)

    s.contacts = ExplodingContacts()
    options = p.grant_options('zdxlz', owner_id, ident['phone'])
    assert [d['dept_id'] for d in options['departments']] == ['本部门', '本部门/下级部门']
    assert [d['member_count'] for d in options['departments']] == [2, 1]
    assert options['selected_departments'][0]['dept_id'] == '本部门'
    assert options['directory_notice'] == ''
    assert p.local_default_department('zdxlz', owner_id, ident['phone'])['dept_id'] == '本部门'
    # 下级部门按 parent 懒加载（弹窗点「+」走这条），仍是本地判定
    children = p.grant_options('zdxlz', owner_id, ident['phone'], parent='本部门')['departments']
    assert [d['dept_id'] for d in children] == ['本部门/下级部门']
    # 选人页与授权口径一致：「本部门」子树 = 两人
    scope = p.recipients(ident)
    assert {m['phone'] for m in scope['members']} == {ident['phone'], MEMBER_B}
    assert [d['dept_id'] for d in scope['departments']] == ['本部门', '本部门/下级部门']
    # 越权部门被拦（本地校验，无远程参与）
    with pytest.raises(BizError) as err:
        p.save_grant('zdxlz', owner_id, ident['phone'], {'enabled': True, 'dept_ids': ['集团/不存在'], 'role_ids': []}, 't')
    assert err.value.status_code == 403


def test_legacy_dept_ids_still_savable(leader):
    """历史远程数字 dept_ids：重新保存原样放行、只读展示为「历史授权」、选人忽略。"""
    p, ident, owner_id, (s, *_) = leader
    s.conn.execute("UPDATE portal_grant SET scope_json=? WHERE phone=? AND platform=?",
                   ('{"dept_ids": ["887766", "本部门"], "role_ids": []}', ident['phone'], 'zdxlz'))
    s.conn.commit()
    options = p.grant_options('zdxlz', owner_id, ident['phone'])
    assert [d['name'] for d in options['selected_departments']] == ['部门 887766（历史授权，请重新勾选）', '本部门']
    resaved = p.save_grant('zdxlz', owner_id, ident['phone'],
                           {'enabled': True, 'dept_ids': ['887766'], 'role_ids': []}, 't')
    assert resaved['ok'] is True
    # 兼容口径：历史 id 无法本地解析 → 回退「成员本人当前部门」子树，旧授权继续生效
    scope = p.recipients(ident)
    assert {m['phone'] for m in scope['members']} == {ident['phone'], MEMBER_B}
    # 重新勾选合法路径后仍按勾选范围生效
    p.save_grant('zdxlz', owner_id, ident['phone'], {'enabled': True, 'dept_ids': ['本部门/下级部门'], 'role_ids': []}, 't')
    assert {m['phone'] for m in p.recipients(ident)['members']} == {MEMBER_B}


def test_h5_pages_trailing_slash_and_query_params(leader):
    """手机端跳转：尾斜杠页面可访问；HTML 用标准 query 传 from=portal；新页面无外部 CDN。"""
    p, ident, owner_id, (s, scheduler, notifier, *_) = leader
    app = FastAPI()
    app.include_router(create_h5_router(s, s.data_dir / 'uploads', scheduler=scheduler, notifier=notifier))
    app.include_router(create_portal_router(s, scheduler=scheduler, uploads_dir=s.data_dir / 'uploads'))
    app.include_router(create_notice_receipt_router(s))
    client = TestClient(app)
    for path in ('/h5/portal', '/h5/portal/', '/h5/portal-create', '/h5/portal-create/',
                 '/h5/task', '/h5/task/', '/h5/notice-receipt', '/h5/notice-receipt/'):
        assert client.get(path).status_code == 200, path

    portal_html = client.get('/h5/portal').text
    assert "location.pathname.includes('/h5/')" in portal_html
    assert 'function portalJump(url)' in portal_html
    assert "location.href = prefix + result.url" not in portal_html
    assert "/h5/portal-create#t=" in portal_html
    create_html = client.get('/h5/portal-create').text
    assert "kind: 'notice'" in create_html
    assert 'crypto.getRandomValues' in create_html
    assert 'crypto.randomUUID' not in create_html
    assert '/api/h5/portal-asset/upload' in create_html
    for html in (portal_html, create_html, client.get('/h5/task').text, client.get('/h5/notice-receipt').text):
        assert 'http://' not in html.split('<script>')[0] or 'cdn' not in html.lower()
        assert 'cdn.' not in html.lower() and 'unpkg' not in html.lower() and 'jsdelivr' not in html.lower()
    task_html = client.get('/h5/task').text
    receipt_html = client.get('/h5/notice-receipt').text
    assert 'new URLSearchParams(window.location.search)' in task_html
    assert "params.get(\"from\")" in task_html
    assert 'new URLSearchParams(location.search)' in receipt_html


def test_notice_pure_broadcast_delivery_and_stats(leader):
    """纯广播通知：一次发布 → 三 tick 只发 1 条；统计与导出走「送达」口径。"""
    p, ident, owner_id, (s, scheduler, notifier, admin, owner, *_) = leader
    task = p.create_task(ident, notice_payload())
    for _ in range(3):
        run_portal_tasks(scheduler, s.now())
    messages = [m for m in notifier.sent_messages if '请阅读本周安全检查通知' in m.text]
    assert len(messages) == 1
    assert len(s.list_reminder_logs(template_id=task['task_id'])) == 1
    stats = p.statistics(p.template(task['task_id']))
    assert stats['kind'] == 'notice' and stats['receipt_mode'] is None
    period = stats['periods'][0]
    assert (period['expected'], period['submitted'], period['pending']) == (1, 1, 0)
    assert period['delivered'] == 1 and period['receipt_expected'] == 0 and period['replied'] == 0
    # 纯广播不进「我的待办」
    member = {'phone': MEMBER_B, 'platform': 'zdxlz'}
    assert not [i for i in p.todos(member) if i['template_id'] == task['task_id']]
    overview = owner.get(f"/api/admin/portal-tasks/{task['task_id']}/overview", params={'period_key': 'once'}).json()
    assert overview['template']['is_notice'] is True and overview['template']['has_receipt'] is False
    assert overview['kpis']['expected_delivery'] == 1 and overview['kpis']['delivered'] == 1
    exported = owner.get(f"/api/admin/portal-tasks/{task['task_id']}/export", params={'period_key': 'once'})
    assert exported.status_code == 200
    book = load_workbook(BytesIO(exported.content))
    assert book.sheetnames == ['送达明细', '送达汇总']
    assert [c.value for c in book['送达明细'][1]][:5] == ['序号', '姓名', '手机号', '完整部门路径', '送达状态']
    assert book['送达明细']['E2'].value == '已送达'
    assert [c.value for c in book['送达汇总'][1]] == ['应送达', '已送达', '未送达', '送达率']


def test_notice_link_receipt_full_chain(leader):
    """链接回执：正文 → 媒体 → H5 确认链接；待办「去查看确认」；确认后移除、统计与导出转回执口径。"""
    p, ident, owner_id, (s, scheduler, notifier, admin, owner, *_) = leader
    asset = image_asset(s, owner_id)
    task = p.create_task(ident, notice_payload(
        request_id='notice-link-01', notice_items=[{'format': 'image', 'asset_id': asset['asset_id']}],
        notice_receipt={'enabled': True, 'mode': 'link', 'button_label': '我已知晓', 'guide_text': '请阅读后确认'}))
    for _ in range(3):
        run_portal_tasks(scheduler, s.now())
    texts = [m.text for m in notifier.sent_messages]
    assert len([t for t in texts if '请阅读本周安全检查通知' in t]) == 1
    assert len([t for t in texts if '/h5/notice-receipt#t=' in t]) == 1
    assert any('请阅读后确认' in t for t in texts if '/h5/notice-receipt#t=' in t)
    member = {'phone': MEMBER_B, 'platform': 'zdxlz'}
    todos = [i for i in p.todos(member) if i['template_id'] == task['task_id']]
    assert len(todos) == 1 and todos[0]['receipt_mode'] == 'link'
    assert todos[0]['template_name'] == '安全通知'
    opened = p.notice_receipt_link(member, todos[0]['id'])
    assert opened['url'].startswith('/h5/notice-receipt#t=')
    confirm_receipt(s, todos[0]['id'])
    assert not [i for i in p.todos(member) if i['template_id'] == task['task_id']]
    stats = p.statistics(p.template(task['task_id']))
    period = stats['periods'][0]
    assert period['receipt_expected'] == 1 and period['replied'] == 1 and period['reply_rate'] == 100
    overview = owner.get(f"/api/admin/portal-tasks/{task['task_id']}/overview", params={'period_key': 'once'}).json()
    assert overview['template']['has_receipt'] is True and overview['kpis']['replied'] == 1
    exported = owner.get(f"/api/admin/portal-tasks/{task['task_id']}/export", params={'period_key': 'once'})
    book = load_workbook(BytesIO(exported.content))
    assert book.sheetnames == ['回执明细', '回执汇总']
    assert book['回执明细']['E2'].value == '已回复'
    assert book['回执明细']['H2'].value == 'H5 链接确认'


def test_notice_text_receipt_todo_and_ownership(leader):
    """文本回执：待办展示短语指引；素材归属校验（他人素材 403）。"""
    p, ident, owner_id, (s, *_rest) = leader
    asset = image_asset(s, owner_id)
    task = p.create_task(ident, notice_payload(
        request_id='notice-text-01', notice_items=[{'format': 'image', 'asset_id': asset['asset_id']}],
        notice_receipt={'enabled': True, 'mode': 'text', 'phrase': '收到'}))
    tpl = p.template(task['task_id'])
    assert tpl['spec_json']['notice_receipt']['mode'] == 'text'
    member = {'phone': MEMBER_B, 'platform': 'zdxlz'}
    run_portal_tasks(_rest[0], s.now())
    todos = [i for i in p.todos(member) if i['template_id'] == task['task_id']]
    assert len(todos) == 1 and todos[0]['receipt_mode'] == 'text' and todos[0]['receipt_phrase'] == '收到'
    # 素材归属：他人 notice_asset 不允许被领导引用
    foreign = store_asset(s, owner_id + 100, 'image', 'x.png', BytesIO(b'\x89PNG\r\n\x1a\n'), 8)
    with pytest.raises(BizError) as err:
        p.create_task(ident, notice_payload(request_id='notice-text-02',
                                            notice_items=[{'format': 'image', 'asset_id': foreign['asset_id']}]))
    assert err.value.status_code == 403 and 'INVALID_NOTICE_ASSET' in str(err.value.code)

def test_h5_leader_notice_http_asset_and_create(leader):
    """领导端 HTTP 链路：素材上传 → 通知发布 → 素材预览（Bearer 鉴权 + 归属校验）。"""
    p, ident, owner_id, (s, scheduler, notifier, admin, owner, *_) = leader
    app = FastAPI()
    app.include_router(create_portal_router(s, scheduler=scheduler, uploads_dir=s.data_dir / 'uploads'))
    client = TestClient(app)
    headers = {'Authorization': 'Bearer ' + p.issue(ident['phone'], ident['platform'])}
    stream = BytesIO()
    Image.new('RGB', (2, 2), 'red').save(stream, format='PNG')
    payload = stream.getvalue()
    uploaded = client.post('/api/h5/portal-asset/upload', headers=headers,
                           data={'kind': 'image'}, files={'file': ('通知图.png', payload, 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    asset_id = uploaded.json()['asset']['asset_id']
    preview = client.get('/api/h5/portal-asset/' + asset_id, headers=headers)
    assert preview.status_code == 200 and preview.content == payload
    created = client.post('/api/h5/portal/create', headers=headers, json=notice_payload(
        request_id='notice-http-01', notice_items=[{'format': 'image', 'asset_id': asset_id}],
        notice_receipt={'enabled': True, 'mode': 'text', 'phrase': '收到'}))
    assert created.status_code == 200, created.text
    task_id = created.json()['task_id']
    assert s.get_template(task_id)['kind'] == 'notice'
    assert client.post('/api/h5/portal/create', headers=headers, json=notice_payload(
        request_id='notice-http-02', notice_items=[{'format': 'file', 'asset_id': asset_id}])).status_code == 403
    foreign = {'Authorization': 'Bearer ' + p.issue(MEMBER_B, ident['platform'])}
    assert client.get('/api/h5/portal-asset/' + asset_id, headers=foreign).status_code == 403
