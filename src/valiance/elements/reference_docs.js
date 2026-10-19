// Progressive enhancement: the full reference remains readable without JavaScript.
(() => {
  const search = document.querySelector('#filter');
  const scope = document.querySelector('#scope');
  const clear = document.querySelector('#clear');
  const count = document.querySelector('#result-count');
  const empty = document.querySelector('#empty-state');
  const cards = [...document.querySelectorAll('.element')].map(card => ({
    card, text: card.textContent.toLocaleLowerCase(),
  }));
  const sections = [...document.querySelectorAll('.reference-group')];
  const navigation = [...document.querySelectorAll('nav a')];
  const groups = [...document.querySelectorAll('.nav-group')];
  const scopes = [...document.querySelectorAll('.nav-scope')];
  const disclosures = [...document.querySelectorAll('nav details')];
  const entryRows = new Map([...document.querySelectorAll('nav [data-entry]')]
    .map(row => [row.dataset.entry, row]));
  let savedExpansion = null;
  const update = () => {
    const words = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const filtering = words.length > 0 || scope.value !== 'all';
    if (filtering && savedExpansion === null) {
      savedExpansion = new Map(disclosures.map(details => [details, details.open]));
    }
    if (!filtering && savedExpansion !== null) {
      savedExpansion.forEach((open, details) => { details.open = open; });
      savedExpansion = null;
    }
    let visible = 0;
    cards.forEach(({card, text}) => {
      card.hidden = !(scope.value === 'all' || card.dataset.scope === scope.value)
        || !words.every(word => text.includes(word));
      entryRows.get(card.id).hidden = card.hidden;
      if (!card.hidden) visible++;
    });
    sections.forEach(section => {
      const matches = section.querySelectorAll('.element:not([hidden])').length;
      section.hidden = matches === 0;
      section.querySelector('.section-heading > span').textContent = `${matches} entries`;
      const group = groups.find(item => item.dataset.section === section.id);
      group.hidden = matches === 0;
      group.querySelector('.nav-count').textContent = matches;
      if (filtering && matches > 0) group.querySelector('details').open = true;
    });
    scopes.forEach(row => {
      row.hidden = ![...row.querySelectorAll('.nav-group')].some(group => !group.hidden);
      if (filtering && !row.hidden) row.querySelector('details').open = true;
    });
    count.textContent = `${visible} of ${cards.length} entries`;
    empty.hidden = visible !== 0;
  };
  const reset = () => {
    search.value = '';
    scope.value = 'all';
    update();
  };
  search.addEventListener('input', update);
  scope.addEventListener('change', update);
  clear.addEventListener('click', () => { reset(); search.focus(); });
  document.addEventListener('keydown', event => {
    const editing = event.target.closest('input, select, textarea, [contenteditable]');
    if (event.key === '/' && !editing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      search.focus();
    }
    if (event.key === 'Escape' && editing) { reset(); search.focus(); }
  });
  const revealTarget = () => {
    const target = document.getElementById(location.hash.slice(1));
    if (target?.classList.contains('element')) {
      reset();
      target.scrollIntoView();
    }
    navigation.forEach(link => {
      const selected = link.hash === location.hash;
      if (selected) {
        link.setAttribute('aria-current', 'location');
        let ancestor = link.parentElement;
        while (ancestor && ancestor.tagName !== 'NAV') {
          if (ancestor.tagName === 'DETAILS') ancestor.open = true;
          ancestor = ancestor.parentElement;
        }
      } else {
        link.removeAttribute('aria-current');
      }
    });
  };
  window.addEventListener('hashchange', revealTarget);
  document.querySelector('.search-panel').hidden = false;
  update();
  revealTarget();
})();
