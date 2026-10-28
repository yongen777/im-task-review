# -*- coding: utf-8 -*-
"""把 AI Studio 前端（Vite 编译产物）的假数据层替换为真 API 适配层。

原则：UI 代码一个字符不动，只做两处修改：
1. 在 api 客户端 I 的定义前注入 __RD（Real-Data adapter）；
2. 把 I 的四个方法从指向假后端 xt 改为指向 __RD.call。

产物：assets/index-realdata.js + index.html 引用更新（原 JS 保留可随时回滚）。
"""
import re
from pathlib import Path

SRC = Path(r"D:\im-task\app\admin_static\assets\index-Dr0uwQ4k.js")
OUT = Path(r"D:\im-task\app\admin_static\assets\index-realdata.js")
HTML = Path(r"D:\im-task\app\admin_static\index.html")

ADAPTER = r"""
/* ===== Real-Data Adapter (2026-10-27 注入)：把内置演示数据层替换为真后端 API =====
   UI 零改动；所有读写直达服务器数据库（/api/admin/*）。 */
const __RD={
  _dk:{},
  get csrf(){return sessionStorage.getItem("__imt_csrf")||""},
  set csrf(v){if(v)sessionStorage.setItem("__imt_csrf",v)},

  mapPath(method,path,params){
    const q=params.toString();
    if(path==="/api/admin/health")return{url:"/healthz"};
    if(path==="/api/admin/logs/reminders")return{url:"/api/admin/reminder-logs"+(q?"?"+q:"")};
    if(path==="/api/admin/logs/audit")return{url:"/api/admin/audit-logs"+(q?"?"+q:"")};
    if(path==="/api/admin/instances/stats")return{url:"/api/admin/stats"+(q?"?"+q:"")};
    if(path==="/api/admin/username"||path==="/api/admin/callback-url")throw new Error("该功能暂未开放");
    if(/\/trigger$/.test(path))throw new Error("在线触发暂未开放：请等待时点自动触发（或用「数据填报」后到点验收）");
    return{url:path+(q?"?"+q:"")};
  },

  async fetch(method,url,body){
    const opt={method,credentials:"same-origin",headers:{}};
    if(body!==undefined){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body)}
    if(method!=="GET")opt.headers["X-CSRF-Token"]=this.csrf;
    const res=await fetch(url,opt);
    let data=null;try{data=await res.json()}catch(e){}
    if(res.status===401)throw new Error("登录已过期，请重新登录");
    if(!res.ok||(data&&data.ok===false)){
      throw new Error((data&&(data.message||(data.error&&data.error.message)))||("HTTP "+res.status));
    }
    return data;
  },

  roleToMock(r){return{role_id:r.role_id,role_name:r.name,description:r.note||"",created_at:r.created_at||""}},
  memberToMock(m){return{role_id:m.role_id,phone:m.phone,name:m.name,dept:m.dept||"",status:m.enabled!==false?"active":"disabled",activation:m.activated?"activated":"pending",created_at:m.created_at||""}},
  tplToMock(t){
    this._dk[t.template_id]=t.data_keys||[];
    const spec={notice_items:[]};
    if((t.kind||"")==="notice"){
      spec.notice_items.push({id:"n_text",format:"text",text_content:t.text_tpl||""});
      if(t.attach_image)spec.notice_items.push({id:"n_file",format:"file",file_name:"数据看板.png",file_caption:"当日数据看板（到点自动生成并推送）"});
    }else{spec.schema_json=t.schema_json}
    return{id:t.template_id,template_id:t.template_id,name:t.name,kind:t.kind==="form"?"upload":(t.kind||"notice"),
      period:t.period||"day",role_id:(t.role_ids||[])[0]||"",remind_at:(t.notice_times||[])[0]||"",deadline:t.deadline||"",
      text_tpl:t.text_tpl||"",spec,enabled:t.enabled!==false,attach_image:!!t.attach_image,
      data_keys:t.data_keys||[],created_at:t.created_at,updated_at:t.updated_at};
  },
  tplFromMock(t){
    t=t||{};
    let text=t.text_tpl||"",attach=false;
    if(t.spec&&Array.isArray(t.spec.notice_items)&&t.spec.notice_items.length){
      const texts=t.spec.notice_items.filter(x=>x.format==="text").map(x=>x.text_content).filter(Boolean);
      if(texts.length)text=texts.join("\n");
      attach=t.spec.notice_items.some(x=>x.format==="file");
      const caps=t.spec.notice_items.filter(x=>x.format==="file").map(x=>x.file_caption).filter(Boolean);
      if(caps.length)text=text?text+"\n"+caps.join("\n"):caps.join("\n");
    }
    const kind=(t.kind==="upload"||t.kind==="form")?"form":(t.kind||"notice");
    const isNotice=kind==="notice";
    const times=isNotice?[t.remind_at].filter(x=>/^\d{1,2}:\d{2}$/.test(x||"")):[];
    return{name:t.name,kind,role_ids:t.role_id?[t.role_id]:(t.role_ids||[]),text_tpl:text,
      notice_times:times,deadline:isNotice?"":(t.deadline||""),period:t.period||"day",
      attach_image:attach,data_keys:t.data_keys||[],enabled:t.enabled!==false};
  },
  remToMock(m){return{id:m.id,instance_id:m.instance_id,slot:(m.kind||"")==="notice_img"?"看板":(m.kind||""),retry_no:m.retry||0,
    planned_at:m.planned_at,attempted_at:m.attempted_at,sent_at:m.sent_at,result:m.result,error:m.error,
    template_name:m.template_name||"",phone:m.phone,member_name:""}},
  auditToMock(a){return{id:a.id,at:a.created_at,actor:a.username,action:a.action,
    target:(a.target_type||"")+((a.target_type&&a.target_id)?":":"")+(a.target_id||""),detail:a.detail||""}},
  instToMock(i){return Object.assign({},i,{member_name_snap:i.name_snap||i.member_name_snap||""})},
  statsToMock(d){const s=(d&&d.stats)||d||{};
    const by={pending:s.pending||0,submitted:s.submitted||0,approved:s.approved||0,rejected:s.rejected||0,overdue:s.overdue||0,expired:s.expired||0};
    const total=s.total||0;
    const rate=(a,b)=>b>0?Number((a/b*100).toFixed(1)):0;
    return{ok:true,total:total,by_status:by,
      completion_rate:rate(by.submitted+by.approved,total),
      on_time_rate:rate(by.approved+by.submitted,total),
      overdue_rate:rate(by.overdue+by.expired,total)}},

  async unactivated(method,params,body){
    const roles=await this.fetch("GET","/api/admin/roles");
    const out=[];
    for(const r of(roles.items||[])){
      const m=await this.fetch("GET","/api/admin/roles/"+encodeURIComponent(r.role_id)+"/members?size=100");
      for(const x of(m.items||[]))if(!x.activated)out.push({phone:x.phone,name:x.name,dept:x.dept||"",role_id:x.role_id,role_name:r.name});
    }
    if(method==="GET")return{ok:true,items:out};
    throw new Error("未激活成员请在「角色与成员」中操作（添加成员后，让成员给机器人发手机号即完成激活）");
  },

  async call(method,path,body){
    let rawPath=path,query="";
    const qi=path.indexOf("?");
    if(qi>=0){rawPath=path.slice(0,qi);query=path.slice(qi+1)}
    const params=new URLSearchParams(query||"");

    if(method==="POST"&&rawPath==="/api/admin/login"){
      const d=await this.fetch("POST","/api/admin/login",body);
      this.csrf=d.csrf_token||"";
      return{ok:true,username:d.username,must_change_password:!!d.must_change,csrf_token:d.csrf_token||""};
    }
    if(rawPath==="/api/admin/unactivated-members")return await this.unactivated(method,params,body);

    const mapped=this.mapPath(method,rawPath,params);

    let reqBody=body;
    if(method!=="GET"){
      if(rawPath==="/api/admin/templates"||rawPath.startsWith("/api/admin/templates/")){
        reqBody=this.tplFromMock(body);
        const mId=rawPath.match(/templates\/(.+)$/);
        const cached=mId?this._dk[mId[1]]:null;
        reqBody.data_keys=(cached&&cached.length)?cached:(reqBody.attach_image?[{key:"kpi_1",label:"当日关键指标",unit:""}]:[]);
      }
      else if(rawPath==="/api/admin/roles"||rawPath.startsWith("/api/admin/roles/")){
        reqBody=Object.assign({},body||{});
        if(reqBody.role_name!==undefined){reqBody.name=reqBody.role_name;delete reqBody.role_name}
        if(reqBody.description!==undefined){reqBody.note=reqBody.description;delete reqBody.description}
        if(reqBody.status!==undefined){reqBody.enabled=reqBody.status==="active";delete reqBody.status}
        if(reqBody.activation!==undefined){delete reqBody.activation}
      }
    }

    const d=await this.fetch(method,mapped.url,reqBody);

    if(rawPath==="/api/admin/me")return{ok:true,username:d.username,must_change_password:!!d.must_change,csrf_token:this.csrf||"imtask-csrf-token"};
    if(rawPath==="/api/admin/health")return{ok:true,service:"im-task-admin",
      channel:d.channel==="ok"?"ready":(d.channel||""),event_crypto:d.crypto==="ok"?"ready":(d.crypto||""),
      public_base_url:d.public_base_url||"",is_loopback_public_url:!!d.is_loopback_public_url};
    if(rawPath==="/api/admin/roles"&&method==="GET")return{ok:true,items:(d.items||[]).map(x=>this.roleToMock(x))};
    if(rawPath.startsWith("/api/admin/roles/")&&rawPath.endsWith("/members")&&method==="GET")return{ok:true,items:(d.items||[]).map(x=>this.memberToMock(x))};
    if(rawPath==="/api/admin/templates"&&method==="GET")return{ok:true,items:(d.items||[]).map(x=>this.tplToMock(x))};
    if(rawPath==="/api/admin/logs/reminders")return{ok:true,items:(d.items||[]).map(x=>this.remToMock(x))};
    if(rawPath==="/api/admin/logs/audit")return{ok:true,items:(d.items||[]).map(x=>this.auditToMock(x))};
    if(rawPath==="/api/admin/instances/stats")return this.statsToMock(d);
    if(rawPath==="/api/admin/instances"&&method==="GET")return{ok:true,total:d.total||0,page:1,size:20,items:(d.items||[]).map(x=>this.instToMock(x))};
    if(rawPath==="/api/admin/instances"&&method==="POST")return d;
    if(/^\/api\/admin\/instances\/\d+$/.test(rawPath)&&method==="GET"){
      return{ok:true,instance:d.instance?this.instToMock(d.instance):this.instToMock(d),submission:d.submission||null};
    }
    return d;
  }
};
"""


