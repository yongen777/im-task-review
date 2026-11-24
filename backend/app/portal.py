"""个人工作台：独立授权/任务存储，共享提交与上传基础设施。"""
import hashlib
import json
from datetime import datetime

from .config import canonical_part, format_dt
from .state import BizError, calc_h5_token_ttl_hours, effective_instance_deadline


def _norm_dept_path(raw):
    """dept_path 规范化：'/' 两侧空格剔除、空段丢弃、'|' 容错替换。

    建树（local_dept_tree）、默认部门（local_default_department）与选人
    （recipients）**必须共用同一份规范化**，否则「本部门 / 下级」这类
    斜杠带空格的历史数据会在子树匹配时静默漏人。
    """
    return '/'.join(p.strip() for p in str(raw or '').replace('|', '/').split('/') if p.strip())


class PortalService:
    def __init__(self, state):
        self.s = state

    # ── 2026-11-23 本地部门树（根治授权弹窗 / 选人页「组织架构读取超时」）──────
    #
    # 授权弹窗（grant_options）与领导 H5 选人页（recipients）**只**基于本地名册
    # member.dept_path 实时聚合，打开即毫秒级，绝不再递归调用全省通讯录接口。
    # 节点 dept_id = 部门全路径（'/' 连接，与 member.dept_path 同格式）：
    # 稳定、可回溯，与「部门路径授权记录」天然兼容；口径与 members() 完全一致
    # （已导入 + 已启用 + 未离职 + 已激活可投递），所以未激活成员不会撑起空部门。

    def local_dept_tree(self, platform, owner):
        """owner 名下名册的部门树：全路径节点 + 各部门子树可投递成员集合。"""
        members = self.members(platform, owner)
        nodes, subtree = {}, {}
        for m in members:
            path = _norm_dept_path(m.get('dept_path'))
            if not path:
                continue
            parts = path.split('/')
            acc = []
            for part in parts:
                acc.append(part)
                nodes.setdefault('/'.join(acc), {'name': part})
            for node in nodes:
                if path == node or path.startswith(node + '/'):
                    subtree.setdefault(node, set()).add(m['phone'])
        departments = [{'dept_id': node, 'name': nodes[node]['name'],
                        'parent_id': node.rsplit('/', 1)[0] if '/' in node else '',
                        'has_children': any(other.startswith(node + '/') for other in nodes),
                        'member_count': len(subtree.get(node) or ())} for node in nodes]
        departments.sort(key=lambda d: d['dept_id'])
        return {'departments': departments, 'paths': set(nodes), 'subtree': subtree}

    def local_default_department(self, platform, owner, phone):
        """成员本人当前部门——直接取本地名册 dept_path，不再远程核对。"""
        row = next((m for m in self.members(platform, owner, phone)), None)
        path = _norm_dept_path((row or {}).get('dept_path'))
        if not path:
            return None
        return {'dept_id': path, 'name': path.replace('/', ' / ')}

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

    def admin_contacts(self, ident):
        return self.s.member_admin_contacts(ident['phone'], ident['platform'])

    # 2026-11-23：旧的远程通讯录树（directory / department_children / department_path /
    # organization / descendants）已整体移除——授权弹窗与选人页改走 local_dept_tree，
    # 打开即毫秒级，不再有「组织架构读取超时」。

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

    def grant_options(self, platform, owner, phone, parent='', with_directory=True):
        info = self.grant_info(platform, owner, phone)
        tree = self.local_dept_tree(platform, owner)
        if parent:
            if parent not in tree['paths']:
                raise BizError(403, 'INVALID_SCOPE', '部门不在当前授权范围内')
            children = [dict(d) for d in tree['departments'] if d['parent_id'] == parent]
            if with_directory:
                children = [c for c in children if c['member_count']]
            return {'departments': children, 'directory_notice': ''}
        departments = [d for d in tree['departments'] if d['member_count']] if with_directory else tree['departments']
        selected, default = [], None
        scope = json.loads(info['grant']['scope_json']) if info['grant'] and info['grant']['enabled'] else None
        if scope:
            for did in scope['dept_ids']:
                known = did in tree['paths']
                selected.append({'dept_id': did,
                                 'name': did.replace('/', ' / ') if known else f'部门 {did}（历史授权，请重新勾选）'})
        else:
            default = self.local_default_department(platform, owner, phone)
            if default:
                selected.append(default)
        return {**info, 'departments': departments, 'selected_departments': selected,
                'default_dept_ids': [default['dept_id']] if default else [], 'directory_notice': ''}

    def save_grant(self, platform, owner, phone, data, actor):
        if not self.members(platform, owner, phone):
            raise BizError(403, 'FORBIDDEN', '成员不属于当前管理员')
        enabled = bool(data.get('enabled'))
        depts, roles = [], []
        if enabled:
            options = self.grant_info(platform, owner, phone)
            depts = data.get('dept_ids')
            if depts is None:
                default = self.local_default_department(platform, owner, phone)
                depts = [default['dept_id']] if default else []
            roles = data.get('role_ids', [])
            if (not isinstance(depts, list) or not isinstance(roles, list)
                    or len(depts) > 100 or any(not isinstance(x, str) or not x for x in depts + roles)):
                raise BizError(400, 'INVALID_SCOPE', '管辖范围格式错误')
            if not depts and not roles:
                # 2026-11-20：开启授权但一个部门/角色都没勾 → 自动回退为「成员当前所在部门」。
                # 2026-11-23：回退数据源改为本地名册 dept_path（零远程调用）。
                default = self.local_default_department(platform, owner, phone)
                depts = [default['dept_id']] if default else []
                if not depts:
                    raise BizError(400, 'SCOPE_UNRESOLVED',
                                   '未能识别该成员当前所在部门，请手动勾选部门或角色分组')
            # 2026-11-23：部门 id = 本地名册部门全路径；**历史保存的旧 dept_ids 原样放行**，
            # 保证已有授权记录重新保存（不勾动）不会被 403 挡住。
            previous = set()
            if options['grant'] and options['grant']['enabled']:
                try:
                    previous = set(json.loads(options['grant']['scope_json']).get('dept_ids') or [])
                except (ValueError, TypeError):
                    previous = set()
            tree_paths = self.local_dept_tree(platform, owner)['paths']
            if set(roles) - {r['role_id'] for r in options['roles']} or any(
                    did not in tree_paths and did not in previous for did in set(depts)):
                raise BizError(403, 'INVALID_SCOPE', '管辖范围超出当前组织或管理员角色')
        with self.s.conn:
            self.s.conn.execute('INSERT INTO portal_grant(owner_id,platform,phone,enabled,scope_json,updated_at) '
                                'VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled),scope_json=VALUES(scope_json),updated_at=VALUES(updated_at)',
                                (owner, platform, phone, int(enabled), json.dumps({'dept_ids': depts, 'role_ids': roles}), self.s.now_str()))
            self.s._insert_audit_unlocked(actor, 'portal.grant', 'member', phone,
                                         {'enabled': enabled, 'dept_ids': depts, 'role_ids': roles}, self.s.now_str())
        return {'ok': True}

    def recipients(self, ident):
        """领导选人页可选范围：**完全走本地名册部门树**（0 次远程通讯录调用）。

        每条授权独立生效：部门授权 → 该部门子树；未勾部门 → 回退「成员本人部门」
        子树（2026-11-20 口径不变）；另叠加角色授权。部门 id = 部门全路径，
        与 grant_options 的树同一口径，授权范围校验与选人 100% 一致。
        """
        grants = self.require_creator(ident)
        all_members, allowed_nodes, node_names, roles = {}, set(), {}, {}
        directory_error = ''
        for grant in grants:
            scope = json.loads(grant['scope_json'])
            tree = self.local_dept_tree(ident['platform'], grant['owner_id'])
            node_names.update({p: d['name'] for p, d in ((n['dept_id'], n) for n in tree['departments'])})
            ds = set()
            for did in scope['dept_ids']:
                if did in tree['paths']:
                    ds |= {p for p in tree['paths'] if p == did or p.startswith(did + '/')}
            if not ds:
                # 2026-11-23 向下兼容：未勾部门，或历史远程 dept_ids 全部无法本地解析时，
                # 回退「成员本人当前部门」子树（授权继续生效，不会因升级突然失效）。
                default = self.local_default_department(ident['platform'], grant['owner_id'], ident['phone'])
                if default:
                    ds |= {p for p in tree['paths']
                           if p == default['dept_id'] or p.startswith(default['dept_id'] + '/')}
                else:
                    directory_error = directory_error or '未能识别成员当前所在部门，请让管理员重新配置授权范围'
            allowed_nodes.update(ds)
            phones = set().union(*(tree['subtree'].get(d, set()) for d in ds)) if ds else set()
            live_roles = {r['role_id']: r for r in self.s.list_roles(platform=ident['platform'], owner_id=grant['owner_id'])}
            rs = set(scope['role_ids']) & set(live_roles)
            roles.update({rid: live_roles[rid] for rid in rs})
            for m in self.members(ident['platform'], grant['owner_id']):
                if m['phone'] not in phones and m['role_id'] not in rs:
                    continue
                item = all_members.setdefault(m['phone'], {'phone': m['phone'], 'name': m['name'],
                    'dept_ids': [], 'role_ids': [], 'owner_ids': [], 'member_role_ids': []})
                # 2026-11-23 补丁：与 local_dept_tree 同一份规范化——斜杠两侧空格
                # 不剔除会让祖先路径对不上 allowed_nodes（成员静默丢部门、漏勾选）。
                path = _norm_dept_path(m.get('dept_path'))
                ancestors = []
                acc = []
                for part in ([p for p in path.split('/') if p] if path else []):
                    acc.append(part)
                    ancestors.append('/'.join(acc))
                item['dept_ids'].extend(did for did in ancestors
                                        if did in allowed_nodes and did not in item['dept_ids'])
                item['role_ids'].extend([m['role_id']] if m['role_id'] in rs else [])
                item['member_role_ids'].append(m['role_id'])
                item['owner_ids'].append(grant['owner_id'])
        if directory_error and not roles and not allowed_nodes:
            raise BizError(503, 'DIRECTORY_UNAVAILABLE', directory_error)
        # 2026-11-20：选人页部门树按「已激活可投递成员」裁剪——子树 0 人的空部门不下发。
        departments = [{'dept_id': path, 'name': node_names.get(path) or path.rsplit('/', 1)[-1],
                        'parent_id': path.rsplit('/', 1)[0] if '/' in path else '',
                        'has_children': any(other.startswith(path + '/') for other in allowed_nodes),
                        'member_count': sum(1 for item in all_members.values() if path in item['dept_ids'])}
                       for path in sorted(allowed_nodes)]
        return {'departments': [d for d in departments if d['member_count']],
                'roles': list(roles.values()), 'members': list(all_members.values()),
                'directory_error': directory_error}

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
        kind = 'notice' if data.get('kind') == 'notice' else 'upload'
        payload = {k: data[k] for k in ('name', 'remind_at', 'has_deadline', 'deadline_type', 'deadline_time', 'anchor_weekday', 'anchor_dom', 'exec_weekdays', 'exec_dom_days') if k in data}
        payload.setdefault('remind_at', '08:00')
        payload.setdefault('has_deadline', mode != 'once')
        payload.setdefault('deadline_time', '23:59')
        if mode == 'once':
            payload.update({'remind_at': '00:00', 'has_deadline': False})
        if kind == 'notice':
            # 2026-11-23：领导 H5「通知提醒」——文本/图片/文件/视频（复用后台同款素材校验与存储）
            # + 可选回执（link 默认 / text），投递走 scheduler._deliver_notice 同一条链。
            spec = self._notice_spec(data, task_owner)
            payload.update({'role_ids': sorted({r for m in selected for r in m['member_role_ids']}),
                            'kind': 'notice', 'need_review': False, 'is_adhoc': False,
                            'period': 'day', 'enabled': True, 'allow_note': False,
                            'text_tpl': data.get('description') or data.get('name') or '',
                            'schema_json': None, 'spec': spec})
            if mode == 'once':
                payload['is_manual'] = True
            else:
                payload['notice_times'] = [str(data.get('remind_at') or '08:00')]
        else:
            raw_spec = data.get('spec')
            if raw_spec is None:
                raw_spec = {}
            if not isinstance(raw_spec, dict):
                raise BizError(400, 'INVALID_MODULES', '提交内容配置格式错误')
            modules = data.get('upload_modules', raw_spec.get('upload_modules', ['text']))
            if not isinstance(modules, list) or not modules or any(m not in ('fields', 'text', 'image', 'file') for m in modules):
                raise BizError(400, 'INVALID_MODULES', '请至少选择一种提交内容类型')
            spec = {key: raw_spec[key] for key in ('fields', 'allow_multiple_images', 'allow_multiple_files') if key in raw_spec}
            for key in ('allow_multiple_images', 'allow_multiple_files'):
                if key in data and key not in spec:
                    spec[key] = data[key]
            spec.update({'upload_modules': list(dict.fromkeys(modules)), 'require_review': False,
                         'text_label': '文字情况说明', 'text_placeholder': '请填写补充说明…'})
            payload.update({'role_ids': sorted({r for m in selected for r in m['member_role_ids']}),
                            'kind': 'upload', 'need_review': False, 'is_adhoc': False,
                            'period': mode if mode != 'once' else 'day', 'enabled': True,
                            'text_tpl': data.get('description') or data.get('name') or '',
                            'schema_json': data.get('schema_json'), 'allow_note': True,
                            'spec': spec, 'max_file_mb': 10 if 'file' in modules else 9})
        # 周期和动态截止字段统一由现有模板校验器处理。
        norm = self.s._validate_and_normalize_template_unlocked(payload, existing=None, owner_id=task_owner)
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

    def _notice_spec(self, data, owner):
        """领导端通知内容 spec：素材（图片/文件/视频，复用 notice_asset）+ 可选回执配置。"""
        from .notice_media import get_asset

        items = data.get('notice_items')
        if items is None:
            items = []
        if not isinstance(items, list) or len(items) > 30:
            raise BizError(400, 'INVALID_NOTICE_ITEMS', '通知附件过多或格式错误')
        out = []
        for item in items:
            if not isinstance(item, dict) or item.get('format') not in ('image', 'file', 'video'):
                raise BizError(400, 'INVALID_NOTICE_ITEMS', '通知附件类型无效')
            fmt = str(item['format'])
            asset = get_asset(self.s, str(item.get('asset_id') or ''))
            if not asset or int(asset['owner_id']) != int(owner) or asset['kind'] != fmt:
                raise BizError(403, 'INVALID_NOTICE_ASSET', '通知附件不属于当前授权范围或已失效，请重新上传')
            out.append({'format': fmt, 'asset_id': asset['asset_id'],
                        'file_name': asset['file_name'], 'file_size': int(asset['size'] or 0),
                        'file_mime': asset['mime']})
        receipt = data.get('notice_receipt')
        if receipt is None:
            receipt = {}
        if not isinstance(receipt, dict):
            raise BizError(400, 'INVALID_RECEIPT', '回执配置格式错误')
        enabled = bool(receipt.get('enabled'))
        mode = 'text' if str(receipt.get('mode') or 'link') == 'text' else 'link'
        raw_label = receipt.get('phrase') if mode == 'text' else receipt.get('button_label')
        label = str(raw_label or '').strip()[:80] or '我已知晓'
        config = {'enabled': enabled, 'mode': mode,
                  ('phrase' if mode == 'text' else 'button_label'): label}
        guide = str(receipt.get('guide_text') or '').strip()
        if mode == 'link' and guide:
            config['receipt_guide_text'] = guide[:120]
        return {'notice_items': out, 'notice_receipt': config}

    def can_submit(self, inst, tpl):
        target = next((m for m in tpl['recipients'] if m['phone'] == inst['phone']), None)
        if not target:
            return False
        return any(m['role_id'] in target['member_role_ids'] and m['owner_id'] in target['owner_ids']
                   for m in self.members(tpl['platform'], phone=inst['phone']))

    def todos(self, ident):
        """我的待办（2026-11-20 切期改造：按「模板 + 归属周期」严格去重）。

        · 同一归属周期内不论定时/手动推了多少批（2026-11-19 批次键），未完成时
          **永远只展示最新 1 条**（MAX(id)）；
        · 成员在任一批次上完成/回执 → sync_family_completion 把周期内全部实例同步为
          完成态，待办里立即消失，后续重推也不再出现；
        · 2026-11-21 需求三：已过截止且本周期仍未完成 → **不再直接隐藏**，改以
          「已逾期」提醒卡片保留（无去完成通道，按钮 =「我知道了」）；成员点过
          「我知道了」（overdue_ack 持久化）后从待办移除，刷新/重进不再复现。
        """
        from .config import canonical_part
        from .scheduler import canonical_period_key

        rows = self.s.conn.execute(
            "SELECT * FROM task_instance WHERE phone=? AND status IN ('pending','rejected','overdue') "
            "ORDER BY deadline_at,id", (ident['phone'],)).fetchall()
        pick: dict = {}
        for row in rows:
            inst = dict(row)
            key = (inst['template_id'], canonical_part(inst['period_key']))
            cur = pick.get(key)
            if cur is None or int(inst['id']) > int(cur['id']):
                pick[key] = inst
        acked = self.s.overdue_acked_keys(ident['phone'])
        now_s = self.s.now_str()
        result = []
        for inst in pick.values():
            t = self.s.get_template(inst['template_id'])
            if (not t or t['kind'] == 'notice' or t.get('is_adhoc') or inst['period_key'].startswith('adhoc-')
                    or self.s.instance_close_reason(inst, t)):
                continue
            plat = t.get('platform') if t.get('portal_task') else next(iter(self.s.role_platforms(t['role_ids'])), '')
            if plat != ident['platform']:
                continue
            if effective_instance_deadline(inst) > now_s:
                inst['template_name'] = t['name']
                result.append(inst)
                continue
            # 已过截止：仅保留「当前归属周期」的逾期提醒（历史周期不长期占位刷屏）
            try:
                current_key = 'once' if t.get('is_manual') else canonical_period_key(t, self.s.now())
            except Exception:  # noqa: BLE001 - 模板字段异常时不阻塞待办渲染
                continue
            if canonical_part(inst['period_key']) != current_key:
                continue
            if (inst['template_id'], current_key) in acked:
                continue
            inst['template_name'] = t['name']
            inst['overdue'] = True
            inst['ack_period_key'] = current_key
            result.append(inst)
        result.sort(key=lambda i: (i['deadline_at'], i['id']))
        # 2026-11-20 切期改造：未回执通知同样按「模板 + 归属周期」去重（同周期
        # 重推多批时只展示最新一条；旧批次的确认入口保留但不再刷屏）。
        receipts = self.s.pending_notice_receipts(ident['phone'], ident['platform'])
        seen: set = set()
        for item in reversed(list(receipts)):
            key = (item['template_id'], canonical_part(item.get('period_key') or ''))
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def dismiss_overdue(self, ident, task_id, period_key):
        """2026-11-21 需求三：「我知道了」——按 (模板, 归属周期, 手机号) 持久化已读。

        校验该实例确实属于本人在当前归属周期内的逾期未完成项，避免任意 task_id
        被当作已读写入（只影响本人待办展示，不写实例状态）。
        """
        from .config import canonical_part

        clean_tpl = str(task_id or '').strip()
        if not clean_tpl:
            raise BizError(400, 'VALIDATION_FAILED', '缺少任务编号')
        current = [i for i in self.todos(ident) if i['template_id'] == clean_tpl]
        inst = current[0] if current else None
        if inst is None or not inst.get('overdue'):
            raise BizError(404, 'NOT_FOUND', '该任务当前没有逾期提醒')
        key = period_key or inst.get('ack_period_key') or inst['period_key']
        self.s.ack_overdue_todo(clean_tpl, str(key), ident['phone'])
        return {'ok': True, 'template_id': clean_tpl, 'period_key': canonical_part(str(key))}

    def task_link(self, ident, instance_id):
        # 2026-11-21 需求三：逾期提醒卡片只做「我知道了」，不提供去完成通道
        inst = next((i for i in self.todos(ident)
                     if i['id'] == instance_id and not i.get('overdue') and not i.get('receipt_phrase')), None)
        if inst is None:
            raise BizError(403, 'TASK_UNAVAILABLE', '任务不在可提交窗口内')
        raw = self.s.issue_h5_token(inst['phone'], inst['template_id'], inst['period_key'], inst['id'],
                                    ttl_hours=calc_h5_token_ttl_hours(inst, self.s.now()))
        return {'ok': True, 'url': '/h5/task#t=' + raw}

    def notice_receipt_link(self, ident, instance_id):
        from .notice_receipts import issue_link

        if not self.s.pending_notice_receipts(ident['phone'], ident['platform'], mode='link', instance_id=instance_id):
            raise BizError(404, 'NOTICE_UNAVAILABLE', '通知已确认或暂不可用')
        instance = self.s.get_instance(instance_id)
        if not instance:
            raise BizError(404, 'NOTICE_UNAVAILABLE', '通知暂不可用')
        return {'ok': True, 'url': issue_link(self.s, instance)}

    def submission_history(self, ident, *, instance_id=None, adhoc_only=False, offset=0, limit=31):
        match, params = [], [ident['phone'], ident['platform'], f"adhoc-{ident['platform']}-%", ident['platform']]
        if instance_id is not None:
            match.append('AND i.id=? ')
            params.append(instance_id)
        if adhoc_only:
            match.append("AND i.period_key LIKE 'adhoc-%' ")
        rows = self.s.conn.execute(
            "SELECT COALESCE(t.name, JSON_UNQUOTE(JSON_EXTRACT(p.template_json,'$.name'))) AS name, "
            "i.id, i.phone, i.template_id, i.period_key, COALESCE(i.submitted_at, "
            "(SELECT MAX(latest.created_at) FROM submission latest WHERE latest.instance_id=i.id)) AS submitted_at, i.status, "
            "(i.period_key LIKE 'adhoc-%') AS is_adhoc, "
            "EXISTS (SELECT 1 FROM submission s WHERE s.instance_id=i.id AND s.status!='rejected') AS has_valid_submission "
            "FROM task_instance i "
            "LEFT JOIN task_template t ON t.template_id=i.template_id LEFT JOIN portal_task p ON p.task_id=i.template_id "
            "WHERE i.phone=? AND EXISTS (SELECT 1 FROM submission s WHERE s.instance_id=i.id) "
            "AND (p.task_id IS NOT NULL OR t.kind!='notice') "
            "AND (p.platform=? OR (t.template_id IS NOT NULL AND (i.period_key LIKE ? OR "
            "(i.period_key NOT REGEXP '^adhoc-(zdxlz|wecom)-' AND EXISTS "
            "(SELECT 1 FROM role r WHERE r.platform=? AND JSON_CONTAINS(t.role_ids,JSON_QUOTE(r.role_id))))))) "
            + ''.join(match) + "ORDER BY submitted_at DESC, i.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            template = (self.s.get_template(row['template_id'])
                        if record.pop('has_valid_submission') and row['status'] in ('submitted', 'approved') else None)
            record['can_edit'] = self.s.h5_submission_editable(row, template)
            record.pop('phone')
            records.append(record)
        return records

    def submission_history_link(self, ident, instance_id, *, edit=False):
        records = self.submission_history(ident, instance_id=instance_id, limit=1)
        if not records:
            raise BizError(404, 'NOT_FOUND', '上报记录不存在')
        report = records[0]
        if edit and not report['can_edit']:
            raise BizError(403, 'TASK_CLOSED', '这条提交已关闭编辑')
        raw = self.s.issue_h5_token(ident['phone'], report['template_id'], report['period_key'], report['id'],
                                    purpose='report_history', ttl_hours=1)
        return {'ok': True, 'url': '/h5/task#t=' + raw + ('&edit=1' if edit else '')}

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
        # 2026-11-20 对齐管理员统计口径：cancelled（发布后成员被移除/停用名册）的
        # 历史行不计入应交，避免「应交」虚高、完成率虚低。
        # 2026-11-23：通知任务改按送达/回执口径——回执通知 = 应回复/已回复/待回复/回复率，
        # 纯广播 = 应送达/已送达（与后台监管面板、.xlsx 导出同一口径）。
        from .notice_media import notice_receipt_config

        is_notice = task.get('kind') == 'notice'
        receipt_mode, receipt_phrase = notice_receipt_config(task) if is_notice else (None, None)
        rows = [dict(r) for r in self.s.conn.execute(
            "SELECT * FROM task_instance WHERE template_id=? AND status != 'cancelled' "
            "ORDER BY period_key DESC,id", (task['template_id'],)).fetchall()]
        periods = {}
        for inst in rows:
            if is_notice:
                inst['submission'] = None
                inst['delivered'] = inst['status'] in ('sent', 'approved')
                inst['replied'] = bool(inst.get('receipt_at'))
            else:
                inst['submission'] = self.s.get_latest_valid_submission(inst['id'])
            group = periods.setdefault(inst['period_key'], {
                'period_key': inst['period_key'], 'expected': 0, 'submitted': 0, 'members': [],
                'kind': 'notice' if is_notice else 'upload'})
            group['expected'] += 1
            group['submitted'] += int(inst['delivered']) if is_notice else int(inst['status'] in ('approved', 'submitted'))
            group['members'].append(inst)
        for group in periods.values():
            group['pending'] = group['expected'] - group['submitted']
            if is_notice:
                group.update({'receipt_mode': receipt_mode, 'receipt_phrase': receipt_phrase,
                              'delivered': group['submitted'],
                              'receipt_expected': group['submitted'] if receipt_mode else 0,
                              'replied': sum(1 for m in group['members'] if m['replied']) if receipt_mode else 0})
                group['pending_reply'] = group['receipt_expected'] - group['replied'] if receipt_mode else 0
                group['reply_rate'] = (round(100.0 * group['replied'] / group['receipt_expected'], 1)
                                       if group['receipt_expected'] else 0)
        return {'task_id': task['template_id'], 'name': task['name'],
                'creator_phone': task['creator_phone'],
                'kind': 'notice' if is_notice else 'upload',
                'receipt_mode': receipt_mode, 'receipt_phrase': receipt_phrase,
                'periods': list(periods.values())}

    # ── 2026-11-20 后台只读监管面板（按发布人筛选，对齐 StatisticsView）──────

    def grant_count(self, platform=None, owner=None):
        """当前已授权、可发布 H5 任务的领导人数（只读监管空状态用）。"""
        where, args = ['enabled=1'], []
        if platform:
            where.append('platform=?')
            args.append(platform)
        if owner is not None:
            where.append('owner_id=?')
            args.append(owner)
        row = self.s.conn.execute(
            'SELECT COUNT(*) AS c FROM portal_grant WHERE ' + ' AND '.join(where),
            tuple(args)).fetchone()
        return int((row['c'] if row else 0) or 0)

    # ── 2026-11-24 授权成员管理闭环（新增 → 修改 → 取消，均在「授权成员发布任务」）──

    def grant_members(self, platform, owner, query=''):
        """本管理员名下全部已授权成员清单（授权管理抽屉用，支持按姓名/手机号过滤）。

        · 姓名/部门取名册**最新一条**档案（同手机号多角色多行时按 id DESC 取首条）；
        · in_roster = 名册仍有档案行；deliverable = 当前口径可投递（members() 同源）；
          成员被停用/移出后两标记转 False，管理员可据此决定是否取消授权；
        · task_count 口径同 publishers()（含已撤授权的历史任务，监管统计完整保留）。
        """
        rows = [dict(r) for r in self.s.conn.execute(
            'SELECT phone, scope_json, updated_at FROM portal_grant '
            'WHERE owner_id=? AND platform=? AND enabled=1 ORDER BY updated_at DESC, phone',
            (owner, platform)).fetchall()]
        if not rows:
            return {'members': []}
        phones = [str(r['phone']) for r in rows]
        profiles = {}
        for start in range(0, len(phones), 900):
            chunk = phones[start:start + 900]
            ph = ','.join('?' for _ in chunk)
            for m in self.s.conn.execute(
                    f'SELECT m.id, m.phone, m.name, m.dept_path FROM member m '
                    f'JOIN role r ON r.role_id = m.role_id '
                    f'WHERE r.platform=? AND m.owner_id=? AND m.phone IN ({ph}) ORDER BY m.id DESC',
                    (platform, owner, *chunk)).fetchall():
                profiles.setdefault(str(m['phone']), dict(m))  # id DESC → 首条即最新
        deliverable = {m['phone'] for m in self.members(platform, owner)}
        task_counts = {str(r['creator_phone']): int(r['c']) for r in self.s.conn.execute(
            'SELECT creator_phone, COUNT(*) AS c FROM portal_task '
            'WHERE platform=? AND owner_id=? GROUP BY creator_phone', (platform, owner)).fetchall()}
        role_names = {r['role_id']: r['name'] for r in self.s.list_roles(platform=platform, owner_id=owner)}
        q = str(query or '').strip()
        members = []
        for r in rows:
            phone = str(r['phone'])
            profile = profiles.get(phone)
            try:
                scope = json.loads(r['scope_json']) or {}
            except (ValueError, TypeError):
                scope = {}
            dept_ids = [str(d) for d in (scope.get('dept_ids') or [])]
            role_ids = [str(x) for x in (scope.get('role_ids') or [])]
            name = (profile or {}).get('name') or phone
            if q and q not in str(name) and q not in phone:
                continue
            members.append({
                'phone': phone, 'name': name,
                'dept_path': _norm_dept_path((profile or {}).get('dept_path')),
                'scope_dept_ids': dept_ids, 'scope_role_ids': role_ids,
                'scope_summary': '、'.join(d.rsplit('/', 1)[-1] or d for d in dept_ids),
                'role_names': [role_names.get(x, x) for x in role_ids],
                'task_count': task_counts.get(phone, 0),
                'in_roster': profile is not None,
                'deliverable': phone in deliverable,
                'updated_at': r['updated_at'],
            })
        return {'members': members}

    def grant_candidates(self, platform, owner, query='', limit=20):
        """可授权成员搜索：本管理员名下已启用、已激活可投递成员（按姓名/手机号过滤）。

        与候选唯一数据源 = PortalService.members()（enabled=1、未离职、delivery_target.usable=1），
        保证「搜得到即能授权」，与 save_grant 的名册校验同一口径。
        """
        q = str(query or '').strip()
        seen, out = set(), []
        for m in sorted(self.members(platform, owner), key=lambda x: ((x.get('name') or ''), x['phone'])):
            if m['phone'] in seen:
                continue
            if q and q not in str(m.get('name') or '') and q not in m['phone']:
                continue
            seen.add(m['phone'])
            out.append({'phone': m['phone'], 'name': m['name'],
                        'dept_path': _norm_dept_path(m.get('dept_path'))})
            if len(out) >= max(1, min(int(limit or 20), 50)):
                break
        return {'members': out}


    def publishers(self, platform=None, owner=None):
        """聚合「授权发布人（领导）」维度：[{phone, name, dept_path, owner_id, task_count}]。

        姓名/部门取名册当前档案（无档案的领导退回手机号本身）；task_count = 该
        领导已发布任务数（含已撤授权的历史任务——历史统计数据须完整保留可查）。
        """
        where, args = [], []
        if platform:
            where.append('platform=?')
            args.append(platform)
        if owner is not None:
            where.append('owner_id=?')
            args.append(owner)
        sql = ('SELECT creator_phone, owner_id, COUNT(*) AS task_count FROM portal_task '
               + ('WHERE ' + ' AND '.join(where) if where else '')
               + ' GROUP BY creator_phone, owner_id ORDER BY MAX(created_at) DESC')
        rows = [dict(r) for r in self.s.conn.execute(sql, tuple(args)).fetchall()]
        phones = sorted({r['creator_phone'] for r in rows})
        profiles = {}
        if phones:
            for start in range(0, len(phones), 900):
                chunk = phones[start:start + 900]
                ph = ','.join('?' for _ in chunk)
                for m in self.s.conn.execute(
                        f'SELECT phone, name, dept_path FROM member WHERE phone IN ({ph}) '
                        'ORDER BY enabled DESC, id DESC', tuple(chunk)).fetchall():
                    profiles.setdefault(str(m['phone']), dict(m))
        return [{
            'phone': r['creator_phone'],
            'name': str((profiles.get(r['creator_phone']) or {}).get('name') or r['creator_phone']),
            'dept_path': str((profiles.get(r['creator_phone']) or {}).get('dept_path') or ''),
            'owner_id': int(r['owner_id'] or 0),
            'task_count': int(r['task_count'] or 0),
        } for r in rows]

    def panel_task_meta(self, task):
        """监管面板的任务卡片元数据（与 stats-panel/tasks 同构，前端组件可复用）。"""
        from .scheduler import canonical_period_kind, canonical_period_key
        if task['mode'] == 'once' or task.get('is_manual'):
            stats_period, current_key = 'day', 'once'
        else:
            stats_period = canonical_period_kind(task)
            current_key = canonical_period_key(task, self.s.now())
            if stats_period == 'once':
                current_key = 'once'
        periods = {str(r['period_key']) for r in self.s.conn.execute(
            'SELECT DISTINCT period_key FROM task_instance WHERE template_id = ? AND status != ?',
            (task['template_id'], 'cancelled')).fetchall()}
        canon_keys = {canonical_part(pk) for pk in periods}
        from .notice_media import notice_receipt_config
        is_notice = task.get('kind') == 'notice'
        return {
            'task_id': task['template_id'], 'name': task['name'],
            'creator_phone': task['creator_phone'],
            'owner_id': task['owner_id'], 'platform': task.get('platform'),
            'kind': 'notice' if is_notice else 'form',
            'is_notice': is_notice,
            'has_receipt': bool(notice_receipt_config(task)[0]) if is_notice else False,
            'period': task.get('period'), 'mode': task.get('mode'),
            'statistics_period': stats_period, 'current_period_key': current_key,
            'period_keys': sorted(canon_keys),
            'remind_at': task.get('remind_at'), 'anchor_weekday': task.get('anchor_weekday'),
            'anchor_dom': task.get('anchor_dom'),
            'exec_weekdays': task.get('exec_weekdays'), 'exec_dom_days': task.get('exec_dom_days'),
            'has_deadline': bool(task.get('has_deadline')),
            'deadline_type': task.get('deadline_type'), 'deadline_time': task.get('deadline_time'),
            'need_review': False, 'created_at': task.get('created_at'),
        }


