# 页码映射大面积错位问题复盘(2026-07)

> 涉及代码:`utils/page_mapping.py`(定位算法)、`service/mineru_client.py`(MinerU 调用)、
> `service/parse_service.py` / `service/pipeline_service.py`(管线接线)。
> 修复分支:`feat/content-list-page-mapping`。

## 1. 问题现象

一份 436 页的扫描件 PDF(多份材料合并装订:项目正文 + 大量佐证扫描件),抽取时注入
LLM 的检索结果页码大面积错误:

- 搜索词「村集体经济收入的」共 12 处命中,其中 10 处是各村《情况说明》正文
  (真实位置在第 218~241 页),但 `lookup_page_num` **全部返回第 55 页**;
- 不同检索词、不同字段普遍出现「多处结果标同一个错误页码」;
- 前端 PDF 高亮框(bbox)同源同错。

该文档实测数据:markdown 全文 1,016,132 字符,middle_json 7.3MB,436 页。

## 2. 排查过程(含被推翻的假设)

### 假设一:OCR 短重复文本导致锚点大面积丢失 → 部分推翻

库里落库的 page_mapping 有 116 个锚点、69 个不同页码,且随 start_pos 单调——
不是「锚点全丢」的退化形态,单纯的丢锚解释不了「集中错标同一页」。

### 假设二:`_extract_block_text` 用空格拼接 span 导致中文前缀匹配失败 → 推翻

最小合成用例能复现「只建 1 锚全返回第 1 页」,但真实数据打脸:全文档 2,395 个文本块,
50 字前缀在 md 中**全局** find 命中率 98.1%,空格拼接与无空格拼接命中率完全相同。
教训:**最小复现"能复现现象"≠"是真实根因",必须拿真实数据验证。**

### 假设三:MinerU 输出的 md 顺序与页序不一致 → 推翻(MinerU 无罪)

初期用「每页首块文本全局 find 首次出现位置」抽样,看到大量"顺序倒挂"
(如 page_idx=20 出现在 md 0.02% 处)。但复核发现这些全是 **cnt>1 的重复文本**
(目录、公文套话)——全局 find 的首次出现位置不是真实位置,倒挂是假象。

改用**全 md 唯一(count==1)的探针**重测:204 个唯一探针,md 位置随 page_idx
**逆序次数 = 0**。md 物理顺序与页序严格一致。`mineru_client` 也确认 md/middle_json
由 MinerU 服务一次返回、本地零拼接。

### 决定性证据:模拟重放 build_page_mapping 逐块跟踪 cursor

| 观测 | 数值 |
|---|---|
| 全文档文本块 | 2,395 个,98.1% 可全局唯一定位 |
| 老算法实际建锚 | 仅 116 个,覆盖 69/436 页 |
| cursor 前向 find 失败的块 | 2,339 个(325 页颗粒无收) |
| 最大单次 cursor 跳跃 | +353,205 字符(第 23 页,md 3.7% → 38.5%) |

罪魁块还原:第 23 页某块真实位置在 md 22,443(前 20 字全局唯一可定位),但 cursor
当时已在 37,706(此前已有小规模误推越过它)。于是 50/30/20 字前缀前向 find 全部 -1,
兜底的 **10 字短前缀「市和美乡村建设项目工」** 恰好横跨命中 38.5% 处另一份公文标题
《关于成立宣城**市**宁国**市和美乡村建设项目工**作专班的通知》——cursor 一次性
推飞 35 万字符。随后表格盲锚接力:第 32 页跳到 49.5%、第 74 页跳到 79%、
第 96 页跳到 89.9%、**第 108 页时 cursor 已在 97.5%**,之后 328 页的内容无论多好找
全部 miss。

## 3. 根因(三个缺陷叠加)

老 `build_page_mapping` 的机制:按页序遍历 middle_json 块,取文本前缀
`md.find(prefix, cursor)` 前向匹配,命中即建锚并推进 cursor。三个缺陷:

1. **兜底短前缀(10 字)无唯一性校验**——在重复公文套话上极易误命中远处文本,
   一次误跳吞掉几十万字符;
2. **表格锚是盲锚**——`md.find("<table", cursor)` 与表格内容完全无关,cursor 错位后
   继续"认领"不相干的表格,把错误合法化并持续推进;
3. **cursor 单调不可回退**——一次误跳永久污染:后续所有块(哪怕在 md 中全局唯一、
   本可精确定位)都因 cursor 已越过其真实位置而 find 失败。

最终大片 md 区间零锚点,`lookup_page_num` 的 bisect 把这些区间的所有查询都落到
前面最后一个锚上——即用户看到的「多处检索结果都是第 55 页」(55 页恰是错位表格锚)。

**触发条件**:长文档 + 重复文本(公文套话/目录/多份材料合并)。文档越长,一次误跳
的破坏面越大。436 页文档是重灾区,但机制上任何长文档都可能中招。

## 4. 修复方案:content_list 顺序重放

### 原理

MinerU `/file_parse` 支持 `return_content_list=true`,返回**阅读序内容列表**——
md 就是按 content_list 逐项渲染拼接出来的,每项自带:

- `page_idx`(0-based 页码)
- `bbox`(**1000×1000 归一化坐标**,见下)
- 内容字段:`text`(text 项,另有 text_level 标题级别)/ `table_body`(table 项,
  HTML 原文)/ `list_items`(list 项,字符串数组)
- `page_number` 类型 = 页眉页脚的页码水印,**md 不渲染,建锚时跳过**

因为「md 顺序 == content_list 顺序」是构造性保证,逐项取探针(内容前 50 字,
回退 20 字)cursor 前向 find 的单调性**天然成立**:

- 单项 miss 只丢该项锚点、**不推进 cursor**,无连锁错位(与老算法的本质区别);
- 实测(14 页真实 PDF《应对气候变化规划》):74 个实内容项 **74/74 全命中、
  page_idx 零逆序**。

### bbox 反归一化(易踩的坑)

content_list 的 bbox 不在 middle_json 的 page_size 坐标系,而是**归一化到 1000×1000**。
实测验证:middle_json 块 `[126,137,474,164]` × 1000/page_size`[595,841]` ≈
content_list 的 `[211,162,796,195]`,完全吻合。

落库前必须反归一化:`bbox_real = [x0*w/1000, y0*h/1000, x1*w/1000, y1*h/1000]`,
w/h 取自 middle_json 同页 `page_size`(**这也是 content_list 路径仍需要 middle_json
的唯一原因**);取不到 page_size 时该锚不挂 bbox(保守,防前端画错)。处理后
page_mapping 的 entry 与老算法坐标语义完全一致,前端 `pdfViewer.js` 零改动。

### 降级策略

`build_page_mapping_auto(md, middle_json, content_list)` 统一入口:

- content_list 存在且重放产出非空 → 用 content_list 路径;
- 否则(老 MinerU 服务、异常返回、全水印页)→ 降级老 `build_page_mapping`
  (middle_json 前缀匹配,原样保留未动)。

content_list **用完即弃**:不落库、不进回调,`file_content` 表结构与
`stage_done.data` 契约均不变。

## 5. 改动清单

| commit | 内容 |
|---|---|
| `b3924b9` | mineru_client 请求加 `return_content_list`,返回透传(list/str 二态归一为 str) |
| `24f1e65` | `build_page_mapping_from_content_list`(顺序重放 + bbox 反归一化)与 `build_page_mapping_auto`(降级入口),9 个单测 |
| `4a2d0a4` | `parse_file` 返回三元组;pipeline 两处调用点接 `build_page_mapping_auto` |
| `0676d0b` | 真实 MinerU 产物固化为 `tests/fixtures/content_list_sample`,3 个回归用例 |
| `73da9a4` / `287cf16` / 后续 | 清理临时诊断脚本与调试数据;文档更新 |

涉及文件:`service/mineru_client.py`、`utils/page_mapping.py`、`service/parse_service.py`、
`service/pipeline_service.py`、`docs/MINERU_INTEGRATION.md`、`CLAUDE.md` 及对应测试。

## 6. 验证

- 全量 `uv run pytest`:**223 passed**(含原有 page_mapping 7 用例不回归);
- 新增 12 个用例:合成场景 9 个(逐项建锚/跳过水印/幽灵项容错/bbox 反归一化/
  JSON 字符串入参/空输入/auto 优先与两种降级)+ 真实产物回归 3 个;
- 真实产物回归关键断言:74 锚全建、锚点单调、bbox 全部落在 page_size 范围内、
  第 4 页表格「专栏1 非二氧化碳温室气体管控工程」`lookup_page_num` 正确返回 "4"。
- 附:14 页样例中第 14 页无锚是**正确行为**——该页 content_list 仅一个 page_number
  水印、无实内容,md 本来就没有它的内容。

## 7. 影响面与兼容性

- `page_mapping` entry schema(`start_pos/end_pos/page_num/bbox/page_size`)一字未变,
  `lookup_page_num` / `lookup_bboxes` / 表格页码 / 分块页码 / 抽取 source_refs /
  前端高亮全部无感;
- 回调契约不变(`stage_done.data` 仍只带 content/middle_json/page_mapping);
- 无表结构变更、无新接口、无新依赖。

## 8. 遗留事项

1. **存量文件不自动修复**(决策:不做重算接口)。content_list 上线前解析的文件,
   库里的错误 page_mapping 保持现状;需要修复的个别文件走重新上传/重新解析。
2. 若未来需要不重新解析地修复存量,备选算法已设计过(未实现):
   **全局唯一锚 + LIS 单调清洗 + 分段回填**——第一遍只用「50 字前缀全局唯一」的块
   直接定锚(不受 cursor 污染,该文档 98.1% 的块满足),对唯一锚序列做最长递增子序列
   清洗剔除异常,第二遍在相邻锚点分段内做受限 cursor 匹配(误配破坏面被钳制在段内),
   表格锚可用 middle_json table 块 spans 里的 `html` 字段做内容校验取代 `<table` 盲锚。
3. 降级路径(老算法)在长文档 + 重复套话下的缺陷依然存在,仅当 MinerU 不返回
   content_list 时才会走到;`docs/MINERU_INTEGRATION.md` 已记录此已知坑。
