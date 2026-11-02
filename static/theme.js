/* Light/dark theme controller, loaded by base.html on every page.
 *
 * The inline bootstrap in base.html's <head> applies the saved or system theme
 * before first paint. This file takes over from there and keeps three states
 * apart:
 *   rendered   what <html> shows now (data-theme="dark", or no attribute);
 *   requested  the newest theme asked for. The toggle's label and pressed
 *              state follow it at once; the rendering catches up in one fade;
 *   explicit   the person's own choice, or null while the page follows the
 *              system setting. Only the toggle sets it.
 *
 * A switch is a short crossfade. With the View Transitions API the old page
 * snapshot stays still and the new one fades in over it; without it, the
 * colour tokens transition briefly (.theme-fade in app.css). One snapshot fade
 * runs at a time: a click during it only updates `requested`, and the fade
 * reconciles when it ends, so the newest request wins and rapid clicks never
 * queue a backlog of animations.
 *
 * No failure blocks the toggle. A transition the browser skips or rejects
 * still applies its update, the state is re-read from the DOM after every
 * fade, and a transition that never settles is abandoned. Switching does not
 * touch forms, focus, scroll positions or the network.
 */
(function () {
  'use strict';

  var KEY = 'amorino_theme';
  var CHROME = {light: '#2c1810', dark: '#120d09'};   // theme-color = nav bar
  var root = document.documentElement;

  function valid(theme) { return theme === 'light' || theme === 'dark' ? theme : null; }
  function saved() {
    try { return valid(window.localStorage.getItem(KEY)); } catch (e) { return null; }
  }
  function save(theme) {
    // Blocked storage only costs persistence; the choice still holds here.
    try { window.localStorage.setItem(KEY, theme); } catch (e) {}
  }
  function matches(query) {
    try { return window.matchMedia(query).matches; } catch (e) { return false; }
  }
  function systemTheme() { return matches('(prefers-color-scheme: dark)') ? 'dark' : 'light'; }
  function shownTheme() { return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; }

  var explicit = saved();
  var rendered = shownTheme();
  var requested = rendered;
  var snapshot = null;        // token of the running snapshot fade
  var snapshotsWork = true;   // false once a transition failed to settle
  var fadeTimer = null;       // cleanup of the running colour fade

  function setMeta(name, value) {
    var meta = document.querySelector('meta[name="' + name + '"]');
    if (meta) meta.setAttribute('content', value);
  }
  // Rendering only: the attribute and the metadata that must agree with it.
  // Never persists; only the toggle makes a choice explicit.
  function render(theme) {
    if (theme === 'dark') root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');
    setMeta('color-scheme', theme);
    setMeta('theme-color', CHROME[theme]);
    rendered = theme;
  }
  function syncControls(theme) {
    var dark = theme === 'dark';
    var label = dark ? 'Ativar modo claro' : 'Ativar modo escuro';
    var buttons = document.querySelectorAll('.theme-toggle');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute('aria-pressed', dark ? 'true' : 'false');
      buttons[i].setAttribute('aria-label', label);
      buttons[i].setAttribute('title', label);
    }
  }
  // The duration lives in app.css (--theme-fade-duration); timers read it.
  function fadeMs() {
    var raw = '';
    try { raw = getComputedStyle(root).getPropertyValue('--theme-fade-duration').trim(); } catch (e) {}
    var value = parseFloat(raw);
    if (!(value >= 0)) return 260;
    return /ms$/.test(raw) ? value : value * 1000;
  }

  function stopColourFade() {
    clearTimeout(fadeTimer);
    fadeTimer = null;
    root.classList.remove('theme-fade');
  }
  // Fallback fade: app.css animates the registered colour tokens on <html>
  // while .theme-fade is present. A request during the fade retargets from
  // the current colours, so a reversal stays continuous; only the newest
  // timer exists.
  function colourFade(theme) {
    root.classList.add('theme-fade');
    render(theme);
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(stopColourFade, fadeMs() + 60);
  }

  // Preferred fade. Returns false when the transition cannot start at all.
  function snapshotFade() {
    var token = {applied: false};
    var transition;
    snapshot = token;
    root.classList.add('theme-switching');   // scopes the pseudo-element rules
    try {
      transition = document.startViewTransition(function () {
        // The newest request; nothing at all once this fade was abandoned.
        if (snapshot !== token) return;
        token.applied = true;
        render(requested);
      });
    } catch (e) {
      snapshot = null;
      root.classList.remove('theme-switching');
      return false;
    }
    var watchdog = setTimeout(function () {
      if (snapshot !== token) return;
      snapshotsWork = false;
      try { transition.skipTransition(); } catch (e) {}
      release(token, false);
    }, fadeMs() + 1500);
    function settle() {
      clearTimeout(watchdog);
      release(token, true);
    }
    function ignore() {}
    if (transition) {
      // `ready` rejects when the browser skips the animation (hidden tab,
      // another transition, failed capture); the update still runs.
      if (transition.ready) transition.ready.then(null, ignore);
      if (transition.updateCallbackDone) transition.updateCallbackDone.then(null, ignore);
      if (transition.finished) transition.finished.then(settle, settle);
    }
    return true;
  }
  function release(token, animate) {
    if (snapshot !== token) return;          // a newer fade owns the state
    snapshot = null;
    root.classList.remove('theme-switching');
    rendered = shownTheme();                 // trust the DOM, not the plan
    // A fade that ended without applying its update failed: apply the
    // newest request directly instead of retrying the same route.
    update(animate && token.applied);
  }

  // Bring the rendering in line with `requested`.
  function update(animate) {
    if (snapshot || requested === rendered) return;
    if (!animate || matches('(prefers-reduced-motion: reduce)') ||
        document.visibilityState === 'hidden') {
      stopColourFade();
      render(requested);
      return;
    }
    // A colour fade in progress retargets smoothly, so it keeps the switch.
    if (!fadeTimer && snapshotsWork && typeof document.startViewTransition === 'function' &&
        snapshotFade()) return;
    colourFade(requested);
  }

  // Called by the toggle buttons in base.html (onclick="toggleTheme(event)").
  window.toggleTheme = function () {
    var next = requested === 'dark' ? 'light' : 'dark';
    explicit = next;
    save(next);
    requested = next;
    syncControls(next);
    update(true);
  };

  // While a snapshot fade runs, the browser hit-tests every click to <html>
  // (the transition overlay), even with pointer-events: none on it. A press
  // that lands on a toggle's box is mapped back to it, so a second click is
  // never lost, and the press keeps focus where it was, as on the button.
  var pressed = false;
  function toggleUnder(event) {
    if (event.target !== root) return false;
    var buttons = document.querySelectorAll('.theme-toggle');
    for (var i = 0; i < buttons.length; i++) {
      var box = buttons[i].getBoundingClientRect();
      if (event.clientX >= box.left && event.clientX < box.right &&
          event.clientY >= box.top && event.clientY < box.bottom) return true;
    }
    return false;
  }
  document.addEventListener('mousedown', function (event) {
    pressed = !!snapshot && toggleUnder(event);
    if (pressed) event.preventDefault();
  }, true);
  document.addEventListener('click', function (event) {
    // The fade may end between press and release; the press decides.
    if ((snapshot || pressed) && toggleUnder(event)) window.toggleTheme();
    pressed = false;
  }, true);

  // Follow the system setting only until the person chooses.
  function followSystem() {
    if (explicit) return;
    requested = systemTheme();
    syncControls(requested);
    update(false);
  }
  try {
    var scheme = window.matchMedia('(prefers-color-scheme: dark)');
    if (scheme.addEventListener) scheme.addEventListener('change', followSystem);
    else if (scheme.addListener) scheme.addListener(followSystem);
  } catch (e) {}

  // Back/forward cache: another page may have changed the choice meanwhile.
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    explicit = saved() || explicit;
    requested = explicit || systemTheme();
    syncControls(requested);
    update(false);
  });

  render(rendered);
  syncControls(rendered);
})();
