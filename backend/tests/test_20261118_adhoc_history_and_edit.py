import json
from datetime import timedelta

from app.portal import PortalService
from app.state import NO_DEADLINE_SENTINEL
from test_20261117_manual_mark_submitted import PNG, ctx


def test_submission_timeline_keeps_four_actions_and_filters_legacy_receipt(ctx):
    state, _, _, _, owner, _, template, instance = ctx
    iid = instance['id']
    path = f'/api/admin/instances/{iid}'
    start = state.now()

    def move(minutes):
        state._now_fn = lambda: start + timedelta(minutes=minutes)
        return state.now_str()

    def detail():
        response = owner.get(path)
        assert response.status_code == 200, response.text
        return response.json()

    state.update_template(template['template_id'], {'need_review': True, 'spec': {'upload_modules': ['fields', 'text']}})
    raw = state.issue_h5_token(instance['phone'], template['template_id'], instance['period_key'], iid)
    content = {'fields': {'qty': 2, 'summary': '测量'}, 'note': '提交内容'}
    original = move(1)
    assert owner.post('/api/h5/task/submit', json={'token': raw, **content}).status_code == 200
    state.review_instance(iid, 'reject', 'manual_owner', '请补充说明')
    delegated = move(2)
    assert owner.post(path + '/mark-submitted', json=content).status_code == 200
    admin_id = detail()['submission']['id']
    admin_changed = move(3)
    res = owner.put(path + '/submission', json={'revision': detail()['submission']['revision'], 'note': '管理员更正'})
    assert res.status_code == 200, res.text

    portal = PortalService(state)
    ident = {'phone': instance['phone'], 'platform': 'zdxlz'}
    history = portal.submission_history_link(ident, iid)['url'].partition('#t=')[2]
    user_changed = move(4)
    res = owner.post('/api/h5/task/save-edit', headers={'Authorization': 'Bearer ' + history}, json={
        **content, 'revision': detail()['submission']['revision'], 'retained_refs': [], 'refs': [],
    })
    assert res.status_code == 200, res.text
    # Legacy corrections predate the explicit editor_source marker.
    legacy = move(5)
    state.write_audit('h5:' + instance['phone'], 'instance.edit_submission', 'task_instance', str(iid), {'submission_id': admin_id})
    state.record_reminder_log(template_id=template['template_id'], instance_id=iid, phone=instance['phone'],
                              kind='submit_ack', planned_at=original, sent_at=original,
                              result='sent', error=None, retry=0, idem_key='legacy-timeline-ack')
    result = detail()
    assert [(item['kind'], item['at']) for item in result['submission_timeline']] == [
        ('user_submit', original), ('admin_submit', delegated), ('admin_edit', admin_changed),
        ('user_edit', user_changed), ('user_edit', legacy),
    ]
    assert result['instance']['submitted_at'] == delegated
    assert result['submission']['id'] == admin_id
    assert len(result['submissions']) == 2
    assert result['reminders'] == []
    assert state.list_reminder_logs(instance_id=iid)[0]['kind'] == 'submit_ack'


