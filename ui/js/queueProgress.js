/**
 * 队列上传描边进度。
 * 通过轮廓路径采样生成一条真实子路径，避免 stroke-dasharray 周期产生重复线段。
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.QueueProgress = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    const sampleCache = new WeakMap();

    function buildPathData(samples, progress) {
        if (!Array.isArray(samples) || samples.length < 2) return '';
        const clamped = Math.min(Math.max(Number(progress) || 0, 0), 1);
        if (clamped === 0) return '';

        const endIndex = Math.max(1, Math.round(clamped * (samples.length - 1)));
        return samples.slice(0, endIndex + 1).map((point, index) => {
            const x = Number(point.x.toFixed ? point.x.toFixed(2) : point.x);
            const y = Number(point.y.toFixed ? point.y.toFixed(2) : point.y);
            return `${index === 0 ? 'M' : 'L'}${x} ${y}`;
        }).join(' ');
    }

    function sampleGuide(guide, count = 400) {
        if (sampleCache.has(guide)) return sampleCache.get(guide);
        const length = guide.getTotalLength();
        const samples = Array.from({ length: count + 1 }, (_, index) => {
            return guide.getPointAtLength(length * index / count);
        });
        sampleCache.set(guide, samples);
        return samples;
    }

    function updateCard(card, percent) {
        if (!card) return;
        const progressPath = card.querySelector('[data-upload-progress-path]');
        const guide = card.querySelector('[data-upload-progress-guide]');
        const percentLabel = card.querySelector('[data-upload-percent]');
        if (!progressPath || !guide) return;

        if (percent == null || !Number.isFinite(Number(percent))) {
            card.classList.add('is-upload-indeterminate');
            progressPath.setAttribute('d', buildPathData(sampleGuide(guide), 0.22));
            if (percentLabel) percentLabel.textContent = '上传中';
            return;
        }

        card.classList.remove('is-upload-indeterminate');
        const normalized = Math.min(Math.max(Number(percent), 0), 100);
        progressPath.setAttribute('d', buildPathData(sampleGuide(guide), normalized / 100));
        if (percentLabel) percentLabel.textContent = `上传中 ${Math.round(normalized)}%`;
    }

    return { buildPathData, updateCard };
});