def run_portal_tasks(scheduler, now):
    """发送前持久化唯一占位；并发 tick、发布重入与进程重启均不重发。"""
    from .scheduler import (canonical_period_deadline, canonical_period_key,
                            canonical_period_kind, is_template_anchor_day)
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
            # _build_planned_dt 恒带上海 tz；测试可能拿 naive now 进来，比较前补齐。
            if now.tzinfo is None:
                now = now.replace(tzinfo=planned.tzinfo)
            # 2026-11-20 切期对齐：归属周期/截止与管理员同一套 canonical 规则——
            # 周/月容器任务整周期一个键（YYYY-Www / YYYY-MM），同周期多执行日
            # 命中同一实例（唯一键），已完成成员不再重复提醒；截止锁定容器锚点。
            kind = canonical_period_kind(task)
            pk = canonical_period_key(task, now)
            deadline = canonical_period_deadline(task, kind, now)
        else:
            planned = now
            pk, deadline = 'once', task['once_deadline']
        if deadline <= format_dt(now):
            continue
        is_notice = task.get('kind') == 'notice'
        notice_instances = []
        for m in task['recipients']:
            if not p.can_submit({'phone': m['phone']}, task):
                continue
            inst = s.ensure_instance(task['template_id'], m['phone'], m['name'], None, pk, deadline)
            if is_notice:
                inst = _prepare_notice_instance(s, task, inst)
                if inst['status'] in ('pending', 'sent') and now >= planned:
                    notice_instances.append(inst)
                continue
            if inst['status'] != 'pending' or now < planned:
                # 已完成（approved/submitted）成员在同一周期内不再被催（切期对齐方案 A）。
                continue
            # 幂等键（防刷屏红线）：一次性任务 = 实例级（发过永远不重发）；
            # 周期任务 = 实例 + **执行日**——同一个执行日的多次 tick 只发一条，
            # 下一个执行日对仍未完成的成员再催一次（与管理员定时链同语义）。
            idem_seed = f"portal|{inst['id']}" if task['mode'] == 'once' else f"portal|{inst['id']}|{planned.date()}"
            idem = hashlib.sha256(idem_seed.encode()).hexdigest()
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
        if is_notice and notice_instances:
            _deliver_portal_notice(scheduler, task, notice_instances, pk, planned, now)