def test_adhoc_open_submit_and_history(ctx):
    state, scheduler, notifier, _, owner, _, regular, _ = ctx
    template = state.create_template({
        'name': '自主上报回归', 'kind': 'form', 'period': 'day', 'is_adhoc': True,
        'role_ids': regular['role_ids'], 'spec': {'upload_modules': ['text', 'image']},
    }, owner_id=regular['owner_id'])
    tid = template['template_id']
    identity = {'phone': '13900009401', 'platform': 'zdxlz'}
    portal = PortalService(state)
    hdr = {'Authorization': 'Bearer ' + portal.issue(**identity)}
    legacy = state.ensure_instance(tid, identity['phone'], '测试成员', None, 'adhoc-20261117-090000-legacy', NO_DEADLINE_SENTINEL)
    assert owner.get(f"/api/admin/instances/{legacy['id']}").status_code == 404
    for _ in range(3):
        assert owner.post(f'/api/admin/templates/{tid}/push-now').status_code == 400
        scheduler.run_once()
    assert not any('自主上报回归' in msg.text for msg in notifier.sent_messages)
    assert not state.list_reminder_logs(template_id=tid)

    def action(name, body):
        return owner.post(f'/api/h5/portal/{name}', headers=hdr, json=body)

    def token(response):
        assert response.status_code == 200, response.text
        return response.json()['url'].partition('#t=')[2]

    first, second = (token(action('adhoc', {'template_id': tid})) for _ in range(2))
    assert first != second
    assert owner.get('/api/admin/instances', params={'template_id': tid}).json()['total'] == 0
    assert not any(row['template_id'] == tid for row in action('info', {}).json()['todos'])
    assert action('info', {}).json()['adhoc_history'] == []
    report = owner.post('/api/h5/task/info', json={'token': first}).json()
    assert report['window_open'] and report['published_at'] is None and report['instance']['id'] is None
    img_response = owner.post('/api/h5/task/upload', headers={'Authorization': 'Bearer ' + first},
                              files={'file': ('现场.png', PNG, 'image/png')})
    assert img_response.status_code == 200, img_response.text
    ref = img_response.json()['ref']
    assert owner.post('/api/h5/task/submit', json={'token': second, 'note': '跨表单附件', 'refs': [ref]}).status_code == 400
    assert owner.get('/api/admin/instances', params={'template_id': tid}).json()['total'] == 0
    submitted = owner.post('/api/h5/task/submit', json={'token': first, 'note': '第一条上报', 'refs': [ref]})
    assert submitted.status_code == 200, submitted.text
    inst = submitted.json()['instance']
    assert inst['status'] == 'approved' and inst['id'] != legacy['id']
    assert owner.get('/api/admin/instances', params={'template_id': tid, 'period_key': inst['submitted_at'][:10]}).json()['total'] == 1
    assert owner.post('/api/h5/task/submit', json={'token': first}).status_code == 400
    assert action('info', {}).json()['adhoc_history'][0]['id'] == inst['id']
    assert action('history-open', {'instance_id': legacy['id']}).status_code == 404
    history = token(action('history-open', {'instance_id': inst['id']}))
    detail = owner.post('/api/h5/task/info', json={'token': history}).json()
    assert detail['readonly_history'] and detail['submission']['note'] == '第一条上报'
    history_headers = {'Authorization': 'Bearer ' + history}
    assert owner.post('/api/h5/task/attachment', headers=history_headers, json={'ref': ref}).content == PNG
    assert owner.post('/api/h5/task/upload', headers=history_headers).status_code == 401
    assert owner.post('/api/h5/task/submit', json={'token': history}).status_code == 404
    edit_image = owner.post('/api/h5/task/edit-upload', headers=history_headers,
                            files={'file': ('更正.png', PNG + b'new', 'image/png')})
    assert edit_image.status_code == 200, edit_image.text
    changed = owner.post('/api/h5/task/save-edit', headers=history_headers, json={
        'revision': detail['submission']['revision'], 'note': '用户自行更正', 'fields': {},
        'retained_refs': [], 'refs': [edit_image.json()['ref']],
    })
    assert changed.status_code == 200, changed.text
    assert owner.get(f"/api/admin/instances/{inst['id']}").json()['submission']['note'] == '用户自行更正'
    assert owner.post('/api/h5/task/attachment', headers=history_headers, json={'ref': ref}).status_code == 404
    assert len(state.list_submissions_for_instance(inst['id'])) == 1
    assert state.get_instance(inst['id'])['submitted_at'] == inst['submitted_at']
    assert not state.list_reminder_logs(template_id=tid)
    state.conn.execute('UPDATE task_instance SET submitted_at=NULL WHERE id=?', (inst['id'],))
    assert action('history', {}).json()['items'][0]['submitted_at'] == inst['submitted_at']
    assert owner.get('/api/admin/instances', params={'template_id': tid, 'period_key': inst['submitted_at'][:10]}).json()['total'] == 1
    assert owner.post('/api/h5/task/info', json={'token': history}).json()['instance']['submitted_at'] == inst['submitted_at']


