# git-sync/ — 工作区多仓的周期性拉取守护（ff-only）

平台仓入口与统一安装契约见 [agent-platform](../README.md)。

## 是什么

工作区（一个主仓 + 若干子仓）的周期性代码拉取守护：每 `--interval` 秒（缺省 60）对每个仓做 `git pull --ff-only`。判定序、日志形态与红线的权威 = `git-sync.py` 的 docstring（不复述）。

- **同步面自动发现**：顶层含 `.git` 且被主仓 `.gitignore` 排除的目录即入面（登记的子仓必然要加 ignore 条目，否则主仓会把它当 gitlink ⇒ 名单自维护，无需与工作区的 clone 名单对齐）；`--repos` 覆盖。
- **输出面只有日志**：一条结果一行（`run/logs/git-sync.log` 由调用方重定向），不写任何协议文件、不读 `pid.json`、与 agent 树零耦合。
- **镜像保鲜（仅镜像宿主机）**：宿主机判据 = `is_mirror_host()`（`--mirror-dir` 下有 bare 仓即是 ⇒ 代码里不写机器名）；镜面 = 该目录下的全部 bare 仓（`*.git` 目录，`mirror_repos()`）⇒ 无名单可漂移，加镜像 = 建 bare 仓。回推上游是另一回事（事件驱动，见下）。fetch 走后台线程 + 每镜像超时，挂起的远端不拖延拉取；上一轮未完则本轮跳过（不重叠、不排队）。只在生产模式跑（缺省 `--root`、未给 `--repos`）⇒ 测试沙箱绝不触碰真实镜面。

## 用法

```bash
git-sync.py                     # 常驻（由调用方的服务层监督；日志重定向到调用方指定处）
git-sync.py --once              # 单轮
git-sync.py --root DIR --repos a,b   # 测试模式（不做镜像 fetch）
git-sync.py --mirror-dir DIR    # 镜面目录（缺省 ~/git）
python3 test_git_sync.py        # 单测：11 组，/tmp 沙箱，不写真实工作区
```

单实例：`<workspace>/run/locks/git-sync.lock` 上的 flock。

## 部署形态（本工作区）

- 服务声明（`cmd`/`match`/`version` 住 profile，期望态住逐机 daemon 声明）住工作区的 `services/`，启停经 `make git-sync.start/.stop/.status`，执行层 = `serviced/serviced.py`；本模块不含任何服务定义。
- **发现面自身的补丁不能自送**：某机的发现面已降级（启动行 `repos=['.']`）时，本模块的新代码不在它的同步面内 ⇒ 该机须人工 `git -C <ws>/git-sync pull --ff-only` 一次再 `make git-sync.stop && make git-sync.start`（profile `version` 未变 ⇒ 常驻纠偏不会替它重启）。
- 镜像宿主机（本工作区 = `dev`；机器身份取自工作区 `env/host-id` 映射，只用于日志行，不参与判定）上，回推上游由每个 bare 镜像的 `post-receive` 钩子承担（钩子脚本住工作区 `bootstrap/git-mirror-post-receive.sh`，`make git-mirror.hooks` 安装；严格 ff-only，非 ff 只 WARN 不 force）；人工全量对账 = `make git-mirror.sync`。
