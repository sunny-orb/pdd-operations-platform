# 业务模块边界

按 overview、sales、products、promotion、aftersales 分业务域实施。每个域按实际需要包含契约、服务和仓储；不提前生成空的多层目录。

路由不写核心公式，仓储不生成建议文本，连接器不处理界面状态。`products/ingestion.py`处理原值规范化，`ingestion_repository.py`处理PG暂存与事务发布；来源字段契约及实际结构以根目录docs为准。
