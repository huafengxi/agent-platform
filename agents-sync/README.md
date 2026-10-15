---
name: agents-sync
anchors:
  watch-cost: "watch 轮次模型：整树协商 + 速率下限（同步面纪律）"
  gc: "gc：清单是权威、schedule 只是计划（删除对账）"
  commands: "常用命令"
---

# agents-sync/ — agents/ 跨机同步（rsync over ssh）

平台仓入口与统一安装契约见 [agent-platform](../README.md)。

`agents/` 运行时树的跨机同步链路：星型拓扑，hub = 中立目录 `dev:/data/shared/agents`（零原件），dev/nv1/nv2/mac 四机对等。服务名 `agents-sync`（`make agents-sync.start/.stop/.status`），常驻监督壳 = `loop.sh`，传输契约权威 = `ssh-sync.py` docstring + `@agentd#sync-channel`。

删除清单的路径、受保护资产与强制标记契约归 `delete_contract.py`；生成端与同步消费端引用同一实现。清理豁免族政策由生成端执行。

## 是什么

| 脚本 | 作用 |
|------|------|
| `ssh-sync.py` | rsync over ssh 目录同步：`push/pull/both` 一次性；`watch` 常驻双向链路（agents/ 跨机同步用，星型拓扑指向 `dev:/data/shared/agents`，见 `@agentd#sync-channel`） |
| `gc.py` / `test_gc.py` | agents/ 删除传播（gc 机制）及其单测 |
| `test_protect.py` | 本地属组不可读或原件路径无法安全编码时，整轮 pull 拒绝，避免保护清单缺项 |
| `test_watch.py` | `watch` 的触发模型、属组闸门传输不变量、同步面排除管道（生产排除面为空）与消费侧铁律的单测（合成树，只用 /tmp） |
| `replica-tag.py` / `test_replica_tag.py` | 副本标记工具（跨机同步的属组闸门辅助；纯手动一次性工具，不在 agents-sync 服务调用链内）及其单测。归属规则（各族/各文件的 owner = 哪台机器）权威 = 脚本 docstring：信封按 `from` 解宿主、共享信箱的命名空间 ack 按订阅者 `spec.host` 解、`enable.json` 归调度方、解不出一律 UNKNOWN = 不打标 |
| `mac-rsync-patch/` | mac rsync 补丁（子目录自带 README） |

## watch 轮次模型：整树协商 + 速率下限（同步面纪律）

`ssh-sync.py watch` 是一个循环：一轮 = 一次整树 push（白名单 = 本地未标记文件）+ 一次整树 pull（黑名单 = 本地未标记文件受保护），跳过判据 = rsync 自己的 `-c` 内容校验和。检测器只回答「有没有变更」：不记路径、不分类事件、不做快照、不参与覆盖裁决——一轮的成本与变更量无关，所以不需要候选集 / 完整性网 / 快照增量刷新这一整层机制。

轮次节奏三个参数（`loop.sh` 里显式传）：

+ `--debounce`（0.5s）：首事件窗口——首个未消费事件记时间戳、后续事件只加计数，故持续 churn 下仍保证「首事件后 ≥debounce 必跑一轮」（静默窗语义会饿死）；
+ `--min-cycle`（3s）：两轮之间的速率下限（一轮实测约 1s，见下）；
+ `--interval`（30s）：强制周期，即完整性上界——deadline 本身就是触发（在任何短路之前评估），树完全静默也照跑一轮。任何变更的重新锚定上界 = `interval`，与检测器是否失效无关（远端流断开、inotify 队列溢出、本机无事件后端——均只剩这个上界）。

三条结构性性质（都是循环形状本身的结果，不靠额外机制）：

