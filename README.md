<h1 align="center">Genapi</h1>

<p align="center">面向网页生图场景的自托管控制台，集成用户注册登录、图片额度、兑换码、号池管理、SMTP 邮箱验证和 ChatGPT 官网生图能力。</p>

<p align="center">
  <a href="https://github.com/1134189025/GenAPI">GitHub</a>
  ·
  <a href="./docs/feature-status.en.md">功能状态</a>
  ·
  <a href="./docs/upstream-sse-conversation.md">上游流式协议说明</a>
</p>

## 项目定位

Genapi 不是通用 OpenAI API 转发服务。当前版本已经关闭 `/v1/*` OpenAI 兼容外部接口，只保留网页端生图工作流。

普通用户通过网页登录后使用 `/image` 生图；管理员通过后台维护用户、图片额度、兑换码、优惠码、SMTP 设置和 OpenAI 账号池。文本接口只作为内部能力保留，不对外提供用户 API 调用入口。

## 主要功能

- 网页生图工作台：支持文生图、图生图、多参考图编辑、多张图片生成和本地会话历史。
- 用户系统：首次安装创建管理员，普通用户使用邮箱密码登录，JWT 会话鉴权。
- 注册控制：支持注册开关、邮箱验证码、邮箱后缀白名单、邀请码和注册优惠码。
- 图片额度：普通用户按图片张数扣减额度，管理员不受额度和并发限制。
- 兑换码：管理员生成图片额度码、并发码、邀请码，用户登录后可兑换额度或并发。
- 优惠码：注册时可选输入优惠码，按配置赠送图片额度并限制使用次数和有效期。
- SMTP 邮件：支持配置发信服务器，验证码默认 15 分钟有效，发送冷却 60 秒。
- 号池管理：支持账号导入、刷新、筛选、导出、代理配置、限流检测和无效 Token 清理。
- sub2api 导入：可连接 sub2api 服务并批量导入 OpenAI OAuth 账号。
- Docker 部署：提供生产 compose 和本地构建 compose。

## 界面预览

文生图界面：

![文生图界面](assets/image.png)

图生图界面：

![图生图界面](assets/image_edit.png)

号池管理：

![号池管理](assets/account_pool.png)

## 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/1134189025/GenAPI.git
cd GenAPI
```

### 2. 启动服务

```bash
docker compose up -d
```

默认端口：

- 宿主机访问：`http://localhost:3000`
- 局域网访问：`http://<服务器局域网 IP>:3000`
- 容器内服务端口：`80`

首次访问会进入 `/setup` 安装向导，创建第一个管理员账号。管理员创建完成后，后续统一使用邮箱、密码登录。

### 3. 常用管理入口

- 生图页面：`/image`
- 用户兑换：`/redeem`
- 管理员账号池：`/admin/accounts`
- 管理员用户管理：`/admin/users`
- 管理员兑换码：`/admin/redeem-codes`
- 管理员优惠码：`/admin/promo-codes`
- 管理员注册机：`/admin/register-machine`
- 管理员设置：`/admin/settings`

## 本地开发

### 后端

项目使用 Python 3.13 和 `uv`。

```bash
uv sync
uv run python main.py
```

默认后端开发端口为 `8000`。

### 前端

项目使用 Next.js、React 和 Bun。

```bash
cd web
bun install
bun run dev
```

