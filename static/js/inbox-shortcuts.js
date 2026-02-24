/**
 * Inbox Keyboard Shortcuts
 *
 * Vim-style shortcuts for common inbox actions.
 * Skips when focus is in input/textarea/select/contenteditable.
 *
 * Shortcuts:
 *   r — Focus reply composer
 *   e — Resolve current conversation
 *   j — Navigate to next conversation
 *   k — Navigate to previous conversation
 *   ? — Show/hide help modal
 *   Escape — Close help modal
 */
class InboxShortcuts {
    constructor() {
        this._bound = this._onKeydown.bind(this);
        document.addEventListener('keydown', this._bound);
    }

    destroy() {
        document.removeEventListener('keydown', this._bound);
    }

    _onKeydown(e) {
        // Skip when typing in form elements
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
        if (e.target.isContentEditable) return;
        // Skip if modifier keys are held (except shift for ?)
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        switch (e.key) {
            case 'r':
                e.preventDefault();
                this._dispatch('inbox-shortcut-reply');
                break;
            case 'e':
                e.preventDefault();
                this._dispatch('inbox-shortcut-resolve');
                break;
            case 'j':
                e.preventDefault();
                this._dispatch('inbox-shortcut-navigate', { direction: 'next' });
                break;
            case 'k':
                e.preventDefault();
                this._dispatch('inbox-shortcut-navigate', { direction: 'prev' });
                break;
            case '?':
                e.preventDefault();
                this._dispatch('inbox-shortcut-help');
                break;
            case 'Escape':
                this._dispatch('inbox-shortcut-escape');
                break;
        }
    }

    _dispatch(name, detail = {}) {
        document.dispatchEvent(new CustomEvent(name, { detail }));
    }
}