1. **无基线可吞写**：没有快照就没有「写入落在基线里却从未传输」的启动对齐窗口；启动对齐传输期间的写入由第一轮普通周期拿到。
2. **失败无需重排**：每轮都重新协商整棵树，所以一轮失败只需退避到 `interval` 节奏，没有「候选集退回」这类待处理状态。
3. **空转静默**：搬了东西才打一行（搬运集取自 rsync -v 自己的输出，不是清单差集）；我自己 push 的回声（hub 事件 → pull 一轮 → 全部受保护）= 一轮空转、零日志。计数每 `--stats-every`（300s）汇总一行，告警/错误总是即时。

**实测基线**（dev，2026-09-12，树 11229 文件 / 187 MB、本机原件 550）：整树 pull 一轮 dry-run 0.78s（实传 55 KB）、整树 push 0.25s（白名单 735 条）；一对轮 ≈ 1.0–1.9s（含 ssh 往返）。据此 `--min-cycle 3` 的占空约 30–60%（持续 churn 时）、静默树约 3%；前向延迟典型 1–2s、最坏 = `interval` + 一轮。hub 作为 4 条链路的 pull 源，校验和读以页缓存为主。

**排障开关**：`SSH_SYNC_DEBUG=1` 每轮一行（触发源 + 两侧事件数 + 搬运数 + 耗时）。同步面排除无运行时开关（安全语义、非性能旋钮）：当前排除面为空（`WATCH_EXCLUDES = []`）——它此前唯一的模式 `*.pending`（receiver 两阶段送达的在飞记账件：只有写它的主机读它，跨机扩散零价值且会回灌「幽灵 pending」）随该记账件移进 receiver 进程内存而失去主体，盘上不再有「只属写者本机」的文件。排除管道保留（常量 + 两侧检测器过滤 + pull 的 `--exclude-from` 槽位）：重新加一条排除 = 改一个常量；后缀/glob 语义与深度无关匹配一字不变，`*.msg` 信封与终态 `ack/<id>` 照常同步。旧实现遗留的 `*.pending` 存量无人再读（无害）：receiver 见到对应终态 ack 时顺手清，其余走 `gc.py`。

**pull 侧的保护在 rsync 模式清单里**（`--exclude-from=-` = 锚定保护项 `/rel` + gc delete-list + 同步面排除（当前为空）），覆盖安全由属组闸门决定。另有一条后置修正：某些 rsync 构建（实测 brew 3.5.0）静默忽略 `--chown`，故 pull 后按 rsync -v 报的已传输集逐个 `chgrp` 打标；darwin 另需把已打标目录改回主组（macOS 新文件继承父目录属组，否则本机新产物出生即带标 → 被 push 白名单跳过 → 永远到不了 hub）。

检测后端：本机 inotify_simple（Linux）→ watchdog/FSEvents（macOS）→ 无（只剩 `--interval` 驱动）；远端 `ssh host python3 -c <inotify_simple 脚本>` 事件流（断流有界重连 5/10/30s，60s 健康运行后复位），远端无 python3+inotify_simple = 启动即拒（一行 ERROR + 修法；不静默降级）。两侧检测器都过滤 `WATCH_EXCLUDES` 名，故被排除记账件的 churn 不会唤醒循环。

启动闸门三道（任一失败拒启）：本机 `replica` 组可解析 · 本机 chgrp 功能自检 · 端到端 `--chown` 管道探针（真传一个探针文件到远端 scratch、核落地属组）。第三道在语义上覆盖「远端组存在 / 远端 chgrp 能力 / 两端 rsync 版本兼容」，故那三项只在探针失败后作为诊断跑（每项一行根因）。

细节 = `ssh-sync.py` docstring；单测 = `test_watch.py`（触发模型 / 传输不变量 / 消费侧铁律 / 检测器活体）。


## gc：清单是权威、schedule 只是计划（删除对账）

`gc.py` 是 hub 侧纯 CLI（add / reap / list）。`add` 是 schedule 的唯一写者：`run/gc/state.json`（hub 本地、gitignored、不同步）只是「哪些 hub 副本待删、何时到期」的计划缓存，`reap` 只执行到期的计划，因此删除面永远等于运营者显式授权过的集合。

