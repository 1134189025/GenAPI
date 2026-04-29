<h1 align="center">Genapi</h1>

<p align="center">面向商业化图片生成站点的自托管运营后台。</p>

<p align="center">
  用户注册 · 图片额度 · 会员套餐 · 兑换码 · 优惠码 · 邮箱验证 · 账号池 · 网页生图
</p>

<p align="center">
  <a href="https://github.com/1134189025/GenAPI">GitHub</a>
  ·
  <a href="./docs/feature-status.en.md">功能状态</a>
  ·
  <a href="./docs/upstream-sse-conversation.md">协议说明</a>
</p>

## 项目定位

Genapi 是一个以网页生图为核心的自托管系统，目标是把 ChatGPT 官网图片能力包装成可运营、可分发、可控额度的图片生成站点。

它更接近一个“图片生成业务后台”，而不是传统 API 中转服务。当前版本已经关闭 `/v1/*` OpenAI 兼容外部接口，普通用户只能通过网页端使用生图能力。管理员负责维护用户、额度、会员套餐、兑换码、优惠码、邮箱验证、账号池和系统配置。

适合的使用场景：

- 搭建私有图片生成站点，给固定用户或小范围用户使用。
- 给会员、客户、群成员分配图片生成次数。
- 通过会员码、兑换码、优惠码、邀请码完成手动售卖、活动发放或用户准入。
- 把多个 OpenAI 账号集中到账号池中，由后台统一调度。
- 给工作室、社群、团队、内部业务提供可控的网页生图入口。

项目本身不内置支付系统。如果需要收费，可以配合第三方发卡平台、人工收款或自建支付系统，将“兑换码”作为交付物。

## 商业化能力

### 用户与权限

- 首次启动通过 `/setup` 创建管理员。
- 用户使用邮箱和密码登录，后端签发 JWT 会话。
- 管理员可以创建、禁用、删除用户。
- 管理员可以调整用户角色、图片额度和图片并发数。
- 普通用户只能访问自己的生图工作台和兑换记录。
- 管理员拥有账号池、用户、码管理、日志、系统设置等后台权限。

### 额度体系

- 图片额度按“生成图片张数”计算。
- 普通用户每次生图会先预留额度，再按实际返回图片数结算。
- 请求失败或没有返回图片时，会退回未使用额度。
- 普通额度和会员额度分开记录，生图时优先消耗会员额度，再消耗普通额度。
- 普通用户受图片并发限制，超限会返回 429。
- 管理员不受图片额度和图片并发限制。
- 文本相关内部能力不扣图片额度。

这套规则适合直接做成“次数包”“会员赠送次数”“活动赠送次数”等业务模型。

### 会员套餐

管理员可以在 `/admin/membership-plans` 配置会员套餐：

- 套餐名称和说明。
- 有效天数，例如日卡、周卡、月卡。
- 额度周期天数，例如每 7 天刷新一次。
- 每周期会员图片额度。
- 启用状态和排序。

用户可以在 `/membership` 查看当前会员、周期额度、周期结束时间、到期时间和可兑换套餐。会员周期从激活时间开始滚动计算，不按自然日、自然周或自然月重置；每个周期到达边界后刷新到套餐额度，不结转上个周期剩余额度。会员到期后只清空会员额度，不影响普通图片额度。用户兑换新的会员码会替换旧会员，并清空旧会员额度后按新套餐重新激活。

### 兑换码

管理员可以批量生成一次性兑换码：

- `image_quota`：兑换图片次数。
- `concurrency`：提升图片并发数。
- `invitation`：作为注册邀请码使用。
- `membership`：激活或替换会员套餐。

用户登录后可在 `/redeem` 输入兑换码。兑换成功后，普通额度、并发或会员套餐立即写入用户账户。同一个码只能使用一次，后台可以查看状态和使用记录。

典型商业用法：

