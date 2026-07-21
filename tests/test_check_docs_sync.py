"""check_docs_sync 一致性校验器测试。"""

from scripts import check_docs_sync as c


def test_coverage_detects_missing():
    spec = {"info": {"version": "1.0.0"}, "paths": {"/a": {"get": {}}, "/b": {"post": {}}}}
    missing, phantom = c.diff_coverage(spec, {("GET", "/a")})
    assert ("POST", "/b") in missing
    assert not phantom


def test_coverage_detects_phantom():
    spec = {"info": {"version": "1.0.0"}, "paths": {"/a": {"get": {}}}}
    missing, phantom = c.diff_coverage(spec, {("GET", "/a"), ("GET", "/ghost")})
    assert ("GET", "/ghost") in phantom


def test_version_ok():
    assert c.version_ok("1.0.0", "1.0.0", "1.0.0") is True
    assert c.version_ok("1.0.0", "0.1.0", "1.0.0") is False
