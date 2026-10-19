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
| `test_pull_manifest.py` / `bench_pull_manifest.py` | 大保护集、精确清单及真实 rsync 边界回归；22 万原件合成树性能实验（仅自建临时树） |
| `test_watch.py` | `watch` 的触发模型、属组闸门传输不变量、同步面排除管道（生产排除面为空）与消费侧铁律的单测（合成树，只用 /tmp） |
| `replica-tag.py` / `test_replica_tag.py` | 副本标记工具（跨机同步的属组闸门辅助；纯手动一次性工具，不在 agents-sync 服务调用链内）及其单测。归属规则（各族/各文件的 owner = 哪台机器）权威 = 脚本 docstring：信封按 `from` 解宿主、共享信箱的命名空间 ack 按订阅者 `spec.host` 解、`enable.json` 归调度方、解不出一律 UNKNOWN = 不打标 |
| `mac-rsync-patch/` | mac rsync 补丁（子目录自带 README） |

## watch 轮次模型：整树协商 + 速率下限（同步面纪律）

`ssh-sync.py watch` 是一个循环：一轮 = 一次整树 push（白名单 = 本地未标记普通文件）+ 一次整树 pull（每轮重新取 hub 全树清单，过滤本地原件、GC 和排除项后精确接收），跳过判据 = rsync 自己的 `-c` 内容校验和。检测器只回答「有没有变更」：不记路径、不分类事件、不做快照、不参与覆盖裁决——每轮重新扫描全树，不保存候选集 / 基线 / 路径账本，也不靠扩大超时或排除业务目录降低成本。

轮次节奏三个参数（`loop.sh` 里显式传）：

+ `--debounce`（0.5s）：首事件窗口——首个未消费事件记时间戳、后续事件只加计数，故持续 churn 下仍保证「首事件后 ≥debounce 必跑一轮」（静默窗语义会饿死）；
+ `--min-cycle`（3s）：两轮之间的速率下限（轮次实际耗时随树规模变化，见下）；
+ `--interval`（30s）：强制周期——deadline 本身就是触发（在任何短路之前评估），树完全静默也照跑一轮。不在传输中的调度等待上界 = `interval`；完整重锚定还需计入在飞轮次耗时，与检测器是否失效无关（远端流断开、inotify 队列溢出、本机无事件后端均由周期兜底）。

三条结构性性质（都是循环形状本身的结果，不靠额外机制）：

1. **无基线可吞写**：没有快照就没有「写入落在基线里却从未传输」的启动对齐窗口；启动对齐传输期间的写入由第一轮普通周期拿到。
2. **失败无需重排**：每轮都重新协商整棵树，所以一轮失败只需退避到 `interval` 节奏，没有「候选集退回」这类待处理状态。
3. **空转静默**：搬了东西才打一行（搬运集取自 rsync -v 自己的输出，不是清单差集）；我自己 push 的回声（hub 事件 → pull 一轮 → 全部受保护）= 一轮空转、零日志。计数每 `--stats-every`（300s）汇总一行，告警/错误总是即时。

**性能口径**：旧小树基线（dev，2026-09-12，11229 文件 / 187 MB、本机原件 550）一对轮约 1.0–1.9s，不能外推到几十万原件。旧 pull 为每个原件生成一条 rsync exclude glob，sender 每个条目都可能遍历这组规则，形成 N×M 成本。2026-10-19 dev 合成对照：固定 3000 sender 文件，旧 rsync dry-run 在 1万 / 5万 / 22万保护规则下耗时 0.356 / 3.022 / 8.456s；新 hash 筛选同规模清单为 0.009 / 0.012 / 0.062s（仅筛选成本，不是端到端传输加速比）。真实本地 rsync 合成树含 220000 本机原件、223000 sender 文件和 2201 个 sender 目录：首次完整 pull 4.90s、传 3000 新文件；次轮 4.64s、传 0 文件，其中 rsync 分别仅 0.225 / 0.119s。本机扫描、hub 全清单、路径筛选和 checksum 成本仍存在；夹具使用分批硬链接降低磁盘占用，SSH 端点以本地执行替代，未含跨机网络与真实大文件读成本；本地源到本地目的的 rsync 也不触发远端 sender 文件清单校验。该校验会把 `--files-from` 展开为隐含 include/filter，再逐项匹配，大量目录和深层路径仍可能产生 N×M 成本；原件 hash 筛选消除的是另一层规则遍历，不能据本地合成数据断言远端接收已线性化。复现入口 = `bench_pull_manifest.py`；远端校验成本须用实际 SSH sender 路径测量。前向完整性延迟还包含在飞轮次耗时；`interval` 是强制调度节奏，不会中断正在执行的轮次。

**排障开关**：`SSH_SYNC_DEBUG=1` 每轮一行（触发源 + 两侧事件数 + 搬运数 + 耗时）。同步面排除无运行时开关（安全语义、非性能旋钮）：当前排除面为空（`WATCH_EXCLUDES = []`）——它此前唯一的模式 `*.pending`（receiver 两阶段送达的在飞记账件：只有写它的主机读它，跨机扩散零价值且会回灌「幽灵 pending」）随该记账件移进 receiver 进程内存而失去主体，该类在飞账本不再参与文件同步。`agentd/proto.py` 原子写的 `.tmp-<pid>-<rand>` 临时文件仍处于当前整树扫描面内，不能由排除面为空推导所有盘上文件均为已发布产物。排除管道保留（常量 + 两侧检测器过滤 + pull 的清单祖先过滤）：重新加一条排除 = 改一个常量；后缀/glob 语义与深度无关匹配一字不变，`*.msg` 信封与终态 `ack/<id>` 照常同步。旧实现遗留的 `*.pending` 存量无人再读（无害）：receiver 见到对应终态 ack 时顺手清，其余走 `gc.py`。