- 发卡平台售卖图片次数兑换码。
- 发卡平台售卖日卡、周卡、月卡会员兑换码。
- 社群活动发放限量兑换码。
- 给代理或客户批量生成独立码。
- 用邀请码控制注册入口，避免公开注册被滥用。

### 优惠码

优惠码用于注册阶段赠送图片额度，适合推广和活动。

支持能力：

- 设置注册赠送图片额度。
- 设置最大使用次数。
- 设置过期时间。
- 禁用或删除优惠码。
- 记录使用情况，避免无限领取。

典型商业用法：

- 新用户注册送额度。
- 推广渠道专属码。
- 节日活动码。
- 小范围测试邀请。

### 邮箱验证与注册控制

注册系统支持：

- 开启或关闭注册入口。
- 开启或关闭邮箱验证码。
- 设置邮箱后缀白名单。
- 开启邀请码要求。
- 开启优惠码注册赠送。
- 配置站点名，让验证码邮件显示自定义品牌名。

验证码默认 15 分钟有效，发送冷却默认 60 秒，错误次数超过限制后会失效。验证码只保存哈希，不保存明文。

SMTP 支持常见邮箱服务。QQ 邮箱示例：

- SMTP Host：`smtp.qq.com`
- SMTP Port：`465`
- SMTP Username：邮箱地址
- SMTP Password：SMTP 授权码
- SMTP From：可留空，默认使用 SMTP Username

端口 `465` 使用 SSL，其他端口默认使用 STARTTLS。

### 账号池运营

Genapi 的账号池用于集中管理上游 OpenAI 账号，并为网页生图提供可用账号。

支持能力：

- access_token 导入。
- 本地 CPA JSON 文件导入。
- 远程 CPA 服务器导入。
- sub2api 服务器导入。
- 批量刷新账号状态。
- 查看账号邮箱、类型、额度和恢复时间。
- 自动检测限流账号并定时刷新。
- 遇到 Token 失效类错误时清理无效 Token。
- 支持 HTTP、HTTPS、SOCKS5、SOCKS5H 代理配置和代理测试。
- 支持搜索、筛选、导出、手动编辑和批量维护。

这部分适合做成后台运维能力：用户只看到网页生图入口，管理员在后台维护上游账号可用性。

### 生图工作台

用户侧核心入口是 `/image`。

当前支持：

- 文生图。
- 图生图。
- 多参考图编辑。
- 多张图片生成。
- 本地会话历史。
- 图片结果回看、删除和清空。
- 服务端图片缓存 URL。
- 用户维度的生图历史隔离。

支持的模型选项包括 `gpt-image-2`、`codex-gpt-image-2`、`auto` 以及若干 ChatGPT 官网相关模型别名。实际可用性取决于账号池中账号的权限、订阅和上游状态。

### 日志与运营排查

管理员可以查看系统日志和图片记录，用于排查：

- 用户调用情况。
- 生图失败原因。
- 上游账号异常。
- 代理连通性。
- 图片生成记录。

日志和运行时数据保存在本地 `data/` 目录，该目录默认不提交到 Git。

### 可选注册辅助

项目保留了注册机相关页面和接口，主要作为管理员内部辅助工具，用于补充账号池来源。它不是 Genapi 的核心商业能力，README 不展开说明。

## 快速部署

### 1. 克隆仓库

```bash
git clone https://github.com/1134189025/GenAPI.git
cd GenAPI
```

### 2. 启动 Docker 服务

```bash
docker compose up -d
```

默认访问地址：

- 本机：`http://localhost:3000`
- 局域网：`http://<服务器局域网 IP>:3000`

首次访问会进入安装向导。创建管理员后，使用邮箱和密码登录后台。

### 3. 常用入口

- `/setup`：首次安装。
- `/login`：登录。
- `/register`：用户注册。
- `/image`：用户生图。
- `/membership`：会员中心。
- `/redeem`：用户兑换。
- `/admin/accounts`：账号池。
- `/admin/users`：用户管理。
- `/admin/membership-plans`：会员套餐管理。
- `/admin/redeem-codes`：兑换码管理。
- `/admin/promo-codes`：优惠码管理。
- `/admin/settings`：系统和注册设置。
- `/admin/logs`：日志。
- `/admin/images`：图片记录。

