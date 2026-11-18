/** 独立 UI 预览数据：仅监听回环地址，不连接数据库、模型或真实上传接口。 */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const ui = path.join(root, 'ui');
const stages = ['parsing', 'tableing', 'chunking', 'embedding', 'extracting', 'analyzing'];
const files = [
    ['2026 年度财务报告.pdf', 'complete', 2480760],
    ['供应链采购合同.pdf', 'extracting', 1249632],
    ['第三季度经营分析.pdf', 'analyzing', 3801450],
    ['产品技术白皮书.pdf', 'complete', 1854760],
    ['项目尽职调查报告.pdf', 'parsing_failed', 4951220],
    ['客户服务协议.pdf', 'complete', 827400],
].map(([file_name, progress, file_size], index) => ({
    file_id: `preview-file-${index + 1}`, file_name, progress, file_size, type_id: 'default',
    create_time: `2026-11-17T${String(10 - index).padStart(2, '0')}:24:00+08:00`,
    ...Object.fromEntries(stages.flatMap((stage, n) => [[`start_${stage}_time`, '2026-11-17T09:24:00+08:00'], [`end_${stage}_time`, `2026-11-17T09:24:${String(10 + n * 5).padStart(2, '0')}+08:00`]])),
}));
const types = [
    { type_id: 'default', type_name: '通用文档', is_default: 1, is_template: 1, file_count: files.length, field_count: 3, rule_count: 2 },
    { type_id: 'contracts', type_name: '合同审查', is_default: 0, is_template: 1, file_count: 0, field_count: 0, rule_count: 0 },
];
const fields = ['企业名称', '营业收入', '报告日期'].map((field_name, i) => ({
    id: i + 1, field_id: `field_${i + 1}`, field_name, source_type: i === 1 ? 'table' : 'text', priority: i, enabled: 1,
    source_config: {}, prompt_template: '请提取 <search_result>正文</search_result>', use_llm: 1, is_advanced: 0,
}));
const rules = [
    { id: 1, rule_id: 'revenue_check', rule_name: '收入完整性检查', rule_type: 'judge', priority: 1, enabled: 1, depend_fields: ['field_2'], expression: '', prompt_template: '判断收入是否完整。' },
    { id: 2, rule_id: 'revenue_scale', rule_name: '营业收入单位换算', rule_type: 'calc', priority: 2, enabled: 1, depend_fields: ['field_2'], expression: '1000 * 10000' },
];
const invocations = files.map((file, i) => ({
    log_id: `preview-call-${i + 1}`, kind: i === 2 ? 'independent_analysis' : i === 4 ? 'file_retry' : 'file_parse',
    ...file, status: file.progress === 'complete' ? 'complete' : file.progress.endsWith('failed') ? 'failed' : 'running',
    created_at: file.create_time, started_at: file.create_time, finished_at: file.create_time,
    duration_ms: 16520 + i * 7450, mode: 'async', biz_ids: i === 2 ? ['季度经营复核'] : [], item_count: 1,
    stage_times: Object.fromEntries(stages.map(stage => [stage, { start: file.start_parsing_time, end: file.end_analyzing_time, duration_ms: 3750 }])),
    request_input: { query: [['mode', 'async']], form: [['file', 'xxx'], ['type_id', 'default']] },
    result: { extraction: [{ field_name: '企业名称', value: '示例企业', reason: 'UI 测试数据' }], analysis: [{ rule_name: '收入完整性检查', result: true }] },
}));
const pools = [
    ['global_llm', '文本 LLM', '模型通道', 16, 7], ['global_embedding', 'Embedding', '模型通道', 4, 1],
    ['global_vl', 'VL 视觉', '模型通道', 8, 2], ['global_rerank', 'Rerank', '模型通道', 8, 0],
    ['global_table_validation', '表名校验', '业务阶段', 10, 2], ['global_extraction', '字段抽取', '业务阶段', 8, 3],
    ['global_analysis', '逻辑分析总池', '业务阶段', 8, 1], ['independent_analysis', '独立分析', '独立接口', 4, 1],
    ['global_pipeline', '文件管线', '管线', 4, 2],
].map(([id,label,group,limit,active]) => ({ id,label,group,limit,active,queued: 0,scope:'global',completed:12,status:'normal',constraints:[],tasks:[],gate_wait_p95_ms:18,total_wait_p95_ms:180 }));
const mime = { '.css':'text/css', '.js':'text/javascript', '.html':'text/html', '.svg':'image/svg+xml', '.ttf':'font/ttf', '.woff2':'font/woff2' };
function createFixtureServer({ before = false } = {}) {
    return http.createServer((req, res) => {
        const url = new URL(req.url, 'http://localhost');
        const pathname = decodeURIComponent(url.pathname);
        const send = data => { res.writeHead(200, {'content-type':'application/json; charset=utf-8', 'cache-control':'no-store'}); res.end(JSON.stringify({code:200,message:'UI 预览测试数据',data})); };
        if (!['GET','HEAD'].includes(req.method)) { res.writeHead(405); return res.end('预览不执行业务写操作'); }
        if (pathname === '/auth/session') return send({authenticated:true});
        if (pathname === '/doctype/projects') return send([{project_id:'preview',project_name:'示例项目',type_count:2}]);
        if (pathname === '/doctype/list') return send(url.searchParams.has('page') ? {items:types,total:types.length} : types);
        if (/\/doctype\/.+\/params$/.test(pathname)) return send([]);
        if (/\/doctype\/.+\/config-version$/.test(pathname) || pathname.includes('config-version')) return send({version:'preview'});
        if (pathname === '/file/list') {
            const items = url.searchParams.get('type_id') === 'contracts' ? [] : files.filter(file => !url.searchParams.get('status') || file.progress === url.searchParams.get('status'));
            return send({items,total:items.length,page:1,page_size:20,total_pages:1});
        }
        if (pathname === '/file/processing') return send(url.searchParams.get('type_id') === 'contracts' ? [] : files.filter(file => ['extracting','analyzing'].includes(file.progress)));
        if (pathname === '/file/stats') return send({
            overview: {total_files:files.length,completed:3,success_rate:50,total_size:files.reduce((s,f)=>s+f.file_size,0),avg_total_seconds:42.8,type_count:2,project_count:1},
            status_distribution: [{status:'complete',count:3},{status:'extracting',count:1},{status:'analyzing',count:1},{status:'parsing_failed',count:1}],
            by_project:[{project_name:'示例项目',count:6}],by_type:[{type_name:'通用文档',count:6}],
            trend:[{date:'2026-11-15',count:1},{date:'2026-11-16',count:2},{date:'2026-11-17',count:3}],
            stage_durations: stages.map((stage,i)=>({stage,avg_seconds:8+i,min_seconds:3,max_seconds:25,count:6})),
        });
        if (/\/file\/.+\/detail$/.test(pathname) || /\/file\/.+\/status$/.test(pathname)) return send(files.find(file=>pathname.includes(file.file_id)) || files[0]);
        if (/\/file\/.+\/outline$/.test(pathname)) return send({sections:[{title:'一、文档概况',level:1,content:'这是独立 UI 预览中的测试内容。',page_num:1,children:[]}],total:1});
        if (/\/file\/.+\/(tables|chunks)$/.test(pathname)) return send({items:[],tables:[],chunks:[],total:0});
        if (/\/file\/.+\/(extraction|analysis)$/.test(pathname)) return send({results:[],total:0});
        if (/\/file\/.+\/content$/.test(pathname)) return send({content:'# 文档概况\n这是 UI 预览测试内容。',page_mapping:[]});
        if (pathname === '/extraction/fields') return send(fields);
        if (pathname === '/extraction/match-prompt-defaults') return send({});
        if (pathname === '/analysis/rules') return send(rules);
        if (pathname === '/invocations') {
            const filtered=invocations.filter(item=>(!url.searchParams.get('kind') || item.kind===url.searchParams.get('kind'))&&(!url.searchParams.get('status') || item.status===url.searchParams.get('status'))&&(!url.searchParams.get('q') || item.file_name.includes(url.searchParams.get('q'))));
            return send({items:filtered,total:filtered.length});
        }
        if (pathname.startsWith('/invocations/')) return send(invocations.find(item=>pathname.endsWith(item.log_id)) || invocations[0]);
        if (pathname === '/log/files') return send([]);
        if (pathname === '/api-reference') return send({content:'# API 手册\n\n## 文件处理\n\n### POST /file/parse\n上传 PDF 文件并启动六阶段处理管线。\n\n## 字段提取\n\n### GET /extraction/fields\n获取当前文档类型的提取配置。',updated_at:'2026-11-17',source:'本地 UI 测试数据'});
        if (pathname === '/runtime/concurrency') return send({
            updated_at:'2026-11-17T10:24:00+08:00',scope:'single-process',summary:{active:pools.reduce((s,p)=>s+p.active,0),capacity:pools.reduce((s,p)=>s+p.limit,0),queued:0,hot_pools:0,total_wait_p95_ms:180},pools,events:[],
            history:{window:url.searchParams.get('window')||'60s',window_seconds:60,bucket_seconds:1,interval_ms:1000,retention_seconds:1800,windows:['60s','5m','30m'],points:Array.from({length:60},(_,i)=>({at:1794882240+i,overall:Math.round(25+8*Math.sin(i/5)),pools:Object.fromEntries(pools.map(p=>[p.id,Math.round(100*p.active/p.limit)]))}))},
        });
        if (pathname === '/settings/session') return send({authenticated:true});
        if (pathname === '/settings/config') return send({version:'preview',config:{
            ...Object.fromEntries(['embedding','extraction','table_name_validation','analysis','agent','rerank','vl_model','web_search'].map(group=>[group,{base_url:'',model:'',api_key:{configured:false}}])),
            mineru:{base_url:'',backend:'pipeline',queue_width:4,parse_timeout:600,max_file_size:104857600},
            chunking:{chunk_size:1000,chunk_overlap:100,max_chunk_size:2000,separators:['\n\n']},
            storage:{max_total_bytes:0,max_retention_minutes:0,cleanup_interval_minutes:10},
        }});
        if (pathname.startsWith('/settings/access')) return send({version:'preview',api_keys:[],ip_allowlist:[],allowed_origins:[],trusted_proxies:[]});
        const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/ui\//,'').replace(/^\//,'');
        const filename = path.resolve(ui, relative);
        if (!filename.startsWith(ui + path.sep) || !fs.existsSync(filename) || fs.statSync(filename).isDirectory()) { res.writeHead(404); return res.end('not found'); }
        res.writeHead(200, {'content-type':`${mime[path.extname(filename)] || 'application/octet-stream'}; charset=utf-8`,'cache-control':'no-store'});
        if (before && ['index.html','js/app.js','js/customSelect.js'].includes(relative)) {
            return res.end(execFileSync('git',['show',`HEAD:ui/${relative}`],{cwd:root}));
        }
        fs.createReadStream(filename).pipe(res);
    });
}
module.exports={createFixtureServer,files};
if (require.main === module) {
    const port=Number(process.env.UI_PREVIEW_PORT || 4173);
    createFixtureServer({before:process.argv.includes('--before')}).listen(port,'127.0.0.1',()=>console.log(`UI 预览（测试数据）：http://127.0.0.1:${port}`));
}
