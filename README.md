# UMI 数据统计工具

> 面向 UMI 具身智能数据采集场景的本地可视化统计分析工具 —— 扫描数据盘、多维度统计采集时长、关联平台任务信息，一站式掌握采集进度与数据质量。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%2F%20Linux-orange)
![Frontend](https://img.shields.io/badge/Frontend-原生%20ES%20Modules-yellow)
![Dependencies](https://img.shields.io/badge/后端依赖-仅%20requests-green)

## 背景

使用觅蜂科技 MEgo View + MEgo Gripper 采集 UMI 数据后，TF 卡数据经 `tf2ssd-gui` 拷贝到 SSD。原有的 `umi-total-duration.sh` 脚本只能手动指定路径、统计总时长，无法回答：

- 每台设备各采集了多少时长？
- 每个任务 / 分组完成了多少，距离目标还差多少？
- 哪些数据段存在异常（时长缺失、目录过深、嵌套段……）？
- 数据盘还剩多少空间？

本工具以本地 Web GUI 的形式解决这些问题：**自动发现数据盘 → 后台扫描 → 九个维度可视化分析 → 关联觅蜂平台任务详情 → 一键导出报表**。

## 功能特性

### 多维度统计视图

| 视图 | 说明 |
| --- | --- |
| 概览 | 总时长、数据段数、设备 / 任务 / 日期跨度等关键指标一览 |
| 设备 | 按设备（AID）统计时长与段数分布 |
| 任务 | 按任务 ID 统计，登录平台后可关联任务名称、采集员、项目、设备类型、目标时长 |
| 分组 | 按采集分组汇总统计 |
| 采集结构 | 数据盘目录结构与分组-任务层级可视化 |
| 日期 | 按采集日期统计时长趋势 |
| 明细 | 数据段（episode）级明细：路径、时长、设备、任务等 |
| 异常 | 时长缺失 / 非数值 / 超范围、目录过深、嵌套段等异常清单 |
| 存储 | 数据盘容量占用、各设备数据量与容量预警 |

### 扫描引擎

- **自动发现数据盘**：读取挂载信息，依据 `.tf2ssd-cards.json` 标记或 `tf-*` 目录识别数据盘，无需手动输入路径
- **后台扫描 + 实时进度**：全局单任务锁，扫描不阻塞界面；进度心跳 + 看门狗机制，卡死自动恢复
- **增量扫描缓存**：基于目录 mtime 的缓存，重复扫描大幅提速
- **口径对齐**：统计口径与 `umi-total-duration.sh` 完全一致（见下文）

### 觅蜂平台集成

- GUI 内登录觅蜂平台（Casdoor 认证），同步任务历史并本地缓存
- 按任务 ID 自动关联平台字段：任务名称、采集员、所属项目、设备类型、目标时长、创建时间
- 离线可用：未登录 / 断网时仅展示本地统计，不影响扫描

### 报表导出

- 设备 / 任务 / 分组 / 日期 / 明细 / 异常六类报表
- 支持 **CSV**（UTF-8 BOM，Excel 直接打开不乱码）与 **JSON** 两种格式

### 发布与自动更新

- `publish.py` 一键打包：自动递增版本号、生成更新包与 `version.json` 清单，可启动只读静态更新源服务
- 客户机启动时自动检查更新：SHA-256 校验 → 备份 → 替换 → 失败自动回滚，更新绝不触碰用户数据

## 快速开始

### 环境要求

- Ubuntu / Linux 桌面环境
- Python 3.8+（推荐 3.10）
- 唯一第三方依赖：`requests`

### 安装与启动

```bash
git clone <本仓库地址>
cd ManiformerDataStatisticsTool

pip3 install requests

./start.sh
```

启动脚本会自动检查更新、检测端口占用，并在浏览器打开 <http://127.0.0.1:8765>。

### 使用流程

1. **选择数据盘**：顶部下拉框自动列出挂载磁盘，数据盘会被标记并默认选中
2. **开始扫描**：点击扫描按钮，实时查看进度；完成后各视图自动刷新
3. **浏览分析**：在九个标签页间切换，查看不同维度的统计结果
4. **（可选）登录平台**：登录觅蜂平台并同步任务历史，任务视图即可显示任务名称等详情
5. **导出报表**：在支持导出的视图点击导出按钮，获取 CSV / JSON 文件

## 统计口径

与 `umi-total-duration.sh` 对齐：

- 数据段（episode）= 含 `meta_info.json` 的目录，相对根目录深度 ≤ 4 计入，深度 5 报告为 `too_deep`
- 发现数据段后不再向下遍历，嵌套段只计数不参与统计
- 剪枝排除：`.` 开头目录、`Thumbs.db`、`System Volume Information`、`$RECYCLE.BIN`、`RECYCLER`
- 仅 `robot_type` 归一化为 `umi`、`duration` 有效（正数且 ≤ 1000000 秒）、AID 非空的段计入总时长
- 时长按段四舍五入到整秒；日期统计使用东八区（Asia/Shanghai）

## 项目结构

```
.
├── app/
│   ├── server.py          # 本地 HTTP 服务（127.0.0.1:8765）与 API 路由
│   ├── scanner.py         # 扫描引擎：episode 发现、剪枝、增量缓存
│   ├── stats.py           # 多维度统计聚合
│   ├── platform_api.py    # 觅蜂平台登录 / 任务同步 / 本地缓存 / 任务关联
│   ├── updater.py         # 客户机自更新：检查 / 校验 / 备份 / 替换 / 回滚
│   └── static/            # 前端：原生 ES Modules，零构建、零外部依赖、离线可用
│       ├── index.html
│       ├── style.css
│       └── js/            # 入口、状态、API 封装与九个视图模块
├── publish.py             # 发布脚本：版本管理 / 打包 / 静态更新源服务
├── start.sh               # 一键启动器
├── VERSION                # 版本号（唯一版本来源）
└── umi-total-duration.sh  # 原始总时长统计脚本（统计口径基准）
```

## 本地数据与安全

- 运行时数据统一存放于 `~/.umi-stats-tool/`：扫描结果、增量缓存、平台 token 与缓存、更新备份与日志
- 服务仅绑定 `127.0.0.1`，不对外网暴露
- 平台密码只经登录请求传输，**绝不写入任何文件或日志**；token 文件权限 `0600`
- 自动更新仅替换 `app/`、`start.sh`、`VERSION`，绝不触碰用户数据目录

## 发布与更新（维护者）

```bash
python3 publish.py                    # 自动递增版本号并打包到 dist/
python3 publish.py --version 2.0.0    # 显式指定版本号
python3 publish.py --no-bump          # 按当前 VERSION 重新发布
python3 publish.py --serve            # 在本机 8766 端口提供只读更新源
```

发布时会自动把 `deploy/install.sh` 与 `deploy/icon.svg` 同步到 `dist/`，由 8766 更新源一并托管。客户机更新源地址可在 `~/.umi-stats-tool/update.json` 的 `manifest_url` 字段覆盖，指向发布机的 `version.json`。

## 一键部署到目标 Ubuntu（在线）

目标机为 Ubuntu 桌面环境（Python 3.8+，能访问发布机 8766 端口）。发布机先执行 `python3 publish.py` 与 `python3 publish.py --serve`（必要时 `sudo ufw allow 8766/tcp`），然后在目标机执行一条命令：

```bash
curl -s http://<发布机IP>:8766/install.sh | bash -s -- --source http://<发布机IP>:8766
```

安装器（[deploy/install.sh](deploy/install.sh)）自动完成：环境与架构检查 → `requests` 依赖安装（apt → pip 两级降级）→ 下载更新包并 SHA-256 校验 → 旧版本备份后安装到 `~/umi-stats-tool/` → 写入更新源配置（对接启动自动更新）→ 创建应用菜单图标与 SVG 图标 → HTTP 接口自检。

- 幂等：重复执行即重新部署，旧版本保留在 `~/umi-stats-tool.bak/`
- 用户数据始终在 `~/.umi-stats-tool/`，与程序目录分离
- 卸载（保留数据）：命令末尾加 `--uninstall`；连数据一并删除：加 `--purge`

## 技术栈

- **后端**：Python 3 标准库 `http.server`（多线程），仅 `requests` 一个第三方依赖
- **前端**：原生 HTML / CSS / JavaScript（ES Modules），无框架、无构建步骤、无 CDN 依赖，完全离线可用
- **更新**：tar 包 + SHA-256 校验 + 原子替换 + 自动回滚