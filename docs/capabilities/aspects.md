# 横切面（Aspects）

> 从零开始理解 libcore 的横切面：如何对所有流经总线的信号统一拦截（日志 / 护栏 / 追踪）。

## 1. 什么是横切面

**横切面（Aspect）** 是包围所有流经总线的信号的插件。它**不参与调度、不决定下一步**，只负责在信号投递前后统一拦截。

类比：电话总机的**录音**——它不决定电话该打给谁，但每一通都记录。

## 2. 横切面 SPI

```python
class Aspect(ABC):
    def matches(self, signal) -> bool: ...          # 过滤条件：只关心特定 target/topic
    async def before(self, signal) -> None: ...     # 投递前（护栏/日志/埋点）
    async def after(self, signal, result) -> None: ...  # 投递后（追踪/结果归一）
```

- `matches`：默认匹配全部信号，子类可只关心特定 target / topic。

- `before`：投递前阶段。若返回非 None（CapabilityResult），视为**护栏阻断**，跳过 handler。

- `after`：投递后阶段。

## 3. 拦截顺序

横切面按**挂载顺序**拦截：

- `before` 正序（先套先跑）

- `after` 逆序（后套先跑）

`aspects.yaml` 的列表顺序 = 拦截顺序。

## 4. 两类横切面

| 类型          | 阶段        | 例子                                      | 说明          |
| ----------- | --------- | --------------------------------------- | ----------- |
| **审查类（护栏）** | before 拒绝 | permission / circuit\_breaker / sandbox | 先拒绝，再让观察类记录 |
| **观察类**     | 记录        | logging / tracing / audit / telemetry   | 记录信号，不阻断    |

## 5. 内置横切面一览

| 横切面                  | 匹配                                         | 职责                                              |
| -------------------- | ------------------------------------------ | ----------------------------------------------- |
| **permission**       | 执行类信号（tool.\* / skill exec / mcp run）      | 权限闸门：拒绝危险命令                                     |
| **circuit\_breaker** | 执行类信号（tools run / mcp run）                 | 熔断：连续失败后短路                                      |
| **sandbox**          | 执行类信号                                      | 沙箱：把执行重定向到隔离子进程（受限环境）                           |
| **budget\_guard**    | `loop.iteration` 广播                        | 迭代预算：三级压力注入 + 100% 熔断                           |
| **loop\_governor**   | `loop.iteration` 广播                        | 死循环检测                                           |
| **trace\_recorder**  | `loop.iteration` / `loop.input_missing` 广播 | 循环追踪记录                                          |
| **logging**          | 全部                                         | 过程日志：调用链、参数摘要、耗时（排障诊断）                          |
| **tracing**          | 点名调度（Dispatch）                             | 过程轨迹：每步 span（含 parent\_cid 链路、耗时）               |
| **audit**            | 执行类信号                                      | 合规审计：who + what + outcome + SHA-256 hash 链（防篡改） |
| **telemetry**        | 全部                                         | 指标遥测：耗时、成功率、计数（趋势 / 告警）                         |

## 6. 配置驱动（唯一真相源）

`libcore/config/aspects.yaml` 是横切面的**唯一真相源**：

```yaml
aspects:
  - name: permission
    enabled: true
  - name: circuit_breaker
    enabled: true
    config:
      threshold: 3
  - name: sandbox
    enabled: true
    config:
      enabled: true   # 沙箱接管执行：把 bash/skill 放到隔离子进程跑（缺省 false = 不接管）
  - name: budget_guard
    enabled: true
    config:
      max_iterations: 25
  - name: loop_governor
    enabled: true
    config:
      threshold: 3
  - name: trace_recorder
    enabled: true
  - name: logging
    enabled: true
  - name: tracing
    enabled: true
  - name: audit
    enabled: true
  - name: telemetry
    enabled: true
```

- **列表顺序 = 拦截顺序**（无需 order / before / after / depends\_on 字段）。

- 每个条目可带 `config` 字典，作为关键字参数传给 `register(bus, **config)`（如 `threshold`、`max_iterations`、`allowlist`）。

- 改 `enabled` 保存即可，`AspectLoader.watch()` 自动重建横切管。

## 7. 加载器（AspectLoader）

`libcore/plugins/loader.py` 的 `AspectLoader` 与 `CapabilityLoader` 同构：

```python
loader = AspectLoader(bus, "libcore/plugins/aspects")
loader.load()          # 按配置顺序装配横切面
await loader.watch()   # 监听配置变化，重建横切管并广播 aspect.changed
```

### 顺序变化处理

`reconcile()` 检测到顺序变化时，会**重建横切管**：移除旧的、按新顺序重放。

## 8. 一个完整例子：权限闸门

```python
class PermissionAspect(Aspect):
    def matches(self, signal) -> bool:
        return is_exec_signal(signal)   # 只审执行类信号

    async def before(self, signal):
        text = _candidate_text(signal)  # 收集命令文本
        for token in DANGEROUS_TOKENS:
            if token in text:
                return CapabilityResult(ok=False, error=f"权限拒绝：{token!r}")
        return None  # 未命中规则 -> 放行

def register(bus) -> None:
    bus.add_aspect(PermissionAspect())
```

当 `before` 返回 `CapabilityResult(ok=False)` 时，总线**阻断 handler 不执行**，但仍走 `after` 让审计/遥测记到"被拒"这一笔。

## 9. 代码位置

- `libcore/plugins/aspects/`：横切面插件

- `libcore/config/aspects.yaml`：横切面配置唯一真相源

- `libcore/plugins/loader.py`：AspectLoader

