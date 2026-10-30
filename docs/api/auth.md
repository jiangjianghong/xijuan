# 管理员登录与接口访问控制

> 对应服务版本 0.3.0

管理员账号固定配置在 `configs/config.yaml`，不提供注册。所有受保护接口均接受管理员会话、有效 API Key 或来源 IP/CIDR 白名单，包括配置、删除、日志与密钥管理。API Key 是本系统生成的调用凭证，与大模型服务的 API Key 无关。

## 部署与首次登录

登录地址为 `/ui/login`，旧 `/ui/login.html` 地址自动跳转到该入口。

通过改密脚本设置的新密码长度为 **6–15 个字符**（含边界），两次输入必须一致。

```bash
uv sync
uv run python scripts/set_admin_password.py --username admin
python app.py
# 使用 uvicorn CLI 时必须关闭其代理头解析，由本系统判断可信代理：
uv run uvicorn app:app --host 0.0.0.0 --port 5019 --no-proxy-headers
```

打开 `/ui/` 会跳转登录页。账号配置为 `settings.username`，密码保存为 `settings.password_hash`（Argon2id）。旧版已填写的 `settings.password` 会在首次启动时自动转为哈希并删除明文，账号默认为 `admin`，原密码不变。未配置密码时拒绝登录，业务接口仍受保护。忘记密码时先停止服务，再运行上述交互命令，最后启动服务；配置锁仅在进程内生效，不能与运行中的设置页面同时写入。密码不接受命令行参数。

HTTPS 部署设置 `settings.secure_cookie: true`。会话 Cookie 为 `xijuan_session`，`Path=/; HttpOnly; SameSite=Strict`，默认 30 分钟。会话保存在单 worker 内存中，重启、退出、过期后重新登录；登录会轮换当前令牌。账号或密码配置变化后旧会话失效。暂不支持多 worker 共享会话。

登录和携带会话 Cookie 的写请求必须发送 `X-Requested-With: XMLHttpRequest`。前端自动附加；命令行使用 Cookie 调用时也需附加。跨站请求不开放 CORS，不能用 Origin/Referer 充当程序身份。

## 权限边界

| 范围 | 允许的凭证 |
|---|---|
| 所有受保护接口：上传、查询、规则及类型配置、删除、调试、日志、系统设置、Key 与白名单管理、API 文档等 | 管理员会话 / API Key / IP 白名单 |
| 登录页与必要静态资源、登录、会话状态、`/health` | 公开；登录仍有限速及请求来源检查 |

权限覆盖所有文档类型与接口，不提供每 Key 的文件、类型或接口隔离；新增接口自动沿用统一鉴权。三种身份均可按相同字段规则使用 `model_config`，包括 `base_url/api_key` 及其别名。未认证 API 请求返回 401，浏览器来源不通过或会话写请求缺少安全头返回 403。开放范围不等于匿名开放；Key 禁用、过期、删除及白名单撤销立即影响后续请求，持续日志流也会结束。

## 创建与调用 API Key

接口访问控制直接嵌入设置分组，API Key 和 IP 白名单在分组内切换。创建的完整密钥在当前分组内展示，可复制或收起；离开设置或会话过期时清除明文。本分组操作即时生效，无需点击普通设置的「保存修改」。

登录后选择「设置 → 接口访问控制 → API Key」，填写调用方名称及可选有效期（留空为永久），点击创建。系统生成至少 256 bit 随机密钥，完整值仅当次展示。列表只显示名称、尾号、有效期和状态，配置文件只保存 SHA-256 哈希。禁用、删除及重新生成即时生效；重新生成保留有效期和启用状态。过期密钥请创建新条目。

```bash
curl -X POST http://localhost:5019/file/parse \
  -H 'Authorization: Bearer xj_你的完整密钥' \
  -F 'file=@report.pdf'
```

## IP 白名单与代理

在「IP 白名单」添加名称和单个 IPv4/IPv6 地址或规范 CIDR 网段，例如 `192.168.1.10`、`192.168.1.0/24`、`::1`。不支持 `IP:port`，匹配来源所有端口。同一来源 IP（包括共用 NAT 出口）下所有程序共享免密权限；若需区分程序，使用独立 Key。默认白名单为空。

默认忽略不可信来源提供的 `X-Forwarded-For`。经过 Nginx 等代理时，只在 YAML 的 `access_control.trusted_proxies` 配置实际代理的 IP/CIDR，代理应正确覆写/追加 X-Forwarded-For，保留原 Host，并设置 X-Forwarded-Proto。系统从直连代理向外逐跳剥离，取第一个不可信地址为客户端地址。不要设置 `0.0.0.0/0` 或 `::/0` 为可信代理。必须禁用 Uvicorn 自带代理解析，原始对端已被它覆写时返回 503。


### Docker 反向代理登录报「不允许跨站访问」

此提示表示浏览器来源检查未通过，与密码错误不同。代理若把浏览器的外部 `Host` 改成内网服务地址，会造成 `Origin` 与后端收到的协议/主机/端口不一致。优先保留原始 Host，非标准端口也必须保留。例如 Nginx 的代理 location：

