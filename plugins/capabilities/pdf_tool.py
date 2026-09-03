"""示例插件：解析 PDF 文本。暴露 register(bus)，由加载器自动调用登记。"""
from libcore.core.event import Dispatch, CapabilityResult

# 盘点用：loader.scan() 把它当默认描述（可被配置里 description 覆盖）
DESCRIPTION = "解析 PDF 提取文本与分块"


def register(bus) -> None:
    def handle(d: Dispatch) -> CapabilityResult:
        print(f"  > [插件] tool.pdf.parse  file={d.payload.get('file')}")
        return CapabilityResult(ok=True, data={"pages": 300, "chunks": 42})

    bus.on("tool.pdf", handle, meta={
        "description": "解析 PDF 提取文本与分块",
        "ops": ["run"],
    })