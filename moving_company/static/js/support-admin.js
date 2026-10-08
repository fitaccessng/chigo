(() => {
  const forms = document.querySelectorAll('[data-admin-reply-form]');
  const latestMessageIds = new Map();
  let refreshTimer = null;

  const messageId = (node) => Number(node.dataset.messageId || 0);
  const syncMessageIds = (container) => {
    container.querySelectorAll('[data-message-id]').forEach((node) => {
      latestMessageIds.set(node.closest('[data-conversation-card]')?.dataset.conversationId, Math.max(
        latestMessageIds.get(node.closest('[data-conversation-card]')?.dataset.conversationId) || 0,
        messageId(node),
      ));
    });
  };

  const appendMessage = (card, payload) => {
    const messages = card.querySelector('[data-admin-messages]');
    if (!messages || !payload.message) return;
    const node = document.createElement('div');
    node.dataset.messageId = payload.message.id;
    node.className = 'rounded-xl bg-stone-50 px-3 py-2 text-xs';
    node.innerHTML = `<span class="font-bold text-emerald-800">${payload.message.sender_type}:</span> ${escapeHtml(payload.message.message)}`;
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    latestMessageIds.set(card.dataset.conversationId, payload.message.id);
  };

  const escapeHtml = (value) => value.replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[character]));

  const refreshInbox = async () => {
    if (document.hidden) return;
    const cards = [...document.querySelectorAll('[data-conversation-card]')];
    for (const card of cards) {
      const conversationId = card.dataset.conversationId;
      const url = new URL(`/admin/support/${conversationId}/messages`, window.location.origin);
      const latestId = latestMessageIds.get(conversationId) || 0;
      if (latestId) url.searchParams.set('after_id', latestId);
      try {
        const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!response.ok) continue;
        const data = await response.json();
        data.messages.forEach((item) => {
          if (item.id <= (latestMessageIds.get(conversationId) || 0)) return;
          const node = document.createElement('div');
          node.dataset.messageId = item.id;
          node.className = 'rounded-xl bg-stone-50 px-3 py-2 text-xs';
          node.innerHTML = `<span class="font-bold text-emerald-800">${item.sender_type}:</span> ${escapeHtml(item.message)}`;
          card.querySelector('[data-admin-messages]').appendChild(node);
          latestMessageIds.set(conversationId, item.id);
        });
      } catch (error) {
        console.warn('Support inbox polling failed:', error);
      }
    }
  };

  forms.forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      const input = form.elements.message;
      const conversationId = form.dataset.conversationId;
      const csrfToken = form.querySelector('[name="csrf_token"]')?.value;
      if (!input.value.trim() || !button) return;
      button.disabled = true;
      button.textContent = 'Sending…';
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSrf-Token': csrfToken,
          },
          body: JSON.stringify({ message: input.value.trim() }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'Unable to send message.');
        const card = document.querySelector(`[data-conversation-card][data-conversation-id="${conversationId}"]`);
        if (card) appendMessage(card, data);
        input.value = '';
        syncMessageIds(card);
      } catch (error) {
        console.error(error);
      } finally {
        button.disabled = false;
        button.textContent = 'Send';
      }
    });
  });

  document.querySelectorAll('[data-conversation-card]').forEach(syncMessageIds);
  refreshTimer = window.setInterval(refreshInbox, 2500);
  window.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshInbox();
  });
  window.addEventListener('beforeunload', () => window.clearInterval(refreshTimer));
})();
