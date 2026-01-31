/**
 * DotMac Chat Widget
 *
 * Embeddable chat widget for customer support.
 *
 * Usage:
 * <script>
 *   window.DotMacChatWidgetConfig = {
 *     configId: 'your-widget-id',
 *     apiUrl: 'https://your-api.com'
 *   };
 * </script>
 * <script src="https://your-api.com/static/js/chat-widget.js" async></script>
 */

(function() {
  'use strict';

  // Storage keys
  const STORAGE_KEY_SESSION = 'dotmac_widget_session';
  const STORAGE_KEY_FINGERPRINT = 'dotmac_widget_fingerprint';

  /**
   * Generate a simple browser fingerprint for session persistence.
   */
  function generateFingerprint() {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillText('DotMac', 2, 2);

    const data = [
      navigator.userAgent,
      navigator.language,
      screen.width + 'x' + screen.height,
      screen.colorDepth,
      new Date().getTimezoneOffset(),
      canvas.toDataURL()
    ].join('|');

    // Simple hash
    let hash = 0;
    for (let i = 0; i < data.length; i++) {
      const char = data.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash;
    }
    return Math.abs(hash).toString(36);
  }

  /**
   * Sanitize HTML to prevent XSS
   */
  function sanitizeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Format timestamp for display
   */
  function formatTime(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString();
  }

  /**
   * DotMac Chat Widget Class
   */
  class DotMacChatWidget {
    constructor(config) {
      this.configId = config.configId;
      this.apiUrl = config.apiUrl.replace(/\/$/, '');
      this.wsUrl = this.apiUrl.replace(/^http/, 'ws');

      this.widgetConfig = null;
      this.session = null;
      this.messages = [];
      this.ws = null;
      this.isOpen = false;
      this.isConnected = false;
      this.reconnectAttempts = 0;
      this.maxReconnectAttempts = 5;
      this.typingTimeout = null;
      this.pollInterval = null;

      // DOM elements
      this.container = null;
      this.bubble = null;
      this.panel = null;
      this.messagesContainer = null;
      this.inputField = null;
      this.sendButton = null;
      this.prechatForm = null;

      // Bind methods
      this.handleBubbleClick = this.handleBubbleClick.bind(this);
      this.handleSendClick = this.handleSendClick.bind(this);
      this.handleInputKeypress = this.handleInputKeypress.bind(this);
      this.handleInputChange = this.handleInputChange.bind(this);
    }

    /**
     * Initialize the widget
     */
    async init() {
      try {
        // Load widget configuration
        await this.loadConfig();

        // Get or create session
        await this.initSession();

        // Render UI
        this.render();

        // Load message history
        await this.loadHistory();

        // Connect WebSocket
        this.connectWebSocket();

        console.log('[DotMac Widget] Initialized');
      } catch (error) {
        console.error('[DotMac Widget] Initialization failed:', error);
      }
    }

    /**
     * Load widget configuration from API
     */
    async loadConfig() {
      const response = await fetch(`${this.apiUrl}/widget/${this.configId}/config`, {
        headers: {
          'Origin': window.location.origin
        }
      });

      if (!response.ok) {
        throw new Error(`Failed to load widget config: ${response.status}`);
      }

      this.widgetConfig = await response.json();
    }

    /**
     * Initialize or restore session
     */
    async initSession() {
      // Check for existing session
      const stored = localStorage.getItem(STORAGE_KEY_SESSION);
      if (stored) {
        try {
          const parsed = JSON.parse(stored);
          if (parsed.configId === this.configId && parsed.visitorToken) {
            this.session = parsed;
            return;
          }
        } catch (e) {
          localStorage.removeItem(STORAGE_KEY_SESSION);
        }
      }

      // Get or create fingerprint
      let fingerprint = localStorage.getItem(STORAGE_KEY_FINGERPRINT);
      if (!fingerprint) {
        fingerprint = generateFingerprint();
        localStorage.setItem(STORAGE_KEY_FINGERPRINT, fingerprint);
      }

      // Create new session
      const response = await fetch(`${this.apiUrl}/widget/${this.configId}/session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        },
        body: JSON.stringify({
          fingerprint: fingerprint,
          page_url: window.location.href,
          referrer_url: document.referrer || null
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to create session: ${response.status}`);
      }

      const data = await response.json();
      this.session = {
        configId: this.configId,
        sessionId: data.session_id,
        visitorToken: data.visitor_token,
        conversationId: data.conversation_id,
        isIdentified: data.is_identified,
        identifiedName: data.identified_name
      };

      localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(this.session));
    }

    /**
     * Render widget UI
     */
    render() {
      // Create container
      this.container = document.createElement('div');
      this.container.id = 'dotmac-chat-widget';
      this.container.innerHTML = this.getWidgetHTML();
      document.body.appendChild(this.container);

      // Apply styles
      this.injectStyles();

      // Get element references
      this.bubble = this.container.querySelector('.dotmac-widget-bubble');
      this.panel = this.container.querySelector('.dotmac-widget-panel');
      this.messagesContainer = this.container.querySelector('.dotmac-widget-messages');
      this.inputField = this.container.querySelector('.dotmac-widget-input');
      this.sendButton = this.container.querySelector('.dotmac-widget-send');
      this.prechatForm = this.container.querySelector('.dotmac-widget-prechat-form');

      // Attach event listeners
      this.bubble.addEventListener('click', this.handleBubbleClick);
      this.container.querySelector('.dotmac-widget-close').addEventListener('click', this.handleBubbleClick);
      this.sendButton.addEventListener('click', this.handleSendClick);
      this.inputField.addEventListener('keypress', this.handleInputKeypress);
      this.inputField.addEventListener('input', this.handleInputChange);
      if (this.prechatForm) {
        this.prechatForm.addEventListener('submit', (e) => this.handlePrechatSubmit(e));
      }

      if (this.shouldShowPrechat()) {
        this.disableInput(true);
      }

      // Show welcome message if configured
      if (this.widgetConfig.welcome_message && this.messages.length === 0) {
        this.addSystemMessage(this.widgetConfig.welcome_message);
      }
    }

    /**
     * Get widget HTML template
     */
    getWidgetHTML() {
      const position = this.widgetConfig.bubble_position === 'bottom-left' ? 'left' : 'right';
      const color = this.widgetConfig.primary_color || '#3B82F6';

      return `
        <div class="dotmac-widget-bubble dotmac-widget-${position}" style="background-color: ${color}">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M21 11.5C21.0034 12.8199 20.6951 14.1219 20.1 15.3C19.3944 16.7118 18.3098 17.8992 16.9674 18.7293C15.6251 19.5594 14.0782 19.9994 12.5 20C11.1801 20.0035 9.87812 19.6951 8.7 19.1L3 21L4.9 15.3C4.30493 14.1219 3.99656 12.8199 4 11.5C4.00061 9.92179 4.44061 8.37488 5.27072 7.03258C6.10083 5.69028 7.28825 4.6056 8.7 3.90003C9.87812 3.30496 11.1801 2.99659 12.5 3.00003H13C15.0843 3.11502 17.053 3.99479 18.5291 5.47089C20.0052 6.94699 20.885 8.91568 21 11V11.5Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="dotmac-widget-unread" style="display: none">0</span>
        </div>

        <div class="dotmac-widget-panel dotmac-widget-${position}" style="display: none">
          <div class="dotmac-widget-header" style="background-color: ${color}">
            <div class="dotmac-widget-title">${sanitizeHtml(this.widgetConfig.widget_title || 'Chat with us')}</div>
            <button class="dotmac-widget-close" aria-label="Close chat">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </div>

          <div class="dotmac-widget-messages">
            ${this.getPrechatHTML()}
          </div>

          <div class="dotmac-widget-footer">
            <div class="dotmac-widget-input-wrapper">
              <input type="text" class="dotmac-widget-input"
                placeholder="${sanitizeHtml(this.widgetConfig.placeholder_text || 'Type a message...')}"
                maxlength="5000">
              <button class="dotmac-widget-send" style="background-color: ${color}" aria-label="Send message">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M22 2L11 13M22 2L15 22L11 13M22 2L2 9L11 13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </button>
            </div>
            <div class="dotmac-widget-powered">
              Powered by <a href="https://dotmac.io" target="_blank" rel="noopener">DotMac</a>
            </div>
          </div>
        </div>
      `;
    }

    getPrechatHTML() {
      if (!this.shouldShowPrechat()) return '';
      const fields = this.widgetConfig.prechat_fields || [];
      const fieldHtml = fields.map((field) => {
        const label = sanitizeHtml(field.label || field.name);
        const placeholder = sanitizeHtml(field.placeholder || '');
        const name = sanitizeHtml(field.name);
        const required = field.required ? 'required' : '';
        if (field.field_type === 'textarea') {
          return `
            <label class="dotmac-widget-prechat-label">${label}${field.required ? ' *' : ''}</label>
            <textarea class="dotmac-widget-prechat-input" name="${name}" placeholder="${placeholder}" ${required}></textarea>
          `;
        }
        if (field.field_type === 'select') {
          const options = (field.options || []).map((opt) => {
            return `<option value="${sanitizeHtml(opt)}">${sanitizeHtml(opt)}</option>`;
          }).join('');
          return `
            <label class="dotmac-widget-prechat-label">${label}${field.required ? ' *' : ''}</label>
            <select class="dotmac-widget-prechat-input" name="${name}" ${required}>
              <option value="">Select...</option>
              ${options}
            </select>
          `;
        }
        const type = field.field_type === 'email' ? 'email' : (field.field_type === 'phone' ? 'tel' : 'text');
        return `
          <label class="dotmac-widget-prechat-label">${label}${field.required ? ' *' : ''}</label>
          <input class="dotmac-widget-prechat-input" type="${type}" name="${name}" placeholder="${placeholder}" ${required}>
        `;
      }).join('');

      return `
        <div class="dotmac-widget-prechat">
          <div class="dotmac-widget-prechat-title">Before we start</div>
          <div class="dotmac-widget-prechat-error" style="display:none"></div>
          <form class="dotmac-widget-prechat-form">
            ${fieldHtml}
            <button type="submit" class="dotmac-widget-prechat-submit">Start chat</button>
          </form>
        </div>
      `;
    }

    shouldShowPrechat() {
      return Boolean(this.widgetConfig && this.widgetConfig.prechat_form_enabled && !this.session?.isIdentified);
    }

    disableInput(disabled) {
      if (this.inputField) this.inputField.disabled = disabled;
      if (this.sendButton) this.sendButton.disabled = disabled;
    }

    getPrechatValues() {
      const fields = {};
      if (!this.prechatForm) return fields;
      const inputs = this.prechatForm.querySelectorAll('.dotmac-widget-prechat-input');
      inputs.forEach((input) => {
        fields[input.name] = input.value;
      });
      return fields;
    }

    validatePrechat(fields) {
      const configFields = this.widgetConfig.prechat_fields || [];
      const errors = [];
      configFields.forEach((field) => {
        const value = (fields[field.name] || '').trim();
        if (field.required && !value) {
          errors.push(`${field.label || field.name} is required`);
          return;
        }
        if (!value) return;
        if (field.field_type === 'email') {
          if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) {
            errors.push(`${field.label || field.name} must be a valid email`);
          }
        }
        if (field.field_type === 'phone') {
          const digits = value.replace(/\D/g, '');
          if (digits.length < 7) {
            errors.push(`${field.label || field.name} must be a valid phone number`);
          }
        }
        if (field.field_type === 'select' && field.options && !field.options.includes(value)) {
          errors.push(`${field.label || field.name} must be a valid option`);
        }
      });
      return errors;
    }

    async handlePrechatSubmit(event) {
      event.preventDefault();
      const fields = this.getPrechatValues();
      const errors = this.validatePrechat(fields);
      const errorEl = this.container.querySelector('.dotmac-widget-prechat-error');
      if (errors.length) {
        if (errorEl) {
          errorEl.textContent = errors[0];
          errorEl.style.display = 'block';
        }
        return;
      }
      if (errorEl) {
        errorEl.textContent = '';
        errorEl.style.display = 'none';
      }
      try {
        const response = await fetch(`${this.apiUrl}/widget/session/${this.session.sessionId}/prechat`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin,
            'X-Visitor-Token': this.session.visitorToken
          },
          body: JSON.stringify({ fields })
        });
        if (!response.ok) {
          throw new Error(`Pre-chat failed: ${response.status}`);
        }
        const data = await response.json();
        this.session.isIdentified = true;
        this.session.identifiedName = data.name;
        localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(this.session));
        this.prechatForm?.closest('.dotmac-widget-prechat')?.remove();
        this.disableInput(false);
      } catch (error) {
        if (errorEl) {
          errorEl.textContent = 'Unable to submit. Please try again.';
          errorEl.style.display = 'block';
        }
        console.error('[DotMac Widget] Pre-chat failed:', error);
      }
    }

    /**
     * Inject widget styles
     */
    injectStyles() {
      if (document.getElementById('dotmac-widget-styles')) return;

      const styles = document.createElement('style');
      styles.id = 'dotmac-widget-styles';
      styles.textContent = `
        #dotmac-chat-widget {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
          font-size: 14px;
          line-height: 1.5;
          color: #1f2937;
        }

        .dotmac-widget-bubble {
          position: fixed;
          bottom: 20px;
          width: 56px;
          height: 56px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
          transition: transform 0.2s, box-shadow 0.2s;
          z-index: 999999;
          color: white;
        }

        .dotmac-widget-bubble:hover {
          transform: scale(1.05);
          box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
        }

        .dotmac-widget-bubble.dotmac-widget-right { right: 20px; }
        .dotmac-widget-bubble.dotmac-widget-left { left: 20px; }

        .dotmac-widget-unread {
          position: absolute;
          top: -4px;
          right: -4px;
          background: #ef4444;
          color: white;
          font-size: 11px;
          font-weight: 600;
          min-width: 18px;
          height: 18px;
          border-radius: 9px;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0 4px;
        }

        .dotmac-widget-panel {
          position: fixed;
          bottom: 90px;
          width: 380px;
          max-width: calc(100vw - 40px);
          height: 520px;
          max-height: calc(100vh - 120px);
          background: white;
          border-radius: 16px;
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
          display: flex;
          flex-direction: column;
          overflow: hidden;
          z-index: 999998;
        }

        .dotmac-widget-panel.dotmac-widget-right { right: 20px; }
        .dotmac-widget-panel.dotmac-widget-left { left: 20px; }

        .dotmac-widget-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 20px;
          color: white;
        }

        .dotmac-widget-title {
          font-weight: 600;
          font-size: 16px;
        }

        .dotmac-widget-close {
          background: transparent;
          border: none;
          color: white;
          cursor: pointer;
          padding: 4px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 4px;
          transition: background 0.2s;
        }

        .dotmac-widget-close:hover {
          background: rgba(255, 255, 255, 0.2);
        }

        .dotmac-widget-messages {
          flex: 1;
          overflow-y: auto;
          padding: 16px;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .dotmac-widget-message {
          max-width: 85%;
          padding: 10px 14px;
          border-radius: 16px;
          word-wrap: break-word;
        }

        .dotmac-widget-message-inbound {
          align-self: flex-end;
          background: #3b82f6;
          color: white;
          border-bottom-right-radius: 4px;
        }

        .dotmac-widget-message-outbound {
          align-self: flex-start;
          background: #f3f4f6;
          color: #1f2937;
          border-bottom-left-radius: 4px;
        }

        .dotmac-widget-message-system {
          align-self: center;
          background: #fef3c7;
          color: #92400e;
          font-size: 13px;
          text-align: center;
          max-width: 90%;
        }

        .dotmac-widget-message-time {
          font-size: 11px;
          opacity: 0.7;
          margin-top: 4px;
        }

        .dotmac-widget-message-author {
          font-size: 12px;
          font-weight: 500;
          margin-bottom: 4px;
          opacity: 0.9;
        }

        .dotmac-widget-prechat {
          border: 1px solid #e5e7eb;
          border-radius: 12px;
          padding: 12px;
          background: #ffffff;
        }

        .dotmac-widget-prechat-title {
          font-size: 13px;
          font-weight: 600;
          margin-bottom: 8px;
        }

        .dotmac-widget-prechat-label {
          display: block;
          font-size: 12px;
          font-weight: 500;
          margin: 6px 0 4px;
        }

        .dotmac-widget-prechat-input {
          width: 100%;
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          padding: 8px 10px;
          font-size: 13px;
        }

        .dotmac-widget-prechat-error {
          margin-bottom: 8px;
          font-size: 12px;
          color: #b91c1c;
        }

        .dotmac-widget-prechat-submit {
          width: 100%;
          margin-top: 10px;
          border: none;
          border-radius: 10px;
          padding: 9px 12px;
          background: #111827;
          color: #ffffff;
          font-size: 13px;
          cursor: pointer;
        }

        .dotmac-widget-footer {
          padding: 12px 16px;
          border-top: 1px solid #e5e7eb;
        }

        .dotmac-widget-input-wrapper {
          display: flex;
          gap: 8px;
        }

        .dotmac-widget-input {
          flex: 1;
          padding: 10px 14px;
          border: 1px solid #e5e7eb;
          border-radius: 24px;
          outline: none;
          font-size: 14px;
          transition: border-color 0.2s;
        }

        .dotmac-widget-input:focus {
          border-color: #3b82f6;
        }

        .dotmac-widget-send {
          width: 40px;
          height: 40px;
          border: none;
          border-radius: 50%;
          color: white;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: opacity 0.2s;
        }

        .dotmac-widget-send:hover {
          opacity: 0.9;
        }

        .dotmac-widget-send:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .dotmac-widget-powered {
          text-align: center;
          font-size: 11px;
          color: #9ca3af;
          margin-top: 8px;
        }

        .dotmac-widget-powered a {
          color: #6b7280;
          text-decoration: none;
        }

        .dotmac-widget-powered a:hover {
          text-decoration: underline;
        }

        .dotmac-widget-typing {
          display: flex;
          gap: 4px;
          padding: 12px 16px;
          align-self: flex-start;
          background: #f3f4f6;
          border-radius: 16px;
        }

        .dotmac-widget-typing-dot {
          width: 8px;
          height: 8px;
          background: #9ca3af;
          border-radius: 50%;
          animation: dotmac-typing 1.4s infinite ease-in-out;
        }

        .dotmac-widget-typing-dot:nth-child(1) { animation-delay: 0s; }
        .dotmac-widget-typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .dotmac-widget-typing-dot:nth-child(3) { animation-delay: 0.4s; }

        @keyframes dotmac-typing {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-4px); }
        }

        @media (max-width: 480px) {
          .dotmac-widget-panel {
            bottom: 0;
            right: 0;
            left: 0;
            width: 100%;
            max-width: 100%;
            height: 100%;
            max-height: 100%;
            border-radius: 0;
          }

          .dotmac-widget-bubble {
            bottom: 16px;
          }
        }
      `;
      document.head.appendChild(styles);
    }

    /**
     * Handle bubble click - toggle panel
     */
    handleBubbleClick() {
      this.isOpen = !this.isOpen;
      this.panel.style.display = this.isOpen ? 'flex' : 'none';

      if (this.isOpen) {
        this.inputField.focus();
        this.scrollToBottom();
        this.updateUnreadBadge(0);
      }
    }

    /**
     * Handle send button click
     */
    async handleSendClick() {
      const body = this.inputField.value.trim();
      if (!body) return;

      this.inputField.value = '';
      await this.sendMessage(body);
    }

    /**
     * Handle input keypress
     */
    handleInputKeypress(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.handleSendClick();
      }
    }

    /**
     * Handle input change - send typing indicator
     */
    handleInputChange() {
      this.sendTyping(true);

      // Clear previous timeout
      if (this.typingTimeout) {
        clearTimeout(this.typingTimeout);
      }

      // Stop typing after 2 seconds of inactivity
      this.typingTimeout = setTimeout(() => {
        this.sendTyping(false);
      }, 2000);
    }

    /**
     * Load message history
     */
    async loadHistory() {
      if (!this.session.conversationId) return;

      try {
        const response = await fetch(
          `${this.apiUrl}/widget/session/${this.session.sessionId}/messages?limit=50`,
          {
            headers: {
              'X-Visitor-Token': this.session.visitorToken,
              'Origin': window.location.origin
            }
          }
        );

        if (response.ok) {
          const data = await response.json();
          this.messages = data.messages || [];
          this.renderMessages();
        }
      } catch (error) {
        console.error('[DotMac Widget] Failed to load history:', error);
      }
    }

    /**
     * Send a message
     */
    async sendMessage(body) {
      // Optimistically add message
      const tempMessage = {
        id: 'temp-' + Date.now(),
        body: body,
        direction: 'inbound',
        created_at: new Date().toISOString(),
        sending: true
      };
      this.messages.push(tempMessage);
      this.renderMessages();
      this.scrollToBottom();

      try {
        const response = await fetch(
          `${this.apiUrl}/widget/session/${this.session.sessionId}/message`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Visitor-Token': this.session.visitorToken,
              'Origin': window.location.origin
            },
            body: JSON.stringify({ body: body })
          }
        );

        if (!response.ok) {
          throw new Error(`Send failed: ${response.status}`);
        }

        const data = await response.json();

        // Update temp message with real data
        const idx = this.messages.findIndex(m => m.id === tempMessage.id);
        if (idx !== -1) {
          this.messages[idx] = {
            id: data.message_id,
            body: body,
            direction: 'inbound',
            created_at: new Date().toISOString()
          };
        }

        // Update session with conversation ID
        if (data.conversation_id && !this.session.conversationId) {
          this.session.conversationId = data.conversation_id;
          localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(this.session));
        }

        this.renderMessages();
      } catch (error) {
        console.error('[DotMac Widget] Failed to send message:', error);
        // Mark message as failed
        const idx = this.messages.findIndex(m => m.id === tempMessage.id);
        if (idx !== -1) {
          this.messages[idx].failed = true;
          this.messages[idx].sending = false;
        }
        this.renderMessages();
      }
    }

    /**
     * Render messages in the panel
     */
    renderMessages() {
      this.messagesContainer.innerHTML = this.messages.map(msg => {
        const direction = msg.direction === 'inbound' ? 'inbound' : 'outbound';
        const classes = ['dotmac-widget-message', `dotmac-widget-message-${direction}`];

        if (msg.sending) classes.push('dotmac-widget-message-sending');
        if (msg.failed) classes.push('dotmac-widget-message-failed');

        let authorHtml = '';
        if (msg.author_name && direction === 'outbound') {
          authorHtml = `<div class="dotmac-widget-message-author">${sanitizeHtml(msg.author_name)}</div>`;
        }

        return `
          <div class="${classes.join(' ')}">
            ${authorHtml}
            <div class="dotmac-widget-message-body">${sanitizeHtml(msg.body)}</div>
            <div class="dotmac-widget-message-time">${formatTime(msg.created_at)}</div>
          </div>
        `;
      }).join('');
    }

    /**
     * Add a system message
     */
    addSystemMessage(text) {
      const msg = document.createElement('div');
      msg.className = 'dotmac-widget-message dotmac-widget-message-system';
      msg.innerHTML = `<div class="dotmac-widget-message-body">${sanitizeHtml(text)}</div>`;
      this.messagesContainer.appendChild(msg);
    }

    /**
     * Scroll messages to bottom
     */
    scrollToBottom() {
      this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    /**
     * Update unread badge
     */
    updateUnreadBadge(count) {
      const badge = this.container.querySelector('.dotmac-widget-unread');
      if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'flex';
      } else {
        badge.style.display = 'none';
      }
    }

    /**
     * Connect WebSocket
     */
    connectWebSocket() {
      if (!this.session.visitorToken) return;

      try {
        this.ws = new WebSocket(`${this.wsUrl}/ws/widget?token=${this.session.visitorToken}`);

        this.ws.onopen = () => {
          console.log('[DotMac Widget] WebSocket connected');
          this.isConnected = true;
          this.reconnectAttempts = 0;

          // Stop polling if running
          if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
          }
        };

        this.ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            this.handleWebSocketMessage(data);
          } catch (e) {
            console.error('[DotMac Widget] WebSocket message parse error:', e);
          }
        };

        this.ws.onclose = () => {
          console.log('[DotMac Widget] WebSocket disconnected');
          this.isConnected = false;
          this.ws = null;
          this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
          console.error('[DotMac Widget] WebSocket error:', error);
        };
      } catch (error) {
        console.error('[DotMac Widget] WebSocket connection failed:', error);
        this.startPolling();
      }
    }

    /**
     * Handle WebSocket message
     */
    handleWebSocketMessage(data) {
      switch (data.event) {
        case 'message_new':
          // Only handle outbound messages (from agent)
          if (data.data.direction === 'outbound') {
            this.messages.push({
              id: data.data.message_id,
              body: data.data.body,
              direction: 'outbound',
              created_at: data.data.created_at || new Date().toISOString(),
              author_name: data.data.author_name
            });
            this.renderMessages();
            this.scrollToBottom();

            // Update unread if panel is closed
            if (!this.isOpen) {
              const badge = this.container.querySelector('.dotmac-widget-unread');
              const current = parseInt(badge.textContent) || 0;
              this.updateUnreadBadge(current + 1);
            }
          }
          break;

        case 'user_typing':
          // Show typing indicator for agent
          if (!data.data.is_visitor) {
            this.showTypingIndicator(data.data.is_typing);
          }
          break;

        case 'heartbeat':
          // Connection is alive
          break;

        case 'connection_ack':
          console.log('[DotMac Widget] Connection acknowledged');
          break;

        case 'conversation_created':
          // Server notifies us of a new conversation (created via REST API)
          if (data.data && data.data.conversation_id) {
            this.session.conversationId = data.data.conversation_id;
            localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(this.session));
            console.log('[DotMac Widget] Conversation created:', data.data.conversation_id);
          }
          break;
      }
    }

    /**
     * Show/hide typing indicator
     */
    showTypingIndicator(show) {
      let indicator = this.messagesContainer.querySelector('.dotmac-widget-typing');

      if (show && !indicator) {
        indicator = document.createElement('div');
        indicator.className = 'dotmac-widget-typing';
        indicator.innerHTML = `
          <div class="dotmac-widget-typing-dot"></div>
          <div class="dotmac-widget-typing-dot"></div>
          <div class="dotmac-widget-typing-dot"></div>
        `;
        this.messagesContainer.appendChild(indicator);
        this.scrollToBottom();
      } else if (!show && indicator) {
        indicator.remove();
      }
    }

    /**
     * Send typing indicator via WebSocket
     */
    sendTyping(isTyping) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          type: 'typing',
          is_typing: isTyping
        }));
      }
    }

    /**
     * Schedule WebSocket reconnection
     */
    scheduleReconnect() {
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        console.log('[DotMac Widget] Max reconnect attempts reached, falling back to polling');
        this.startPolling();
        return;
      }

      const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
      this.reconnectAttempts++;

      console.log(`[DotMac Widget] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
      setTimeout(() => this.connectWebSocket(), delay);
    }

    /**
     * Start polling for new messages (fallback when WebSocket unavailable)
     */
    startPolling() {
      if (this.pollInterval) return;

      this.pollInterval = setInterval(async () => {
        if (!this.session.conversationId) return;

        try {
          const response = await fetch(
            `${this.apiUrl}/widget/session/${this.session.sessionId}/status`,
            {
              headers: {
                'X-Visitor-Token': this.session.visitorToken,
                'Origin': window.location.origin
              }
            }
          );

          if (response.ok) {
            const data = await response.json();
            if (data.unread_count > 0 && !this.isOpen) {
              this.updateUnreadBadge(data.unread_count);
              // Reload messages to get new ones
              await this.loadHistory();
              this.renderMessages();
            }
          }
        } catch (error) {
          console.error('[DotMac Widget] Polling error:', error);
        }
      }, 10000); // Poll every 10 seconds
    }

    /**
     * Identify the visitor
     */
    async identify(email, name, customFields) {
      try {
        const response = await fetch(
          `${this.apiUrl}/widget/session/${this.session.sessionId}/identify`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Visitor-Token': this.session.visitorToken,
              'Origin': window.location.origin
            },
            body: JSON.stringify({
              email: email,
              name: name,
              custom_fields: customFields
            })
          }
        );

        if (response.ok) {
          const data = await response.json();
          this.session.isIdentified = true;
          this.session.identifiedName = data.name;
          localStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(this.session));
          return true;
        }
      } catch (error) {
        console.error('[DotMac Widget] Identification failed:', error);
      }
      return false;
    }

    /**
     * Destroy the widget
     */
    destroy() {
      if (this.ws) {
        this.ws.close();
      }
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
      }
      if (this.container) {
        this.container.remove();
      }
    }
  }

  // Auto-initialize if config is present
  if (window.DotMacChatWidgetConfig) {
    window.DotMacChatWidget = new DotMacChatWidget(window.DotMacChatWidgetConfig);

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        window.DotMacChatWidget.init();
      });
    } else {
      window.DotMacChatWidget.init();
    }
  }

  // Export for manual initialization
  window.DotMacChatWidgetClass = DotMacChatWidget;
})();