def main() -> int:
    js = SRC.read_text(encoding="utf-8")
    print(f"原产物: {len(js)} 字符")

    # 1) 精确定位 I 的定义（取上下文切片做无歧义替换）
    anchor = "I={get:e=>xt(`GET`,e),post:(e,t)=>xt(`POST`,e,t),put:(e,t)=>xt(`PUT`,e,t),delete:e=>xt(`DELETE`,e)}"
    i = js.find(anchor)
    if i < 0:
        raise SystemExit("✗ 未找到 api 客户端 I 的定义锚点（产物结构可能变化）")
    print(f"I 定义锚点 @ {i}")

    replacement = "I={get:e=>__RD.call(`GET`,e),post:(e,t)=>__RD.call(`POST`,e,t),put:(e,t)=>__RD.call(`PUT`,e,t),delete:e=>__RD.call(`DELETE`,e)}"
    # 适配层注入到文件顶层（锚点位于逗号表达式序列中，不能就地插入语句）
    new_js = ADAPTER + "\n" + js[:i] + replacement + js[i + len(anchor):]

    # 2) 语法自检（node --check）
    OUT.write_text(new_js, encoding="utf-8")
    print(f"新产物写入: {OUT}（{len(new_js)} 字符，原文件保留）")

    import subprocess
    r = subprocess.run(["node", "--check", str(OUT)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("✗ node 语法检查失败:\n" + (r.stderr or "")[:800])
    print("✓ node --check 语法通过")

    # 3) index.html 引用切换（保留原引用为注释便于回滚）
    html = HTML.read_text(encoding="utf-8")
    if "index-realdata.js" not in html:
        html = html.replace(
            'src="/assets/index-Dr0uwQ4k.js"',
            'src="/assets/index-realdata.js" /* 回滚: src="/assets/index-Dr0uwQ4k.js" */',
        )
        HTML.write_text(html, encoding="utf-8")
        print("✓ index.html 已切换到 index-realdata.js（原 JS 保留在目录中，随时回滚）")
    else:
        print("· index.html 已是 realdata 引用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