# ── 2026-11-23 领导端通知任务（与后台通知模板共用同一条投递链）────────────────

def _prepare_notice_instance(state, task, inst):
    """pt_ 通知实例：固化回执模式与短语（pt_ 建实例不走管理员实例建链）。"""
    if inst.get('receipt_phrase'):
        return inst
    from .notice_media import notice_receipt_config

    mode, phrase = notice_receipt_config(task)
    if not phrase:
        return inst
    state.conn.execute(
        "UPDATE task_instance SET receipt_phrase=?, receipt_mode=?, receipt_platform=? "
        "WHERE id=? AND receipt_prompt_at IS NULL",
        (phrase, mode, task['platform'], inst['id']))
    return dict(state.conn.execute("SELECT * FROM task_instance WHERE id=?", (inst['id'],)).fetchone())


def _deliver_portal_notice(scheduler, task, instances, period_key, planned, now):
    """通知投递：正文 → 媒体（图片/文件/视频）→ 收尾（H5 确认链接 / 回执短语）。

    直接复用 scheduler._deliver_notice——幂等占位、回执提示标记、状态置 sent、
    附件不可用告警全部与后台通知模板逐字节一致（所见即所得）。
    """
    stats = {'sent': 0, 'skipped': 0, 'failed': 0, 'pushed_instances': 0}
    return scheduler._deliver_notice(
        task, instances, task.get('period') or 'day', 1, format_dt(planned), now, {},
        stats, once=task['mode'] == 'once')
