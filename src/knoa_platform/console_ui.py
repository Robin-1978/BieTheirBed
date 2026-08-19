"""Self-contained management consoles shipped with Hub and Node hosts."""

from __future__ import annotations

import html


_STYLE = """
:root{color-scheme:light;--bg:#f4f0e8;--panel:#fffdf8;--ink:#23211d;--muted:#746f66;
--line:#ddd6c9;--brand:#476b5c;--brand2:#dfe9e3;--danger:#a3483d}*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(880px,calc(100% - 32px));margin:36px auto 80px}.hero{display:flex;gap:16px;align-items:center;margin-bottom:22px}
.mark{width:54px;height:54px;border-radius:17px;background:var(--brand);color:white;display:grid;place-items:center;font-size:24px;font-weight:800}
h1,h2,p{margin:0}h1{font-size:27px}h2{font-size:18px;margin-bottom:12px}.lead,.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 8px 24px #4d45340d}
label{display:block;color:var(--muted);font-size:13px;margin:12px 0 5px}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:11px;background:white;color:var(--ink);padding:11px 12px;font:inherit}
textarea{min-height:170px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;resize:vertical}
button{border:0;border-radius:11px;background:var(--brand);color:white;padding:11px 15px;font:600 14px inherit;cursor:pointer;margin-top:14px}button.secondary{background:var(--brand2);color:var(--brand)}button:disabled{opacity:.45;cursor:not-allowed}
.status{margin-top:14px;padding:11px 12px;border-radius:11px;background:var(--brand2);color:var(--brand);white-space:pre-wrap}.error{background:#f7e3df;color:var(--danger)}
.hidden{display:none!important}.nodes{display:grid;gap:9px;margin-top:12px}.node{display:flex;justify-content:space-between;gap:12px;padding:11px;border:1px solid var(--line);border-radius:12px}.online{color:var(--brand);font-weight:700}.offline{color:var(--muted)}
.steps{padding-left:20px;color:var(--muted)}code{background:#ece6db;padding:2px 5px;border-radius:5px}img.qr{display:block;width:min(320px,100%);margin:16px auto;border:12px solid white;border-radius:14px}
"""


def _lifecycle_card(role: str) -> str:
    return f"""<section class="card" style="margin-top:16px"><h2>本机产品管理</h2><p class="muted">管理这台电脑上的 Hub / Node 服务和签名产品更新。停用角色保留数据，Windows 与 Linux 使用同一流程。</p><div id="lifecycleStatus" class="status">正在读取…</div><button id="lifecycleRefresh" class="secondary">刷新</button><button data-life="restart" data-role="{role}">重启当前服务</button><button data-life="activate" data-role="hub" class="secondary">启用 Hub</button><button data-life="deactivate" data-role="hub" class="secondary">停用 Hub</button><button data-life="activate" data-role="node" class="secondary">启用 Node</button><button data-life="deactivate" data-role="node" class="secondary">停用 Node</button><button data-life="rollback" class="secondary">回退上一版本</button><label>签名 Knoa 产品 Bundle（.zip）</label><input id="bundleFile" type="file" accept=".zip"><button id="updateButton">安装更新</button><div id="lifecycleAction" class="status hidden"></div></section>"""


def _node_configuration_card() -> str:
    return """<section class="card" style="margin-top:16px"><h2>LLM、Agent 与能力配置</h2><p class="muted">配置属于当前 Node。发布前自动执行 schema 校验和 preflight；成功后对新会话、Task 和 Invocation 热生效。</p><div class="grid"><div><label>Provider</label><select id="providerSelect"></select><label>Driver</label><select id="providerDriver"><option>llamacpp</option><option>openai</option><option>openai_compatible</option><option>anthropic</option><option>workspace_remote</option></select><label>Server URL / API Base</label><input id="providerEndpoint"><label>API Key 引用</label><input id="providerKeyRef" placeholder="provider-openai"><button id="applyProvider" class="secondary">应用 Provider 修改</button></div><div><label>Model</label><select id="modelSelect"></select><label>Provider</label><select id="modelProvider"></select><label>模型名称</label><input id="modelName"><label>Agent</label><select id="agentSelect"></select><label>Agent 使用模型</label><select id="agentModel"></select><button id="applyModelAgent" class="secondary">应用 Model / Agent 修改</button></div></div><label>写入 API Key（不会回显）</label><input id="secretValue" type="password" autocomplete="new-password"><button id="saveSecret">保存当前 Provider API Key</button><details style="margin-top:16px"><summary>高级完整配置</summary><textarea id="configDocument" style="min-height:360px"></textarea></details><button id="reloadConfig" class="secondary">重新加载</button><button id="publishConfig">校验并热发布</button><div id="configStatus" class="status hidden"></div></section>"""


