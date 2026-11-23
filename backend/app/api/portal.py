import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..portal import PortalService, run_portal_tasks
from ..state import BizError


def create_portal_router(state, scheduler=None, uploads_dir=None):
    router, p = APIRouter(), PortalService(state)

    @router.get('/h5/portal')
    @router.get('/h5/portal/')
    def page():
        return HTMLResponse((Path(__file__).parents[1] / 'h5' / 'portal.html').read_text(encoding='utf-8'),
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @router.get('/h5/portal-create')
    @router.get('/h5/portal-create/')
    def create_page():
        """2026-11-23：领导「新建任务」独立全屏页（上传任务 / 通知提醒双模）。"""
        return HTMLResponse((Path(__file__).parents[1] / 'h5' / 'portal-create.html').read_text(encoding='utf-8'),
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @router.post('/api/h5/portal-asset/upload')
    async def asset_upload(request: Request):
        """领导端通知素材上传：复用后台 notice_asset 存储与校验（限 9/64/100MB）。"""
        import io

        from starlette.datastructures import UploadFile as StarletteUploadFile

        from ..notice_media import LIMITS, store_asset

        try:
            ident = p.authenticate(request.headers.get('Authorization', '').removeprefix('Bearer '))
            grants = p.require_creator(ident)
            owner = min(int(g['owner_id']) for g in grants)
            async with request.form() as form:
                kind = str(form.get('kind') or '').strip()
                file = form.get('file')
                if kind not in LIMITS:
                    raise BizError(400, 'INVALID_UPLOAD', '请选择要上传的图片、文件或视频')
                if not isinstance(file, StarletteUploadFile) or not str(file.filename or '').strip():
                    raise BizError(400, 'VALIDATION_FAILED', '请选择上传文件')
                content = await file.read(LIMITS[kind] + 1)
                if len(content) > LIMITS[kind]:
                    raise BizError(400, 'FILE_SIZE_REJECTED',
                                   f'附件必须非空且不超过 {LIMITS[kind] // (1024 * 1024)} MB')
                asset = await asyncio.to_thread(
                    store_asset, state, owner, kind, file.filename, io.BytesIO(content), len(content))
                return {'ok': True, 'asset': asset}
        except BizError as exc:
            return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)
        except (ValueError, TypeError):
            return JSONResponse({'code': 'INVALID_BODY', 'message': '上传请求格式错误'}, status_code=400)

    @router.get('/api/h5/portal-asset/{asset_id}')
    def asset_preview(asset_id: str, request: Request):
        """通知素材预览/下载（仅发布人本人授权范围可见）。"""
        from ..notice_media import asset_path, get_asset

        try:
            ident = p.authenticate(request.headers.get('Authorization', '').removeprefix('Bearer '))
            owners = {int(g['owner_id']) for g in p.require_creator(ident)}
            asset = get_asset(state, asset_id)
            if not asset or int(asset['owner_id']) not in owners:
                raise BizError(404, 'NOT_FOUND', '附件不存在')
            path = asset_path(state, asset)
            if not path:
                raise BizError(404, 'NOT_FOUND', '附件不可用')
            inline = asset['kind'] in ('image', 'video')
            return FileResponse(path, filename=asset['file_name'], media_type=asset['mime'],
                                content_disposition_type='inline' if inline else 'attachment',
                                headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except BizError as exc:
            return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)

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
            if action == 'dismiss-overdue':
                return p.dismiss_overdue(ident, data.get('task_id'), data.get('period_key'))
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
    from .admin import require_session, _admin_profile, _is_super_admin
    from ..stats_export import build_stats_xlsx, export_filename
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

    # ── 2026-11-20 只读监管面板（按发布人筛选，布局对齐任务数据统计）──────────
    # 权限边界：sub_admin 仅看归属自己授权的领导及任务；super_admin 全局只读
    # （所有平台/管理员下的授权发布任务）；全链路只读，无任何写端点。

    def _monitor_owner(admin):
        """监管视图的数据归属：sub_admin=自己；super_admin=None（全局）。"""
        return None if _is_super_admin(admin) else int(admin['id'])

    def _guard_monitor_task(admin, task_id):
        task = p.template(str(task_id or ''))
        if not task:
            raise BizError(404, 'NOT_FOUND', '任务不存在')
        if not _is_super_admin(admin) and (task['owner_id'] != int(admin['id'])
                                          or task['platform'] not in (admin.get('allowed_platforms') or [])):
            raise BizError(403, 'FORBIDDEN', '无权查看此任务')
        return task

    @router.get('/portal-tasks')
    async def tasks(request: Request, platform: str = 'zdxlz', creator_phone: str = ''):
        """发布人 + 任务双维度清单（一次拉全，前端本地级联筛选）。"""
        sess, err = require_session(request, state)
        if err is not None:
            return err
        admin = _admin_profile(state, sess)
        if platform not in ('zdxlz', 'wecom') or (not _is_super_admin(admin) and platform not in (admin.get('allowed_platforms') or [])):
            return JSONResponse({'message': '无权访问该平台'}, status_code=403)
        owner = _monitor_owner(admin)
        creators = str(creator_phone or '').strip() or None
        tasks_list = [t for t in p.tasks(owner=owner, platform=platform)
                      if creators is None or t['creator_phone'] == creators]
        publishers = [row for row in p.publishers(platform=platform, owner=owner)
                      if creators is None or row['phone'] == creators]
        # 2026-11-23：空状态卡片展示「当前已授权人数」，供分级管理员判断先去名册配置授权。
        grant_count = p.grant_count(platform=platform, owner=owner)
        return {'ok': True,
                'grant_count': grant_count,
                'publishers': [{'id': pub['phone'], **pub} for pub in publishers],
                'tasks': [p.panel_task_meta(t) for t in tasks_list]}

    @router.get('/portal-tasks/{task_id}/overview')
    async def overview(request: Request, task_id: str, period_key: str = '', dept_path: str = ''):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        admin = _admin_profile(state, sess)
        try:
            task = _guard_monitor_task(admin, task_id)
            # owner_id 传任务归属管理员：既过 guard 属主校验，也让部门兜底取正确名册。
            return await asyncio.to_thread(
                state.stats_panel_overview, task_id, period_key, int(task['owner_id']),
                dept_path=dept_path, allow_portal_task=True)
        except BizError as exc:
            return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    @router.get('/portal-tasks/{task_id}/instances')
    async def instances(request: Request, task_id: str, period_key: str = '', dept_path: str = '',
                        status_group: str = 'all', q: str = '', page: int = 1, size: int = 50):
        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        admin = _admin_profile(state, sess)
        try:
            task = _guard_monitor_task(admin, task_id)
            return await asyncio.to_thread(
                lambda: state.stats_panel_instances(
                    task_id, period_key, int(task['owner_id']), dept_path=dept_path,
                    status_group=status_group, q=q, page=page, size=size,
                    allow_portal_task=True))
        except BizError as exc:
            return JSONResponse({'message': exc.message}, status_code=exc.status_code)

    @router.get('/portal-tasks/{task_id}/export')
    async def export(request: Request, task_id: str, period_key: str = '', dept_path: str = ''):
        """按当前任务、归属周期与部门范围导出 .xlsx（stats_export 同一出口）。"""
        from urllib.parse import quote

        sess, err = require_session(request, state, check_csrf=False)
        if err is not None:
            return err
        assert sess is not None
        admin = _admin_profile(state, sess)
        try:
            task = _guard_monitor_task(admin, task_id)
            bundle = await asyncio.to_thread(
                state.stats_panel_export_rows, task_id, period_key, int(task['owner_id']),
                dept_path=dept_path, allow_portal_task=True)
        except BizError as exc:
            return JSONResponse({'message': exc.message}, status_code=exc.status_code)
        import pathlib
        cfg = getattr(request.app.state, 'config', None)
        uploads_dir = (cfg.uploads_dir if cfg else state.data_dir / 'uploads') or pathlib.Path(state.data_dir) / 'uploads'
        payload = await asyncio.to_thread(build_stats_xlsx, bundle, pathlib.Path(uploads_dir))
        fname = export_filename(dept_path, bundle['template']['name'], state.now())
        return Response(
            content=payload,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename="portal-stats.xlsx"; filename*=UTF-8\'\'{quote(fname, safe="")}',
                'Cache-Control': 'no-store',
            },
        )

    @router.get('/portal-tasks/{task_id}/attachments/{ref}')
    async def attachment(task_id: str, ref: str, request: Request):
        sess, err = require_session(request, state)
        if err is not None:
            return err
        admin = _admin_profile(state, sess)
        try:
            task = _guard_monitor_task(admin, task_id)
            return attachment_response(state, task, ref, None)
        except BizError as exc:
            return JSONResponse({'message': exc.message}, status_code=exc.status_code)
    return router
