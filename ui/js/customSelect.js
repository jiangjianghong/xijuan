/**
 * 自定义下拉组件
 * 保留原生 <select> 作为数据载体（value 同步、change 事件透传），
 * 在外层套一层可自定义样式的触发器与弹出面板，使下拉框与整体风格统一。
 *
 * 自动接管所有 select.form-select / select.status-filter，
 * 动态插入的 <select> 通过 MutationObserver 接管。
 */

const CustomSelect = {
    state: {
        activeDropdown: null,
        activeWrapper: null,
        activeTrigger: null,
        activeKeyHandler: null,
        enhanced: new WeakSet(),
    },

    init() {
        this.enhanceAll(document);

        const observer = new MutationObserver(mutations => {
            for (const m of mutations) {
                for (const node of m.addedNodes) {
                    if (node.nodeType !== 1) continue;
                    if (node.tagName === 'SELECT') {
                        this.enhance(node);
                    } else {
                        this.enhanceAll(node);
                    }
                }
                if (this.state.activeWrapper) {
                    for (const node of m.removedNodes) {
                        if (node.nodeType !== 1) continue;
                        if (node === this.state.activeWrapper || node.contains?.(this.state.activeWrapper)) {
                            this.close();
                            break;
                        }
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });

        document.addEventListener('click', e => {
            if (!this.state.activeDropdown) return;
            if (this.state.activeDropdown.contains(e.target)) return;
            if (this.state.activeWrapper?.contains(e.target)) return;
            this.close();
        });

        window.addEventListener('scroll', () => this.reposition(), true);
        window.addEventListener('resize', () => this.reposition());
    },

    enhanceAll(root) {
        const selects = root.querySelectorAll?.('select.form-select, select.status-filter');
        if (selects) selects.forEach(s => this.enhance(s));
    },

    enhance(select) {
        if (!select.classList.contains('form-select') && !select.classList.contains('status-filter')) return;
        if (this.state.enhanced.has(select)) return;
        this.state.enhanced.add(select);

        // 复用既有 cs-wrapper(innerHTML 往返后产生的"哑"结构):
        // 序列化保留了 wrapper / cs-native select / trigger DOM,但 JS 监听器已丢失,
        // 需重建 trigger 并重新挂载所有监听。
        let wrapper = select.parentNode && select.parentNode.classList?.contains('cs-wrapper')
            ? select.parentNode
            : null;

        if (!wrapper) {
            wrapper = document.createElement('div');
            wrapper.className = 'cs-wrapper';
            if (select.classList.contains('status-filter')) wrapper.classList.add('cs-pill');

            if (select.style.width) {
                wrapper.style.width = select.style.width;
                select.style.width = '';
            }
            if (select.style.minWidth) {
                wrapper.style.minWidth = select.style.minWidth;
                select.style.minWidth = '';
            }

            select.parentNode.insertBefore(wrapper, select);
            wrapper.appendChild(select);
        } else {
            wrapper.classList.remove('cs-active');
            wrapper.querySelectorAll(':scope > :not(select)').forEach(n => n.remove());
        }

        const trigger = document.createElement('div');
        trigger.className = 'cs-trigger';
        trigger.tabIndex = 0;
        trigger.setAttribute('role', 'combobox');
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');

        const label = document.createElement('span');
        label.className = 'cs-label';
        trigger.appendChild(label);

        const chevron = document.createElement('span');
        chevron.className = 'cs-chevron';
        chevron.setAttribute('aria-hidden', 'true');
        trigger.appendChild(chevron);

        wrapper.appendChild(trigger);
        select.classList.add('cs-native');

        const syncLabel = () => {
            const opt = select.options[select.selectedIndex];
            if (opt) {
                label.textContent = opt.textContent;
                label.classList.toggle('cs-placeholder', opt.value === '');
            } else {
                label.textContent = select.dataset.placeholder || '';
                label.classList.add('cs-placeholder');
            }
        };
        syncLabel();
        select.addEventListener('change', syncLabel);
        new MutationObserver(syncLabel).observe(select, { childList: true, subtree: true });

        // 拦截 select.value 直接赋值(原生 setter 不触发 change/不改 DOM 属性,
        // 无法被 MutationObserver 捕获)。同步标签后保持原生语义。
        const proto = HTMLSelectElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
            Object.defineProperty(select, 'value', {
                get() { return desc.get.call(this); },
                set(v) {
                    desc.set.call(this, v);
                    syncLabel();
                },
                configurable: true,
            });
        }

        const syncDisabled = () => {
            trigger.classList.toggle('cs-trigger-disabled', select.disabled);
        };
        syncDisabled();
        new MutationObserver(syncDisabled).observe(select, { attributes: true, attributeFilter: ['disabled'] });

        trigger.addEventListener('click', e => {
            e.stopPropagation();
            if (select.disabled) return;
            if (this.state.activeWrapper === wrapper) this.close();
            else this.open(wrapper, trigger, select);
        });

        trigger.addEventListener('keydown', e => {
            if (select.disabled) return;
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                this.open(wrapper, trigger, select);
            }
        });
    },

    open(wrapper, trigger, select) {
        this.close();

        const dropdown = document.createElement('div');
        dropdown.className = 'cs-dropdown';
        if (wrapper.classList.contains('cs-pill')) dropdown.classList.add('cs-dropdown-pill');

        Array.from(select.options).forEach((opt, idx) => {
            const item = document.createElement('div');
            item.className = 'cs-option';
            if (idx === select.selectedIndex) item.classList.add('cs-selected');
            if (opt.disabled) item.classList.add('cs-disabled');
            item.textContent = opt.textContent;
            item.dataset.value = opt.value;
            item.addEventListener('click', e => {
                e.stopPropagation();
                if (opt.disabled) return;
                select.value = opt.value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
                this.close();
            });
            dropdown.appendChild(item);
        });

        document.body.appendChild(dropdown);
        wrapper.classList.add('cs-active');
        trigger.setAttribute('aria-expanded', 'true');

        this.state.activeDropdown = dropdown;
        this.state.activeWrapper = wrapper;
        this.state.activeTrigger = trigger;

        this.reposition();
        requestAnimationFrame(() => dropdown.classList.add('cs-open'));

        const items = Array.from(dropdown.querySelectorAll('.cs-option:not(.cs-disabled)'));
        let focusIdx = items.findIndex(it => it.classList.contains('cs-selected'));
        if (focusIdx < 0) focusIdx = 0;
        const focusItem = i => {
            items.forEach(it => it.classList.remove('cs-focused'));
            items[i]?.classList.add('cs-focused');
            items[i]?.scrollIntoView({ block: 'nearest' });
        };
        focusItem(focusIdx);

        const onKeyDown = e => {
            if (!this.state.activeDropdown) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                focusIdx = (focusIdx + 1) % items.length;
                focusItem(focusIdx);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                focusIdx = (focusIdx - 1 + items.length) % items.length;
                focusItem(focusIdx);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                items[focusIdx]?.click();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                this.close();
            }
        };
        this.state.activeKeyHandler = onKeyDown;
        document.addEventListener('keydown', onKeyDown);
    },

    close() {
        const { activeDropdown, activeWrapper, activeTrigger, activeKeyHandler } = this.state;
        if (!activeDropdown) return;

        activeDropdown.classList.remove('cs-open');
        activeWrapper?.classList.remove('cs-active');
        activeTrigger?.setAttribute('aria-expanded', 'false');
        if (activeKeyHandler) document.removeEventListener('keydown', activeKeyHandler);

        const el = activeDropdown;
        setTimeout(() => el.remove(), 180);

        this.state.activeDropdown = null;
        this.state.activeWrapper = null;
        this.state.activeTrigger = null;
        this.state.activeKeyHandler = null;
    },

    reposition() {
        const { activeDropdown, activeTrigger } = this.state;
        if (!activeDropdown || !activeTrigger) return;
        if (!document.contains(activeTrigger)) {
            this.close();
            return;
        }

        const rect = activeTrigger.getBoundingClientRect();
        activeDropdown.style.left = rect.left + 'px';
        activeDropdown.style.minWidth = rect.width + 'px';

        const dropdownH = activeDropdown.offsetHeight;
        const spaceBelow = window.innerHeight - rect.bottom;
        const spaceAbove = rect.top;

        if (spaceBelow < dropdownH + 8 && spaceAbove > spaceBelow) {
            activeDropdown.style.top = '';
            activeDropdown.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
            activeDropdown.classList.add('cs-flip-up');
        } else {
            activeDropdown.style.bottom = '';
            activeDropdown.style.top = (rect.bottom + 4) + 'px';
            activeDropdown.classList.remove('cs-flip-up');
        }
    },
};

document.addEventListener('DOMContentLoaded', () => CustomSelect.init());
