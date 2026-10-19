# agent-platform — 文件协议自动化运行平台

本仓维护任务调度与监督、RPC 会话封装、运行树同步、服务纠偏、定时触发和工作区代码同步机制。各模块保持独立进程与 CLI，共用一个源码与发布边界。

## 边界

调用方维护任务与角色正文、知识索引、服务和定时声明、主机映射、端点、凭据与运维记录。改变部署或政策不应产生本仓 diff。

| 模块 | 职责 |
|---|---|
| agentd/ | 文件协议、任务调度与监督、任务/常驻 RPC 适配及人格装配 |
| pi-host/ | 独立标准库 pi 宿主：PIPE/socket、原生 RPC 重放、真实进程退出；不依赖 agentd |
| agents-sync/ | 运行树传输与删除传播 |
| serviced/ | 服务启停、探测与期望态纠偏 |
| heartbeatd/ | 声明式定时触发 |
| git-sync/ | 工作区多仓代码拉取 |

## 安装

陌生用户可先用[绿色最小运行入口](portable/README.md)：clone 或解包后直接运行 `portable/run.sh doctor/demo/ctl`，无复制安装或必填配置；只需 Python 标准库与 bash，包含检查、CLI 和真实无模型 shell 任务链路，不包含完整六模块部署或个人配置。默认状态在包内 `workspace/`，可用 `PLATFORM_WORKSPACE` 外置；打包方可按显式文件清单构建归档。

将本仓放到调用方工作区下，使用仓内模块路径调用 CLI，例如：

```bash
python3 agent-platform/agentd/agentctl.py --help
```

调用方显式提供服务注册表、启动器与配置。各模块监督壳按自身目录定位实现脚本。

工作区根由入口路径现场发现。直接调用仓内模块时缺省取平台仓的父目录，也可用 `PLATFORM_WORKSPACE` 覆盖；模块已有的 `--root` 和专用环境变量优先。各模块用法见其 README。

## 验证

```bash
python3 test.py --workspace /path/to/workspace --integration
```

统一入口运行工作区路径契约、六模块回归及可选端到端场景，失败返回非零；`--module` 可选单模块。测试只写临时沙箱。调用方提供角色、扩展与解密器资产；各模块的单独回归入口仍见其 README。