```nginx
location / {
    proxy_pass http://10.5.0.103:5019;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

来源比较会规范化主机名大小写与默认端口（HTTP 80、HTTPS 443），但外部非标准端口必须一致。前端使用相对路径 `/auth/login`；HTTP→HTTP 且 Host 正确时无需配置可信代理。Cookie 或密码问题不会产生这个来源检查错误。

更新代码后重试一次登录，查看 `docker compose logs --tail=100 wanz-prase2` 中的「登录来源校验拒绝」。日志包含 `origin`、`host_origin`、连接协议、`forwarded_proto`、`fetch_site`、直连对端与可信代理判定，不记录密码、Cookie、API Key 或请求体。应先用实际日志核对代理行为，再决定是否需要外部入口配置；客户端填写的转发 Host/Port 不被盲目信任。

若无法调整代理，更新应用代码后，在 `configs/config.yaml` 的现有 `access_control` 分组添加实际的外部入口：

```yaml
access_control:
  # 保留原有 api_keys、ip_allowlist 和 trusted_proxies 配置
  allowed_origins:
    - "http://59.203.14.205:8121"
```

`allowed_origins` 默认空，只在 YAML 配置；精确匹配协议、主机和端口，不允许通配符、路径或账号密码。它仅通过来源检查，接口仍须管理员会话、有效 API Key 或 IP 白名单认证；登录及会话写请求仍检查安全头，不会因此授予 IP 白名单权限或信任转发头。明确标为 `Sec-Fetch-Site: cross-site` 的请求仍然拒绝。

上述 IP 是部署示例，请替换为实际入口。配置写入后在服务器项目目录执行 `docker compose restart wanz-prase2`，再从外部 `/ui/login` 重新登录。仓库 Compose 挂载宿主机代码和配置；若自定义部署只使用镜像内代码，则需重新构建镜像并重建容器。HTTPS 入口请使用 `https://` 并启用 `settings.secure_cookie`。

## 管理接口

所有 `/settings/access` 接口接受管理员会话、有效 API Key 或 IP 白名单。写操作携带最近读取的 `base_version`，与普通设置共享 YAML 版本锁；冲突返回 409，重新获取列表后再提交。写入失败不改变正在运行的访问权限。`access_control` 不能通过普通 `PATCH /settings/config` 修改。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | `{username,password}`，返回会话 Cookie |
| GET | `/auth/session` | `{authenticated}` |
| POST | `/auth/logout` | 吊销当前会话 |
| GET | `/settings/access` | `{version,api_keys,ip_allowlist}`；永不返回完整 Key 或哈希 |
| POST | `/settings/access/keys` | `{base_version,name,expires_at?}`；返回列表和一次性 `key` |
| POST | `/settings/access/allowlist` | `{base_version,name,network}` |
| PATCH | `/settings/access/{kind}/{entry_id}` | `{base_version,enabled}`，kind 为 keys/allowlist |
| DELETE | `/settings/access/{kind}/{entry_id}` | JSON body `{base_version}` |
| POST | `/settings/access/keys/{entry_id}/rotate` | `{base_version}`，返回列表和新 `key` |

`expires_at` 使用带时区的 ISO 8601，例如 `2026-12-31T23:59:59+08:00`；必须晚于当前时间。旧 `/settings/login|session|logout` 保留为兼容路径，登录请求同样需要 username/password 和安全请求头。

<!-- AUTOGEN:request-body POST /auth/login -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| username | string | 是 | — |  |
| password | string | 是 | — |  |
<!-- /AUTOGEN:request-body -->
<!-- AUTOGEN:response GET /auth/session status=200 -->
无结构化字段（见示例 / 权威页）
<!-- /AUTOGEN:response -->
<!-- AUTOGEN:response POST /auth/logout status=200 -->
无结构化字段（见示例 / 权威页）
<!-- /AUTOGEN:response -->
<!-- AUTOGEN:response GET /settings/access status=200 -->
无结构化字段（见示例 / 权威页）
<!-- /AUTOGEN:response -->
<!-- AUTOGEN:request-body POST /settings/access/keys -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
| name | string | 是 | — |  |
| expires_at | string | 否 | — |  |
<!-- /AUTOGEN:request-body -->
<!-- AUTOGEN:request-body POST /settings/access/allowlist -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
| name | string | 是 | — |  |
| network | string | 是 | — |  |
<!-- /AUTOGEN:request-body -->
<!-- AUTOGEN:request-body PATCH /settings/access/{kind}/{entry_id} -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
| enabled | boolean | 是 | — |  |
<!-- /AUTOGEN:request-body -->
<!-- AUTOGEN:request-body DELETE /settings/access/{kind}/{entry_id} -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
<!-- /AUTOGEN:request-body -->
<!-- AUTOGEN:request-body POST /settings/access/keys/{entry_id}/rotate -->
| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:--:|---|---|
| base_version | string | 是 | — |  |
<!-- /AUTOGEN:request-body -->
