# 部署完整服务到 Railway

部署包含 Python 后台和网页。不要只托管静态 `web/` 目录：搜索、下载、音乐库与歌词缓存需要持续运行的 Python 服务。

## 从 GitHub 部署

1. 在 Railway 创建项目，选择本仓库作为服务来源。仓库根目录的 `Dockerfile` 会构建 Python 3.12 与 musicdl 环境，无需前端构建。
2. 添加持久 Volume，挂载到 `/data`。下载音频、历史和缓存歌词都会保存在其中。启动脚本准备目录权限后，以非 root 用户运行 Python。
3. 在服务 Variables 中设置以下变量。实际密码只填写在托管平台中，不提交到 Git。

   | 变量 | 值 |
   | --- | --- |
   | `MUSICDL_USERNAME` | 自己选择的用户名，默认 `musicdl` |
   | `MUSICDL_PASSWORD` | 至少 16 字符的随机密码 |
   | `MUSICDL_DATA_DIR` | `/data` |
   | `HOST` | `0.0.0.0`（镜像已设默认值） |
   | `PORT` | 平台端口，镜像默认 `8000` |

4. 在服务 Settings 中设置健康检查路径 `/healthz`、超时 120 秒、失败重启最多 5 次。保持单实例，关闭自动休眠，避免后台下载中断。
5. 在 Networking 中生成 Railway HTTPS 域名，路由到应用监听端口。程序会读取 `RAILWAY_PUBLIC_DOMAIN`。自定义域名需设置 `MUSICDL_PUBLIC_ORIGIN=https://你的域名`，不带路径。
6. 部署完成后打开 HTTPS 地址并登录。认证覆盖网页、API、音频和歌词；健康检查只返回就绪状态，无需登录。
7. 验证实际搜索、下载、试听，以及重新部署后文件与历史是否保留。平台接口受部署区域和访问限制影响。

[官方 Docker 部署](https://docs.railway.com/builds/dockerfiles) · [持久磁盘](https://docs.railway.com/volumes) · [HTTPS 域名](https://docs.railway.com/networking/public-networking)

## 资源与费用

私人使用可从单实例、0.5 GB 内存和 0.5 vCPU 上限开始。当前下载队列与搜索缓存由一个进程管理，不支持多副本。搜索出现内存不足时，减少同时搜索的平台，或提高内存上限。

持久磁盘容量按需要与套餐限制设置。大量 FLAC 文件会很快占满磁盘，下载和试听还会产生出口流量。长期使用需关注套餐、试用有效期和用量限制，以 [Railway 官方价格](https://docs.railway.com/pricing/plans) 与账单为准。试用数据保留规则见 [官方试用说明](https://docs.railway.com/pricing/free-trial)。

## 更新与数据

仓库接入 Railway 后，可按平台设置从所选分支自动部署。也可以使用 [官方 CLI](https://docs.railway.com/cli) 上传本地源码：

```powershell
& .\deploy\package.ps1
Expand-Archive -LiteralPath .\deploy-source.zip -DestinationPath .\.deploy-upload -Force
railway login --browserless
railway up .\.deploy-upload --path-as-root --project <项目ID> --environment <环境ID> --service <服务ID> --detach
```

- 部署包采用源码白名单，不包含本地音乐、历史、访问密码或虚拟环境。
- 同一部署的设备共享服务器上的音乐、下载历史与保存的歌词；收藏、偏好、歌词偏移保存在各设备浏览器中。
- 原文和译文缓存在 `/data/.musicdl-web/lyrics/`；可离线读取已有缓存。
- 不要删除 Volume，重要文件另做备份。重新部署无法恢复已删除的磁盘。
- 修改平台中的 `MUSICDL_PASSWORD` 后，使用新密码登录。

## 自托管 Docker

```sh
docker build -t musicdl-web .
docker run --rm -p 8000:8000 --env-file .env -v musicdl-data:/data musicdl-web
```

先将 `.env.example` 复制为 `.env` 并设置强密码；`.env` 已被 Git 忽略。向公网提供服务时，请配置 HTTPS 反向代理与 `MUSICDL_PUBLIC_ORIGIN`。以 `/healthz` 作为健康检查。
