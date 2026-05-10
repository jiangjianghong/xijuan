"""VL 端到端抽取方法包。

三种方法：
- vl_model_extract: 全量模式
- vl_progressive_extract: 逐批扫描
- vl_locate_extract: 缩略图定位 + 高清提取
"""

from service.vl_service.locate import vl_locate_extract
from service.vl_service.model import vl_model_extract
from service.vl_service.progressive import vl_progressive_extract

__all__ = [
    "vl_model_extract",
    "vl_progressive_extract",
    "vl_locate_extract",
]