state 丢失/重置的后果因此是有界的：`next_seq` 从磁盘清单续号（不与存活清单撞号），已列而未删的路径停在 hub 上成为残留（不是数据损失，也不会被再次接收——消费侧按清单永久拒收），处置 = 对这些路径重跑一次 `gc.py add`。`list` 末尾的 `unscheduled` 段只读报这个集合（不改 state、不起宽限时钟）。

三条硬边界：

+ 宽限 = `--delay`（缺省 300s）：清单要先到达每个节点，hub 副本才消失。唯一的复活向量是某节点在已列路径上持有未标记本机原件；hub 副本在场期间任何重推都无害，节点一旦应用清单就删掉自己的原件，窗口关闭。离线节点不需要宽限覆盖（离线期间不推不拉，回归后拉到累积清单即收敛）。
+ 清单文件自身的退役只由 `add` 的授权压缩路径排程（新清单列旧清单并排程它们）：清单内容——包括自称更高序号的伪造清单——无法把权威清单投票掉。
+ 删除必须指名到具体参与方目录（`task/<id>/`、`bot/<名字>/`、`topic/<id>/`）：裸族级容器 / 树根同形条目（`agents/`、`task/`、`bot/`、`topic/`、`run/`）一律拒，`--force` 也不解除——一条裸族级条目等于整族删除，而累积清单只追加会让它永久存续。旧清单里的这类 legacy 条目在读阶段被过滤，下一次 `add` 压缩时自动不进新清单（旧清单文件逐字不改写，append-only 不破）。

**消费侧同形拒绝**：`ssh-sync.py` 的 pull 侧按同一形状集拒绝（裸族级容器、`gc/` 自树、无审计标记的保护资产〔职位信箱两载体〕、绝对路径 / `..` / 通配符），因为消费侧信任清单、删除就发生在那里——存量 junk 或伪造条目若不在此拦住，一条 `agents/` 就等于四机各自 `rmtree` 整棵树。两类拒绝的 marker 语义不同：形状类永不放行（条目有效性）；保护资产类以 `#FORCED:` 审计标记为门——hub 侧 `gc.py add --force`（用户授权旁路）写的标记条目必须放行，否则 hub 删完、节点副本回推复活。形状权威 = `gc.py`（元组在 `ssh-sync.py` 逐字镜像：该进程不得 `import gc`，脚本形态下 `sys.path[0]` = `agents-sync/` 会遮蔽标准库同名模块）。拒绝告警在常驻侧按 300s 节流（一次一条汇总、带被抑制计数），短命 CLI 侧每次调用一行——两侧读清单的频率差三个量级。

**版本纪律口径**：`gc.py` 是 per-invocation CLI（无常驻进程面），不入 `agents-sync` 服务版本面——改动在下次调用即生效，不 bump 服务 profile 的 `version`、无需重启任何服务；该服务的版本面是常驻的 `ssh-sync.py watch`（消费侧改动属常驻面，要 bump + 各机重启）。口径与边界权威 = `gc.py` docstring。

## 常用命令

```
agents-sync/ssh-sync.py push ./a dev:/b --dry-run   # host 来自 env/.live/ssh-hosts
agents-sync/ssh-sync.py --help
python3 agents-sync/test_watch.py                   # 触发模型/闸门/排除管道/铁律单测（合成树，/tmp）
python3 agents-sync/gc.py list                      # 只读：清单 + schedule + 未排程残留（在 hub 上跑）
python3 agents-sync/test_gc.py                      # gc 机制单测（合成树，/tmp）
make agents-sync.start/.stop/.status                # 本机 agents/ 跨机同步链路常驻态
```

## 指针

- 部署面（服务定义、拓扑、属组闸门接入流程）：工作区的 `services/profiles/agents-sync.json` + 各机 `services/daemons/<机>.agents-sync.json`、`loop.sh`、`bootstrap/SETUP.md` §8
- 传输契约与文件协议：`@agentd#sync-channel`、`agentd/agent-file-protocol.md`
