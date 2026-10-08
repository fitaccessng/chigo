(function () {
  if (window.__chigoLocationAutocompleteInitialized) return;
  window.__chigoLocationAutocompleteInitialized = true;

  const state = {
    pickup: { selected: null },
    destination: { selected: null },
  };

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]));
  }

  function fieldIds(kind) {
    return {
      area: `${kind}_area`,
      city: `${kind}_city`,
      state: `${kind}_state`,
      address: `${kind}_address`,
      formatted: `${kind}_formatted_address`,
      latitude: `${kind}_latitude`,
      longitude: `${kind}_longitude`,
      placeId: `${kind}_place_id`,
      provider: `${kind}_provider`,
      rawId: `${kind}_provider_raw_id`,
    };
  }

  function getField(id) {
    return document.getElementById(id);
  }

  function setValue(id, value) {
    const field = getField(id);
    if (field) field.value = value ?? '';
  }

  function buildSelection(result, originalQuery) {
    const address = result.address || result.address_components || {};
    const formatted = result.formatted_address || result.display_name || result.name || 'Selected location';
    return {
      name: result.name || formatted.split(',')[0].trim() || 'Selected location',
      formatted_address: formatted,
      latitude: Number(result.latitude ?? result.lat),
      longitude: Number(result.longitude ?? result.lon),
      provider: result.provider || 'nominatim',
      provider_place_id: result.provider_place_id || result.place_id || result.id || '',
      address,
      area: result.area || address.suburb || address.neighbourhood || address.city_district || address.quarter || '',
      city: result.city || address.city || address.town || address.village || 'Abuja',
      state: result.state || address.state || address.state_district || 'Federal Capital Territory',
      original_query: result.original_query || originalQuery || '',
      normalized_query: result.normalized_query || originalQuery || '',
    };
  }

  function setHiddenFields(kind, selection) {
    const ids = fieldIds(kind);
    setValue(ids.area, selection.area);
    setValue(ids.city, selection.city);
    setValue(ids.state, selection.state);
    setValue(ids.address, selection.formatted_address);
    setValue(ids.formatted, selection.formatted_address);
    setValue(ids.latitude, Number.isFinite(selection.latitude) ? selection.latitude.toFixed(6) : '');
    setValue(ids.longitude, Number.isFinite(selection.longitude) ? selection.longitude.toFixed(6) : '');
    setValue(ids.placeId, selection.provider_place_id);
    setValue(ids.provider, selection.provider);
    setValue(ids.rawId, selection.provider_place_id);
  }

  function clearLocation(kind) {
    state[kind].selected = null;
    const ids = fieldIds(kind);
    Object.values(ids).forEach((id) => setValue(id, ''));
    setValue(ids.city, 'Abuja');
    setValue(ids.state, 'FCT');
    const input = getField(`${kind}_search`);
    if (input) input.value = '';
    const summary = getField(`${kind}_selected_summary`);
    if (summary) {
      summary.classList.add('hidden');
      summary.innerHTML = '';
    }
    invalidateRoute();
  }

  function setSummary(kind, selection) {
    const summary = getField(`${kind}_selected_summary`);
    if (!summary) return;

    summary.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="font-bold text-[11px]">Selected location</div>
          <div class="mt-1 leading-5">${escapeHtml(selection.formatted_address)}</div>
        </div>
        <button type="button" class="shrink-0 rounded-lg border border-white/60 bg-white/70 px-2 py-1 text-[10px] font-bold text-slate-600 hover:bg-white" data-clear-location="${kind}">Clear</button>
      </div>
    `;
    summary.classList.remove('hidden');
    const clearButton = summary.querySelector(`[data-clear-location="${kind}"]`);
    if (clearButton) clearButton.addEventListener('click', () => clearLocation(kind));
  }

  function invalidateRoute() {
    const footer = getField('footer_actions');
    const summary = getField('route_summary');
    const distanceField = getField('distance_km');
    if (footer) footer.classList.add('hidden');
    if (summary) {
      summary.classList.add('hidden');
      summary.innerHTML = '';
    }
    if (distanceField) distanceField.value = '';
  }

  function maybeUnlockDestination(kind) {
    if (kind !== 'pickup') return;
    const dropoffCard = getField('dropoff_card');
    if (dropoffCard) dropoffCard.classList.remove('hidden', 'opacity-0', 'translate-y-4');
  }

  function maybeShowFooter() {
    const footer = getField('footer_actions');
    if (footer && state.pickup.selected && state.destination.selected) {
      footer.classList.remove('hidden');
    }
  }

  function selectLocation(kind, result, originalQuery) {
    const selection = buildSelection(result, originalQuery);
    if (!Number.isFinite(selection.latitude) || !Number.isFinite(selection.longitude)) return;

    state[kind].selected = selection;
    const input = getField(`${kind}_search`);
    if (input) input.value = selection.formatted_address;
    const dropdown = getField(`${kind}_dropdown`);
    if (dropdown) dropdown.classList.add('hidden');

    setHiddenFields(kind, selection);
    setSummary(kind, selection);
    invalidateRoute();
    maybeUnlockDestination(kind);
    maybeShowFooter();
    calculateRoute();
  }

  function renderResults(dropdown, items, activeIndex, onSelect) {
    dropdown.innerHTML = '';
    const deduped = [];
    const seen = new Set();

    (items || []).forEach((item) => {
      const key = [
        item.formatted_address || item.display_name || item.name || '',
        item.latitude || item.lat || '',
        item.longitude || item.lon || '',
      ].join('|');
      if (seen.has(key)) return;
      seen.add(key);
      deduped.push(item);
    });

    if (!deduped.length) {
      dropdown.innerHTML = '<div class="px-4 py-3 text-[11px] text-slate-500">No matching address found. Try a fuller estate, road, landmark, or area name.</div>';
      dropdown.classList.remove('hidden');
      return [];
    }

    deduped.slice(0, 8).forEach((item, index) => {
      const address = item.address || {};
      const area = item.area || address.suburb || address.neighbourhood || address.city_district || '';
      const cityLine = [item.city || address.city || address.town || 'Abuja', item.state || address.state || 'FCT'].filter(Boolean).join(', ');
      const option = document.createElement('button');
      option.type = 'button';
      option.className = [
        'block w-full px-4 py-3 text-left transition-colors hover:bg-slate-50 focus:bg-slate-50 focus:outline-none',
        index === activeIndex ? 'bg-indigo-50' : '',
      ].join(' ');
      option.innerHTML = `
        <div class="min-w-0">
          <div class="truncate text-[13px] font-bold text-slate-900">${escapeHtml(item.name || item.formatted_address || 'Location')}</div>
          <div class="mt-1 text-[11px] leading-4 text-slate-500">${escapeHtml(item.formatted_address || item.display_name || 'Address unavailable')}</div>
          ${area || cityLine ? `<div class="mt-1 text-[10px] font-semibold text-indigo-600">${escapeHtml([area, cityLine].filter(Boolean).join(' · '))}</div>` : ''}
        </div>
      `;
      option.addEventListener('click', () => onSelect(item));
      dropdown.appendChild(option);
    });

    dropdown.classList.remove('hidden');
    return deduped.slice(0, 8);
  }

  function attachClearButton(kind, input) {
    if (getField(`${kind}_clear_search`)) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.id = `${kind}_clear_search`;
    button.className = 'absolute right-3 top-3.5 hidden h-6 w-6 items-center justify-center rounded-full bg-slate-200 text-xs font-bold text-slate-600 hover:bg-slate-300';
    button.setAttribute('aria-label', `Clear ${kind} search`);
    button.textContent = 'x';
    input.parentElement.appendChild(button);
    input.classList.remove('pr-4');
    input.classList.add('pr-12');
    button.addEventListener('click', () => {
      clearLocation(kind);
      button.classList.add('hidden');
      input.focus();
    });
    input.addEventListener('input', () => {
      button.classList.toggle('hidden', !input.value);
      button.classList.toggle('flex', !!input.value);
    });
  }

  function attachAutocomplete(kind) {
    const input = getField(`${kind}_search`);
    const dropdown = getField(`${kind}_dropdown`);
    if (!input || !dropdown) return;

    attachClearButton(kind, input);
    let requestId = 0;
    let timeoutId = null;
    let results = [];
    let activeIndex = -1;

    function closeDropdown() {
      dropdown.classList.add('hidden');
      activeIndex = -1;
    }

    input.addEventListener('input', function () {
      const query = this.value.trim();
      if (timeoutId) clearTimeout(timeoutId);
      requestId += 1;
      results = [];
      activeIndex = -1;
      state[kind].selected = null;
      invalidateRoute();

      if (query.length < 2) {
        closeDropdown();
        return;
      }

      const currentRequest = requestId;
      dropdown.innerHTML = '<div class="px-4 py-3 text-[11px] text-slate-500">Searching...</div>';
      dropdown.classList.remove('hidden');

      timeoutId = setTimeout(() => {
        fetch(`/api/locations/autocomplete?q=${encodeURIComponent(query)}`)
          .then((response) => response.json())
          .then((payload) => {
            if (currentRequest !== requestId) return;
            results = renderResults(dropdown, payload && payload.success ? payload.results || [] : [], activeIndex, (item) => {
              selectLocation(kind, item, query);
            });
          })
          .catch(() => {
            if (currentRequest !== requestId) return;
            dropdown.innerHTML = '<div class="px-4 py-3 text-[11px] text-slate-500">Unable to search right now. Please try again.</div>';
            dropdown.classList.remove('hidden');
          });
      }, 300);
    });

    input.addEventListener('keydown', function (event) {
      if (dropdown.classList.contains('hidden')) return;
      if (event.key === 'Escape') {
        closeDropdown();
        return;
      }
      if (!results.length) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activeIndex = activeIndex >= results.length - 1 ? 0 : activeIndex + 1;
        renderResults(dropdown, results, activeIndex, (item) => selectLocation(kind, item, input.value.trim()));
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        activeIndex = activeIndex <= 0 ? results.length - 1 : activeIndex - 1;
        renderResults(dropdown, results, activeIndex, (item) => selectLocation(kind, item, input.value.trim()));
      }
      if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        selectLocation(kind, results[activeIndex], input.value.trim());
      }
    });

    document.addEventListener('click', function (event) {
      if (!input.contains(event.target) && !dropdown.contains(event.target)) closeDropdown();
    });
  }

  function attachCurrentLocation(kind) {
    const button = getField(`${kind}_current_location`);
    if (!button) return;
    button.addEventListener('click', function () {
      if (!navigator.geolocation) {
        alert('Your browser does not support geolocation. Please type a location manually.');
        return;
      }
      button.textContent = 'Finding...';
      navigator.geolocation.getCurrentPosition((position) => {
        fetch('/api/locations/reverse-geocode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          }),
        })
          .then((response) => response.json())
          .then((payload) => {
            if (payload && payload.success && payload.location) {
              selectLocation(kind, payload.location, 'Current location');
            }
          })
          .finally(() => {
            button.textContent = 'Use my current location';
          });
      }, () => {
        alert('We could not access your location. Please search manually.');
        button.textContent = 'Use my current location';
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    });
  }

  function calculateRoute() {
    if (!state.pickup.selected || !state.destination.selected) return;
    const summary = getField('route_summary');
    const distanceField = getField('distance_km');
    if (summary) {
      summary.classList.remove('hidden');
      summary.innerHTML = '<p class="text-xs font-semibold text-slate-600">Calculating driving distance...</p>';
    }

    fetch('/api/locations/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pickup: {
          latitude: state.pickup.selected.latitude,
          longitude: state.pickup.selected.longitude,
        },
        destination: {
          latitude: state.destination.selected.latitude,
          longitude: state.destination.selected.longitude,
        },
      }),
    })
      .then((response) => response.json())
      .then((payload) => {
        if (!payload || !payload.success || !payload.route) throw new Error('No route');
        const route = payload.route;
        if (distanceField) distanceField.value = route.distance_km;
        if (summary) {
          summary.innerHTML = `
            <div class="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p class="text-xs font-bold uppercase tracking-wider text-slate-500">Route distance</p>
                <p class="mt-1 text-2xl font-black text-slate-900">${escapeHtml(route.distance_km)} km</p>
              </div>
              <p class="text-xs font-medium text-slate-500">Estimated drive time: ${escapeHtml(route.duration_minutes)} minutes</p>
            </div>
          `;
        }
      })
      .catch(() => {
        if (summary) {
          summary.innerHTML = '<p class="text-xs font-semibold text-rose-600">Unable to calculate route distance right now. Please select both locations again or continue after entering the distance manually.</p>';
        }
      });
  }

  function hydrateExisting(kind) {
    const lat = Number(getField(`${kind}_latitude`)?.value);
    const lon = Number(getField(`${kind}_longitude`)?.value);
    const formatted = getField(`${kind}_formatted_address`)?.value || getField(`${kind}_address`)?.value;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !formatted) return;
    selectLocation(kind, {
      formatted_address: formatted,
      latitude: lat,
      longitude: lon,
      provider: getField(`${kind}_provider`)?.value || 'nominatim',
      provider_place_id: getField(`${kind}_place_id`)?.value || '',
    }, formatted);
  }

  function init() {
    ['pickup', 'destination'].forEach((kind) => {
      attachAutocomplete(kind);
      attachCurrentLocation(kind);
      hydrateExisting(kind);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
