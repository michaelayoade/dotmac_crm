/**
 * Inbox Shortcodes — Type `/` in the reply composer to trigger template autocomplete.
 *
 * Usage: new InboxShortcodes(textareaSelector)
 */
class InboxShortcodes {
    constructor(textareaSelector = '#reply-textarea') {
        this.textarea = null;
        this.dropdown = null;
        this.results = [];
        this.selectedIndex = -1;
        this.debounceTimer = null;
        this.isActive = false;
        this.slashStart = -1;

        this._init(textareaSelector);
    }

    _init(selector) {
        const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!el) return;
        this.textarea = el;

        // Create dropdown
        this.dropdown = document.createElement('div');
        this.dropdown.className = 'inbox-shortcode-dropdown';
        this.dropdown.style.cssText = 'display:none; position:absolute; z-index:100; max-height:240px; overflow-y:auto; width:320px; background:white; border:1px solid #e2e8f0; border-radius:12px; box-shadow:0 10px 25px -5px rgba(0,0,0,0.1); padding:4px 0;';
        this.textarea.parentElement.style.position = 'relative';
        this.textarea.parentElement.appendChild(this.dropdown);

        // Apply dark mode
        if (document.documentElement.classList.contains('dark')) {
            this.dropdown.style.background = '#1e293b';
            this.dropdown.style.borderColor = '#334155';
        }

        this.textarea.addEventListener('input', () => this._onInput());
        this.textarea.addEventListener('keydown', (e) => this._onKeydown(e));
        this.textarea.addEventListener('blur', () => {
            setTimeout(() => this._hide(), 200);
        });
    }

    _onInput() {
        const value = this.textarea.value;
        const cursor = this.textarea.selectionStart;

        // Find the last `/` before cursor that's at start of line or after a space
        let slashPos = -1;
        for (let i = cursor - 1; i >= 0; i--) {
            if (value[i] === '/') {
                if (i === 0 || value[i - 1] === ' ' || value[i - 1] === '\n') {
                    slashPos = i;
                }
                break;
            }
            if (value[i] === ' ' || value[i] === '\n') break;
        }

        if (slashPos === -1) {
            this._hide();
            return;
        }

        const query = value.substring(slashPos + 1, cursor);
        if (query.length === 0) {
            this._hide();
            return;
        }

        this.slashStart = slashPos;
        this.isActive = true;

        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => this._search(query), 200);
    }

    _onKeydown(e) {
        if (!this.isActive || this.results.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.selectedIndex = Math.min(this.selectedIndex + 1, this.results.length - 1);
            this._renderResults();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
            this._renderResults();
        } else if (e.key === 'Enter' && this.selectedIndex >= 0) {
            e.preventDefault();
            this._selectResult(this.selectedIndex);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            this._hide();
        }
    }

    async _search(query) {
        try {
            const resp = await fetch(`/admin/crm/inbox/templates/search?q=${encodeURIComponent(query)}`);
            if (!resp.ok) return;
            this.results = await resp.json();
            this.selectedIndex = this.results.length > 0 ? 0 : -1;
            this._renderResults();
        } catch (e) {
            // Silently fail
        }
    }

    _renderResults() {
        if (this.results.length === 0) {
            this._hide();
            return;
        }

        const isDark = document.documentElement.classList.contains('dark');

        this.dropdown.innerHTML = this.results.map((r, i) => {
            const isSelected = i === this.selectedIndex;
            const bg = isSelected
                ? (isDark ? 'background:#334155;' : 'background:#f1f5f9;')
                : '';
            const textColor = isDark ? 'color:#e2e8f0;' : 'color:#334155;';
            const subColor = isDark ? 'color:#94a3b8;' : 'color:#64748b;';
            const preview = (r.body || '').substring(0, 60) + ((r.body || '').length > 60 ? '...' : '');
            return `<div class="shortcode-item" data-index="${i}" style="padding:8px 12px; cursor:pointer; ${bg}"
                         onmouseenter="this.style.background='${isDark ? '#334155' : '#f1f5f9'}'"
                         onmouseleave="this.style.background='${isSelected ? (isDark ? '#334155' : '#f1f5f9') : ''}'">
                <div style="font-size:13px; font-weight:600; ${textColor}">${this._escapeHtml(r.name)}</div>
                <div style="font-size:11px; ${subColor}; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${this._escapeHtml(preview)}</div>
            </div>`;
        }).join('');

        this.dropdown.style.display = 'block';
        this.dropdown.style.bottom = (this.textarea.offsetHeight + 4) + 'px';
        this.dropdown.style.left = '0';

        // Click handlers
        this.dropdown.querySelectorAll('.shortcode-item').forEach(el => {
            el.addEventListener('mousedown', (e) => {
                e.preventDefault();
                this._selectResult(parseInt(el.dataset.index));
            });
        });
    }

    _selectResult(index) {
        const result = this.results[index];
        if (!result) return;

        const value = this.textarea.value;
        const cursor = this.textarea.selectionStart;
        const before = value.substring(0, this.slashStart);
        const after = value.substring(cursor);
        this.textarea.value = before + result.body + after;
        this.textarea.selectionStart = this.textarea.selectionEnd = before.length + result.body.length;

        // Trigger input event for Alpine/HTMX
        this.textarea.dispatchEvent(new Event('input', { bubbles: true }));
        this._hide();
        this.textarea.focus();
    }

    _hide() {
        this.isActive = false;
        this.results = [];
        this.selectedIndex = -1;
        if (this.dropdown) {
            this.dropdown.style.display = 'none';
            this.dropdown.innerHTML = '';
        }
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