def hub_console_html(csrf_token: str, public_url: str) -> str:
    csrf = html.escape(csrf_token, quote=True)
    public_hub = html.escape(public_url, quote=True)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knoa Hub Console</title><style>{_STYLE}</style></head><body><main>
<div class="hero"><div class="mark">K</div><div><h1>Knoa Hub Console</h1><p class="lead">管理帐号、Workspace、Node Enrollment 与本机 Hub 生命周期</p></div></div>
<div class="grid"><section class="card" id="loginCard"><h2>登录 Hub</h2><label>登录帐号</label><input id="login" autocomplete="username"><label>密码</label><input id="password" type="password" autocomplete="current-password"><button id="loginButton">登录</button><div id="loginStatus" class="status hidden"></div></section>
<section class="card hidden" id="workspaceCard"><h2>当前 Workspace</h2><label>Workspace</label><select id="workspace"></select><button id="selectButton">打开 Workspace</button><button id="logoutButton" class="secondary">退出登录</button><div id="workspaceStatus" class="status hidden"></div></section></div>
<section class="card hidden" id="nodeCard" style="margin-top:16px"><h2>添加 Node</h2><ol class="steps"><li>在这里生成一次性 Enrollment Code。</li><li>在目标电脑打开本地 Node Console：<code>http://127.0.0.1:9531/console</code>。</li><li>粘贴 Code 并点击“加入 Workspace”。</li></ol><button id="grantButton">生成 Enrollment Code</button><button id="copyButton" class="secondary hidden">复制 Code</button><textarea id="grant" class="hidden" readonly></textarea><div id="grantStatus" class="status hidden"></div><div class="nodes" id="nodes"></div></section>
{_lifecycle_card("hub")}
</main><script>
let token="", selected=null, timer=null;const csrf="{csrf}",publicHub="{public_hub}";
const el=id=>document.getElementById(id); const show=(id,on=true)=>el(id).classList.toggle("hidden",!on);
function status(id,text,bad=false){{const box=el(id);box.textContent=text;box.classList.toggle("error",bad);show(id,true)}}
async function api(path,options={{}}){{const headers={{"Content-Type":"application/json",...(options.headers||{{}})}};if(token)headers.Authorization=`Bearer ${{token}}`;const response=await fetch(path,{{...options,headers}});const body=await response.json().catch(()=>({{}}));if(!response.ok)throw new Error(body.error||`HTTP ${{response.status}}`);return body}}
el("loginButton").onclick=async()=>{{try{{status("loginStatus","正在登录…");const body=await api("/v1/hosted/sessions",{{method:"POST",body:JSON.stringify({{login_identity:el("login").value.trim(),password:el("password").value}})}});token=body.access_token;el("password").value="";el("workspace").innerHTML=body.workspaces.map(w=>`<option value="${{w.workspace_id}}">${{w.display_name}} · ${{w.kind}}</option>`).join("");el("workspace").dataset.items=JSON.stringify(body.workspaces);show("loginCard",false);show("workspaceCard");status("workspaceStatus",`已登录：${{body.display_name}}`);}}catch(e){{status("loginStatus",`登录失败：${{e.message}}`,true)}}}};
el("selectButton").onclick=async()=>{{const items=JSON.parse(el("workspace").dataset.items||"[]");selected=items.find(w=>w.workspace_id===el("workspace").value);if(!selected)return;show("nodeCard");status("workspaceStatus",`已选择：${{selected.display_name}}`);await refreshNodes();clearInterval(timer);timer=setInterval(refreshNodes,5000)}};
el("logoutButton").onclick=async()=>{{try{{await api("/v1/hosted/session",{{method:"DELETE"}})}}catch(_){{}}token="";selected=null;clearInterval(timer);show("workspaceCard",false);show("nodeCard",false);show("loginCard")}};
el("grantButton").onclick=async()=>{{if(!selected)return;try{{status("grantStatus","正在生成…");const base=selected.workspace_path;const [hub,grant]=await Promise.all([api(`${{base}}/v1/hub`),api(`${{base}}/v1/node-enrollment-grants`,{{method:"POST",body:JSON.stringify({{ttl_seconds:600}})}})]);const payload={{version:"knoa-node-enrollment-v1",hub_url:publicHub+base,hub_id:hub.hub_id,hub_signing_public_key:hub.signing_public_key,grant_id:grant.grant_id,grant_secret:grant.secret,challenge:grant.challenge,expires_at:grant.expires_at}};el("grant").value=JSON.stringify(payload);show("grant");show("copyButton");status("grantStatus","Code 10 分钟内有效，只能使用一次。")}}catch(e){{status("grantStatus",`生成失败：${{e.message}}`,true)}}}};
el("copyButton").onclick=async()=>{{await navigator.clipboard.writeText(el("grant").value);status("grantStatus","已复制。现在到目标电脑的 Node Console 粘贴。")}};
async function refreshNodes(){{if(!selected)return;try{{const body=await api(`${{selected.workspace_path}}/v1/nodes`);el("nodes").innerHTML=body.nodes.length?body.nodes.map(n=>`<div class="node"><span><strong>${{n.display_name}}</strong><br><span class="muted">${{n.platform}} · ${{n.node_id}}</span></span><span class="${{n.online?'online':'offline'}}">${{n.online?'在线':'离线'}}</span></div>`).join(""):"<p class='muted'>当前 Workspace 还没有 Node。</p>"}}catch(e){{status("grantStatus",`Node 状态加载失败：${{e.message}}`,true)}}}}
async function life(path,options={{}}){{const response=await fetch(path,{{...options,headers:{{"X-Knoa-Console":csrf,...(options.headers||{{}})}}}});const body=await response.json().catch(()=>({{}}));if(!response.ok)throw new Error(body.error||`HTTP ${{response.status}}`);return body}}
function renderLife(body){{el("lifecycleStatus").textContent=`平台：${{body.platform}} / ${{body.architecture}}\n版本：${{body.current_release||'未安装'}}\nHub：${{body.services.hub.active?'运行中':'已停止'}}\nNode：${{body.services.node.active?'运行中':'已停止'}}`}}
async function refreshLife(){{try{{renderLife(await life("/v1/console/lifecycle"))}}catch(e){{status("lifecycleStatus",`生命周期服务不可用：${{e.message}}`,true)}}}}
async function lifeAction(action,role=null,bundle_name=null){{try{{status("lifecycleAction","正在执行，请稍候…");const body=await life("/v1/console/lifecycle/actions",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{action,role,bundle_name}})}});renderLife(body);status("lifecycleAction","操作完成。")}}catch(e){{status("lifecycleAction",`操作已提交或失败：${{e.message}}`,true);setTimeout(refreshLife,4000)}}}}
el("lifecycleRefresh").onclick=refreshLife;document.querySelectorAll("[data-life]").forEach(button=>button.onclick=()=>lifeAction(button.dataset.life,button.dataset.role||null));
el("updateButton").onclick=async()=>{{const file=el("bundleFile").files[0];if(!file)return status("lifecycleAction","请选择 .zip Bundle。",true);try{{status("lifecycleAction","正在上传签名 Bundle…");const uploaded=await life(`/v1/console/lifecycle/bundles/${{encodeURIComponent(file.name)}}`,{{method:"PUT",headers:{{"Content-Type":"application/zip"}},body:file}});await lifeAction("update",null,uploaded.bundle_name)}}catch(e){{status("lifecycleAction",`更新失败：${{e.message}}`,true)}}}};
refreshLife();
</script></body></html>"""


def node_console_html(csrf_token: str) -> str:
    token = html.escape(csrf_token, quote=True)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knoa Node Console</title><style>{_STYLE}</style></head><body><main>
<div class="hero"><div class="mark">N</div><div><h1>Knoa Node Console</h1><p class="lead">仅本机可访问，用于连接 Hub、查看 Relay 和配对 App</p></div></div>
<div class="grid"><section class="card"><h2>连接 Workspace Hub</h2><label>Hub Console 生成的 Enrollment Code</label><textarea id="payload" placeholder='{{"version":"knoa-node-enrollment-v1", ...}}'></textarea><label>Node 显示名称</label><input id="displayName" value="Windows Desktop"><button id="enrollButton">加入 Workspace</button><div id="enrollStatus" class="status hidden"></div></section>
<section class="card"><h2>Node 状态</h2><div id="status" class="status">正在读取…</div><button id="refreshButton" class="secondary">刷新状态</button><button id="pairButton">生成 App 配对二维码</button><img id="qr" class="qr hidden" alt="App pairing QR"><div id="pairStatus" class="status hidden"></div></section></div>
{_node_configuration_card()}
{_lifecycle_card("node")}
</main><script>
const csrf="{token}";const el=id=>document.getElementById(id);const show=(id,on=true)=>el(id).classList.toggle("hidden",!on);function note(id,text,bad=false){{const box=el(id);box.textContent=text;box.classList.toggle("error",bad);show(id)}}
async function localApi(path,options={{}}){{const response=await fetch(path,{{...options,headers:{{"Content-Type":"application/json","X-Knoa-Console":csrf,...(options.headers||{{}})}}}});const type=response.headers.get("content-type")||"";if(!response.ok){{const body=type.includes("json")?await response.json():{{}};throw new Error(body.error||`HTTP ${{response.status}}`)}}return type.includes("json")?response.json():response.blob()}}
async function refresh(){{try{{const body=await localApi("/v1/console/status");const hub=body.hub;el("status").textContent=hub.enrolled?`已连接\nHub: ${{hub.hub.hub_url}}\nRelay: ${{hub.relay_connected?'在线':'连接中'}}\nNode: ${{body.node.display_name||body.node.node_id}}`:"尚未加入 Workspace";el("status").classList.remove("error")}}catch(e){{note("status",`状态读取失败：${{e.message}}`,true)}}}}
el("refreshButton").onclick=refresh;
el("enrollButton").onclick=async()=>{{try{{let payload=JSON.parse(el("payload").value.trim());if(payload.version!=="knoa-node-enrollment-v1")throw new Error("Enrollment Code 类型不正确");delete payload.version;delete payload.expires_at;payload.display_name=el("displayName").value.trim()||"Knoa Node";note("enrollStatus","正在加入 Workspace…");await localApi("/v1/console/hub/enroll",{{method:"POST",body:JSON.stringify(payload)}});el("payload").value="";note("enrollStatus","Enrollment 成功，Relay 正在连接。返回 Hub Console 即可看到 Node。");await refresh()}}catch(e){{note("enrollStatus",`Enrollment 失败：${{e.message}}`,true)}}}};
el("pairButton").onclick=async()=>{{try{{note("pairStatus","正在生成二维码…");const blob=await localApi("/v1/console/pairing",{{method:"POST",body:"{{}}"}});if(el("qr").src)URL.revokeObjectURL(el("qr").src);el("qr").src=URL.createObjectURL(blob);show("qr");note("pairStatus","请在 App 的当前 Workspace 中点击“配对 App”并扫描。二维码 5 分钟有效。")}}catch(e){{note("pairStatus",`生成失败：${{e.message}}`,true)}}}};
let configDoc=null;function options(target,values){{target.innerHTML=values.map(value=>`<option value="${{value}}">${{value}}</option>`).join("")}}
function syncConfigForm(){{if(!configDoc)return;const providers=Object.keys(configDoc.providers),models=Object.keys(configDoc.models),agents=Object.keys(configDoc.agents.agents);options(el("providerSelect"),providers);options(el("modelProvider"),providers);options(el("modelSelect"),models);options(el("agentModel"),models);options(el("agentSelect"),agents);loadProvider();loadModelAgent()}}
function loadProvider(){{if(!configDoc)return;const provider=configDoc.providers[el("providerSelect").value];if(!provider)return;el("providerDriver").value=provider.driver;el("providerEndpoint").value=provider.server_url||provider.api_base||"";el("providerKeyRef").value=provider.api_key_ref||"";el("secretValue").value=""}}
function loadModelAgent(){{if(!configDoc)return;const model=configDoc.models[el("modelSelect").value];if(model){{el("modelProvider").value=model.provider;el("modelName").value=model.model}}const agent=configDoc.agents.agents[el("agentSelect").value];if(agent&&agent.model_binding.ownership==="platform")el("agentModel").value=agent.model_binding.model}}
function updateEditor(){{el("configDocument").value=JSON.stringify(configDoc,null,2)}}
async function loadConfig(){{try{{note("configStatus","正在加载…");const body=await localApi("/v1/console/config");configDoc=body.revision.document;updateEditor();syncConfigForm();note("configStatus",`已应用版本 ${{body.state.applied_revision_id}} · ${{body.state.apply_status}}`)}}catch(e){{note("configStatus",`配置加载失败：${{e.message}}`,true)}}}}
el("providerSelect").onchange=loadProvider;el("modelSelect").onchange=loadModelAgent;el("agentSelect").onchange=loadModelAgent;
el("applyProvider").onclick=()=>{{const provider=configDoc.providers[el("providerSelect").value];provider.driver=el("providerDriver").value;provider.server_url=provider.driver==="llamacpp"?el("providerEndpoint").value.trim():"";provider.api_base=["openai","openai_compatible","anthropic"].includes(provider.driver)?el("providerEndpoint").value.trim():"";provider.api_key_ref=el("providerKeyRef").value.trim();provider.api_key_env="";updateEditor();note("configStatus","Provider 修改已进入本地草稿，点击“校验并热发布”。")}};
el("applyModelAgent").onclick=()=>{{const model=configDoc.models[el("modelSelect").value];model.provider=el("modelProvider").value;model.model=el("modelName").value.trim();const agent=configDoc.agents.agents[el("agentSelect").value];if(agent.model_binding.ownership==="platform")agent.model_binding.model=el("agentModel").value;updateEditor();note("configStatus","Model / Agent 修改已进入本地草稿，点击“校验并热发布”。")}};
el("saveSecret").onclick=async()=>{{const reference=el("providerKeyRef").value.trim(),value=el("secretValue").value;if(!reference||!value)return note("configStatus","请填写 API Key 引用和值。",true);try{{await localApi(`/v1/console/secrets/${{encodeURIComponent(reference)}}`,{{method:"PUT",body:JSON.stringify({{value}})}});el("secretValue").value="";note("configStatus","API Key 已写入 Node Secret Store，不会回显。")}}catch(e){{note("configStatus",`API Key 保存失败：${{e.message}}`,true)}}}};
el("reloadConfig").onclick=loadConfig;el("publishConfig").onclick=async()=>{{try{{configDoc=JSON.parse(el("configDocument").value);note("configStatus","正在校验、preflight 并发布…");const body=await localApi("/v1/console/config/publish",{{method:"POST",body:JSON.stringify({{document:configDoc,summary:"Node Console update"}})}});note("configStatus",`发布成功：${{body.result.revision.revision_id}}。新 Invocation 已热生效。`);await loadConfig()}}catch(e){{note("configStatus",`发布失败：${{e.message}}`,true)}}}};
function renderLife(body){{el("lifecycleStatus").textContent=`平台：${{body.platform}} / ${{body.architecture}}\n版本：${{body.current_release||'未安装'}}\nHub：${{body.services.hub.active?'运行中':'已停止'}}\nNode：${{body.services.node.active?'运行中':'已停止'}}`}}
async function refreshLife(){{try{{renderLife(await localApi("/v1/console/lifecycle"))}}catch(e){{note("lifecycleStatus",`生命周期服务不可用：${{e.message}}`,true)}}}}
async function lifeAction(action,role=null,bundle_name=null){{try{{note("lifecycleAction","正在执行，请稍候…");const body=await localApi("/v1/console/lifecycle/actions",{{method:"POST",body:JSON.stringify({{action,role,bundle_name}})}});renderLife(body);note("lifecycleAction","操作完成。")}}catch(e){{note("lifecycleAction",`操作已提交或失败：${{e.message}}`,true);setTimeout(refreshLife,4000)}}}}
el("lifecycleRefresh").onclick=refreshLife;document.querySelectorAll("[data-life]").forEach(button=>button.onclick=()=>lifeAction(button.dataset.life,button.dataset.role||null));
el("updateButton").onclick=async()=>{{const file=el("bundleFile").files[0];if(!file)return note("lifecycleAction","请选择 .zip Bundle。",true);try{{note("lifecycleAction","正在上传签名 Bundle…");const response=await fetch(`/v1/console/lifecycle/bundles/${{encodeURIComponent(file.name)}}`,{{method:"PUT",headers:{{"Content-Type":"application/zip","X-Knoa-Console":csrf}},body:file}});const uploaded=await response.json();if(!response.ok)throw new Error(uploaded.error||`HTTP ${{response.status}}`);await lifeAction("update",null,uploaded.bundle_name)}}catch(e){{note("lifecycleAction",`更新失败：${{e.message}}`,true)}}}};
refresh();setInterval(refresh,5000);
loadConfig();
refreshLife();
</script></body></html>"""


__all__ = ["hub_console_html", "node_console_html"]