def test_owner_replaces_submission_without_changing_status_or_completion(ctx):
    state, scheduler, _, admin, owner, sub, template, instance = ctx
    iid = instance['id']
    path = f'/api/admin/instances/{iid}'
    scheduler.run_once()
    raw = state.issue_h5_token(instance['phone'], template['template_id'], instance['period_key'], iid)
    info = owner.post('/api/h5/task/info', json={'token': raw}).json()
    assert info['published_at'] == state.now_str()
    uploads = []
    for name, content, mime in (('旧图.png', PNG, 'image/png'), ('保留.pdf', b'%PDF-1.4\n', 'application/pdf')):
        res = owner.post('/api/h5/task/upload', files={'token': (None, raw), 'file': (name, content, mime)})
        assert res.status_code == 200, res.text
        uploads.append(res.json()['ref'])
    assert owner.post('/api/h5/task/submit', json={
        'token': raw, 'note': '初版', 'fields': {'qty': '2', 'summary': '初版'}, 'refs': uploads,
    }).status_code == 200
    old = owner.get(path).json()['submission']
    payload = {'revision': old['revision'], 'note': '更正', 'fields': {'qty': '3', 'summary': '现场核实'}, 'retained_refs': [uploads[1]]}
    _, outsider = sub('another_edit_admin')
    assert admin.put(path + '/submission', json=payload).status_code == 403
    assert outsider.put(path + '/submission', json=payload).status_code == 403
    bad = owner.put(path + '/submission', files={
        'payload': (None, json.dumps(payload)), 'attachments': ('伪图.png', b'bad', 'image/png'),
    })
    assert bad.status_code == 400
    assert owner.get(path).json()['submission'] == old
    update = owner.put(path + '/submission', files={
        'payload': (None, json.dumps(payload)), 'attachments': ('更正.png', PNG + b'replacement', 'image/png'),
    })
    assert update.status_code == 200, update.text
    assert owner.put(path + '/submission', json=payload).status_code == 409
    detail = owner.get(path).json()
    changed = detail['submission']
    assert (changed['id'], changed['source'], changed['submitted_at'], changed['status']) == (
        old['id'], old['source'], old['submitted_at'], old['status'],
    )
    assert len(detail['submissions']) == 1
    assert changed['note'] == '更正' and str(changed['form_values']['qty']) == '3'
    assert [item['file_name'] for item in changed['attachments']] == ['保留.pdf', '更正.png']
    h5 = owner.post('/api/h5/task/info', json={'token': raw}).json()
    assert h5['published_at'] is None and h5['submission']['note'] == '更正'
    assert owner.post('/api/h5/task/attachment', headers={'Authorization': 'Bearer ' + raw}, json={'ref': uploads[0]}).status_code == 404
    assert state.conn.execute("SELECT 1 FROM admin_audit WHERE action='instance.edit_submission' AND target_id=?", (str(iid),)).fetchone()
    portal = PortalService(state)
    headers = {'Authorization': 'Bearer ' + portal.issue(instance['phone'], 'zdxlz')}
    reports = owner.post('/api/h5/portal/history', headers=headers, json={}).json()['items']
    assert [(row['id'], bool(row['is_adhoc'])) for row in reports] == [(iid, False)]
    other_phone = '13900009402'
    state.add_role_member(template['role_ids'][0], other_phone, '另一位成员', owner_id=template['owner_id'])
    state.upsert_delivery_target(other_phone, 'another_activated_member')
    other_headers = {'Authorization': 'Bearer ' + portal.issue(other_phone, 'zdxlz')}
    assert owner.post('/api/h5/portal/history-open', headers=other_headers, json={'instance_id': iid}).status_code == 404
