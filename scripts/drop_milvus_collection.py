"""一次性删除 Milvus collection（度量方式 L2 -> COSINE 迁移用）。

背景：Milvus 的 metric_type 绑定在索引上，无法原地修改。切换到 COSINE 后，
需要删掉旧的 L2 collection，应用下次启动时 `ensure_collection()` 会按新配置
（configs/config.yaml 的 metric_type=COSINE）重新建库建索引。

⚠️ 破坏性操作：会清空该 collection 的所有向量数据。删除后所有已入库文件都需要
重新跑 embedding 阶段（POST /file/{id}/retry/embedding，或删除后重新上传）。

用法：
    uv run python scripts/drop_milvus_collection.py
"""

from __future__ import annotations

from pymilvus import connections, utility

from utils.config import get_config


def main() -> None:
    cfg = get_config().milvus
    name = cfg.collection_name

    connect_kwargs = {"alias": "default", "host": cfg.host, "port": cfg.port}
    if cfg.user:
        connect_kwargs["user"] = cfg.user
    if cfg.password:
        connect_kwargs["password"] = cfg.password
    connections.connect(**connect_kwargs)

    if not utility.has_collection(name):
        print(f"collection '{name}' 不存在，无需删除。")
        return

    utility.drop_collection(name)
    print(f"已删除 collection '{name}'。")
    print("下一步：重启应用（ensure_collection 会用 COSINE 重建空库），")
    print("      然后对已有文件重跑 embedding：POST /file/{id}/retry/embedding。")


if __name__ == "__main__":
    main()
