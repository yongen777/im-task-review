"""个人工作台：独立授权/任务存储，共享提交与上传基础设施。"""
import hashlib
import json
import os
from datetime import datetime

from .config import format_dt
from .state import BizError, calc_h5_token_ttl_hours, effective_instance_deadline


class PortalService:
    def __init__(self, state):
        self.s = state

    def issue(self, phone, platform):
        if not self.members(platform, phone=phone):
            raise BizError(403, 'NOT_REGISTERED', '当前成员不可用')
        return self.s.issue_h5_token(phone, '', platform, None, purpose='portal', ttl_hours=168)

    def authenticate(self, raw):
        tok = self.s.get_h5_token(raw)
        if not tok or tok['purpose'] != 'portal' or tok['consumed'] or tok['expires_at'] <= self.s.now_str():
            raise BizError(401, 'PORTAL_EXPIRED', '链接已过期，请回到聊天框向机器人发送任意消息重新获取最新工作台入口')
        identity = {'phone': tok['phone'], 'platform': tok['period_key']}
        if not self.members(identity['platform'], phone=identity['phone']):
            raise BizError(401, 'PORTAL_EXPIRED', '链接已过期，请回到聊天框向机器人发送任意消息重新获取最新工作台入口')
        return identity

    def members(self, platform, owner=None, phone=None):
        sql = ('SELECT m.*, r.name AS role_name FROM member m JOIN role r ON r.role_id=m.role_id '
               'JOIN delivery_target d ON d.phone=m.phone AND d.platform=r.platform '
               'WHERE r.platform=? AND m.enabled=1 AND m.is_left_org=0 AND d.usable=1')
        args = [platform]
        if owner is not None:
            sql += ' AND m.owner_id=?'
            args.append(owner)
        if phone is not None:
            sql += ' AND m.phone=?'
            args.append(phone)
        return [dict(r) for r in self.s.conn.execute(sql, tuple(args)).fetchall()]

    def directory(self, platform):
        c = getattr(self.s, 'wecom_contacts' if platform == 'wecom' else 'contacts', None)
        if c is None:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', '最新组织架构暂不可用，请稍后重试')
        return c

    def department_children(self, platform, parent=''):
        c = self.directory(platform)
        try:
            if platform == 'wecom':
                departments = c.org_departments(parent)
            elif parent:
                departments = c.department_children(parent)
            else:
                root = os.environ.get('IMT_ORG_ROOT_DEPT_ID', '').strip()
                if not root:
                    raise BizError(503, 'DIRECTORY_UNAVAILABLE', '量子密信尚未配置组织根部门')
                item = c.get_department(root)
                departments = [item] if item else []
            if not parent and not departments:
                raise BizError(503, 'DIRECTORY_UNAVAILABLE', '组织根部门读取失败，请检查通讯录配置')
            return [{'dept_id': str(d['dept_id']), 'name': d['name'], 'parent_id': parent,
                     'has_children': d.get('has_children', True)} for d in departments]
        except BizError:
            raise
        except Exception as exc:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', '组织部门读取失败，请稍后重试') from exc

    def department_path(self, platform, dept_id):
        c = self.directory(platform)
        if platform == 'wecom':
            nodes = {str(d['dept_id']): d for d in c.org_directory()}
            item = nodes.get(dept_id)
            trail = []
            while item and len(trail) < len(nodes):
                trail.append(item)
                parent = str(item['parent_id'])
                if parent == '0':
                    return list(reversed(trail))
                item = nodes.get(parent)
            return []
        root = os.environ.get('IMT_ORG_ROOT_DEPT_ID', '').strip()
        current = c.get_department(dept_id) if root else None
        if not current:
            return []
        trail = [current] + (c.department_parents(dept_id) if dept_id != root else [])
        for idx, item in enumerate(trail):
            if str(item['dept_id']) == root:
                return list(reversed(trail[:idx+1]))
        return []

    def organization(self, platform, root_ids=None):
        c = self.directory(platform)
        nodes, users, seen = [], {}, set()
        def visit(d, parent=''):
            did = str(d['dept_id'])
            if did in seen:
                return
            if len(seen) >= 3000:
                raise BizError(503, 'DIRECTORY_LIMIT', '组织架构过大，请联系管理员')
            seen.add(did)
            nodes.append({'dept_id': did, 'name': d['name'], 'parent_id': parent})
            rows = c.org_department_users(did) if platform == 'wecom' else c.dept_users_all(did)
            users[did] = {str(u.get('phone') or '') for u in rows if u.get('phone')}
            children = self.department_children(platform, did) if d.get('has_children', True) else []
            for child in children:
                visit(child, did)
        try:
            roots = self.department_children(platform) if root_ids is None else []
            if root_ids is not None:
                paths = [self.department_path(platform, did) for did in root_ids]
                roots = [path[-1] for path in paths if path and not any(str(d['dept_id']) in root_ids for d in path[:-1])]
            for root in roots:
                visit(root)
        except BizError:
            raise
        except Exception as exc:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', '最新组织架构查询失败，请稍后重试') from exc
        return nodes, users

    @staticmethod
    def descendants(nodes, roots):
        result = set(roots)
        for _ in range(len(nodes)):
            added = {n['dept_id'] for n in nodes if n['parent_id'] in result} - result
            if not added:
                break
            result.update(added)
        return result

    def grants(self, ident):
        rows = self.s.conn.execute('SELECT * FROM portal_grant WHERE phone=? AND platform=? AND enabled=1',
                                   (ident['phone'], ident['platform'])).fetchall()
        return [dict(r) for r in rows if self.s._template_owner_enabled(int(r['owner_id']))
                and ident['platform'] in self.s._owner_allowed_platforms(int(r['owner_id']))
                and self.members(ident['platform'], int(r['owner_id']), ident['phone'])]

    def require_creator(self, ident):
        grants = self.grants(ident)
        if not grants:
            raise BizError(403, 'PORTAL_FORBIDDEN', '您还未开放此权限，具体请找相关管理员')
        return grants

    def grant_info(self, platform, owner, phone):
        if not self.members(platform, owner, phone):
            raise BizError(403, 'FORBIDDEN', '成员不在当前管理员名册内或未激活')
        row = self.s.conn.execute('SELECT * FROM portal_grant WHERE owner_id=? AND platform=? AND phone=?',
                                 (owner, platform, phone)).fetchone()
        return {'roles': self.s.list_roles(platform=platform, owner_id=owner), 'grant': dict(row) if row else None}

    def default_department(self, platform, phone, roots):
        c = self.directory(platform)
        verify = c.resolve_user_strict if platform == 'wecom' else c.search_by_phone_strict
        status, profile = verify(phone)
        if status != 'ok':
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', '成员当前部门核对失败，请稍后重试')
        if not profile or not profile.get('dept_path'):
            return None
        official_path = profile['dept_path'].replace('|', '/')
        exact = next((d for d in profile.get('departments', []) if isinstance(d, dict) and d.get('dept_id')
                      and str(d.get('full_name') or '').replace('|', '/') == official_path), None)
        if exact:
            path = self.department_path(platform, str(exact['dept_id']))
            if path:
                return {'dept_id': str(path[-1]['dept_id']), 'name': ' / '.join(d['name'] for d in path)}
        names = [name.strip() for name in official_path.split('/') if name.strip()]
        for index, name in enumerate(names):
            root = next((item for item in roots if item['name'] == name), None)
            if root:
                path = [root]
                for segment in names[index+1:]:
                    candidates = [d for d in self.department_children(platform, path[-1]['dept_id']) if d['name'] == segment]
                    if len(candidates) != 1:
                        return None
                    path.append(candidates[0])
                return {'dept_id': path[-1]['dept_id'], 'name': ' / '.join(d['name'] for d in path)}
        return None

    def grant_options(self, platform, owner, phone, parent=''):
        info = self.grant_info(platform, owner, phone)
        if parent and not self.department_path(platform, parent):
            raise BizError(403, 'INVALID_SCOPE', '部门不在当前组织架构中')
        departments = self.department_children(platform, parent)
        if parent:
            return {'departments': departments}
        selected = []
        default = None
        notice = ''
        scope = json.loads(info['grant']['scope_json']) if info['grant'] and info['grant']['enabled'] else None
        if scope:
            for did in scope['dept_ids']:
                path = self.department_path(platform, did)
                selected.append({'dept_id': did, 'name': ' / '.join(d['name'] for d in path) if path else f'部门 {did}（已不可用）'})
        else:
            try:
                default = self.default_department(platform, phone, departments)
            except BizError as exc:
                notice = exc.message
            if default:
                selected.append(default)
        return {**info, 'departments': departments, 'selected_departments': selected,
                'default_dept_ids': [default['dept_id']] if default else [], 'directory_notice': notice}

    def save_grant(self, platform, owner, phone, data, actor):
        if not self.members(platform, owner, phone):
            raise BizError(403, 'FORBIDDEN', '成员不属于当前管理员')
        enabled = bool(data.get('enabled'))
        depts, roles = [], []
        if enabled:
            options = self.grant_info(platform, owner, phone)
            depts = data.get('dept_ids')
            if depts is None:
                depts = self.grant_options(platform, owner, phone)['default_dept_ids']
            roles = data.get('role_ids', [])
            if (not isinstance(depts, list) or not isinstance(roles, list)
                    or len(depts) > 100 or any(not isinstance(x, str) or not x for x in depts + roles)):
                raise BizError(400, 'INVALID_SCOPE', '管辖范围格式错误')
            if set(roles) - {r['role_id'] for r in options['roles']} or any(not self.department_path(platform, did) for did in set(depts)):
                raise BizError(403, 'INVALID_SCOPE', '管辖范围超出当前组织或管理员角色')
            if not depts and not roles:
                raise BizError(400, 'EMPTY_SCOPE', '请选择当前部门或角色分组')
        with self.s.conn:
            self.s.conn.execute('INSERT INTO portal_grant(owner_id,platform,phone,enabled,scope_json,updated_at) '
                                'VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled),scope_json=VALUES(scope_json),updated_at=VALUES(updated_at)',
                                (owner, platform, phone, int(enabled), json.dumps({'dept_ids': depts, 'role_ids': roles}), self.s.now_str()))
            self.s._insert_audit_unlocked(actor, 'portal.grant', 'member', phone,
                                         {'enabled': enabled, 'dept_ids': depts, 'role_ids': roles}, self.s.now_str())
        return {'ok': True}

    def recipients(self, ident):
        grants = self.require_creator(ident)
        scopes = [json.loads(grant['scope_json']) for grant in grants]
        root_ids = {did for scope in scopes for did in scope['dept_ids']}
        nodes, users, directory_error = [], {}, ''
        if root_ids:
            try:
                nodes, users = self.organization(ident['platform'], root_ids)
            except BizError as exc:
                directory_error = exc.message
        all_members, allowed_nodes, roles = {}, set(), {}
        for grant, scope in zip(grants, scopes):
            ds = self.descendants(nodes, scope['dept_ids'])
            allowed_nodes.update(ds)
            phones = set().union(*(users.get(d, set()) for d in ds)) if ds else set()
            live_roles = {r['role_id']: r for r in self.s.list_roles(platform=ident['platform'], owner_id=grant['owner_id'])}
            rs = set(scope['role_ids']) & set(live_roles)
            roles.update({rid: live_roles[rid] for rid in rs})
            for m in self.members(ident['platform'], grant['owner_id']):
                if m['phone'] in phones or m['role_id'] in rs:
                    item = all_members.setdefault(m['phone'], {'phone': m['phone'], 'name': m['name'],
                        'dept_ids': [], 'role_ids': [], 'owner_ids': [], 'member_role_ids': []})
                    item['dept_ids'].extend(did for did in ds if did not in item['dept_ids'] and m['phone'] in users.get(did, set()))
                    item['role_ids'].extend([m['role_id']] if m['role_id'] in rs else [])
                    item['member_role_ids'].append(m['role_id'])
                    item['owner_ids'].append(grant['owner_id'])
        if directory_error and not roles:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', directory_error)
        return {'departments': [d for d in nodes if d['dept_id'] in allowed_nodes],
                'roles': list(roles.values()), 'members': list(all_members.values()), 'directory_error': directory_error}

    def template(self, task_id):
        row = self.s.conn.execute('SELECT * FROM portal_task WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            return None
        t = json.loads(row['template_json'])
        t.update({'template_id': task_id, 'portal_task': True, 'platform': row['platform'],
                  'creator_phone': row['creator_phone'], 'owner_id': int(row['owner_id']),
                  'created_at': row['created_at'], 'recipients': json.loads(row['recipients_json'])})
        return t

    def create_task(self, ident, data):
        self.require_creator(ident)
        if not isinstance(data.get('name'), str) or not 1 <= len(data['name'].strip()) <= 128 or len(str(data.get('description', ''))) > 2000:
            raise BizError(400, 'INVALID_NAME', '任务名称限 1–128 字，任务要求限 2000 字')
        available = {m['phone']: m for m in self.recipients(ident)['members']}
        phones = data.get('phones')
        if not isinstance(phones, list) or not phones or len(phones) > 5000 or any(p not in available for p in phones):
            raise BizError(403, 'RECIPIENT_OUT_OF_SCOPE', '接收成员超出授权范围或未选择成员')
        selected = [dict(available[p]) for p in sorted(set(phones))]
        owners = set.intersection(*(set(m['owner_ids']) for m in selected))
        if not owners:
            raise BizError(403, 'OWNER_SCOPE_MISMATCH', '接收成员须属于同一授权管理员范围，请分批发布')
        task_owner = min(owners)
        owner_roles = {}
        for m in self.members(ident['platform'], task_owner):
            owner_roles.setdefault(m['phone'], []).append(m['role_id'])
        for m in selected:
            m['owner_ids'] = [task_owner]
            m['member_role_ids'] = owner_roles[m['phone']]
        mode = data.get('mode', 'once')
        if mode not in ('once', 'day', 'week', 'month'):
            raise BizError(400, 'INVALID_PERIOD', '任务周期无效')
        payload = {k: data[k] for k in ('name', 'remind_at', 'has_deadline', 'deadline_type', 'deadline_time', 'anchor_weekday', 'anchor_dom', 'exec_weekdays', 'exec_dom_days') if k in data}
        payload.setdefault('remind_at', '08:00')
        payload.setdefault('has_deadline', mode != 'once')
        payload.setdefault('deadline_time', '23:59')
        if mode == 'once':
            payload.update({'remind_at': '00:00', 'has_deadline': False})
        modules = data.get('upload_modules', ['text'])
        if not isinstance(modules, list) or not modules or any(m not in ('text', 'image') for m in modules):
            raise BizError(400, 'INVALID_MODULES', '请选择文字说明或图片')
        payload.update({'role_ids': sorted({r for m in selected for r in m['member_role_ids']}),
                        'kind': 'form', 'need_review': False, 'is_adhoc': False,
                        'period': mode if mode != 'once' else 'day', 'enabled': True,
                        'text_tpl': data.get('description') or data.get('name') or '',
                        'schema_json': [], 'allow_note': True,
                        'spec_json': {'upload_modules': list(dict.fromkeys(modules))}, 'max_file_mb': 10})
        # 周期和动态截止字段统一由现有模板校验器处理。
        norm = self.s._validate_and_normalize_template_unlocked(payload, existing=None)
        norm['role_ids'] = json.loads(norm.pop('role_ids_json'))
        norm['schema_json'] = json.loads(norm['schema_json'] or '[]')
        norm['spec_json'] = json.loads(norm['spec_json'] or '{}')
        norm.update({'mode': mode, 'deleted': 0, 'need_review': False, 'is_adhoc': False})
        if mode == 'once':
            deadline = str(data.get('deadline_at') or '').replace('T', ' ')
            if deadline:
                try:
                    dt = datetime.strptime(deadline, '%Y-%m-%d %H:%M' if len(deadline) == 16 else '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    raise BizError(400, 'INVALID_DEADLINE', '截止时间格式错误') from None
                deadline = format_dt(dt)
                if deadline <= self.s.now_str():
                    raise BizError(400, 'INVALID_DEADLINE', '截止时间必须晚于当前时间')
            norm['once_deadline'] = deadline or '9999-12-31 23:59:59'
            norm['has_deadline'] = bool(deadline)
            norm['deadline_type'] = None
            norm['deadline_time'] = deadline[11:16] if deadline else None
            norm['deadline'] = norm['deadline_time']
        key = str(data.get('request_id') or '')
        if not 8 <= len(key) <= 64:
            raise BizError(400, 'REQUEST_ID_REQUIRED', '发布请求缺少有效幂等编号')
        task_id = 'pt_' + hashlib.sha256((ident['platform'] + ident['phone'] + key).encode()).hexdigest()[:40]
        with self.s.conn:
            self.s.conn.execute('INSERT OR IGNORE INTO portal_task(task_id,platform,creator_phone,owner_id,template_json,recipients_json,created_at) VALUES(?,?,?,?,?,?,?)',
                (task_id, ident['platform'], ident['phone'], task_owner, json.dumps(norm, ensure_ascii=False),
                 json.dumps(selected, ensure_ascii=False), self.s.now_str()))
        return {'ok': True, 'task_id': task_id}

    def can_submit(self, inst, tpl):
        target = next((m for m in tpl['recipients'] if m['phone'] == inst['phone']), None)
        if not target:
            return False
        return any(m['role_id'] in target['member_role_ids'] and m['owner_id'] in target['owner_ids']
                   for m in self.members(tpl['platform'], phone=inst['phone']))

    def todos(self, ident):
        rows = self.s.conn.execute("SELECT * FROM task_instance WHERE phone=? AND status IN ('pending','rejected') ORDER BY deadline_at,id", (ident['phone'],)).fetchall()
        result = []
        for row in rows:
            inst = dict(row)
            t = self.s.get_template(inst['template_id'])
            if (not t or t['kind'] == 'notice' or t.get('is_adhoc') or inst['period_key'].startswith('adhoc-')
                    or self.s.instance_close_reason(inst, t)):
                continue
            plat = t.get('platform') if t.get('portal_task') else next(iter(self.s.role_platforms(t['role_ids'])), '')
            if plat == ident['platform'] and effective_instance_deadline(inst) > self.s.now_str():
                inst['template_name'] = t['name']
                result.append(inst)
        return result

    def task_link(self, ident, instance_id):
        inst = next((i for i in self.todos(ident) if i['id'] == instance_id), None)
        if inst is None:
            raise BizError(403, 'TASK_UNAVAILABLE', '任务不在可提交窗口内')
        raw = self.s.issue_h5_token(inst['phone'], inst['template_id'], inst['period_key'], inst['id'],
                                    ttl_hours=calc_h5_token_ttl_hours(inst, self.s.now()))
        return {'ok': True, 'url': '/h5/task#t=' + raw}

    def submission_history(self, ident, *, instance_id=None, adhoc_only=False, offset=0, limit=31):
        match, params = [], [ident['phone'], ident['platform'], f"adhoc-{ident['platform']}-%", ident['platform']]
        if instance_id is not None:
            match.append('AND i.id=? ')
            params.append(instance_id)
        if adhoc_only:
            match.append("AND i.period_key LIKE 'adhoc-%' ")
        rows = self.s.conn.execute(
            "SELECT COALESCE(t.name, JSON_UNQUOTE(JSON_EXTRACT(p.template_json,'$.name'))) AS name, "
            "i.id, i.template_id, i.period_key, COALESCE(i.submitted_at, "
            "(SELECT MAX(latest.created_at) FROM submission latest WHERE latest.instance_id=i.id)) AS submitted_at, i.status, "
            "(i.period_key LIKE 'adhoc-%') AS is_adhoc FROM task_instance i "
            "LEFT JOIN task_template t ON t.template_id=i.template_id LEFT JOIN portal_task p ON p.task_id=i.template_id "
            "WHERE i.phone=? AND EXISTS (SELECT 1 FROM submission s WHERE s.instance_id=i.id) "
            "AND (p.platform=? OR (t.template_id IS NOT NULL AND (i.period_key LIKE ? OR "
            "(i.period_key NOT REGEXP '^adhoc-(zdxlz|wecom)-' AND EXISTS "
            "(SELECT 1 FROM role r WHERE r.platform=? AND JSON_CONTAINS(t.role_ids,JSON_QUOTE(r.role_id))))))) "
            + ''.join(match) + "ORDER BY submitted_at DESC, i.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [{**dict(row), 'can_edit': row['status'] in ('submitted', 'approved')} for row in rows]

    def submission_history_link(self, ident, instance_id):
        records = self.submission_history(ident, instance_id=instance_id, limit=1)
        if not records:
            raise BizError(404, 'NOT_FOUND', '上报记录不存在')
        report = records[0]
        raw = self.s.issue_h5_token(ident['phone'], report['template_id'], report['period_key'], report['id'],
                                    purpose='report_history', ttl_hours=1)
        return {'ok': True, 'url': '/h5/task#t=' + raw}

    def tasks(self, ident=None, owner=None, platform=None):
        where, args = [], []
        if ident:
            where.extend(['creator_phone=?', 'platform=?'])
            args.extend([ident['phone'], ident['platform']])
        if owner is not None:
            where.append('owner_id=?')
            args.append(owner)
        if platform:
            where.append('platform=?')
            args.append(platform)
        rows = self.s.conn.execute('SELECT task_id FROM portal_task' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY created_at DESC', tuple(args)).fetchall()
        return [self.template(r['task_id']) for r in rows]

    def statistics(self, task):
        rows = [dict(r) for r in self.s.conn.execute('SELECT * FROM task_instance WHERE template_id=? ORDER BY period_key DESC,id', (task['template_id'],)).fetchall()]
        periods = {}
        for inst in rows:
            sub = self.s.get_latest_valid_submission(inst['id'])
            inst['submission'] = sub
            group = periods.setdefault(inst['period_key'], {'period_key': inst['period_key'], 'expected': 0, 'submitted': 0, 'members': []})
            group['expected'] += 1
            group['submitted'] += int(inst['status'] in ('approved', 'submitted'))
            group['members'].append(inst)
        for group in periods.values():
            group['pending'] = group['expected'] - group['submitted']
        return {'task_id': task['template_id'], 'name': task['name'], 'creator_phone': task['creator_phone'], 'periods': list(periods.values())}


def run_portal_tasks(scheduler, now):
    """发送前持久化唯一占位；并发 tick、发布重入与进程重启均不重发。"""
    from .scheduler import calc_instance_deadline, is_template_anchor_day, period_key
    s, p = scheduler.state, PortalService(scheduler.state)
    for task in p.tasks():
        if not scheduler.notifiers and s.platform != task['platform']:
            continue
        if not p.grants({'phone': task['creator_phone'], 'platform': task['platform']}):
            continue
        if task['mode'] != 'once':
            if not is_template_anchor_day(task, now):
                continue
            planned = scheduler._build_planned_dt(now, task.get('remind_at') or '08:00')
            pk = period_key(task['period'], now)
            deadline = calc_instance_deadline(task, now)
        else:
            planned = now
            pk, deadline = 'once', task['once_deadline']
        if deadline <= format_dt(now):
            continue
        for m in task['recipients']:
            if not p.can_submit({'phone': m['phone']}, task):
                continue
            inst = s.ensure_instance(task['template_id'], m['phone'], m['name'], None, pk, deadline)
            if inst['status'] != 'pending' or now < planned:
                continue
            idem = hashlib.sha256(f"portal|{inst['id']}".encode()).hexdigest()
            # 任意已占位结果均不再发送：失败保留给监管，不自动冒险重发。
            if s.list_reminder_attempts(idem):
                continue
            late = task['mode'] != 'once' and (now - planned).total_seconds() > scheduler.COMPENSATION_WINDOW_SECONDS
            claimed = s.record_reminder_log(template_id=task['template_id'], instance_id=inst['id'], phone=m['phone'], kind='portal',
                planned_at=format_dt(planned), sent_at=None, result='missed' if late else 'pending', error='COMPENSATION_WINDOW_EXCEEDED' if late else None, idem_key=idem)
            if not claimed or late:
                continue
            for _, external_id, ext_type, notifier in scheduler._targets_for(m['phone'], only_platform=task['platform']):
                if not external_id:
                    continue
                link, _ = scheduler.build_h5_submit_url(phone=m['phone'], template_id=task['template_id'], period_key_val=pk, instance_id=inst['id'])
                try:
                    scheduler._delivery_bucket.acquire()
                    fresh = s.get_instance(inst['id'])
                    if not fresh or fresh['status'] != 'pending' or not p.can_submit(fresh, task):
                        s.conn.execute("UPDATE reminder_log SET result='skipped',error='TASK_CLOSED' WHERE idem_key=?", (idem,))
                        continue
                    res = notifier.send_text(external_id, f"【{task['name']}】\n{task['text_tpl']}\n提交入口：{link}", idem, receive_type=ext_type)
                    result = 'sent' if res.ok else 'failed'
                except Exception:
                    result = 'failed'
                s.conn.execute('UPDATE reminder_log SET result=?,sent_at=? WHERE idem_key=?', (result, s.now_str() if result == 'sent' else None, idem))
