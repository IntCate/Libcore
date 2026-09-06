"""libroce 已更名为 libcore —— 此文件为 libcore 的横切面插件包。

libcore.plugins.aspects —— 横切面插件（拦截信号的"截面"）。

与能力插件（capabilities/）**不是一个维度**：
- 能力  = 可被点名的"节点"（横向铺开，进总线点名表）;
- 横切面 = 包住流经总线的信号的"管道拦截器"（纵向切过总线，进横切管）。

统一机制（与能力一致）：
- 插件契约唯一：模块暴露 ``register(bus)``，内部 ``bus.add_aspect(instance)``；
- 由统一加载器（``plugins.loader.AspectLoader``）扫描装配 + ``aspects.yaml`` 驱动热更新；
- 顺序 = ``aspects.yaml`` 里的列表顺序（装配顺序即拦截顺序，before 正序 / after 逆序）；
- 每个横切面用 ``Aspect.matches(signal)`` 声明"我管哪类信号"
  （如沙箱只匹配 ``tool.*``），无生效的 add_on 依赖、无 order 字段。

如何新增一个横切面：
1. 在本目录新建 ``<name>.py``：
   ``class X(Aspect): ...`` + ``def register(bus): bus.add_aspect(X())``；
2. 在 ``aspects.yaml`` 里登记一行（位置 = 拦截顺序）。
"""