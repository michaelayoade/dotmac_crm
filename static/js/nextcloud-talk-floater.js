/* global Alpine, getCsrfToken */

function _ncTalkHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  try {
    const token = (typeof getCsrfToken === 'function') ? getCsrfToken() : '';
    if (token) headers['X-CSRF-Token'] = token;
  } catch (e) {
    // ignore
  }
  return headers;
}

async function _ncTalkGetJson(url) {
  const resp = await fetch(url, { method: 'GET', headers: _ncTalkHeaders() });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data && typeof data.detail === 'string') detail = data.detail;
    } catch (e) {
      // ignore
    }
    throw new Error(detail);
  }
  return await resp.json();
}

async function _ncTalkDeleteJson(url) {
  const resp = await fetch(url, { method: 'DELETE', headers: _ncTalkHeaders() });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data && typeof data.detail === 'string') detail = data.detail;
    } catch (e) {
      // ignore
    }
    throw new Error(detail);
  }
  return await resp.json();
}

async function _ncTalkPostJson(url, payload) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: _ncTalkHeaders(),
    body: JSON.stringify(payload || {}),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data && typeof data.detail === 'string') detail = data.detail;
    } catch (e) {
      // ignore
    }
    throw new Error(detail);
  }
  return await resp.json();
}

function nextcloudTalkFloater() {
  const WINDOW_STORAGE_KEY = 'dotmac:nextcloudTalk:window';

  function loadWindowState() {
    try {
      const raw = localStorage.getItem(WINDOW_STORAGE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      return {
        x: Number.isFinite(data.x) ? Number(data.x) : null,
        y: Number.isFinite(data.y) ? Number(data.y) : null,
        w: Number.isFinite(data.w) ? Number(data.w) : null,
        h: Number.isFinite(data.h) ? Number(data.h) : null,
      };
    } catch (e) {
      return null;
    }
  }

  function saveWindowState(state) {
    try {
      localStorage.setItem(WINDOW_STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // ignore
    }
  }

  return {
    open: false,
    connected: false,
    isMobile: window.innerWidth < 640,
    loadingStatus: false,
    loggingIn: false,
    loggingOut: false,
    loadingRooms: false,
    loadingMessages: false,
    sending: false,
    error: null,

    login: {
      base_url: '',
      username: '',
      app_password: '',
    },

    rooms: [],
    selectedRoomToken: null,
    selectedRoomName: null,

    newRoomOpen: false,
    newRoomName: '',
    newRoomType: 'public',
    creatingRoom: false,

    // cursor per room
    lastKnownMessageIdByRoom: {},
    messagesByRoom: {},
    draft: '',

    pollMs: 2500,
    pollTimer: null,

    // Floating window state (desktop)
    win: {
      x: null,
      y: null,
      w: 448,
      h: 640,
    },
    dragging: false,
    dragOffset: { x: 0, y: 0 },
    resizeObserver: null,

    async init() {
      const saved = loadWindowState();
      if (saved) {
        if (saved.x !== null) this.win.x = saved.x;
        if (saved.y !== null) this.win.y = saved.y;
        if (saved.w !== null) this.win.w = saved.w;
        if (saved.h !== null) this.win.h = saved.h;
      }
      this._ensureWindowInBounds();
      window.addEventListener('resize', () => {
        this.isMobile = window.innerWidth < 640;
        this._ensureWindowInBounds();
      });
      await this.refreshStatus();
    },

    _ensureWindowInBounds() {
      // Desktop defaults only; mobile uses full-screen.
      if (this.isMobile) return;
      const margin = 16;
      const minW = 320;
      const minH = 360;

      const w = Math.max(minW, Math.min(this.win.w || 448, window.innerWidth - margin * 2));
      const h = Math.max(minH, Math.min(this.win.h || 640, window.innerHeight - margin * 2));

      if (this.win.x === null || this.win.y === null) {
        this.win.x = Math.max(margin, window.innerWidth - w - margin);
        this.win.y = Math.max(margin, 88);
      }

      const maxX = Math.max(margin, window.innerWidth - w - margin);
      const maxY = Math.max(margin, window.innerHeight - h - margin);
      this.win.w = w;
      this.win.h = h;
      this.win.x = Math.max(margin, Math.min(this.win.x, maxX));
      this.win.y = Math.max(margin, Math.min(this.win.y, maxY));
      saveWindowState(this.win);
    },

    windowStyle() {
      if (this.isMobile) return {};
      return {
        left: `${Math.round(this.win.x || 0)}px`,
        top: `${Math.round(this.win.y || 0)}px`,
        width: `${Math.round(this.win.w || 448)}px`,
        height: `${Math.round(this.win.h || 640)}px`,
      };
    },

    async refreshStatus() {
      this.loadingStatus = true;
      this.error = null;
      try {
        const data = await _ncTalkGetJson('/nextcloud-talk/me/status');
        this.connected = !!(data && data.connected);
        if (this.connected) {
          this.login.base_url = data.base_url || '';
          this.login.username = data.username || '';
          this.login.app_password = '';
        } else {
          // keep whatever the user typed (don’t erase) except password
          this.login.app_password = '';
        }
      } catch (e) {
        // If status fails, don’t hard-block UI; show error.
        this.error = e && e.message ? e.message : 'Failed to load Talk status';
        this.connected = false;
      } finally {
        this.loadingStatus = false;
      }
    },

    toggle() {
      this.open = !this.open;
      this.error = null;
      if (this.open) {
        this._ensureWindowInBounds();
        this.ensureStarted();
        this.$nextTick(() => this._installResizeObserver());
      } else {
        this.stopPolling();
      }
    },

    close() {
      this.open = false;
      this.stopPolling();
    },

    startDrag(evt) {
      if (this.isMobile) return;
      try {
        const e = evt && evt.touches && evt.touches[0] ? evt.touches[0] : evt;
        this.dragging = true;
        this.dragOffset.x = Number(e.clientX || 0) - Number(this.win.x || 0);
        this.dragOffset.y = Number(e.clientY || 0) - Number(this.win.y || 0);
        const move = (ev) => this._onDragMove(ev);
        const up = () => this._onDragEnd(move, up);
        window.addEventListener('mousemove', move);
        window.addEventListener('mouseup', up, { once: true });
        window.addEventListener('touchmove', move, { passive: false });
        window.addEventListener('touchend', up, { once: true });
      } catch (e) {
        this.dragging = false;
      }
    },

    _onDragMove(evt) {
      if (!this.dragging || this.isMobile) return;
      const e = evt && evt.touches && evt.touches[0] ? evt.touches[0] : evt;
      if (evt && evt.cancelable) evt.preventDefault();
      const margin = 16;
      const x = Number(e.clientX || 0) - Number(this.dragOffset.x || 0);
      const y = Number(e.clientY || 0) - Number(this.dragOffset.y || 0);
      const maxX = Math.max(margin, window.innerWidth - (this.win.w || 448) - margin);
      const maxY = Math.max(margin, window.innerHeight - (this.win.h || 640) - margin);
      this.win.x = Math.max(margin, Math.min(x, maxX));
      this.win.y = Math.max(margin, Math.min(y, maxY));
      saveWindowState(this.win);
    },

    _onDragEnd(move, up) {
      this.dragging = false;
      try {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('touchmove', move);
      } catch (e) {
        // ignore
      }
      try {
        window.removeEventListener('mouseup', up);
        window.removeEventListener('touchend', up);
      } catch (e) {
        // ignore
      }
    },

    _installResizeObserver() {
      if (this.isMobile) return;
      if (!this.$refs || !this.$refs.panel) return;
      if (this.resizeObserver) return;
      try {
        this.resizeObserver = new ResizeObserver((entries) => {
          const entry = entries && entries[0];
          if (!entry || !entry.contentRect) return;
          const w = Number(entry.contentRect.width || 0);
          const h = Number(entry.contentRect.height || 0);
          if (w > 0 && h > 0) {
            this.win.w = w;
            this.win.h = h;
            this._ensureWindowInBounds();
          }
        });
        this.resizeObserver.observe(this.$refs.panel);
      } catch (e) {
        // ignore
      }
    },

    loginReady() {
      const base_url = (this.login.base_url || '').trim();
      const username = (this.login.username || '').trim();
      const app_password = (this.login.app_password || '').trim();
      return !!(base_url && username && app_password);
    },

    async connect() {
      if (!this.loginReady()) return;
      this.loggingIn = true;
      this.error = null;
      try {
        const payload = {
          base_url: (this.login.base_url || '').trim(),
          username: (this.login.username || '').trim(),
          app_password: (this.login.app_password || '').trim(),
        };
        const data = await _ncTalkPostJson('/nextcloud-talk/me/login', payload);
        this.connected = !!(data && data.connected);
        this.login.app_password = '';
        if (this.open && this.connected) {
          await this.refreshRooms();
          this.startPolling();
        }
      } catch (e) {
        this.connected = false;
        this.error = e && e.message ? e.message : 'Login failed';
      } finally {
        this.loggingIn = false;
      }
    },

    async disconnect() {
      this.loggingOut = true;
      this.error = null;
      try {
        await _ncTalkDeleteJson('/nextcloud-talk/me/logout');
      } catch (e) {
        this.error = e && e.message ? e.message : 'Logout failed';
      } finally {
        this.loggingOut = false;
      }

      this.connected = false;
      this.rooms = [];
      this.selectedRoomToken = null;
      this.selectedRoomName = null;
      this.messagesByRoom = {};
      this.lastKnownMessageIdByRoom = {};
      this.draft = '';
      this.stopPolling();
    },

    async ensureStarted() {
      await this.refreshStatus();
      if (!this.connected) return;
      await this.refreshRooms();
      this.startPolling();
    },

    async refreshRooms() {
      if (!this.connected) return;
      this.loadingRooms = true;
      this.error = null;
      try {
        const data = await _ncTalkGetJson('/nextcloud-talk/me/rooms');
        this.rooms = Array.isArray(data) ? data : [];

        // Keep selection if still present
        if (this.selectedRoomToken) {
          const stillThere = this.rooms.find((r) => (r && (r.token || r.roomToken)) === this.selectedRoomToken);
          if (!stillThere) {
            this.selectedRoomToken = null;
            this.selectedRoomName = null;
          }
        }

        if (!this.selectedRoomToken && this.rooms.length) {
          const first = this.rooms[0] || {};
          const token = first.token || first.roomToken || null;
          const name = first.displayName || first.name || first.roomName || 'Room';
          if (token) {
            await this.selectRoom(String(token), String(name || 'Room'));
          }
        }
      } catch (e) {
        const msg = e && e.message ? e.message : 'Failed to load rooms';
        this.error = msg;
        // If server indicates the user isn't connected, fall back to login view.
        if (typeof msg === 'string' && msg.toLowerCase().includes('not connected')) {
          this.connected = false;
          this.stopPolling();
        }
      } finally {
        this.loadingRooms = false;
      }
    },

    async selectRoom(token, name) {
      this.selectedRoomToken = token;
      this.selectedRoomName = name || token;
      if (!this.messagesByRoom[this.selectedRoomToken]) {
        this.messagesByRoom[this.selectedRoomToken] = [];
      }
      if (typeof this.lastKnownMessageIdByRoom[this.selectedRoomToken] !== 'number') {
        this.lastKnownMessageIdByRoom[this.selectedRoomToken] = 0;
      }
      await this.refreshMessages(true);
      this.$nextTick(() => this.scrollToBottom());
    },

    async createRoom() {
      if (!this.connected) return;
      const roomName = (this.newRoomName || '').trim();
      if (!roomName) return;
      this.creatingRoom = true;
      this.error = null;
      try {
        const payload = { room_name: roomName, room_type: this.newRoomType || 'public' };
        const data = await _ncTalkPostJson('/nextcloud-talk/me/rooms', payload);
        this.newRoomName = '';
        this.newRoomOpen = false;
        await this.refreshRooms();
        // Best-effort: select token from response if present
        if (data && typeof data === 'object') {
          const token = data.token || data.roomToken || (data.data && (data.data.token || data.data.roomToken));
          if (token) {
            await this.selectRoom(String(token), roomName);
          }
        }
      } catch (e) {
        this.error = e && e.message ? e.message : 'Failed to create room';
      } finally {
        this.creatingRoom = false;
      }
    },

    displayedMessages() {
      if (!this.selectedRoomToken) return [];
      return this.messagesByRoom[this.selectedRoomToken] || [];
    },

    isMine(msg) {
      try {
        const my = String((this.login && this.login.username) || '').trim();
        if (!my) return false;
        const actorId = msg && (msg.actorId || msg.actor_id);
        const actorName = msg && (msg.actorDisplayName || msg.actor_display_name);
        return (actorId && String(actorId) === my) || (actorName && String(actorName) === my);
      } catch (e) {
        return false;
      }
    },

    async refreshMessages(forceFull) {
      if (!this.connected || !this.selectedRoomToken) return;
      this.loadingMessages = true;
      this.error = null;
      try {
        const lastKnown = forceFull ? 0 : (this.lastKnownMessageIdByRoom[this.selectedRoomToken] || 0);
        const payload = { last_known_message_id: Number(lastKnown || 0), limit: 100, timeout: 0 };
        const data = await _ncTalkPostJson(
          `/nextcloud-talk/me/rooms/${encodeURIComponent(this.selectedRoomToken)}/messages/list`,
          payload,
        );
        const incoming = Array.isArray(data) ? data : [];

        let maxId = Number(this.lastKnownMessageIdByRoom[this.selectedRoomToken] || 0);
        const normalized = incoming
          .filter((m) => m && typeof m === 'object')
          .map((m) => {
            const id = Number(m.id || 0);
            if (id > maxId) maxId = id;
            return m;
          });

        if (forceFull) {
          this.messagesByRoom[this.selectedRoomToken] = normalized;
        } else if (normalized.length) {
          const existing = this.messagesByRoom[this.selectedRoomToken] || [];
          const seen = new Set(existing.map((m) => String(m && m.id)));
          for (const m of normalized) {
            const key = String(m && m.id);
            if (!seen.has(key)) {
              existing.push(m);
              seen.add(key);
            }
          }
          this.messagesByRoom[this.selectedRoomToken] = existing;
        }

        this.lastKnownMessageIdByRoom[this.selectedRoomToken] = maxId;
      } catch (e) {
        this.error = e && e.message ? e.message : 'Failed to load messages';
      } finally {
        this.loadingMessages = false;
      }
    },

    startPolling() {
      this.stopPolling();
      this.pollTimer = setInterval(async () => {
        if (!this.open) return;
        if (!this.connected) return;
        if (document.hidden) return;
        if (!this.selectedRoomToken) return;
        if (this.sending) return;
        await this.refreshMessages(false);
      }, this.pollMs);
    },

    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },

    scrollToBottom() {
      try {
        const el = this.$refs && this.$refs.messages;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
      } catch (e) {
        // ignore
      }
    },

    async send() {
      if (!this.connected || !this.selectedRoomToken) return;
      const text = (this.draft || '').trim();
      if (!text) return;
      this.sending = true;
      this.error = null;
      try {
        await _ncTalkPostJson(
          `/nextcloud-talk/me/rooms/${encodeURIComponent(this.selectedRoomToken)}/messages`,
          { message: text },
        );
        this.draft = '';
        await this.refreshMessages(false);
        this.$nextTick(() => this.scrollToBottom());
      } catch (e) {
        this.error = e && e.message ? e.message : 'Send failed';
      } finally {
        this.sending = false;
      }
    },
  };
}

// Make available to Alpine templates
window.nextcloudTalkFloater = nextcloudTalkFloater;