## 配置

可以从示例文件创建本地环境配置：

```bash
cp .env.example .env
```

常用环境变量：

- `JWT_SECRET`：JWT 签名密钥，生产环境建议手动设置。
- `GENAPI_CONFIG_FILE`：运行时配置文件，默认 `data/config.json`。
- `GENAPI_USER_DATABASE_URL`：用户系统数据库，默认 `data/users.db`。
- `GENAPI_BASE_URL`：对外访问地址，用于生成图片 URL。
- `STORAGE_BACKEND`：账号池存储后端，可选 `json`、`sqlite`、`postgres`、`git`。
- `DATABASE_URL`：账号池数据库地址。
- `GIT_REPO_URL`、`GIT_TOKEN`、`GIT_BRANCH`、`GIT_FILE_PATH`：Git 存储后端配置。
- `GENAPI_DEPLOYMENT_MODE`：部署模式。systemd 二进制安装使用 `systemd-binary`，Docker 使用 `docker`，源码运行使用 `source`。
- `GENAPI_BUILD_TYPE`：构建类型。GitHub Release 二进制使用 `release`，Docker 镜像使用 `docker`，源码运行使用 `source`。
- `GENAPI_DATA_DIR`：运行时数据目录。systemd 二进制安装默认使用 `/opt/genapi/data`。
- `GITHUB_TOKEN`：可选。用于访问 GitHub Releases API，避免匿名请求限流。

运行时数据：

- `data/users.db`：用户、额度、会员套餐、兑换码、优惠码、验证码等数据。
- `data/config.json`：后台系统设置。
- `data/jwt_hmac_secret`：自动生成的 JWT HMAC 密钥。
- `data/accounts.json` 或数据库账号池：上游账号数据。
- `data/images/`：生成图片缓存，默认保留 30 天且全局上限 10GB，可在后台系统设置调整。
- `data/logs.jsonl`：运行日志。

这些文件包含敏感信息，不应提交到仓库。

图片缓存会在服务启动、保存新图片后和后台定时任务中自动清理。后台系统设置支持调整图片保留天数、缓存大小上限和自动删除开关。

## 网页更新中心

网页一键更新只支持 systemd 二进制部署。该模式和 sub2api 一样：更新中心检查 GitHub Releases，下载当前系统架构对应的 `genapi_*_linux_*.tar.gz`，校验 `checksums.txt`，并把 `genapi`、`VERSION`、`web_dist` 作为同一组 release 资产安装到 `/opt/genapi/app/releases/`。运行入口是 `/opt/genapi/app/current` symlink，更新时会先准备完整新目录，再原子切换 `current`，旧版本保留在 `/opt/genapi/app/previous` 供网页回滚。安装脚本升级时会把完整旧版本备份到 `/var/backups/genapi`，回滚前同样会校验压缩包结构。更新完成后，网页会提示重启；重启通过退出进程触发 systemd 的 `Restart=always` 自动拉起。

推荐使用安装脚本部署：

```bash
curl -fsSL https://raw.githubusercontent.com/1134189025/GenAPI/main/deploy/install.sh | sudo bash
```

systemd 服务会写入以下关键环境变量：

```ini
Environment=GENAPI_DEPLOYMENT_MODE=systemd-binary
Environment=GENAPI_BUILD_TYPE=release
Environment=GENAPI_DATA_DIR=/opt/genapi/data
Environment=GENAPI_HOST=0.0.0.0
Environment=GENAPI_PORT=3000
```

Docker 和源码部署仍会显示版本检查结果，但网页不会执行一键更新。Docker 部署请继续使用手动 `docker compose pull app && docker compose up -d app`；源码部署请手动 `git pull` 后重启服务。

如果网页更新后服务无法启动，可以通过 SSH 手动恢复上一版：

