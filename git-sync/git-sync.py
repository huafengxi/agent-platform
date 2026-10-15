#!/usr/bin/env python3
"""git-sync — periodic code pull daemon for the ~/m workspace.

Every --interval seconds (default 60), for the ~/m main repo and every
discovered sub-repo:

  * dirty TRACKED entries (`git status --porcelain -uno -z` non-empty)
                                                       -> SKIP this round.
    Untracked files do not block `git pull --ff-only` (ignored ones are
    silently overwritten by git, non-ignored ones are left alone), so they are
    never inspected.
  * local unpushed commits (HEAD not reachable from origin) -> SKIP;
    if upstream is also ahead this is DIVERGED -> loud WARN.
  * otherwise `git pull --ff-only`; a non-ff failure can never auto-merge
    (defensive; with no local commits pull is always ff) -> WARN.

Every SKIP line carries the damage scale: `behind=N` = how many origin commits
this round failed to bring in (0 = nothing was missed, the local tree simply
has an edit in flight). A dirty worktree is the normal, self-healing state of a
multi-writer workspace, so it is logged and left alone.

Red lines: ff-only + clean-worktree checks only; never merge, never force,
never interrupt a machine mid-edit. Every outcome is exactly one log line in
run/logs/git-sync.log; a repo stuck in SKIP shows up as a repeating line whose
`behind=` tells whether it matters.

Sub-repo discovery (no list to maintain): top-level dirs of the workspace that
contain a `.git` AND are excluded by the main repo's `.gitignore` — a
registered sub-repo must be gitignored anyway, otherwise the main repo would
track it as a gitlink. `--repos` overrides. Clones absent on this machine are
simply not in the face (Makefile `repo-list` is the *clone* list and may
differ: it pulls from the mirror hub's ~/git mirrors, which do not host repos
whose remote is a private host).

Classification is PER CANDIDATE, and one odd entry can never collapse the face:
git refuses a pathspec beyond a symlink (rc 128), so a candidate whose slashed
form errors is re-asked unslashed (git sees a symlink as a file, hence only
slash-free patterns can match it), and a candidate that cannot be classified at
all is dropped with exactly one WARN line. A batch `check-ignore --stdin` cannot
be used here: its rc 128 on any single pathspec would hide every other
candidate's verdict (the silent `repos=['.']` degradation this replaces).

Mirror host only (DISCOVERED, never named — this repo carries no machine list):
on the machine whose mirror dir holds bare repos, every MIRROR_FETCH_EVERY rounds
a background thread refreshes them from their own upstream — the FETCH half of the
workspace's manual mirror reconciliation; the push-back half is event-driven via
the mirrors' post-receive hook (installed by the workspace's Makefile). Threaded + per-fetch timeout so
a hanging GitHub fetch can never delay pulls; if the previous round is still
running the new one is skipped (no overlap, no queueing). Mirror fetch runs
only in production mode (default --root, no --repos) so test sandboxes never
touch ~/git.

The mirror face needs no list either: it IS the set of bare repos under the
mirror dir (`mirror_repos()`), the same glob the workspace's mirror targets
iterate ⇒ adding a mirror = creating the bare repo, no second registry to keep
in step, and no machine name to hardcode.

Single instance: flock on <workspace>/run/locks/git-sync.lock (same pattern as
the other dsync daemons). Log goes to stdout; the Makefile redirects it to
run/logs/git-sync.log. Independent of the ssh-sync transport (different dirs
and locks) — the two services run in parallel without interaction.

Usage:
  git-sync.py [--interval N] [--once] [--root DIR] [--repos a,b,c]
              [--mirror-dir DIR]
              (--once = a single round; --root/--repos = testing;
               --mirror-dir = where the bare mirrors live, default ~/git)
"""

import argparse
import fcntl
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from platform_paths import workspace_root

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = workspace_root(__file__)

