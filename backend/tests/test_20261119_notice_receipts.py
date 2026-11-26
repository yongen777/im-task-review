from io import BytesIO
from datetime import timedelta

from openpyxl import load_workbook

from app.api.events import handle_inbound_text
from app.portal import PortalService
from test_20261117_manual_mark_submitted import NOW, ctx


def test_notice_receipt_over_chat_and_statistics(ctx):
    store, scheduler, _, _, owner, _, existing, member = ctx
    ids = []
    for name in ('回执通知甲', '回执通知乙', '已停用通知', '未送达通知'):
        response = owner.post('/api/admin/templates', json={
            'name': name, 'kind': 'notice', 'period': 'day', 'is_manual': True,
            'role_ids': existing['role_ids'], 'text_tpl': '请查阅',
            'spec': {'notice_receipt': {'enabled': True, 'phrase': '已经查阅'}},
        })
        assert response.status_code == 200, response.text
        template_id = response.json()['template_id']
        ids.append(template_id)
        if name != '未送达通知':
            assert scheduler.push_now(template_id)['pushed_instances'] == 1
        else:
            scheduler.push_now_prepare(template_id)
    store.update_template(ids[2], {'enabled': False})
    template_id = ids[1]

    args = {'template_id': template_id, 'period_key': 'once'}
    overview = owner.get('/api/admin/stats-panel/overview', params=args)
    assert overview.status_code == 200, overview.text
    assert overview.json()['kpis']['not_done'] == 1

    later = NOW + timedelta(days=20)
    store._now_fn = lambda: later
    store.advance_instance_statuses(later)
    portal = PortalService(store)
    ident = {'phone': member['phone'], 'platform': 'zdxlz'}
    todos = portal.todos(ident)
    assert [(item['template_name'], item['receipt_phrase']) for item in todos if item.get('receipt_phrase')] == [
        ('回执通知乙', '已经查阅'), ('回执通知甲', '已经查阅'),
    ]
    assert not portal.submission_history(ident)

    replies = []
    def reply(text, event):
        return handle_inbound_text(state=store, user_id='test_recipient', msg_text=text,
                                   platform='zdxlz', event_id=event, base_url='http://local',
                                   send=lambda body, tag: replies.append((body, tag)))

    reply('其他消息', 'portal-entry')
    assert '/h5/portal' in replies[-1][0]
    _, receipt = reply('已经查阅。', 'same-event')
    assert receipt['path'] == 'notice_receipt' and receipt['count'] == 2
    for _ in range(2):
        assert reply('已经查阅。', 'same-event')[1]['duplicate'] is True
    assert [tag for _, tag in replies] == ['portal']
    assert not [item for item in portal.todos(ident) if item.get('receipt_phrase')]
    assert not portal.submission_history(ident)
    instance = store.get_instance_by_key(template_id, member['phone'], 'once')
    assert instance['receipt_at'] > instance['deadline_at']
    store._now_fn = lambda: NOW
    overview = owner.get('/api/admin/stats-panel/overview', params=args).json()
    assert overview['kpis']['done'] == 1 and overview['kpis']['rate'] == 100
    assert owner.get('/api/admin/stats-panel/instances', params={**args, 'status_group': 'done'}).json()['total'] == 1
    response = owner.get('/api/admin/stats-panel/export', params=args)
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), read_only=True)
    rows = list(workbook.active.values)
    assert '回复时间' in rows[0] and '回复内容' in rows[0]
    assert '已回复' in rows[1] and '已经查阅。' in rows[1] and member['phone'] in rows[1]
    assert list(workbook['回执汇总'].values)[0] == ('应回复', '已回复', '待回复', '回复率')


def test_single_notice_receipt_keeps_workbench_link_for_other_messages(ctx):
    store, scheduler, _, _, _, _, existing, member = ctx
    notice = store.create_template({
        'name': '单条通知', 'kind': 'notice', 'period': 'day', 'is_manual': True,
        'role_ids': existing['role_ids'], 'text_tpl': '请查阅',
        'spec': {'notice_receipt': {'enabled': True, 'phrase': '已经查阅'}},
    }, owner_id=existing['owner_id'])
    scheduler.push_now(notice['template_id'])
    instance = store.get_instance_by_key(notice['template_id'], member['phone'], 'once')
    assert instance['receipt_prompt_at']
    store._now_fn = lambda: NOW + timedelta(days=365)
    replies = []
    for index, message in enumerate(('已经查阅。', '其他消息')):
        result = handle_inbound_text(state=store, user_id='test_recipient', msg_text=message,
                                     platform='zdxlz', event_id=f'event-{index}', base_url='http://local',
                                     send=lambda body, tag: replies.append((body, tag)))
        assert result[0] == 200
    [(body, tag)] = replies
    assert '/h5/portal#t=' in body and tag == 'portal'
    assert store.get_instance(instance['id'])['receipt_at'] > instance['deadline_at']
