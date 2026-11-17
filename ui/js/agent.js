/* 析卷智能体：前端只编排展示与交互，保留现有 SSE 和历史上下文契约。 */
(() => {
    const TOOLS = [
        ["search_context", "上下文检索", "text-search"], ["search_rule", "规则检索", "list-filter"],
        ["search_chunk_db", "分块检索", "database"], ["search_bm25", "关键词检索", "list-ordered"],
        ["search_vector_db", "语义检索", "scan-search"], ["search_page", "页码检索", "files"],
        ["list_sections", "章节目录", "list-tree"], ["read_section", "章节精读", "book-open-text"],
        ["list_tables", "表格目录", "table-2"], ["read_table", "表格精读", "table-properties"],
        ["web_search", "联网搜索", "globe-2"], ["pdf_reader", "PDF 视觉阅读", "scan-eye"],
    ];
    const ids = ["tool-catalog", "reference-summary", "reference-count", "source-caption", "workspace-note", "context-usage", "context-meter", "context-fill", "conversation", "conversation-status", "chat-scroll", "chat-inner", "welcome", "composer", "composer-files", "composer-guidance", "question", "attach-files", "send-button", "stop-button", "new-chat", "file-picker", "close-picker", "cancel-picker", "add-picker", "file-search", "file-list", "picker-count", "picker-total", "rail-add-files", "welcome-add-files", "toggle-rail", "close-rail", "rail-backdrop", "reference-panel", "jump-latest", "export-chat", "new-chat-dialog", "cancel-new-chat", "confirm-new-chat", "toast"];
    const els = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
    const state = {
        history: [], conversationFiles: [], transcript: [],
        pickerFiles: new Map(), pickerSelection: new Set(), pickerPage: 1, pickerTotal: 0,
        pickerLoading: false, pickerVersion: 0, pickerController: null,
        controller: null, currentRun: null, searchTimer: null, toastTimer: null,
        followOutput: true, scrollFrame: null, hasStarted: false,
    };
    const icon = name => { const node = document.createElement("i"); node.setAttribute("data-lucide", name); return node; };
    const refreshIcons = () => window.lucide?.createIcons({ attrs: { "stroke-width": 1.6, "aria-hidden": "true" } });
    const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    const toolInfo = name => TOOLS.find(item => item[0] === name);
    const toolIcon = name => toolInfo(name)?.[2] || "wrench";
    const toolLabel = name => toolInfo(name)?.[1] || name || "准备调用工具";
    const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    function toast(text) {
        clearTimeout(state.toastTimer);
        els.toast.textContent = text;
        els.toast.classList.remove("hidden");
        state.toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 3200);
    }
    function setStatus(text, stateName = "idle") {
        els["conversation-status"].textContent = text;
        els["conversation-status"].dataset.state = stateName;
        els["workspace-note"].dataset.state = stateName;
    }
    function syncComposer() {
        const running = !!state.controller;
        const hasQuestion = !!els.question.value.trim();
        els["send-button"].disabled = running || !hasQuestion;
        els["send-button"].classList.toggle("hidden", running);
        els["stop-button"].classList.toggle("hidden", !running);
        for (const id of ["attach-files", "rail-add-files", "welcome-add-files"]) els[id].disabled = running;
        els["export-chat"].disabled = running || !state.transcript.length;
        els["composer-guidance"].textContent = running ? "正在查阅资料；你可以随时停止研究" : state.conversationFiles.length ? `已引用 ${state.conversationFiles.length} 份文档，可继续追问` : "添加文档后，即可开始有据可循的研究";
        els["send-button"].title = !hasQuestion ? "请输入研究问题" : !state.conversationFiles.length ? "先选择研究文档" : "发送问题（Ctrl / ⌘ + Enter）";
    }
    function resizeQuestion() {
        els.question.style.height = "auto";
        els.question.style.height = `${Math.min(160, Math.max(64, els.question.scrollHeight))}px`;
    }
    function removeFile(id) {
        if (state.controller || state.hasStarted) return;
        state.conversationFiles = state.conversationFiles.filter(file => file.file_id !== id);
        renderReferences();
    }
    function removeButton(file) {
        const button = document.createElement("button");
        button.type = "button"; button.className = "icon-button";
        button.setAttribute("aria-label", `移除 ${file.file_name || file.file_id}`);
        button.append(icon("x")); button.addEventListener("click", () => removeFile(file.file_id));
        return button;
    }
    function renderReferenceSummary() {
        els["reference-summary"].replaceChildren();
        els["reference-count"].textContent = `${state.conversationFiles.length} / 20`;
        if (!state.conversationFiles.length) {
            const empty = document.createElement("div"); empty.className = "ref-empty";
            const line = document.createElement("p"); line.textContent = "好的研究，从一份资料开始。";
            empty.append(line); els["reference-summary"].append(empty);
        }
        for (const file of state.conversationFiles) {
            const item = document.createElement("div"); item.className = "ref-item"; item.title = file.file_name || file.file_id;
            const copy = document.createElement("div");
            const name = document.createElement("strong"); name.textContent = file.file_name || file.file_id;
            const detail = document.createElement("span"); detail.textContent = "PDF · 已处理完成";
            copy.append(name, detail); item.append(icon("file-text"), copy);
            if (!state.hasStarted) item.append(removeButton(file));
            else { const check = icon("check"); check.setAttribute("title", "持续引用"); item.append(check); }
            els["reference-summary"].append(item);
        }
        els["source-caption"].textContent = state.hasStarted ? "资料在本次对话持续引用。更换资料范围，请开启新研究。" : "选择已处理完成的 PDF，建立本次研究的资料范围。";
        els["welcome-add-files"].querySelector("span").textContent = state.conversationFiles.length ? `已选 ${state.conversationFiles.length} 份 · 继续添加` : "选择研究文档";
    }
    function renderComposerFiles() {
        els["composer-files"].replaceChildren();
        for (const file of state.conversationFiles) {
            const chip = document.createElement("span"); chip.className = "pending-chip"; chip.title = file.file_name || file.file_id;
            const label = document.createElement("span"); label.textContent = file.file_name || file.file_id;
            chip.append(icon("file-text"), label);
            if (!state.hasStarted) chip.append(removeButton(file));
            els["composer-files"].append(chip);
        }
    }
    function renderReferences() { renderReferenceSummary(); renderComposerFiles(); syncComposer(); refreshIcons(); }
    function renderCatalog() {
        for (const [name, label, iconName] of TOOLS) {
            const row = document.createElement("div"); row.className = "catalog-row"; row.dataset.tool = name; row.title = name;
            const text = document.createElement("span"); text.textContent = label;
            row.append(icon(iconName), text); els["tool-catalog"].append(row);
        }
    }
    function markTool(name) {
        [...els["tool-catalog"].children].find(row => row.dataset.tool === name)?.classList.add("used");
    }
    function createMessage(role, text) {
        els.welcome.classList.add("hidden");
        const article = document.createElement("article"); article.className = `message ${role}`;
        const meta = document.createElement("div"); meta.className = "message-meta";
        meta.append(icon(role === "user" ? "user-round" : "book-open"), document.createTextNode(role === "user" ? "你" : "析卷 AI"));
        const bubble = document.createElement("div"); bubble.className = "bubble"; bubble.textContent = text || "";
        article.append(meta, bubble); els["chat-inner"].append(article);
        refreshIcons(); scrollToBottom(); return { article, bubble };
    }
    function createAssistantMessage() {
        const view = createMessage("assistant", "");
        const progress = document.createElement("div"); progress.className = "research-status"; progress.dataset.phase = "reading";
        const bars = document.createElement("span"); bars.className = "activity-bars"; bars.setAttribute("aria-hidden", "true");
        bars.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
        const progressText = document.createElement("span"); progressText.textContent = "正在理解问题";
        const elapsed = document.createElement("span"); elapsed.className = "elapsed";
        progress.append(bars, progressText, elapsed);
        const answer = document.createElement("div"); answer.className = "answer-text";
        const answerNode = document.createTextNode(""); answer.append(answerNode);
        const evidence = document.createElement("div"); evidence.className = "evidence";
        const evidenceDetails = document.createElement("details"); evidenceDetails.className = "research-evidence"; evidenceDetails.open = true;
        const evidenceSummary = document.createElement("summary"); evidenceSummary.append(icon("route"), document.createTextNode("研究过程"));
        const evidenceCount = document.createElement("span"); evidenceCount.className = "evidence-count"; evidenceSummary.append(evidenceCount);
        evidenceSummary.addEventListener("click", () => { view.evidenceTouched = true; });
        evidenceDetails.append(evidenceSummary, evidence);
        view.bubble.replaceChildren(progress, evidenceDetails, answer);
        Object.assign(view, { answer, answerNode, evidence, evidenceDetails, evidenceCount, progress, progressText, elapsed, thinkingRounds: new Map() });
        return view;
    }
    function setRunPhase(run, text) { if (run.view.progressText) run.view.progressText.textContent = text; }
    function appendThinking(run, event) {
        const view = run.view;
        if (!view.thinkingDetails) {
            const details = document.createElement("details"); details.className = "thinking-details";
            const summary = document.createElement("summary"); summary.className = "thinking-summary"; summary.textContent = "思考过程";
            const body = document.createElement("div"); body.className = "thinking-body";
            details.append(summary, body); view.bubble.insertBefore(details, view.evidenceDetails);
            view.thinkingDetails = details; view.thinkingBody = body;
        }
        const round = event.round ?? 0;
        let segment = view.thinkingRounds.get(round);
        if (!segment) { segment = document.createElement("div"); segment.className = "thinking-round"; view.thinkingRounds.set(round, segment); view.thinkingBody.append(segment); }
        segment.textContent += event.text || ""; scrollToBottom();
    }
    function createToolCard(run, event) {
        const slot = `slot:${event.round ?? 0}:${event.index ?? 0}`;
        const callId = event.tool_call_id || event.id;
        const key = callId || slot;
        // 错误结果可能没有 index；此时只能按调用 ID 查找，不能误落到第一个工具。
        let card = run.toolCards.get(key) || (event.index != null ? run.toolCards.get(slot) : null);
        if (card) { run.toolCards.set(key, card); return card; }
        const details = document.createElement("details"); details.className = "tool-card"; details.dataset.running = "true";
        const summary = document.createElement("summary");
        const glyph = document.createElement("span"); glyph.className = "tool-glyph"; glyph.append(icon(toolIcon(event.name)));
        const label = document.createElement("span"); label.className = "tool-name"; label.textContent = toolLabel(event.name);
        const status = document.createElement("span"); status.className = "tool-state"; status.textContent = "执行中";
        summary.append(glyph, label, status);
        const body = document.createElement("div"); body.className = "tool-body";
        const args = document.createElement("details"); args.className = "tool-subdetail"; args.open = true;
        const argsSummary = document.createElement("summary"); argsSummary.textContent = "调用参数";
        const argsPre = document.createElement("pre"); args.append(argsSummary, argsPre); body.append(args);
        details.append(summary, body); run.view.evidence.append(details);
        card = { details, glyph, label, status, argsPre, body, name: event.name || "" };
        run.toolCards.set(key, card); if (event.index != null || !callId) run.toolCards.set(slot, card);
        run.view.evidenceCount.textContent = `${run.view.evidence.querySelectorAll(".tool-card").length} 次工具调用`;
        markTool(event.name); refreshIcons(); scrollToBottom(); return card;
    }
    function parseResult(raw) { try { return JSON.parse(raw); } catch { return raw; } }
    function showToolResult(run, event) {
        const card = createToolCard(run, event);
        const parsed = parseResult(event.content);
        const failed = !!(parsed && typeof parsed === "object" && parsed.error);
        card.details.dataset.running = "false"; card.status.textContent = failed ? "未完成" : "已完成"; card.status.classList.toggle("fail", failed);
        const result = document.createElement("details"); result.className = "tool-subdetail";
        const summary = document.createElement("summary"); summary.textContent = "检索结果";
        const pre = document.createElement("pre"); pre.textContent = typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
        result.append(summary, pre); card.body.append(result);
        if (Array.isArray(event.images) && event.images.length) {
            const grid = document.createElement("div"); grid.className = "page-grid";
            for (const data of event.images) {
                if (!/^data:image\/(png|jpe?g|webp);base64,/i.test(data.data_url || "")) continue;
                const figure = document.createElement("figure"); figure.className = "page-figure";
                const image = document.createElement("img"); image.src = data.data_url; image.alt = `${data.file_name || "PDF"} 第 ${data.page} 页`; image.loading = "lazy";
                const caption = document.createElement("figcaption"); caption.textContent = `${data.file_name || data.file_id || "PDF"} · 第 ${data.page} 页`;
                figure.append(image, caption); grid.append(figure);
            }
            card.body.append(grid);
        }
        refreshIcons(); scrollToBottom();
    }
    function appendEvidenceNote(run, text) {
        const note = document.createElement("div"); note.className = "evidence-note";
        note.append(icon("check"), document.createTextNode(text)); run.view.evidence.append(note); refreshIcons(); scrollToBottom();
    }
    function updateCompaction(run, event) {
        if (event.task_id && event.task_id < (run.compactionTaskId || 0)) return;
        run.compactionTaskId = event.task_id || run.compactionTaskId || 0;
        const status = event.state || "completed";
        if (status === "skipped" || status === "cancelled") { run.compactionNote?.remove(); run.compactionNote = null; return; }
        const labels = { started: "正在后台整理较早的工具结果，可继续研究", waiting: "正在等待后台整理完成，以满足上下文预算", completed: "较早的工具结果已整理为对话记忆" };
        if (!labels[status]) return;
        if (!run.compactionNote) {
            const note = document.createElement("div"); note.className = "evidence-note context-note";
            note.setAttribute("role", "status"); note.setAttribute("aria-live", "polite");
            run.view.evidence.append(note); run.compactionNote = note;
        }
        run.compactionNote.dataset.state = status;
        run.compactionNote.replaceChildren(icon("notebook-pen"), document.createTextNode(labels[status]));
        refreshIcons(); scrollToBottom();
    }
    function clearPendingCompaction(run) {
        if (run.compactionNote && run.compactionNote.dataset.state !== "completed") { run.compactionNote.remove(); run.compactionNote = null; }
    }
    function showError(run, text) {
        run.failed = true;
        const note = document.createElement("div"); note.className = "error-note"; note.setAttribute("role", "alert"); note.textContent = text || "研究暂时中断，请重试。";
        if (run.question) {
            const retry = document.createElement("button"); retry.type = "button"; retry.className = "quiet-button"; retry.textContent = "重新尝试";
            retry.addEventListener("click", () => { if (state.controller) return; els.question.value = run.question; resizeQuestion(); syncComposer(); els.composer.requestSubmit(); });
            note.append(retry);
        }
        run.view.evidence.append(note); refreshIcons(); scrollToBottom();
    }
    // 阅读旧消息时保留当前位置；只在用户跟随输出时滚动，合并同一帧的 token 更新。
    function scrollToBottom(force = false) {
        if (force) state.followOutput = true;
        if (!state.followOutput || state.scrollFrame) return;
        state.scrollFrame = requestAnimationFrame(() => {
            state.scrollFrame = null;
            if (state.followOutput) els["chat-scroll"].scrollTop = els["chat-scroll"].scrollHeight;
        });
    }
    // 只解析常用 Markdown 结构；所有模型文本先转义，绝不执行原始 HTML。
    function renderAnswer(text) {
        const inline = value => {
            const code = [];
            let escaped = escapeHtml(value).replace(/`([^`]+)`/g, (_, content) => { code.push(`<code>${content}</code>`); return `\u0000${code.length - 1}\u0000`; });
            escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
            return escaped.replace(/\u0000(\d+)\u0000/g, (_, index) => code[Number(index)] || "");
        };
        const lines = String(text).replace(/\r\n/g, "\n").split("\n");
        const html = []; let paragraph = [], list = [], listTag = "", fence = null;
        const flush = () => {
            if (paragraph.length) { html.push(`<p>${paragraph.map(inline).join("<br>")}</p>`); paragraph = []; }
            if (list.length) { html.push(`<${listTag}>${list.map(item => `<li>${inline(item)}</li>`).join("")}</${listTag}>`); list = []; listTag = ""; }
        };
        const cells = line => {
            const source = line.trim(); const result = []; let cell = "", ticks = 0;
            for (let position = 0; position < source.length; position++) {
                const char = source[position];
                if (char === "\\" && /[|\\]/.test(source[position + 1] || "")) { cell += source[++position]; continue; }
                if (char === "`") {
                    let count = 1; while (source[position + count] === "`") count++;
                    if (!ticks) ticks = count; else if (ticks === count) ticks = 0;
                    cell += "`".repeat(count); position += count - 1; continue;
                }
                if (char === "|" && !ticks) { result.push(cell.trim()); cell = ""; }
                else cell += char;
            }
            result.push(cell.trim());
            if (source.startsWith("|") && result[0] === "") result.shift();
            if (source.endsWith("|") && result.at(-1) === "") result.pop();
            return result;
        };
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            if (/^\s*```/.test(line)) { flush(); if (fence) { html.push(`<pre><code>${escapeHtml(fence.join("\n"))}</code></pre>`); fence = null; } else fence = []; continue; }
            if (fence) { fence.push(line); continue; }
            if (!line.trim()) { flush(); continue; }
            if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*\|/.test(lines[i + 1])) {
                flush(); const start = i; const headings = cells(line); const separators = cells(lines[++i]);
                const rows = [];
                while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) rows.push(cells(lines[++i]));
                if (separators.length !== headings.length || !separators.every(cell => /^:?-{3,}:?$/.test(cell)) || rows.some(row => row.length !== headings.length)) {
                    html.push(`<pre>${escapeHtml(lines.slice(start, i + 1).join("\n"))}</pre>`); continue;
                }
                html.push(`<div class="answer-table"><table><thead><tr>${headings.map(cell => `<th scope="col">${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headings.map((_, index) => `<td>${inline(row[index] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`); continue;
            }
            const heading = line.match(/^(#{1,6})\s+(.+)$/);
            if (heading) { flush(); const level = Math.min(4, heading[1].length + 1); html.push(`<h${level}>${inline(heading[2])}</h${level}>`); continue; }
            const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
            if (item) { const tag = item[1] ? "ul" : "ol"; if (paragraph.length || (listTag && listTag !== tag)) flush(); listTag = tag; list.push(item[2]); continue; }
            if (line.startsWith("> ")) { flush(); html.push(`<blockquote>${inline(line.slice(2))}</blockquote>`); continue; }
            if (list.length) flush(); paragraph.push(line);
        }
        flush(); if (fence) html.push(`<pre><code>${escapeHtml(fence.join("\n"))}</code></pre>`);
        return html.join("");
    }
    async function copyAnswer(text) {
        try { await navigator.clipboard.writeText(text); toast("回答已复制"); }
        catch { toast("无法访问剪贴板，请选中回答后复制"); }
    }
    function finishAnswer(run) {
        if (run.finalized) return;
        run.finalized = true;
        const text = run.answerText ?? run.view.answer.textContent;
        if (text) {
            run.view.answer.innerHTML = renderAnswer(text); run.view.answer.classList.add("rendered");
            const actions = document.createElement("div"); actions.className = "answer-actions";
            const copy = document.createElement("button"); copy.type = "button"; copy.className = "quiet-button";
            copy.append(icon("copy"), document.createTextNode("复制回答")); copy.addEventListener("click", () => copyAnswer(text)); actions.append(copy);
            run.view.bubble.append(actions);
        }
        run.view.progress.dataset.phase = "done";
        if (!run.view.evidenceTouched && !run.failed) run.view.evidenceDetails.open = false;
        for (const card of new Set(run.toolCards.values())) {
            if (card.details.dataset.running === "true") { card.details.dataset.running = "false"; card.status.textContent = "未完成"; }
        }
        refreshIcons(); scrollToBottom();
    }
    function handleEvent(run, event) {
        // 已放弃的请求不能覆盖新对话的状态，也不能写入新对话的 history。
        if (run.discarded || run.terminal) return;
        switch (event.type) {
            case "status": setStatus("正在研究", "running"); setRunPhase(run, event.message || "正在理解问题"); break;
            case "context_ready": appendEvidenceNote(run, `已载入 ${event.files?.length || 0} 份文档的检索上下文`); setRunPhase(run, "正在查阅研究资料"); break;
            case "context_window": {
                const used = Number(event.estimated_request_tokens ?? event.estimated_tokens) || 0;
                const max = Number(event.max_tokens) || 0;
                if (max > 0) {
                    const percent = Math.max(0, Math.min(100, used / max * 100));
                    els["context-usage"].textContent = `约 ${Math.round(percent)}%`;
                    els["context-meter"].setAttribute("aria-valuenow", String(Math.round(percent)));
                    els["context-meter"].setAttribute("aria-valuetext", `估算 ${used.toLocaleString()} / ${max.toLocaleString()} tokens`);
                    els["context-fill"].style.transform = `scaleX(${percent / 100})`;
                    els["workspace-note"].textContent = "较早的工具证据会按需整理为记忆，为后续追问保留空间。";
                }
                break;
            }
            case "context_compact": updateCompaction(run, event); break;
            case "thinking": appendThinking(run, event); setRunPhase(run, "正在分析文档与证据"); break;
            case "token": run.answerText = (run.answerText || "") + (event.text || ""); run.view.answerNode.appendData(event.text || ""); setRunPhase(run, "正在整理回答"); scrollToBottom(); break;
            case "tool_call_delta": {
                const card = createToolCard(run, { ...event, name: event.name_delta || "" });
                if (event.name_delta) { card.name = (card.deltaName || "") + event.name_delta; card.deltaName = card.name; card.label.textContent = toolLabel(card.name); }
                card.argsPre.textContent += event.arguments_delta || ""; break;
            }
            case "tool_call": {
                const card = createToolCard(run, event); if (event.tool_call_id) run.toolCards.set(event.tool_call_id, card);
                card.label.textContent = toolLabel(event.name); card.glyph.replaceChildren(icon(toolIcon(event.name)));
                card.details.title = event.name || ""; card.argsPre.textContent = JSON.stringify(event.arguments || {}, null, 2);
                markTool(event.name); setRunPhase(run, `正在${toolLabel(event.name)}`); refreshIcons(); break;
            }
            case "tool_result": showToolResult(run, event); break;
            case "retry": appendEvidenceNote(run, event.message || "正在重新连接模型"); break;
            case "history_state": state.history = Array.isArray(event.messages) ? event.messages : state.history; break;
            case "done": run.terminal = true; run.completed = true; clearPendingCompaction(run); setStatus("研究完成", "done"); setRunPhase(run, "研究完成 · 证据可展开查看"); run.view.answer.dataset.complete = "true"; finishAnswer(run); break;
            case "error": run.terminal = true; clearPendingCompaction(run); setStatus("研究中断", "error"); setRunPhase(run, "研究暂时中断"); showError(run, event.message); finishAnswer(run); break;
        }
    }
    async function consumeStream(run, response) {
        if (!response.body) throw new Error("未收到响应内容，请重新尝试");
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
        const dispatch = block => {
            const data = block.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("\n");
            if (!data) return;
            let event;
            try { event = JSON.parse(data); } catch { throw new Error("响应格式异常，请重新尝试"); }
            handleEvent(run, event);
        };
        try {
            while (!run.terminal && !run.discarded) {
                const { value, done } = await reader.read(); if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop() || "";
                for (const block of blocks) dispatch(block);
            }
            buffer += decoder.decode(); if (buffer.trim() && !run.terminal) dispatch(buffer);
            if (!run.terminal && !run.discarded) throw new Error("连接提前结束，回答可能不完整。请重新尝试。");
        } finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
    }
    async function submit(event) {
        event.preventDefault();
        const question = els.question.value.trim();
        if (!question || state.controller) return;
        if (!state.conversationFiles.length) { toast("先选择研究文档，问题会为你保留"); openPicker(); return; }
        const fileIds = state.conversationFiles.map(file => file.file_id);
        state.followOutput = true; state.hasStarted = true; createMessage("user", question);
        const view = createAssistantMessage();
        const run = { view, question, toolCards: new Map(), startedAt: Date.now(), answerText: "" };
        state.currentRun = run; state.controller = new AbortController();
        const controller = state.controller;
        state.transcript.push({ role: "user", text: question });
        els.question.value = ""; resizeQuestion(); renderReferences(); setStatus("正在研究", "running");
        const timer = setInterval(() => { view.elapsed.textContent = `${Math.floor((Date.now() - run.startedAt) / 1000)} 秒`; }, 1000);
        try {
            const response = await fetch("/agent/stream", {
                method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
                body: JSON.stringify({ file_ids: fileIds, question, history: state.history }), signal: controller.signal,
            });
            if (run.discarded) return;
            if (!response.ok) {
                let message = response.status === 401 ? "登录状态已失效，请返回工作台重新登录。" : `请求失败（${response.status}），请稍后重试。`;
                try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch {}
                throw new Error(message);
            }
            await consumeStream(run, response);
        } catch (error) {
            if (run.discarded) return;
            if (error.name === "AbortError") { run.stopped = true; appendEvidenceNote(run, "本次研究已停止，已生成的内容保留在下方"); setStatus("已停止", "idle"); setRunPhase(run, "研究已停止"); }
            else { showError(run, error.message); setStatus("研究中断", "error"); setRunPhase(run, "研究暂时中断"); }
        } finally {
            clearInterval(timer);
            if (run.discarded) return;
            clearPendingCompaction(run); finishAnswer(run);
            view.elapsed.textContent = `${Math.max(1, Math.round((Date.now() - run.startedAt) / 1000))} 秒`;
            state.transcript.push({ role: "assistant", text: run.answerText || "", outcome: run.failed ? "研究中断" : run.stopped ? "研究已停止" : "" });
            if (state.currentRun === run) { state.controller = null; state.currentRun = null; syncComposer(); }
        }
    }
    function clearConversation() {
        if (state.currentRun) state.currentRun.discarded = true;
        state.controller?.abort(); state.controller = null; state.currentRun = null;
        state.history = []; state.transcript = []; state.conversationFiles = []; state.hasStarted = false; state.followOutput = true;
        els["chat-inner"].replaceChildren(els.welcome); els.welcome.classList.remove("hidden");
        els.question.value = ""; resizeQuestion();
        els["tool-catalog"].querySelectorAll(".used").forEach(row => row.classList.remove("used"));
        els["context-usage"].textContent = "尚未开始"; els["context-fill"].style.transform = "scaleX(0)";
        els["context-meter"].setAttribute("aria-valuenow", "0"); els["context-meter"].removeAttribute("aria-valuetext");
        els["workspace-note"].textContent = "智能体按需检索章节、表格与原始页图，保留证据，连接上下文。";
        els["jump-latest"].classList.add("hidden"); renderReferences(); setStatus("准备就绪"); toggleRail(false); els.question.focus();
    }
    function selectedCount() {
        const count = state.pickerSelection.size;
        els["picker-count"].textContent = `已选 ${count} 份 · 本次研究已引用 ${state.conversationFiles.length} / 20`;
        els["add-picker"].textContent = count ? `加入 ${count} 份文档` : "加入研究";
        els["add-picker"].disabled = !count || count + state.conversationFiles.length > 20;
    }
    function formatDate(value) {
        if (!value) return "";
        const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
    }
    function renderFileList(items, { append = false } = {}) {
        if (append) els["file-list"].querySelector(".load-more")?.remove(); else els["file-list"].replaceChildren();
        if (!items.length && !append) {
            const empty = document.createElement("div"); empty.className = "picker-empty";
            const text = document.createElement("p"); text.textContent = els["file-search"].value.trim() ? "没有找到匹配的文档，换个关键词试试。" : "文档库中还没有处理完成的 PDF。";
            empty.append(text);
            if (!els["file-search"].value.trim()) { const link = document.createElement("a"); link.className = "quiet-button"; link.href = "/ui/"; link.textContent = "前往文档工作台上传"; empty.append(link); }
            els["file-list"].append(empty);
        }
        for (const file of items) {
            state.pickerFiles.set(file.file_id, file);
            const row = document.createElement("div"); row.className = "file-row";
            const input = document.createElement("input"); input.type = "checkbox"; input.id = `file-${file.file_id}`; input.dataset.fileId = file.file_id;
            const added = state.conversationFiles.some(item => item.file_id === file.file_id);
            input.checked = added || state.pickerSelection.has(file.file_id); input.disabled = added || (!input.checked && state.pickerSelection.size >= 20 - state.conversationFiles.length);
            input.addEventListener("change", () => {
                if (input.checked) state.pickerSelection.add(file.file_id); else state.pickerSelection.delete(file.file_id);
                els["file-list"].querySelectorAll('input[type="checkbox"]').forEach(box => {
                    const inConversation = state.conversationFiles.some(item => item.file_id === box.dataset.fileId);
                    box.disabled = inConversation || (!box.checked && state.pickerSelection.size >= 20 - state.conversationFiles.length);
                }); selectedCount();
            });
            const label = document.createElement("label"); label.htmlFor = input.id;
            const name = document.createElement("strong"); name.textContent = file.file_name || file.file_id;
            const detail = document.createElement("span"); detail.textContent = `PDF · ${file.file_id}`;
            label.append(name, detail); const time = document.createElement("time"); time.textContent = added ? "已加入研究" : formatDate(file.create_time);
            row.append(input, icon("file-text"), label, time); els["file-list"].append(row);
        }
        if (state.pickerPage * 50 < state.pickerTotal) {
            const more = document.createElement("button"); more.className = "load-more"; more.type = "button"; more.textContent = "加载更多文档";
            more.addEventListener("click", () => loadFiles(true)); els["file-list"].append(more);
        }
        selectedCount(); refreshIcons();
    }
    async function loadFiles(append = false) {
        if (append && state.pickerLoading) return;
        state.pickerController?.abort(); state.pickerController = new AbortController();
        const version = ++state.pickerVersion;
        const page = append ? state.pickerPage + 1 : 1;
        if (!append) {
            state.pickerFiles = new Map([...state.pickerFiles].filter(([id]) => state.pickerSelection.has(id)));
            els["file-list"].innerHTML = '<div class="picker-empty">正在查找研究资料…</div>';
        } else { const more = els["file-list"].querySelector(".load-more"); if (more) more.disabled = true; }
        state.pickerLoading = true; els["file-list"].setAttribute("aria-busy", "true"); els["picker-total"].textContent = "正在载入";
        try {
            const params = new URLSearchParams({ q: els["file-search"].value.trim(), page: String(page), page_size: "50" });
            const response = await fetch(`/agent/files?${params}`, { credentials: "same-origin", signal: state.pickerController.signal });
            if (!response.ok) throw new Error(response.status === 401 ? "登录状态已失效，请返回工作台重新登录。" : `文档加载失败（${response.status}）`);
            const body = await response.json(); if (version !== state.pickerVersion) return;
            state.pickerPage = page; state.pickerTotal = body.total || 0;
            els["picker-total"].textContent = `共 ${state.pickerTotal} 份文档`; renderFileList(body.items || [], { append });
        } catch (error) {
            if (version !== state.pickerVersion || error.name === "AbortError") return;
            const empty = document.createElement("div"); empty.className = "picker-empty";
            const text = document.createElement("p"); text.textContent = error.message || "暂时无法加载文档";
            const retry = document.createElement("button"); retry.type = "button"; retry.className = "quiet-button"; retry.textContent = "重新加载";
            retry.addEventListener("click", () => { empty.remove(); loadFiles(append); }); empty.append(text, retry);
            if (!append) els["file-list"].replaceChildren(empty); else els["file-list"].append(empty);
            els["picker-total"].textContent = "加载失败";
        } finally {
            if (version === state.pickerVersion) {
                state.pickerLoading = false; els["file-list"].setAttribute("aria-busy", "false");
                const more = els["file-list"].querySelector(".load-more"); if (more) more.disabled = false;
            }
        }
    }
    function closeDialog(dialog) {
        if (!dialog.open || dialog.classList.contains("is-closing")) return;
        if (reducedMotion()) { dialog.close(); return; }
        dialog.classList.add("is-closing");
        setTimeout(() => { dialog.close(); dialog.classList.remove("is-closing"); }, 180);
    }
    function openPicker() {
        if (state.controller || els["file-picker"].open) return;
        clearTimeout(state.searchTimer); toggleRail(false);
        state.pickerSelection = new Set(); state.pickerFiles = new Map(); els["file-search"].value = "";
        els["file-picker"].classList.remove("is-closing"); els["file-picker"].showModal(); selectedCount(); loadFiles(); els["file-search"].focus();
    }
    function addSelectedFiles() {
        if (!state.pickerSelection.size || state.controller || state.conversationFiles.length + state.pickerSelection.size > 20) return;
        const existing = new Set(state.conversationFiles.map(file => file.file_id));
        const added = [...state.pickerSelection].map(id => state.pickerFiles.get(id)).filter(file => file && !existing.has(file.file_id));
        state.conversationFiles.push(...added); renderReferences(); closeDialog(els["file-picker"]);
        toast(`已加入 ${added.length} 份文档，可以开始提问了`);
    }
    function toggleRail(open) {
        document.body.classList.toggle("rail-open", open); els["toggle-rail"].setAttribute("aria-expanded", String(open));
        const small = window.matchMedia?.("(max-width: 860px)").matches ?? false;
        els.conversation.inert = open && small;
        els["reference-panel"].inert = !open && small;
        if (open && small) els["close-rail"].focus();
    }
    function exportConversation() {
        if (state.controller || !state.transcript.length) return;
        const refs = state.conversationFiles.map(file => `- ${file.file_name || file.file_id}（${file.file_id}）`).join("\n");
        const messages = state.transcript.map(item => `## ${item.role === "user" ? "研究问题" : "析卷回答"}\n\n${item.text}${item.outcome ? `\n\n> ${item.outcome}` : ""}`).join("\n\n---\n\n");
        const blob = new Blob([`# 析卷 AI · 文档研究\n\n## 引用文档\n\n${refs}\n\n${messages}\n`], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "析卷-研究记录.md";
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast("研究记录已导出");
    }
    els.composer.addEventListener("submit", submit);
    els.question.addEventListener("input", () => { resizeQuestion(); syncComposer(); });
    els.question.addEventListener("keydown", event => {
        if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); els.composer.requestSubmit(); }
    });
    for (const id of ["attach-files", "rail-add-files", "welcome-add-files"]) els[id].addEventListener("click", openPicker);
    for (const id of ["close-picker", "cancel-picker"]) els[id].addEventListener("click", () => closeDialog(els["file-picker"]));
    els["add-picker"].addEventListener("click", addSelectedFiles);
    els["file-search"].addEventListener("input", () => {
        clearTimeout(state.searchTimer); state.pickerController?.abort(); ++state.pickerVersion;
        state.searchTimer = setTimeout(() => loadFiles(), 220);
    });
    els["file-picker"].addEventListener("close", () => {
        clearTimeout(state.searchTimer); state.pickerController?.abort(); ++state.pickerVersion; state.pickerLoading = false;
        els.question.focus();
    });
    for (const dialog of [els["file-picker"], els["new-chat-dialog"]]) {
        dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog); });
        dialog.addEventListener("click", event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog(dialog); } });
    }
    els["new-chat"].addEventListener("click", () => {
        if (!state.hasStarted && !els.question.value.trim() && !state.conversationFiles.length) { clearConversation(); return; }
        els["new-chat-dialog"].showModal();
    });
    els["cancel-new-chat"].addEventListener("click", () => closeDialog(els["new-chat-dialog"]));
    els["confirm-new-chat"].addEventListener("click", () => { els["new-chat-dialog"].close(); clearConversation(); });
    els["stop-button"].addEventListener("click", () => state.controller?.abort());
    els["toggle-rail"].addEventListener("click", () => toggleRail(!document.body.classList.contains("rail-open")));
    for (const id of ["close-rail", "rail-backdrop"]) els[id].addEventListener("click", () => { toggleRail(false); els["toggle-rail"].focus(); });
    document.addEventListener("keydown", event => {
        if (!document.body.classList.contains("rail-open") || els["file-picker"].open || els["new-chat-dialog"].open) return;
        if (event.key === "Escape") { toggleRail(false); els["toggle-rail"].focus(); }
        if (event.key === "Tab") {
            const focusable = [...els["reference-panel"].querySelectorAll('button:not(:disabled), a[href], summary')].filter(el => el.getClientRects().length);
            const first = focusable[0], last = focusable.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
    });
    window.matchMedia?.("(max-width: 860px)").addEventListener("change", () => toggleRail(false));
    els["chat-scroll"].addEventListener("scroll", () => {
        const el = els["chat-scroll"]; state.followOutput = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
        els["jump-latest"].classList.toggle("hidden", state.followOutput);
    }, { passive: true });
    els["jump-latest"].addEventListener("click", () => { scrollToBottom(true); els["jump-latest"].classList.add("hidden"); });
    document.querySelectorAll(".prompt-card").forEach(button => button.addEventListener("click", () => {
        els.question.value = button.dataset.prompt; resizeQuestion(); syncComposer(); els.question.focus();
        if (!state.conversationFiles.length) openPicker();
    }));
    els["export-chat"].addEventListener("click", exportConversation);
    renderCatalog(); renderReferences(); toggleRail(false); refreshIcons();
})();