**pull 精确清单与保护边界**：

1. 每轮用 SSH `python3` / `scandir` 遍历 hub 全树，输出 NUL 分隔清单；包含目录（含空目录）、普通文件、symlink 和特殊文件，但不跟随 symlink。SSH/扫描非零、超时或不完整清单一律整轮失败，不拿部分结果传输。
2. 本地重新扫描属组：未标记的普通文件、symlink（含目录链接）和特殊文件都是原件；真实目录不带原件闸门状态。扫描/属组失败则整轮拒绝，仅本地已消失文件可跳过。原件和 GC 条目用 hash 集合，按每个远端路径的祖先查找：原件在 sender 恰为目录时，其整个子树也不接收；GC 目录前缀及同名类型冲突也不接收。`WATCH_EXCLUDES` 的后缀/glob 按路径或 basename 任意深度匹配，被排除目录的子树不传。复杂度由路径长度和排序决定，不再按原件数 × sender 条目数匹配 glob。
3. 只向 rsync 送 `--files-from=- --from0 --no-recursive --dirs` 的精确相对路径；**目录名不带尾斜杠**（`dir/` 即使在 no-recursive 下也会展开直接子项）。未列目录不能递归绕过过滤。保留 `-c`、`--chown=:replica`、无 `--delete`；空清单也执行成功轮次后的 GC 对账。
4. 传输清单与原件 hash 保护中的 `*?[]` 按字面路径处理，不作模式匹配；历史信封/ack 中的方括号无需改名或排除。此边界与 GC 删除授权独立，GC 的 `GLOB_UNSAFE` 禁名契约不变。不做路径规范化：绝对路径、空/`.`/`..` 分量、分量首尾空白、反斜杠、不可打印字符（含 newline、Unicode 行分隔符）和非 UTF-8 名字均整轮拒绝，避免清单寻址或落地打标歧义。GC 条目有效性仍只按 `delete_contract.py` 裁定；pull 前不可读清单使该轮失败（缺少整个 gc/ 目录视为空）。新收到的删除清单仍在成功传输后重读、执行；`#FORCED:` 和保护资产边界不变。
5. sender 文件在扫描后消失等导致 rsync 非零，该轮失败且不执行 GC，下一轮完整重锚定，无候选状态要修复。两次扫描不构成文件系统快照，原有单写者/原子 rename 协议与下轮重试约束不变。

**落地打标**：某些 rsync 构建（实测 brew 3.5.0）静默忽略 `--chown`。pull 使用带固定前缀的 `--out-format` 和 `--8-bit-output`，只认实际传输记录，不把诊断行或 symlink 目标文本当文件名；随后对实际传输的非目录项以 no-follow `chgrp` 打 replica 标，不跟随 symlink 目标。darwin 另需把已打标目录改回主组（macOS 新文件继承父目录属组，否则本机新产物出生即带标 → 被 push 白名单跳过 → 永远到不了 hub）。push 和 one-shot 接口不变。

检测后端：本机 inotify_simple（Linux）→ watchdog/FSEvents（macOS）→ 无（只剩 `--interval` 驱动）；远端 `ssh host python3 -c <inotify_simple 脚本>` 事件流（断流有界重连 5/10/30s，60s 健康运行后复位），远端无 python3+inotify_simple = 启动即拒（一行 ERROR + 修法；不静默降级）。两侧检测器都过滤 `WATCH_EXCLUDES` 名，故被排除记账件的 churn 不会唤醒循环。

启动闸门三道（任一失败拒启）：本机 `replica` 组可解析 · 本机 chgrp 功能自检 · 端到端 `--chown` 管道探针（真传一个探针文件到远端 scratch、核落地属组）。第三道在语义上覆盖「远端组存在 / 远端 chgrp 能力 / 两端 rsync 版本兼容」，故那三项只在探针失败后作为诊断跑（每项一行根因）。

细节 = `ssh-sync.py` docstring；单测 = `test_watch.py`（触发模型 / 传输不变量 / 消费侧铁律 / 检测器活体）、`test_pull_manifest.py`（大保护清单 / 真实 rsync 字面 glob 名传输、精确原件保护、replica 打标及不递归 / fail-closed）及 `test_delete_contract.py`（GC glob 禁名不随传输放宽，含强制条目）。inotify ENOSPC 是用户 watch 配额耗尽，不是磁盘满；检测器无法覆盖时，完整性仍由周期全树扫描保证。测试会显式跳过无配额的活体检测器，不调 sysctl、不影响现网 watches。


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
python3 agents-sync/test_pull_manifest.py           # 大保护集及真实 rsync 精确清单边界（/tmp）
python3 agents-sync/bench_pull_manifest.py          # 22 万原件/22.3 万 sender 文件性能实验（/tmp）
python3 agents-sync/gc.py list                      # 只读：清单 + schedule + 未排程残留（在 hub 上跑）
python3 agents-sync/test_gc.py                      # gc 机制单测（合成树，/tmp）
make agents-sync.start/.stop/.status                # 本机 agents/ 跨机同步链路常驻态
```

## 指针

- 部署面（服务定义、拓扑、属组闸门接入流程）：工作区的 `services/profiles/agents-sync.json` + 各机 `services/daemons/<机>.agents-sync.json`、`loop.sh`、`bootstrap/SETUP.md` §8
- 传输契约与文件协议：`@agentd#sync-channel`、`agentd/agent-file-protocol.md`
