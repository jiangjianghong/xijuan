/** 用真实浏览器验证重构后的导航、现有模块、响应式布局与减少动效偏好。 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const {createFixtureServer}=require('./workbench_fixture.cjs');
const output=path.resolve(__dirname,'../../.taste-skill/after');

async function main() {
    fs.mkdirSync(output,{recursive:true});
    const server=createFixtureServer();
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const base=`http://127.0.0.1:${server.address().port}`;
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    const failures=[];
    try {
        for(const width of [1440,768,390]) {
            const page=await browser.newPage({viewport:{width,height:900}});
            const errors=[];
            page.on('pageerror',error=>errors.push(error.message));
            await page.goto(base);
            await page.waitForSelector('#file-list-body tr[data-id]');
            await page.evaluate(()=>document.fonts.ready);
            const overflow=async label=>{
                const dims=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,viewport:window.innerWidth}));
                assert.ok(dims.scroll<=dims.viewport+1,`${width} ${label} 水平溢出 ${JSON.stringify(dims)}`);
            };
            await overflow('文件处理');
            assert.equal(await page.locator('#workspace-file-total').innerText(),'共 6 份');
            if(width<=540) {
                assert.equal(await page.locator('#file-list-body td.col-size').first().isVisible(),true);
                assert.equal(await page.locator('#file-list-body td.col-time').first().isVisible(),true);
            }
            await page.waitForTimeout(400);
            await page.screenshot({path:path.join(output,`files-${width}.png`),fullPage:true});
            const navigate=async pageId=>{
                if(width<=1100) await page.locator('#sidebar-toggle').click();
                await page.locator(`.nav-btn[data-page="${pageId}"]`).click();
                await page.waitForSelector(`#page-${pageId}.active`);
                await page.waitForTimeout(450);
                await overflow(pageId);
                assert.equal(await page.locator('.nav-btn[aria-current="page"]').count(),1);
                if(width<=1100) assert.equal(await page.evaluate(()=>document.activeElement.id),'workspace-title');
                assert.equal(await page.locator('.doctype-bar').isVisible(),['file-processing','rule-config','stream-demo'].includes(pageId));
            };
            await navigate('rule-config');
            await page.waitForSelector('#field-list-body tr');
            await page.screenshot({path:path.join(output,`rules-${width}.png`),fullPage:true});
            for(const pageId of ['stream-demo','invocations','runtime-logs','api-docs','statistics','runtime-monitor']) {
                await navigate(pageId);
                if(pageId==='invocations') {
                    await page.waitForSelector('.invocation-row');
                    await page.locator('[data-invocation-toggle]').first().click();
                    await page.waitForSelector('.invocation-detail-top');
                    await page.locator('[data-invocation-result]').first().click();
                    await page.waitForSelector('#invocation-result-dialog[open]');
                    await page.locator('#invocation-result-close').click();
                }
                if(['invocations','stream-demo','statistics','runtime-monitor'].includes(pageId)) await page.screenshot({path:path.join(output,`${pageId}-${width}.png`),fullPage:true});
            }
            await page.locator('#runtime-help-open').click();
            await page.waitForSelector('#runtime-help-backdrop.open');
            await page.keyboard.press('Control+k');
            assert.equal(await page.locator('#workspace-command').isVisible(),false);
            await page.locator('#runtime-help-close').click();
            await page.keyboard.press('Control+k');
            await page.waitForSelector('#workspace-command[open]');
            await page.locator('#workspace-command-input').fill('日志');
            assert.equal(await page.locator('.command-item').count(),2);
            await page.keyboard.press('ArrowDown');
            await page.keyboard.press('Enter');
            await page.waitForSelector('#page-runtime-logs.active');
            await page.goBack();
            await page.waitForSelector('#page-runtime-monitor.active');
            await page.goForward();
            await page.waitForSelector('#page-runtime-logs.active');
            await navigate('file-processing');
            await page.locator('#file-list-body input[type="checkbox"]').first().check();
            await page.evaluate(()=>App.loadFileList());
            assert.equal(await page.locator('#file-list-body input[type="checkbox"]').first().isChecked(),true);
            // 用户实际操作增强下拉框，确认其可访问名称与筛选行为。
            await page.getByRole('combobox',{name:'按处理状态筛选文件'}).click();
            await page.getByRole('option',{name:'已完成',exact:true}).click();
            await page.waitForFunction(()=>document.querySelectorAll('#file-list-body tr[data-id]').length===3);
            await page.getByRole('combobox',{name:'按处理状态筛选文件'}).focus();
            await page.keyboard.press('Enter');
            await page.waitForSelector('.cs-dropdown.cs-open');
            await page.keyboard.press('ArrowUp');
            await page.keyboard.press('ArrowUp');
            await page.keyboard.press('Enter');
            await page.waitForFunction(()=>App.state.statusFilter==='extracting' && document.querySelectorAll('#file-list-body tr[data-id]').length===1);
            await page.keyboard.press('Enter');
            await page.keyboard.press('Space');
            assert.equal(await page.getByRole('combobox',{name:'按处理状态筛选文件'}).getAttribute('aria-expanded'),'false');
            await page.selectOption('#status-filter','queued');
            await page.waitForSelector('.workspace-empty');
            await page.screenshot({path:path.join(output,`empty-${width}.png`),fullPage:true});
            await page.locator('.workspace-empty button').click();
            await page.waitForSelector('#file-list-body tr[data-id]');
            await page.locator('.file-name-cell').first().click();
            await page.waitForSelector('#drawer-overlay.active');
            await page.keyboard.press('Control+k');
            assert.equal(await page.locator('#workspace-command').isVisible(),false);
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('#drawer-overlay').isVisible(),false);
            assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('file-name-cell')),true);
            // 同文件重复选择仍触发处理；测试替换上传处理器，不发业务写请求。
            await page.evaluate(()=>{ window.previewHandleFiles=App.handleFiles; window.previewUploads=[]; App.handleFiles=files=>window.previewUploads.push(files.map(file=>file.name)); });
            const upload={name:'重复选择.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n')};
            await page.locator('#file-input').setInputFiles(upload);
            await page.locator('#file-input').setInputFiles(upload);
            assert.deepEqual(await page.evaluate(()=>window.previewUploads),[['重复选择.pdf'],['重复选择.pdf']]);
            await page.evaluate(()=>{ App.handleFiles=window.previewHandleFiles; });
            await page.locator('.scope-manage').click();
            await page.waitForSelector('#doctype-modal-overlay.active');
            await page.waitForSelector('#doctype-table-body tr');
            await overflow('类型管理');
            await page.locator('#doctype-modal-overlay .rule-modal-close').click();
            if(width<=1100) await page.locator('#sidebar-toggle').click();
            await page.locator('.settings-entry-btn').click();
            await page.waitForSelector('#settings-modal-overlay.active');
            await overflow('系统设置');
            await page.screenshot({path:path.join(output,`settings-${width}.png`),fullPage:true});
            await page.locator('#settings-modal-overlay .rule-modal-close').click();
            if(width<=1100) {
                await page.locator('#sidebar-toggle').click();
                assert.equal(await page.locator('#workspace-sheet').evaluate(el=>el.inert),true);
                await page.screenshot({path:path.join(output,`navigation-${width}.png`),fullPage:true});
                await page.keyboard.press('Escape');
                assert.equal(await page.locator('#workspace-sheet').evaluate(el=>el.inert),false);
                assert.equal(await page.evaluate(()=>document.activeElement.id),'sidebar-toggle');
            }
            await page.emulateMedia({reducedMotion:'reduce'});
            await navigate('invocations');
            assert.equal(await page.evaluate(()=>Workbench.animations.length),0);
            assert.equal(errors.length,0,errors.join('\n'));
            console.log(`${width}px：页面、导航、日志展开/结果、历史、筛选、选择、详情、减少动效均通过`);
            await page.close();
        }
    } catch(error) { failures.push(error); }
    finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
    if(failures.length) throw failures[0];
}
main().catch(error=>{console.error(error);process.exitCode=1;});
