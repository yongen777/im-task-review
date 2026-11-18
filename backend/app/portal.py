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

    def organization(self, platform):
        c = getattr(self.s, 'wecom_contacts' if platform == 'wecom' else 'contacts', None)
        if c is None:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', '最新组织架构暂不可用，请稍后重试')
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
            children = c.org_departments(did) if platform == 'wecom' else c.department_children(did)
            for child in children:
                visit(child, did)
        try:
            if platform == 'wecom':
                roots = c.org_departments('')
            else:
                root = os.environ.get('IMT_ORG_ROOT_DEPT_ID', '').strip()
                roots = [c.get_department(root)] if root else []
            if not roots or not all(roots):
                raise ValueError('无根部门')
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

    def grant_options(self, platform, owner, phone):
        if not self.members(platform, owner, phone):
            raise BizError(403, 'FORBIDDEN', '成员不在当前管理员名册内或未激活')
        nodes, users = self.organization(platform)
        row = self.s.conn.execute('SELECT * FROM portal_grant WHERE owner_id=? AND platform=? AND phone=?',
                                 (owner, platform, phone)).fetchone()
        return {'departments': nodes, 'roles': self.s.list_roles(platform=platform, owner_id=owner),
                'default_dept_ids': [did for did, phones in users.items() if phone in phones],
                'grant': dict(row) if row else None}

    def save_grant(self, platform, owner, phone, data, actor):
        if not self.members(platform, owner, phone):
            raise BizError(403, 'FORBIDDEN', '成员不属于当前管理员')
        enabled = bool(data.get('enabled'))
        depts, roles = [], []
        if enabled:
            options = self.grant_options(platform, owner, phone)
            depts = data.get('dept_ids', options['default_dept_ids'])
            roles = data.get('role_ids', [])
            if not isinstance(depts, list) or not isinstance(roles, list):
                raise BizError(400, 'INVALID_SCOPE', '管辖范围格式错误')
            if set(depts) - {d['dept_id'] for d in options['departments']} or set(roles) - {r['role_id'] for r in options['roles']}:
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
        nodes, users = self.organization(ident['platform'])
        all_members, allowed_nodes, roles = {}, set(), {}
        for grant in grants:
            scope = json.loads(grant['scope_json'])
            ds = self.descendants(nodes, scope['dept_ids'])
            allowed_nodes.update(ds)
            phones = set().union(*(users.get(d, set()) for d in ds)) if ds else set()
            live_roles = {r['role_id']: r for r in self.s.list_roles(platform=ident['platform'], owner_id=grant['owner_id'])}
            rs = set(scope['role_ids']) & set(live_roles)
            roles.update({rid: live_roles[rid] for rid in rs})
            for m in self.members(ident['platform'], grant['owner_id']):
                if m['phone'] in phones or m['role_id'] in rs:
                    item = all_members.setdefault(m['phone'], {'phone': m['phone'], 'name': m['name'],
                        'dept_ids': [did for did in ds if m['phone'] in users.get(did, set())], 'role_ids': [],
                        'owner_ids': [], 'member_role_ids': []})
                    item['role_ids'].extend([m['role_id']] if m['role_id'] in rs else [])
                    item['member_role_ids'].append(m['role_id'])
                    item['owner_ids'].append(grant['owner_id'])
        return {'departments': [d for d in nodes if d['dept_id'] in allowed_nodes],
                'roles': list(roles.values()), 'members': list(all_members.values())}

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
            if not t or t['kind'] == 'notice' or self.s.instance_close_reason(inst, t):
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
            if now < planned:
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
                    res = notifier.send_text(external_id, f"【{task['name']}】\n{task['text_tpl']}\n提交入口：{link}", idem, receive_type=ext_type)
                    result = 'sent' if res.ok else 'failed'
                except Exception:
                    result = 'failed'
                s.conn.execute('UPDATE reminder_log SET result=?,sent_at=? WHERE idem_key=?', (result, s.now_str() if result == 'sent' else None, idem))
