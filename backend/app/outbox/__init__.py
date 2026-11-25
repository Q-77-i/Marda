"""事件与 outbox（P5-J5）：报告收尾事件、outbox relay、DLQ 工具。

边界：Kafka **不在面试请求路径**——finalize 只把事件写进本地 outbox，relay 独立进程发布。
"""