# mirror hub: fetch half of the workspace's mirror reconciliation
# (push-back = the mirrors' post-receive hook)
MIRROR_DIR = os.path.join(os.path.expanduser("~"), "git")
MIRROR_FETCH_EVERY = 10  # rounds (~10 min at the default interval)
MIRROR_FETCH_TIMEOUT = 120  # per mirror; a hung GitHub must not pile up

# informational fetches (SKIP rounds only, to report behind=): short leash so a
# unreachable remote cannot stretch a round; the clean path already pays a
# full `git pull` per round
INFO_FETCH_TIMEOUT = 60

# max dirty paths listed on one SKIP log line (rest summarized as +N)
DIRTY_LIST_LIMIT = 10


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def git(cwd, *args, timeout=300):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def host_id(root):
    """本机规范名：调用方工作区的 `env/host-id` 按 $(hostname) 查表，未命中/不可读
    回退 hostname（只用于日志行；本文件只此一处机器身份判据，不写死任何机器名）。"""
    import socket
    hn = socket.gethostname()
    try:
        with open(os.path.join(root, "env", "host-id"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower() == hn.lower():
                    return parts[1]
    except OSError:
        pass
    return hn


def _ignored(root, name):
    """`name` 是否被主仓 .gitignore 排除：True/False；无法分类 → None。

    先问尾斜杠形态（目录型 pattern 如 `ido/`、`scratch/*` 只对带斜杠的路径生效）；
    git 对「符号链接之后」的 pathspec 直接 fatal（rc=128：顶层某个指向工作区外的
    链接里另有 .git 时即此形态）⇒ 退问不带斜杠的 name（git 眼里符号链接是文件，
    只有无斜杠的 pattern 能匹配它）。两问都失败 = 无法分类。
    """
    for pathspec in (name + "/", name):
        p = subprocess.run(["git", "check-ignore", "-q", pathspec], cwd=root,
                           capture_output=True, text=True)
        if p.returncode in (0, 1):  # 0=命中, 1=未命中
            return p.returncode == 0
    return None


def discover_repos(root):
    """同步面 = 主仓 "." + 自动发现的顶层子仓（含 .git ∧ 被主仓 .gitignore 排除）。
    发现失败（列目录/git 不可用）→ 只同步主仓。分类**逐枚**做：无法分类的候选只记一行
    WARN 并排除它自己 —— 一枚坏候选 ⛔ 不得把整张面打回「只同步主仓」（那是静默降级：
    子仓全部退出同步面，日志里只有启动行的 repos=['.'] 可看出端倪）。"""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ["."]
    cands = [n for n in names if os.path.exists(os.path.join(root, n, ".git"))]
    out = ["."]
    for n in cands:
        verdict = _ignored(root, n)
        if verdict is None:
            log(f"[{n}] WARN 候选无法分类（git check-ignore 两问都失败）⇒ 不入同步面")
        elif verdict:
            out.append(n)
    return out


def dirty_tracked_entries(path):
    """`git status --porcelain -uno -z` → [(code, relpath)]（只路径，零 diff 内容）。
    -z 免引号/转义歧义；重命名/复制记录形如 `R  new\\0old\\0`，取 new 并跳过源记录。
    返回 (entries, err)；err 非空 = git 失败，entries=None。"""
    r = git(path, "status", "--porcelain", "-uno", "-z")
    if r.returncode != 0:
        return None, r.stderr.strip()
    out, recs, i = [], r.stdout.split("\0"), 0
    while i < len(recs):
        rec = recs[i]
        if len(rec) < 4:
            i += 1
            continue
        code, rel = rec[:2], rec[3:]
        i += 2 if code[0] in ("R", "C") else 1  # 跳过重命名/复制的源路径记录
        out.append((code.strip(), rel))
    return out, ""


def _count(path, *args):
    r = git(path, *args)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return None


def ahead_behind(path, fetch=False):
    """(ahead, behind) = 本机未推送提交数 / origin 领先本机的提交数；不可得为 None。

    fetch=True 时先做一次 best-effort fetch：跳过拉取的轮次（脏树/本地领先）否则
    只能读陈旧的 remote-tracking refs——SKIP 久了会低估 behind，而刚 push 完的仓会
    多报一轮 ahead。"""
    if fetch:
        try:
            git(path, "fetch", "-q", "origin", timeout=INFO_FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, None
    ahead = _count(path, "rev-list", "--count", "HEAD", "--not", "--remotes=origin")
    u = git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    behind = None if u.returncode != 0 else _count(path, "rev-list", "--count",
                                                   "HEAD.." + u.stdout.strip())
    return ahead, behind


def sync_repo(root, name):
    """One sync round for one repo. Never raises. Returns the round kind:

      "skip_dirty" / "skip_ahead" / "skip_diverged" -> not pulled this round;
      "ok"    -> pulled (or already up to date);
      "other" -> absent clone, or a transient git failure (reported as WARN).

    Each kind emits exactly one log line."""
    path = os.path.join(root, name) if name != "." else root
    label = "(main)" if name == "." else name
    try:
        if not os.path.exists(os.path.join(path, ".git")):
            return "other"  # absent clone: not part of this machine's face
        entries, err = dirty_tracked_entries(path)
        if entries is None:
            log(f"[{label}] WARN git status failed: {err}")
            return "other"
        if entries:
            shown = "; ".join(f"{c} {p}" for c, p in entries[:DIRTY_LIST_LIMIT])
            more = f"; +{len(entries) - DIRTY_LIST_LIMIT} more" \
                if len(entries) > DIRTY_LIST_LIMIT else ""
            behind = ahead_behind(path, fetch=True)[1]
            log(f"[{label}] SKIP dirty worktree ({len(entries)} tracked entries, "
                f"behind={'?' if behind is None else behind}) | {shown}{more}")
            return "skip_dirty"
        ahead = ahead_behind(path)[0]  # 不 fetch：干净轮的 refs 至多陈旧一轮，下一行就 pull
        if ahead is None:
            log(f"[{label}] WARN rev-list failed (no origin?)")
            return "other"
        if ahead > 0:
            ahead, behind = ahead_behind(path, fetch=True)  # 报准再判分叉
            ahead = "?" if ahead is None else ahead
            if behind:
                log(f"[{label}] WARN DIVERGED (ahead={ahead} behind={behind}): "
                    f"local commits not pushed AND remote moved; SKIP, no "
                    f"auto-merge — resolve manually")
                return "skip_diverged"
            log(f"[{label}] SKIP local ahead by {ahead} unpushed commit(s)")
            return "skip_ahead"
        before = git(path, "rev-parse", "HEAD").stdout.strip()
        r = git(path, "pull", "--ff-only")
        if r.returncode != 0:
            # defensive: with ahead==0 pull should always be ff
            log(f"[{label}] WARN pull --ff-only failed (non-ff or error); NOT merging: "
                f"{' '.join((r.stderr or r.stdout).strip().splitlines()[:2])}")
            return "other"
        after = git(path, "rev-parse", "HEAD").stdout.strip()
        if after != before:
            log(f"[{label}] ff updated {before[:9]} -> {after[:9]}")
        return "ok"
    except Exception as e:  # noqa: BLE001 — one repo must never kill the loop
        log(f"[{label}] ERROR {type(e).__name__}: {e}")
        return "other"


def is_mirror_host(gitdir=MIRROR_DIR):
    """Whether THIS machine hosts the mirror face — discovered, never named:
    it hosts mirrors iff the mirror dir holds at least one bare repo."""
    return bool(mirror_repos(gitdir))


def mirror_repos(gitdir=MIRROR_DIR):
    """The mirror face = every bare repo under `gitdir` (`<name>.git` that is a
    directory). No list to maintain: the same glob is what `make
    git-mirror.sync` / `make git-mirror.hooks` iterate on the mirror host."""
    if not os.path.isdir(gitdir):
        return []
    return sorted(d[:-len(".git")] for d in os.listdir(gitdir)
                  if d.endswith(".git") and os.path.isdir(os.path.join(gitdir, d)))


def mirror_fetch(gitdir=MIRROR_DIR):
    """Mirror host only: fetch half of the mirror reconciliation
    (upstream -> mirrors). Returns the number of warnings (one log line each)."""
    warns = 0
    for repo in mirror_repos(gitdir):
        d = os.path.join(gitdir, repo + ".git")
        try:
            r = git(d, "fetch", "origin", "refs/heads/*:refs/heads/*",
                    "refs/tags/*:refs/tags/*", timeout=MIRROR_FETCH_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — one mirror must never kill the round
            log(f"[mirror:{repo}] WARN fetch exception ({type(e).__name__}): "
                f"{' '.join(str(e).splitlines()[:2])}")
            warns += 1
            continue
        if r.returncode != 0:
            out = (r.stderr or r.stdout).strip()
            log(f"[mirror:{repo}] WARN fetch (non-ff or error): "
                f"{' '.join(out.splitlines()[:2])}")
            warns += 1
    return warns


_MIRROR_LOCK = threading.Lock()


def start_mirror_fetch(gitdir=MIRROR_DIR):
    """把一轮镜像 fetch 放到后台线程：GitHub 挂起绝不拖延拉取；上一轮未完则本轮
    跳过（不重叠、不排队）。返回线程对象（--once 时 join 用）。"""
    if not _MIRROR_LOCK.acquire(blocking=False):
        log("[mirror] previous fetch round still running; skipping this round")
        return None

    def run():
        t0 = time.time()
        try:
            warns = mirror_fetch(gitdir)
            log(f"[mirror] fetch round done in {time.time() - t0:.1f}s "
                f"({len(mirror_repos(gitdir))} mirrors, {warns} warn)")
        finally:
            _MIRROR_LOCK.release()

    thread = threading.Thread(target=run, name="mirror-fetch", daemon=True)
    thread.start()
    return thread


def acquire_lock(root):
    lockdir = os.path.join(root, "run", "locks")
    os.makedirs(lockdir, exist_ok=True)
    fh = open(os.path.join(lockdir, "git-sync.lock"), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another git-sync instance holds run/locks/git-sync.lock; exiting.")
        sys.exit(1)
    return fh


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=60, help="seconds between rounds (default 60)")
    ap.add_argument("--once", action="store_true", help="run a single round and exit")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="workspace root (default: ~/m)")
    ap.add_argument("--repos", default=None,
                    help="comma-separated repo dirs relative to --root "
                         "(default: auto-discovered sub-repos + main repo)")
    ap.add_argument("--mirror-dir", default=MIRROR_DIR,
                    help="directory holding the bare mirrors (default ~/git; "
                         "only read on the mirror host, in production mode)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, ".git")):
        log(f"workspace root {root} is not a git checkout; nothing to do, exiting.")
        sys.exit(1)
    repos = args.repos.split(",") if args.repos else discover_repos(root)
    hid = host_id(root)
    # mirror fetch is the mirror host's production chore; skip it in test mode
    # (explicit --root/--repos) so sandbox runs neither touch real mirrors nor
    # block on their (sometimes slow) upstream fetches
    do_mirror = (is_mirror_host(args.mirror_dir) and root == DEFAULT_ROOT
                 and args.repos is None)

    lock_fh = acquire_lock(root)  # noqa: F841 (held for process lifetime)
    log(f"git-sync started (root={root}, host={hid}, repos={repos}, "
        f"interval={args.interval}s, mirror={'yes' if do_mirror else 'no'})")

    round_no = 0
    while True:
        round_no += 1
        for name in repos:
            sync_repo(root, name)
        if do_mirror and round_no % MIRROR_FETCH_EVERY == 1:
            th = start_mirror_fetch(args.mirror_dir)
            if args.once and th:
                th.join()  # 单轮模式：别把在飞的镜像 fetch 截断
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
