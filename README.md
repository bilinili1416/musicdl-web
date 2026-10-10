# Musicdl Web

基于 [CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl) 的中文网页工作台，支持多平台搜索、下载、音乐库及逐句双语歌词。后台采用轻量 Python HTTP 服务，前端采用原生 HTML / CSS / JavaScript，无需 Node.js 或前端构建。

## 启动

需要 Python 3.10+，推荐 Python 3.12。克隆仓库后，在项目目录创建环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

浏览器访问 http://127.0.0.1:8000 。也可以运行 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，脚本会在缺少虚拟环境或 musicdl 时自动安装。更换端口：

```powershell
.\.venv\Scripts\python.exe server.py --port 8080
```

按 Ctrl+C 关闭服务。macOS / Linux 可使用 `python3 -m venv .venv`，随后运行 `.venv/bin/pip install -r requirements.txt` 和 `.venv/bin/python server.py`。

## 功能

- 网易云、QQ、酷我、酷狗、咪咕、千千音乐，多平台并行搜索。
- 按平台、无损音质筛选，按歌名或文件大小排序。
- 单首及批量下载，真实排队、下载中、完成、失败状态。
- 收藏与搜索偏好保存在浏览器 localStorage。
- 下载历史保存在 `.musicdl-web/history.json`，重启后仍可查看。
- 下载文件保存在 `musicdl_outputs/平台名称/`，可在网页试听或保存到浏览器。
- 音频采用 HTTP Range 支持拖动进度；浏览器支持的格式因平台而异。
- 播放时逐句高亮、自动滚动原文与译文，点击歌词可跳转，支持每首歌曲单独调整歌词偏移。
- 支持移动端、键盘导航和 `/` 快捷聚焦搜索。

音质由 musicdl 平台接口实际返回，不进行转码。搜索过程会解析可下载音频，因此可能需要几十秒；单个平台超过 65 秒会终止该平台查询并显示超时。当前搜索结果缓存两小时，服务重启后需要重新搜索；收藏页提供重新搜索按钮获取最新链接。服务重启会将未完成任务标为失败，已下载文件保留。

## 文件与接口

`web/` 是网页，`server.py` 封装 musicdl。服务默认只监听 `127.0.0.1`。完整云端部署步骤见 [DEPLOY.md](DEPLOY.md)，已提供 Railway / Docker 配置、密码认证、HTTPS 域名检查和持久存储支持。

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 引擎版本、平台列表、下载目录 |
| `POST /api/search` | `{keyword, sources, limit}`，返回搜索任务 ID |
| `GET /api/search/{id}` | 搜索进度、结果、平台错误 |
| `POST /api/downloads` | `{ids: [搜索结果ID]}`，加入队列 |
| `GET /api/downloads` | 下载队列与历史 |
| `GET /api/library` | 本地音频文件列表 |
| `GET /api/files/{id}` | 试听；加 `?download=1` 保存文件 |
| `GET /api/lyrics/{id}` | 已下载文件的本地／内嵌歌词与译文时间轴 |
| `POST /api/lyrics/{id}/lookup` | 自动匹配或按 `{keyword}` 查找版本；`{songId}` 选择网易云歌词版本 |
| `POST /api/lyrics/{id}/import` | `{kind: "original" 或 "translation", text}`，保存导入歌词 |

## 逐句歌词

打开本地音乐，点击播放，歌词面板会自动展开。优先读取同名 `.lrc` 或音频内嵌歌词，以及同名 `.translation.lrc`、`.zh.lrc`、`.trans.lrc` 译文。双语 LRC 在相同时间戳下的第二行也会显示为译文。

译文缺失时，播放器会将歌名和歌手发送给网易云查找同一版本，只在歌名和歌手匹配时自动保存。获取后保存在 `.musicdl-web/lyrics/`，再次播放不需要联网。音频不会上传。没有自动匹配时点击「在线找歌词」选择同一版本，或用「导入原文／导入译文」选择不超过 256 KB 的 LRC／TXT 文件。导入和在线歌词只保存独立缓存，不覆盖已有音频及原始 LRC。

原文与译文按时间戳对齐，允许 0.25 秒的舍入误差；没有时间轴的歌词以静态文本展示，不假造同步。未匹配的译文会提示。播放器底部可以调整 ±30 秒的偏移，正值让歌词提前，偏移保存在该浏览器。翻译来自平台或导入文件，不自动生成机器翻译；并非所有歌曲都有译文。纯音乐或平台不可用时仍可正常播放。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web/app.js
```

平台可能因接口更新、网络或访问限制返回空结果或错误。不要将无结果视为成功下载。仅下载你有权使用的内容，并遵守平台条款及上游许可。本项目没有绕过登录、付费或 DRM 的逻辑。

原仓库：[CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl)。本项目通过已安装的包调用上游，没有复制或修改其源代码。
