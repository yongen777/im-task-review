from io import BytesIO

from openpyxl import load_workbook

from app.api.events import handle_inbound_text
from test_20261117_manual_mark_submitted import ctx


def test_notice_receipt_over_chat_and_statistics(ctx):
    store, scheduler, _, _, owner, _, existing, member = ctx
    for name in ('回执通知甲', '回执通知乙'):
        response = owner.post('/api/admin/templates', json={
            'name': name, 'kind': 'notice', 'period': 'day', 'is_manual': True,
            'role_ids': existing['role_ids'], 'text_tpl': '请查阅',
            'spec': {'notice_receipt': {'enabled': True, 'phrase': '已经查阅'}},
        })
        assert response.status_code == 200, response.text
        template_id = response.json()['template_id']
        assert scheduler.push_now(template_id)['sent'] == 1

    args = {'template_id': template_id, 'period_key': 'once'}
    overview = owner.get('/api/admin/stats-panel/overview', params=args)
    assert overview.status_code == 200, overview.text
    assert overview.json()['kpis']['not_done'] == 1

    replies = []
    def reply(text, event):
        return handle_inbound_text(state=store, user_id='test_recipient', msg_text=text,
                                   platform='zdxlz', event_id=event, base_url='http://local',
                                   send=lambda body, tag: replies.append((body, tag)))

    reply('其他消息', 'portal-entry')
    assert '/h5/portal' in replies[-1][0]
    reply('已经查阅。', 'same-event')
    reply('已经查阅。', 'same-event')
    assert len(replies) == 2 and '2 条' in replies[-1][0]
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
