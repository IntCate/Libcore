***

name: deploy-k8s
description: 在本地 minikube/cluster 上用 kubectl 部署一个 YAML 清单并等待就绪。
---------------------------------------------------------------

# Deploy to Kubernetes

把给定 K8s YAML 清单应用到当前集群，并等待 Deployment 就绪。

## 何时使用

- 需要把一份 Kubernetes 清单部署到默认 context 时。

- 需要校验清单是否已成功 rollout。

## 操作步骤

1. 确认目标集群 context（默认使用当前 kubeconfig 的 current-context）。
2. 将清单内容写入临时文件并执行 `kubectl apply -f <file>`。
3. 对 Deployment 执行 `kubectl rollout status` 等待就绪。

## 约束

- 只允许部署到用户的 `devops` namespace；禁止生产 namespace。

- 执行前必须把目标 namespace 写入清单/参数，缺省拒绝。

