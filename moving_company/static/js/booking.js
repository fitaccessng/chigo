(() => {
  const fieldValue = (form, name) => {
    const field = form.elements.namedItem(name);
    if (!field) return null;
    if (field instanceof RadioNodeList) return field.value || '';
    if (field.type === 'checkbox') return field.checked ? field.value : '';
    return field.value || '';
  };

  document.querySelectorAll('form[data-question-form]').forEach((form) => {
    const conditionalFields = [...form.querySelectorAll('[data-question-field][data-condition-key]')];
    const sync = () => conditionalFields.forEach((wrapper) => {
      const dependency = form.elements.namedItem(wrapper.dataset.conditionKey);
      const dependencyHidden = dependency && [...(dependency instanceof RadioNodeList ? dependency : [dependency])]
        .some((input) => input.disabled);
      const allowed = (wrapper.dataset.conditionValue || '').split('|').map((value) => value.toLocaleLowerCase());
      const fallback = wrapper.dataset.conditionAnswer || '';
      const answer = fieldValue(form, wrapper.dataset.conditionKey);
      const visible = !dependencyHidden && allowed.includes(String(answer === null ? fallback : answer).toLocaleLowerCase());
      const wasHidden = wrapper.hidden;
      wrapper.hidden = !visible;
      if (visible && wasHidden) wrapper.setAttribute('data-condition-revealed', '');
      if (!visible) wrapper.removeAttribute('data-condition-revealed');
      wrapper.querySelectorAll('input, select, textarea').forEach((input) => {
        if (input.dataset.initialRequired === undefined) input.dataset.initialRequired = String(input.required);
        input.disabled = !visible;
        input.required = visible && input.dataset.initialRequired === 'true';
      });
    });
    form.addEventListener('change', sync);
    sync();
  });

  document.querySelectorAll('.booking-experience [role="alert"]').forEach((alert) => {
    if (!alert.hasAttribute('tabindex')) alert.tabIndex = -1;
  });
})();