前端开发服务默认监听 `3000`，并会根据当前访问主机名推导后端地址 `http://<host>:8000`。如需指定后端地址：

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 bun run dev
```

### 本地 Docker 构建

```bash
docker compose -f docker-compose.local.yml up --build
```

`docker-compose.local.yml` 会把容器发布到宿主机 `8000`，适合本机调试构建结果。

## 配置说明

可以复制 `.env.example` 并按需设置环境变量。

```bash
cp .env.example .env
```

常用环境变量：

- `JWT_SECRET`：JWT 签名密钥，生产环境建议设置为至少 32 字节的随机字符串。
- `GENAPI_USER_DATABASE_URL`：用户系统数据库地址，默认使用 `data/users.db`。
- `GENAPI_BASE_URL`：生成图片 URL 时使用的外部访问地址。
- `STORAGE_BACKEND`：号池存储后端，可选 `json`、`sqlite`、`postgres`、`git`。
- `DATABASE_URL`：账号池数据库地址，`STORAGE_BACKEND=sqlite/postgres` 时使用。
- `GIT_REPO_URL`、`GIT_TOKEN`、`GIT_BRANCH`、`GIT_FILE_PATH`：Git 存储后端配置。

运行时敏感数据默认保存在 `data/` 目录，包括用户数据库、JWT 密钥、日志和账号池数据。该目录已被 `.gitignore` 排除，不应提交到 Git 仓库。

## 邮箱验证码

在管理员设置页配置 SMTP 后，注册页可以发送邮箱验证码。常见 QQ 邮箱配置示例：

- SMTP Host：`smtp.qq.com`
- SMTP Port：`465`
- SMTP Username：你的 QQ 邮箱地址
- SMTP Password：QQ 邮箱 SMTP 授权码，不是登录密码
- SMTP From：可留空，默认使用 SMTP Username

端口 `465` 使用 SSL，其他端口默认使用 STARTTLS。验证码邮件会使用站点名作为发信显示名，站点名可在管理员设置中修改。

## 用户与额度规则

- 管理员不受图片额度和图片并发限制。
- 普通用户调用网页生图会按生成图片数量扣减额度。
- 生图请求采用先预留、后结算逻辑；失败或未实际返回图片时会退回未生成部分额度。
- 图片并发超限会返回 429。
- 额度不足会返回 OpenAI 兼容的 `insufficient_quota` 错误结构。
- 文本相关能力不扣图片额度。

## 账号池导入方式

管理员可以在后台导入和维护 OpenAI 账号：

- 本地 CPA JSON 文件导入
- 远程 CPA 服务器导入
- sub2api 服务器导入
- access_token 手动导入

导入后可在账号池页面刷新账号状态、查看额度恢复时间、筛选账号类型、导出数据、配置代理并清理失效 Token。

## 接口边界

当前版本保留网页内部接口，例如：

- `POST /api/image/generations`
- `POST /api/image/edits`
- `POST /api/auth/login`
- `POST /api/auth/register`
- `POST /api/auth/send-verify-code`
- `GET /api/auth/me`

`/v1/*` OpenAI 兼容外部 API 已下线，直接访问会返回 `404 Not Found`。如果需要重新开放外部 API，应先重新设计权限、额度、审计和滥用控制，不建议直接恢复旧接口。

## 验证命令

后端测试：

```bash
uv run python -m unittest discover -s test
```

前端测试、类型检查和构建：

```bash
cd web
bun test
bun run typecheck
bun run build
```

## 安全注意事项

> [!WARNING]
> 本项目涉及对 ChatGPT 官网相关能力的逆向研究，仅供个人学习、技术研究与非商业性技术交流使用。

- 不要把 `data/`、`.env`、`config.json`、数据库文件、JWT 密钥、SMTP 授权码、OpenAI Token 提交到 Git 仓库。
- 不建议直接公网裸露部署；如需公网访问，请自行配置 HTTPS、反向代理、访问控制和备份策略。
- 不要使用你的重要 OpenAI 账号或高价值账号测试号池能力。
- 使用者应自行承担账号限制、封禁、数据泄露、违规使用等风险。

## 目录结构

```text
.
├── api/                 # FastAPI 路由
├── services/            # 账号池、用户、邮箱、额度、生图等服务逻辑
├── utils/               # 通用工具
├── test/                # 后端单元测试
├── web/                 # Next.js 前端
├── docs/                # 项目文档
├── assets/              # README 截图资源
├── data/                # 运行时数据，已忽略
├── Dockerfile
├── docker-compose.yml
└── docker-compose.local.yml
```

## License

本项目使用 MIT License。详见 [LICENSE](./LICENSE)。
