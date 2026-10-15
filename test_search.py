import sys
import json
sys.path.insert(0, r'C:\Users\19404\Desktop\Projects\wanz_prase2_001')

# 测试单独 context 检索
test_config = {
    "file_id": "b94fd78661ec93ddd35d9fd0a6f2b488",
    "keywords": ["企业"],
    "context_before": 100,
    "context_after": 200,
    "max_results": 100,
    "sort_order": "asc",
    "ignore_whitespace": False
}

print("=" * 60)
print("单独 context 检索「企业」的配置:")
print("=" * 60)
print(json.dumps(test_config, ensure_ascii=False, indent=2))
print("\n准备测试...")
