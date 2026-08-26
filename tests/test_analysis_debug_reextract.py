"""逻辑分析调试临时重新抽取测试。"""

from model.schemas import AnalysisTestRequest


def test_analysis_test_request_re_extract_defaults_false():
    req = AnalysisTestRequest(file_id="file-1", config={"rule_type": "judge"})

    assert req.re_extract is False


def test_analysis_test_request_accepts_re_extract_true():
    req = AnalysisTestRequest(
        file_id="file-1",
        config={"rule_type": "judge"},
        re_extract=True,
    )

    assert req.re_extract is True
