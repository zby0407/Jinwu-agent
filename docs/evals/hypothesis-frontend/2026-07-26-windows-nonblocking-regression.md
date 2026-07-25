# Windows 异步 workspace cache 非阻塞回归记录

## 范围

- 基底：`origin/main` / 当前 `HEAD` 均为 `37a2088`（核验时 ahead/behind 为 `0/0`）。
- 目标：证明已预热的 workspace binding 在异步事件循环中只进行内存缓存查询，不触发同步路径解析或全局放宽 BlockBuster。

## RED：已发生的回归失败

执行命令：

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest tests/test_workspaces.py -k "cached_binding and nonblocking" -vv
```

结果：`1 failed, 12 deselected`。失败为 `blockbuster.blockbuster.BlockingError: Blocking call to os.path.abspath`。

调用链：`get_cached_binding()` → `_binding_cache_key()` → `Path(base_workspace).expanduser().resolve()` → `os.path.abspath`。因此问题位于缓存键构造，而不是需要通过 `--allow-blocking` 放宽运行时保护。

## GREEN：修复后的验证

缓存键改为纯词法处理：`os.fspath` / `os.fsdecode`、绝对路径边界、`os.path.normpath` 与 `os.path.normcase`。所有缓存写入和读取共用该键；相对路径会被明确拒绝。

这里验证的是：预热后的事件循环查询不再执行同步文件系统调用。缓存读写使用独立短临界区，不与 registry 文件 I/O 共用锁；但这不是“完全无锁”或对调度等待时间的证明。查询入口只接受在事件循环外已解析 symlink 与相对组件的 canonical absolute base。

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest tests/test_workspaces.py -k "cached_binding_lookup" -q
```

结果：`4 passed, 3 skipped, 12 deselected`。三项 skip 是仅在 Windows 解释器上执行的 root-relative、drive-relative 与 UNC 路径语义检查。

审查还发现 worker-thread 工具入口可能收到相对 `base_workspace`。新增测试在修复前以 `ValueError: resolved base workspace must be an absolute path` 失败；随后把 `binding_from_config()` 明确作为同步边界，在调用缓存读取前完成一次 `expanduser().resolve()`：

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest tests/test_workspaces.py -k "binding_from_config_resolves_relative_worker_workspace" -q
```

结果：`1 passed, 19 deselected`。

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest tests/test_workspaces.py tests/test_backends.py -q
```

结果：`185 passed, 3 skipped`。

这些结果证明了该缓存查询路径和相邻 backend 回归通过；不等同于 Windows 前后端启动或真实模型执行已完成。
