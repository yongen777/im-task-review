import pytest

from app.state import BizError, task_attachment_limits
from admin_test_helpers import post_verified_admin
from test_20261117_manual_mark_submitted import ctx
from test_20261118_portal import portal, publish


def test_h5_lists_all_enabled_role_owners_without_creator_grants(portal):
    p, ident, (s, _, _, admin, owner, _, tpl, _) = portal
    phone = ident['phone']
    primary = tpl['owner_id']
    assert owner.put(f'/api/admin/accounts/{primary}', json={'phone': '13900009501'}).status_code == 403
    assert admin.put(f'/api/admin/accounts/{primary}', json={'phone': 'wrong'}).status_code == 400
    response = admin.put(f'/api/admin/accounts/{primary}', json={'display_name': '主管一', 'phone': '13900009501'})
    assert response.status_code == 200 and response.json()['account']['phone'] == '13900009501'

    for username, platform, allowed_platforms, enabled in (
        ('new_owner', 'zdxlz', ['zdxlz'], True),
        ('disabled_owner', 'zdxlz', ['zdxlz'], False),
        ('other_platform', 'wecom', ['wecom'], True),
        ('revoked_platform', 'zdxlz', ['wecom'], True),
    ):
        response = post_verified_admin(admin, {
            'username': username, 'phone': '13900009502', 'allowed_platforms': ['zdxlz', 'wecom'],
        })
        assert response.status_code == 200, response.text
        account = response.json()['account']
        assert account['phone'] == '13900009502'
        for i in range(2):
            rid = s.create_role(f'{username}-{i}', owner_id=account['id'], platform=platform)['role_id']
            s.add_role_member(rid, phone, '测试成员', owner_id=account['id'])
        s.update_admin_account(account['id'], enabled=enabled, allowed_platforms=allowed_platforms)

    p.save_grant(ident['platform'], primary, phone, {'enabled': False}, 't')
    headers = {'Authorization': 'Bearer ' + p.issue(phone, ident['platform'])}
    response = admin.post('/api/h5/portal/info', json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['can_create'] is False
    assert response.json()['admins'] == [
        {'name': '主管一', 'phone': '13900009501'},
        {'name': 'new_owner', 'phone': '13900009502'},
    ]


def test_department_choices_use_explicit_scope_or_current_subtree(portal, monkeypatch):
    p, ident, (s, _, _, _, _, _, tpl, _) = portal
    platform, phone, owner = ident['platform'], ident['phone'], tpl['owner_id']
    rid = s.create_role('授权角色', owner_id=owner)['role_id']
    s.add_role_member(rid, phone, '测试成员', owner_id=owner)
    # 2026-11-23：部门树改由本地名册 dept_path 实时聚合（旧的 department_children /
    # default_department 已整体移除），这里不再需要替身。

    p.save_grant(platform, owner, phone, {'enabled': True, 'dept_ids': [], 'role_ids': [rid]}, 't')
    scope = p.recipients(ident)
    assert {d['dept_id'] for d in scope['departments']} == {'本部门', '本部门/下级部门'}
    assert {m['phone'] for m in scope['members']} == {phone, '13900009402'}
    with pytest.raises(BizError, match='接收成员超出授权范围'):
        publish(p, ident, phones=['13900009403'])

    p.save_grant(platform, owner, phone, {'enabled': True, 'dept_ids': ['跨部门'], 'role_ids': [rid]}, 't')
    scope = p.recipients(ident)
    assert {d['dept_id'] for d in scope['departments']} == {'跨部门'}
    assert {m['phone'] for m in scope['members']} == {phone, '13900009403'}
    with pytest.raises(BizError, match='接收成员超出授权范围'):
        publish(p, ident, phones=['13900009402'])


@pytest.mark.parametrize(('modules', 'kind'), [
    (['fields', 'text', 'image', 'file'], 'form'), (['image'], 'image'), (['file'], 'file'),
])
def test_h5_create_normalizes_bundle_upload_spec(portal, modules, kind):
    p, ident, (s, _, _, admin, *_) = portal
    fields = [{'key': n, 'label': n, 'type': n, 'required': n != 'text'}
              for n in ('number', 'text', 'boolean', 'date')] if 'fields' in modules else []
    data = {
        'name': '现场采集', 'description': '请按要求提交', 'mode': 'day', 'remind_at': '08:30',
        'has_deadline': True, 'deadline_type': 'month_end', 'deadline_time': '20:00',
        'exec_weekdays': None, 'exec_dom_days': [31, 15], 'phones': [ident['phone']],
        'request_id': 'bundle-submit-1', 'upload_modules': modules, 'schema_json': fields, 'need_review': True,
        'spec': {'upload_modules': modules, 'fields': fields, 'require_review': True,
                 'allow_multiple_images': 'image' in modules, 'allow_multiple_files': 'file' in modules,
                 'text_label': '文字情况说明', 'text_placeholder': '请填写补充说明…'},
    }
    headers = {'Authorization': 'Bearer ' + p.issue(ident['phone'], ident['platform'])}
    response = admin.post('/api/h5/portal/create', json=data, headers=headers)
    assert response.status_code == 200, response.text
    assert admin.post('/api/h5/portal/create', json=data, headers=headers).json() == response.json()
    tpl = p.template(response.json()['task_id'])
    assert tpl['kind'] == kind and tpl['need_review'] is False
    assert tpl['spec_json'] == {**data['spec'], 'require_review': False}
    assert tpl['schema_json'] == [dict(field, max_len=200) if field['type'] == 'text' else field for field in fields]
    assert tpl['period'] == 'day' and tpl['deadline_type'] == 'month_end'
    assert tpl['exec_dom_days'] == '[15, 31]'
    assert task_attachment_limits(tpl) == {key: 10 if key in modules else 1 for key in ('image', 'file')}
