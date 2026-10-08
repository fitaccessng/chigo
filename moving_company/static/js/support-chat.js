(() => {
  const widget = document.getElementById('chigo-support-widget');
  if (!widget) return;
  const fab = document.getElementById('chigo-support-fab');
  const panel = document.getElementById('chigo-support-panel');
  const messages = document.getElementById('chigo-support-messages');
  const form = document.getElementById('chigo-support-form');
  const input = document.getElementById('chigo-support-input');
  const humanButton = document.getElementById('chigo-support-human');
  const csrfToken = document.getElementById('chigo-support-csrf-token')?.value;
  let conversationId = null;
  let pollingTimer = null;
  const seenMessageIds = new Set();
  let latestMessageId = 0;
  let messageListOpen = false;

  const jsonHeaders = {
    'Content-Type': 'application/json',
    ...(csrfToken ? {'X-CSrf-Token': csrfToken} : {}),
  };

  const setOpen = (open) => {
    panel.classList.toggle('hidden', !open);
    fab.setAttribute('aria-expanded', String(open));
    if (open) requestAnimationFrame(() => input.focus());
  };

  const renderMessage = (item) => {
    if (!item.id || seenMessageIds.has(item.id)) return;
    seenMessageIds.add(item.id);
    latestMessageId = Math.max(latestMessageId, Number(item.id) || 0);
    const bubble = document.createElement('div');
    bubble.className = item.sender_type === 'customer'
      ? 'ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-emerald-700 px-4 py-3 text-left text-xs leading-relaxed text-white'
      : 'max-w-[88%] rounded-2xl rounded-tl-sm bg-white px-4 py-3 text-left text-xs leading-relaxed text-stone-700 shadow-sm ring-1 ring-stone-200';
    bubble.textContent = item.message;
    messages.appendChild(bubble);
    if (messageListOpen || messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80) {
      messages.scrollTop = messages.scrollHeight;
    }
  };

  const loadMessages = async () => {
    if (!conversationId) return;
    const url = new URL(`/support/chat/${conversationId}/messages`, window.location.origin);
    if (latestMessageId) url.searchParams.set('after_id', latestMessageId);
    const response = await fetch(url);
    if (!response.ok) return;
    const data = await response.json();
    data.messages.forEach(renderMessage);
  };

  const startPolling = () => {
    clearInterval(pollingTimer);
    pollingTimer = window.setInterval(() => {
      if (!document.hidden) loadMessages();
    }, 2500);
  };

  const createConversation = async () => {
    const response = await fetch('/support/chat/conversations', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({service_type: 'general', booking_id: null}),
    });
    if (!response.ok) return;
    const data = await response.json();
    conversationId = data.conversation_id;
    data.messages.forEach((item) => {
      if (item.id) {
        seenMessageIds.add(item.id);
        latestMessageId = Math.max(latestMessageId, Number(item.id));
      }
      renderMessage(item);
    });
    startPolling();
  };

  const requestHuman = async () => {
    if (!conversationId) await createConversation();
    if (!conversationId) return;
    humanButton.disabled = true;
    humanButton.textContent = 'Requesting human support…';
    const response = await fetch(`/support/chat/${conversationId}/human-request`, {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({reason: 'Customer requested a human agent'}),
    });
    humanButton.disabled = false;
    humanButton.textContent = 'Talk to a human';
    if (response.ok) await sendMessage('I have requested a human support agent. Please wait for an agent to join.');
  };

  fab.addEventListener('click', () => setOpen(panel.classList.contains('hidden')));
  document.getElementById('chigo-support-close').addEventListener('click', () => setOpen(false));
  document.getElementById('chigo-support-minimize').addEventListener('click', () => setOpen(false));
  document.querySelectorAll('[data-support-quick]').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.supportQuick)));
  humanButton.addEventListener('click', requestHuman);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) return;
    input.value = '';
    await sendMessage(value);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) loadMessages();
  });
  window.addEventListener('beforeunload', () => clearInterval(pollingTimer));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => createConversation());
  else createConversation();
})();
