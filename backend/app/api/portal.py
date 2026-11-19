import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..portal import PortalService, run_portal_tasks
from ..state import BizError


def create_portal_router(state, scheduler=None, uploads_dir=None):
    router, p = APIRouter(), PortalService(state)

    @router.get('/h5/portal')
    def page():
        return HTMLResponse((Path(__file__).parents[1] / 'h5' / 'portal.html').read_text(encoding='utf-8'),
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @router.post('/api/h5/portal/{action}')
    async def api(action: str, request: Request, background: BackgroundTasks):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise BizError(400, 'INVALID_BODY', '请求格式错误')
            ident = p.authenticate(request.headers.get('Authorization', '').removeprefix('Bearer '))
            if action == 'info':
                return {'ok': True, 'can_create': bool(p.grants(ident)), 'todos': p.todos(ident),
                        'admins': p.admin_contacts(ident),
                        'adhoc': state.list_adhoc_menu_items(ident['phone'], platform=ident['platform']),
                        'adhoc_history': p.submission_history(ident, adhoc_only=True, limit=10)}
            if action == 'open':
                return p.task_link(ident, int(data.get('instance_id', 0)))
            if action == 'notice-open':
                return p.notice_receipt_link(ident, int(data.get('instance_id', 0)))
            if action == 'adhoc':
                task_id = str(data.get('template_id') or '')
                raw = state.issue_adhoc_report(task_id, ident['phone'], ident['platform'])
                return {'ok': True, 'url': '/h5/task#t=' + raw}
            if action == 'history':
                offset = int(data.get('offset', 0))
                if not 0 <= offset <= 2_147_483_647:
                    raise ValueError
                rows = p.submission_history(ident, offset=offset)
                return {'ok': True, 'items': rows[:30], 'has_more': len(rows) > 30}
            if action == 'history-open':
                return p.submission_history_link(ident, int(data.get('instance_id', 0)), edit=data.get('edit') is True)
            p.require_creator(ident)
            if action == 'recipients':
                return {'ok': True, **await asyncio.to_thread(p.recipients, ident)}
            if action == 'create':
                result = await asyncio.to_thread(p.create_task, ident, data)
                if scheduler:
                    background.add_task(run_portal_tasks, scheduler, state.now())
                return result
            if action == 'tasks':
                return {'ok': True, 'items': [p.statistics(t) for t in p.tasks(ident)]}
            if action == 'attachment':
                task = next((t for t in p.tasks(ident) if t['template_id'] == data.get('task_id')), None)
                if not task:
                    raise BizError(403, 'FORBIDDEN', '无权查看此任务')
                return attachment_response(state, task, str(data.get('ref') or ''), uploads_dir)
            raise BizError(404, 'NOT_FOUND', '接口不存在')
        except BizError as exc:
            return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)
        except (ValueError, TypeError):
            return JSONResponse({'code': 'INVALID_BODY', 'message': '请求格式错误'}, status_code=400)
    return router


def attachment_response(state, task, ref, uploads_dir):
    import json
    valid = set()
    for row in state.conn.execute('SELECT id FROM task_instance WHERE template_id=?', (task['template_id'],)).fetchall():
        sub = state.get_latest_valid_submission(row['id'])
        if sub:
            valid.update(json.loads(sub.get('refs_json') or '[]'))
            if sub.get('file_ref'):
                valid.add(sub['file_ref'])
    upload = state.get_upload_ref(ref) if ref in valid else None
    if not upload:
        raise BizError(404, 'NOT_FOUND', '附件不存在')
    base = Path(uploads_dir or state.data_dir / 'uploads').resolve()
    path = (base / upload['rel_path']).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise BizError(404, 'NOT_FOUND', '附件不存在')
    return FileResponse(path, media_type=upload['mime'], filename=upload['file_name'], content_disposition_type='inline',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def create_portal_admin_router(state):
    from .admin import require_session, _admin_profile, _owner_filter, _is_super_admin
    router, p = APIRouter(prefix='/api/admin'), PortalService(state)

    @router.api_route('/portal-grants/{phone}', methods=['GET', 'PUT'])
    async def grant(phone: str, request: Request, platform: str = 'zdxlz', directory: bool = False, parent_id: str = ''):
        sess, err = require_session(request, state, check_csrf=request.method == 'PUT')
        if err is not None:
            return err
        admin = _admin_profile(state, sess)
        try:
            if platform not in ('zdxlz', 'wecom') or (not _is_super_admin(admin) and platform not in (admin.get('allowed_platforms') or [])):
                raise BizError(403, 'FORBIDDEN', '无权访问该平台')
            if _is_super_admin(admin):
                raise BizError(403, 'SUPER_ADMIN_READONLY', '请由成员所属分级管理员配置授权')
            if request.method == 'GET':
                if directory:
                    return {'ok': True, **await asyncio.to_thread(p.grant_options, platform, admin['id'], phone, parent_id)}
                return {'ok': True, **p.grant_info(platform, admin['id'], phone)}
            data = await request.json()
            if not isinstance(data, dict):
                raise BizError(400, 'INVALID_BODY', '请求格式错误')
            return await asyncio.to_thread(p.save_grant, platform, admin['id'], phone, data, sess['username'])
        except BizError as exc:
            return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)
        except (ValueError, TypeError):
            return JSONResponse({'message': '请求格式错误'}, status_code=400)

    @router.get('/portal-tasks')
    async def tasks(request: Request, platform: str = 'zdxlz'):
        sess, err = require_session(request, state)
        if err is not None:
            return err
        admin = _admin_profile(state, sess)
        if _is_super_admin(admin):
            return JSONResponse({'message': '此面板仅限分级管理员使用'}, status_code=403)
        if platform not in (admin.get('allowed_platforms') or []):
            return JSONResponse({'message': '无权访问该平台'}, status_code=403)
        return {'ok': True, 'items': [p.statistics(t) for t in p.tasks(owner=_owner_filter(admin, None), platform=platform)]}

    @router.get('/portal-tasks/{task_id}/attachments/{ref}')
    async def attachment(task_id: str, ref: str, request: Request):
        sess, err = require_session(request, state)
        if err is not None:
            return err
        admin = _admin_profile(state, sess)
        if _is_super_admin(admin):
            return JSONResponse({'message': '此面板仅限分级管理员使用'}, status_code=403)
        task = p.template(task_id)
        if not task or task['owner_id'] != admin['id'] or task['platform'] not in (admin.get('allowed_platforms') or []):
            return JSONResponse({'message': '无权查看'}, status_code=403)
        try:
            return attachment_response(state, task, ref, None)
        except BizError as exc:
            return JSONResponse({'message': exc.message}, status_code=exc.status_code)
    return router
