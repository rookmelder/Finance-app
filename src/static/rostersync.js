/**
 * Roster sync front end.
 *
 * Handles progress bar display and polling when syncing rosters.
 * Plain JS and standard DOM tree operations only.
 */
(function() {
  'use strict';

  var pollTimer = null;
  var pollStartTime = 0;
  var isPolling = false;
  var TIMEOUT_MS = 180000; // 3 minutes

  var panelEl = null;
  var titleEl = null;
  var percentEl = null;
  var trackEl = null;
  var fillEl = null;
  var messagesEl = null;

  function getButtons() {
    return document.querySelectorAll('form[data-roster-sync] button');
  }

  function disableButtons() {
    var btns = getButtons();
    for (var i = 0; i < btns.length; i++) {
      btns[i].disabled = true;
    }
  }

  function enableButtons() {
    var btns = getButtons();
    for (var i = 0; i < btns.length; i++) {
      btns[i].disabled = false;
    }
  }

  function getOrCreatePanel() {
    var panel = document.querySelector('[data-rs-panel]');
    if (!panel) {
      panel = document.createElement('div');
      panel.setAttribute('data-rs-panel', '');
      var container = document.querySelector('main.container') || document.body;
      if (container.firstChild) {
        container.insertBefore(panel, container.firstChild);
      } else {
        container.appendChild(panel);
      }
    }
    panel.classList.add('rs-progress');
    panelEl = panel;
    return panel;
  }

  function showPanel() {
    var panel = getOrCreatePanel();
    while (panel.firstChild) {
      panel.removeChild(panel.firstChild);
    }

    var head = document.createElement('div');
    head.className = 'rs-head';

    titleEl = document.createElement('div');
    titleEl.className = 'rs-title';
    titleEl.setAttribute('aria-live', 'polite');
    titleEl.textContent = 'Starting...';

    percentEl = document.createElement('div');
    percentEl.className = 'rs-percent';
    percentEl.setAttribute('aria-live', 'polite');
    percentEl.textContent = '0%';

    head.appendChild(titleEl);
    head.appendChild(percentEl);

    trackEl = document.createElement('div');
    trackEl.className = 'rs-track';
    trackEl.setAttribute('role', 'progressbar');
    trackEl.setAttribute('aria-valuemin', '0');
    trackEl.setAttribute('aria-valuemax', '100');
    trackEl.setAttribute('aria-valuenow', '0');

    fillEl = document.createElement('div');
    fillEl.className = 'rs-fill rs-stripes';
    fillEl.style.width = '0%';

    trackEl.appendChild(fillEl);

    messagesEl = document.createElement('div');
    messagesEl.className = 'rs-messages';
    messagesEl.setAttribute('aria-live', 'polite');

    panel.appendChild(head);
    panel.appendChild(trackEl);
    panel.appendChild(messagesEl);
    panel.style.display = '';
  }

  function updateProgress(data) {
    if (!trackEl) {
      showPanel();
    }
    var pct = Math.max(0, Math.min(100, Math.round(data.percent || 0)));
    if (titleEl) {
      titleEl.textContent = data.text || 'Starting...';
    }
    if (percentEl) {
      percentEl.textContent = pct + '%';
    }
    if (trackEl) {
      trackEl.setAttribute('aria-valuenow', String(pct));
    }
    if (fillEl) {
      fillEl.style.width = pct + '%';
      if (data.running) {
        fillEl.classList.add('rs-stripes');
      }
    }
  }

  function handleDone(data) {
    if (!trackEl) {
      showPanel();
    }
    if (fillEl) {
      fillEl.style.width = '100%';
      fillEl.classList.remove('rs-stripes');
    }
    if (trackEl) {
      trackEl.setAttribute('aria-valuenow', '100');
    }
    if (percentEl) {
      percentEl.textContent = '100%';
    }
    if (titleEl && data.text) {
      titleEl.textContent = data.text;
    }
    if (messagesEl) {
      while (messagesEl.firstChild) {
        messagesEl.removeChild(messagesEl.firstChild);
      }
      var msgEl = document.createElement('div');
      msgEl.className = data.ok ? 'rs-ok' : 'rs-error';
      msgEl.textContent = data.message || (data.ok ? 'Roster updated.' : 'Sync failed.');
      messagesEl.appendChild(msgEl);

      if (data.gcal === 'error') {
        var gcalEl = document.createElement('div');
        gcalEl.className = 'rs-error';
        gcalEl.textContent = 'Google Calendar update failed';
        messagesEl.appendChild(gcalEl);
      }
    }
    if (data.ok) {
      setTimeout(function() {
        window.location.reload();
      }, 1500);
    } else {
      enableButtons();
    }
  }

  function showError(errText) {
    if (!trackEl) {
      showPanel();
    }
    if (titleEl) {
      titleEl.textContent = 'Error';
    }
    if (fillEl) {
      fillEl.classList.remove('rs-stripes');
    }
    if (messagesEl) {
      while (messagesEl.firstChild) {
        messagesEl.removeChild(messagesEl.firstChild);
      }
      var errEl = document.createElement('div');
      errEl.className = 'rs-error';
      errEl.textContent = errText;
      messagesEl.appendChild(errEl);
    }
    enableButtons();
  }

  function startPolling() {
    if (isPolling) return;
    isPolling = true;
    pollStartTime = Date.now();
    poll();
  }

  function stopPolling() {
    isPolling = false;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function poll() {
    if (!isPolling) return;
    if (Date.now() - pollStartTime > TIMEOUT_MS) {
      stopPolling();
      showError('Roster sync timed out. Please try again.');
      return;
    }
    fetch('/roster/progress', {
      headers: {
        'Accept': 'application/json'
      },
      cache: 'no-store'
    })
      .then(function(res) {
        if (!res.ok) {
          throw new Error('Progress fetch failed');
        }
        return res.json();
      })
      .then(function(data) {
        if (!isPolling) return;
        if (!data) return;
        updateProgress(data);
        if (data.done) {
          stopPolling();
          handleDone(data);
        } else {
          pollTimer = setTimeout(poll, 600);
        }
      })
      .catch(function() {
        stopPolling();
        showError('Could not reach the server');
      });
  }

  function init() {
    var forms = document.querySelectorAll('form[data-roster-sync]');

    for (var i = 0; i < forms.length; i++) {
      forms[i].addEventListener('submit', function(e) {
        e.preventDefault();
        var form = this;
        disableButtons();
        showPanel();

        var actionUrl = form.getAttribute('action') || form.action || '/roster/sync';
        var formData = new FormData(form);

        fetch(actionUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'X-Requested-With': 'fetch',
            'Accept': 'application/json'
          },
          body: formData
        })
          .then(function(res) {
            return res.json().then(function(data) {
              return { ok: res.ok, data: data };
            }).catch(function() {
              return { ok: res.ok, data: null };
            });
          })
          .then(function(result) {
            if (!result.ok || !result.data || !result.data.ok) {
              var msg = (result.data && result.data.message) ? result.data.message : 'Could not start sync';
              showError(msg);
            } else {
              startPolling();
            }
          })
          .catch(function() {
            showError('Could not reach the server');
          });
      });
    }

    // On page load GET /roster/progress once: if running, resume showing the panel and polling
    fetch('/roster/progress', {
      headers: {
        'Accept': 'application/json'
      },
      cache: 'no-store'
    })
      .then(function(res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function(data) {
        if (data && data.running) {
          disableButtons();
          showPanel();
          updateProgress(data);
          startPolling();
        }
      })
      .catch(function() {
        // Page-load check error ignored
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
