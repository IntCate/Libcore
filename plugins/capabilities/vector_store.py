"""示例插件：向量化入库。暴露 register(bus)，由加载器自动调用登记。"""
from libcore.core.event import Dispatch, CapabilityResult

# 盘点用：loader.scan() 把它当默认描述（可被配置里 description 覆盖）
DESCRIPTION = "向量化入库文档"


def register(bus) -> None:
    def handle(d: Dispatch) -> CapabilityResult:
        print(f"  > [插件] storage.vector.upsert  index={d.payload.get('index')}")
        return CapabilityResult(ok=True, data={"stored": True})

    bus.on("storage.vector", handle, meta={
        "description": "向量化入库文档",
        "ops": ["run"],
    })