```bash
sudo ln -sfn "$(readlink -f /opt/genapi/app/previous)" /opt/genapi/app/current
sudo systemctl restart genapi
```

如果是通过安装脚本执行的升级，也可以使用脚本回滚到 `/var/backups/genapi` 中最近一次完整备份：

```bash
curl -fsSL https://raw.githubusercontent.com/1134189025/GenAPI/main/deploy/install.sh | sudo bash -s -- rollback
```

## 手动更新与恢复

手动更新前先备份运行时数据：

```bash
tar -czf genapi-data-backup-$(date +%Y%m%d-%H%M%S).tar.gz data
```

拉取并重启应用服务：

```bash
docker compose pull app
docker compose up -d app
docker compose ps app
```

如果应用镜像需要回滚，先选择要回滚到的旧镜像标签或镜像 digest，然后只覆盖应用服务镜像：

```bash
PREVIOUS_IMAGE=ghcr.io/1134189025/genapi:0.1.3
cat > /tmp/genapi-rollback.yml <<EOF
services:
  app:
    image: ${PREVIOUS_IMAGE}
EOF
docker compose -f docker-compose.yml -f /tmp/genapi-rollback.yml up -d app
rm -f /tmp/genapi-rollback.yml
```

上面的回滚只回滚应用镜像，不会恢复数据库、图片缓存、配置或账号池数据。若需要恢复数据，请先停止服务，再从更新前备份中手动恢复 `data/` 或外部数据库，并确认文件权限后重新启动服务。

## 本地开发

### 后端

```bash
uv sync
uv run python main.py
```

默认后端端口为 `8000`。

### 前端

```bash
cd web
bun install
bun run dev
```

前端默认端口为 `3000`。开发环境会根据当前访问主机推导后端地址 `http://<host>:8000`。如需手动指定：

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 bun run dev
```

### 本地 Docker 构建

```bash
docker compose -f docker-compose.local.yml up --build
```

本地构建 compose 默认把服务发布到宿主机 `8000`。

## 接口边界

Genapi 当前只保留网页内部接口。

主要内部接口包括：

- `POST /api/image/generations`
- `POST /api/image/edits`
- `POST /api/auth/login`
- `POST /api/auth/register`
- `POST /api/auth/send-verify-code`
- `GET /api/auth/me`
- `GET /api/membership/plans`
- `GET /api/membership/me`
- `POST /api/redeem`
- `GET /api/redeem/history`
- `GET /api/admin/users`
- `GET /api/admin/membership-plans`
- `POST /api/admin/membership-plans`
- `POST /api/admin/redeem-codes/generate`
- `GET /api/admin/promo-codes`

`/v1/*` OpenAI 兼容外部 API 已关闭，直接访问会返回 `404 Not Found`。如果要重新开放外部 API，需要重新设计独立的密钥体系、额度计费、审计日志和滥用控制。

## 验证命令

后端单元测试：

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

## 部署建议

- 生产环境建议设置 `JWT_SECRET`。
- 生产环境建议使用 SQLite 或 PostgreSQL 存储用户数据。
- 定期备份 `data/` 或外部数据库。
- 不要把 `data/`、`.env`、`config.json`、SMTP 授权码、OpenAI Token、JWT 密钥提交到 Git。
- 公网部署建议使用 HTTPS、反向代理、防火墙和访问控制。
- 账号池中的上游账号建议按用途分组管理，不要使用重要账号测试。

## 目录结构

```text
.
├── api/                 # FastAPI 路由
├── services/            # 用户、额度、邮箱、账号池、生图等服务逻辑
├── utils/               # 通用工具
├── test/                # 后端测试
├── web/                 # Next.js 前端
├── docs/                # 补充文档
├── assets/              # 静态资源
├── data/                # 运行时数据，默认忽略
├── Dockerfile
├── docker-compose.yml
└── docker-compose.local.yml
```

## License

本项目使用 MIT License。详见 [LICENSE](./LICENSE)。
