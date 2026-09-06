"""deploy-k8s 技能的辅助脚本：生成一份 K8s Deployment 清单（纯演示，不真正执行 kubectl）。

约定：暴露 run(args) -> dict，由 engine.skill exec 进程内调用。
"""


def run(args: dict) -> dict:
    name = args.get("name") or "demo"
    image = args.get("image") or "nginx:1.27"
    replicas = int(args.get("replicas") or 3)
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "devops"},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {"containers": [{"name": name, "image": image}]},
            },
        },
    }
    return {
        "manifest": manifest,
        "hint": "把该清单写入临时文件后执行 kubectl apply -f <file>",
    }