"""调用日志交互回归：使用隔离响应验证刷新、原始输入、弹窗和筛选。"""

import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "invocation-ui-qa"
OUTPUT.mkdir(exist_ok=True)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if urlsplit(self.path).path.endswith("/result.json"):
            payload = json.dumps(detail["result"], ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="qa.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            super().do_GET()


server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()

original = {
    "log_id": "qa-old", "kind": "file_parse", "status": "complete",
    "file_id": "qa-file-id", "task_id": None, "type_id": "contract", "file_name": "合同资料.pdf",
    "file_size": 123456, "mode": "sync", "stage": None, "source": None,
    "item_count": 0, "biz_ids": [], "created_at": "2026-10-13T09:00:00",
    "started_at": "2026-10-13T09:00:01", "finished_at": "2026-10-13T09:00:11",
    "updated_at": "2026-10-13T09:00:11", "duration_ms": 11000, "error": None,
}
detail = {**original, "parameters": {"input_params": {"SHOULD_NOT_RENDER": "hidden"}},
          "request_input": {"query": [["mode", "sync"], ["type_id", "contract"]],
                            "form": [["params", '{"年度":"2026"}']]},
          "stage_times": {stage: {"start": "2026-10-13T09:00:01", "end": "2026-10-13T09:00:02", "duration_ms": 1000}
                          for stage in ["parsing", "tableing", "chunking", "embedding", "extracting", "analyzing"]},
          "result": {"extraction": [{"field_id": "amount", "extracted_value": "100", "reason": "已抽取"}],
                     "analysis": [{"rule_id": "check", "result_value": "true"}]} }
state = {"items": [original], "queries": []}


def route_api(route):
    url = urlsplit(route.request.url)
    if url.path == "/invocations":
        state["queries"].append(parse_qs(url.query))
        route.fulfill(json={"code": 200, "data": {"items": state["items"], "total": len(state["items"]), "page": 1, "page_size": 20}})
    elif url.path.endswith("/result.json"):
        route.continue_()
    else:
        route.fulfill(json={"code": 200, "data": detail})


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route("**/invocations**", route_api)
        # 启动时其它页面的外部服务响应保持隔离，不访问项目业务数据库。
        page.route("**/file/**", lambda route: route.fulfill(json={"code": 200, "data": [] if urlsplit(route.request.url).path == "/file/processing" else {"items": [], "total": 0, "total_pages": 1}}))
        page.route("**/doctype/**", lambda route: route.fulfill(json={"code": 200, "data": []}))
        page.route("**/log/files", lambda route: route.fulfill(json={"code": 200, "data": {"items": [], "current": None}}))
        page.route("**/log/stream**", lambda route: route.fulfill(content_type="text/event-stream", body='event: ready\ndata: {"file":null}\n\n'))
        page.route("**/runtime/**", lambda route: route.fulfill(json={"code": 200, "data": {}}))
        page.goto(f"http://127.0.0.1:{server.server_port}/ui/index.html?page=invocations")
        page.locator('[data-invocation-toggle="qa-old"]').click()
        page.locator('#invocation-detail-qa-old .invocation-inputs pre').wait_for()
        raw = page.locator('#invocation-detail-qa-old .invocation-inputs pre').inner_text()
        assert json.loads(raw)["file"] == "xxx"
        assert "SHOULD_NOT_RENDER" not in page.locator('#invocation-detail-qa-old').inner_text()
        assert page.locator('[data-page="runtime-logs"]').count() == 1
        assert page.locator('#invocation-kind').count() == 0
        assert page.locator('#invocation-status').evaluate("el => el.classList.contains('cs-native')")

        # 记录原 DOM、子区开关、内层滚动和选中文字。
        page.evaluate("""() => {
            window.qaRow = document.querySelector('[data-log-id="qa-old"]');
            window.qaPre = qaRow.querySelector('.invocation-inputs pre');
            window.qaInput = qaRow.querySelector('.invocation-inputs details');
            qaInput.open = false;
            qaRow.scrollIntoView({block:'start'});
        }""")
        anchor_before = page.locator('[data-log-id="qa-old"]').bounding_box()["y"]
        state["items"] = [{**original, "log_id": f"new-{i}", "file_name": f"新文件-{i}.pdf"} for i in range(3)] + [original]
        page.evaluate("() => InvocationLogs.load(true)")
        assert page.evaluate("() => window.qaRow === document.querySelector('[data-log-id=qa-old]')")
        assert page.evaluate("() => window.qaPre === document.querySelector('#invocation-detail-qa-old .invocation-inputs pre')")
        assert not page.evaluate("() => qaInput.open")
        anchor_after = page.locator('[data-log-id="qa-old"]').bounding_box()["y"]
        assert abs(anchor_after - anchor_before) < 2

        # 新记录将旧行挤出当页时，仍然保留展开中的旧行。
        state["items"] = state["items"][:-1]
        page.evaluate("() => InvocationLogs.load(true)")
        assert page.locator('[data-log-id="qa-old"]').count() == 1
        assert page.locator('[data-log-id="qa-old"] .invocation-row-summary').get_attribute("aria-expanded") == "true"

        page.locator('[data-invocation-result="qa-old"]').click()
        dialog = page.locator('#invocation-result-dialog')
        dialog.wait_for(state="visible")
        assert dialog.is_visible()
        for width, height in [(1440, 950), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            bounds = dialog.bounding_box()
            assert abs(bounds["x"] + bounds["width"] / 2 - width / 2) < 2, bounds
            assert abs(bounds["y"] + bounds["height"] / 2 - height / 2) < 2, bounds
            page.screenshot(path=str(OUTPUT / f"result-{width}.png"))
        with page.expect_download() as download:
            page.locator('#invocation-result-download').click()
        download.value.save_as(str(OUTPUT / "result.json"))
        assert set(json.loads((OUTPUT / "result.json").read_text(encoding="utf-8"))) == {"extraction", "analysis"}
        page.locator('#invocation-result-close').click()
        page.set_viewport_size({"width": 1440, "height": 950})

        page.locator('[data-invocation-kind="independent_analysis"]').click()
        page.wait_for_function("() => !InvocationLogs.loading")
        assert state["queries"][-1]["kind"] == ["independent_analysis"]
        page.locator('#invocation-filters .cs-trigger').click()
        assert page.locator('.cs-dropdown.cs-open').is_visible()
        page.locator('.cs-dropdown .cs-option', has_text="部分失败").click()
        page.wait_for_function("() => !InvocationLogs.loading")
        assert state["queries"][-1]["status"] == ["partial_failed"]
        page.locator('.cs-dropdown').wait_for(state="hidden")
        page.screenshot(path=str(OUTPUT / "list-desktop.png"))

        # 运行中记录完成时更新状态和结果按钮，原始输入区仍保留。
        state["items"] = [{**original, "status": "running", "finished_at": None}]
        detail["status"] = "running"
        detail["result"] = None
        page.evaluate("() => InvocationLogs.load()")
        page.locator('[data-invocation-toggle="qa-old"]').click()
        page.locator('#invocation-detail-qa-old .invocation-inputs pre').wait_for()
        page.evaluate("() => window.qaUpdatedPre = document.querySelector('#invocation-detail-qa-old .invocation-inputs pre')")
        assert page.locator('[data-invocation-result="qa-old"]').is_disabled()
        detail["status"] = "complete"
        detail["result"] = {"analysis": []}
        state["items"] = [original]
        page.evaluate("() => InvocationLogs.load(true)")
        assert not page.locator('[data-invocation-result="qa-old"]').is_disabled()
        assert page.evaluate("() => qaUpdatedPre === document.querySelector('#invocation-detail-qa-old .invocation-inputs pre')")
        # 切换筛选由用户主动触发，此时正常收起详情。
        page.locator('[data-invocation-kind="file_parse"]').click()
        page.wait_for_function("() => !InvocationLogs.loading")
        assert page.locator('[data-invocation-toggle="qa-old"]').get_attribute("aria-expanded") == "false"
        page.locator('[data-page="runtime-logs"]').click()
        page.wait_for_function("() => LogViewer.active && !!LogViewer.source")
        assert page.locator('#page-runtime-logs').is_visible()
        for control in ["runtime-log-file", "runtime-log-level", "runtime-log-tail"]:
            assert page.locator(f'#{control}').evaluate("el => el.classList.contains('cs-native')")
        assert not page_errors, page_errors
        browser.close()
        print("PASS: 原始输入、实时日志入口、类型 tab、展开行与滚动保留、跨分页保留、桌面/手机弹窗居中、自定义下拉、结果下载")
finally:
    server.shutdown()
