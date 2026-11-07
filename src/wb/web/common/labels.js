// Japanese display labels for the annotation app.
(function (global) {
  'use strict';

  // The server supplies translations from wb.readiness.
  const REASON_TEXT = global.WB_REASON_TEXT || {};

  // Codes become text; anything else (already a sentence) passes through.
  function reasonText(reason) {
    return REASON_TEXT[reason] || reason;
  }

  // "K / HA" when both are known; panel is {user_label, label, panel_id}.
  function panelName(panel) {
    if (panel.label && panel.user_label) return panel.user_label + ' / ' + panel.label;
    return panel.label || panel.user_label || panel.panel_id;
  }

  // One blocking item as "K / HA: 領域の割り当てが未確認"; panel may be null.
  function describeBlocker(blocker, panel) {
    const who = panel ? panelName(panel) : blocker.panel_id || '全体';
    return who + ': ' + reasonText(blocker.reason);
  }

  global.WbLabels = { REASON_TEXT, reasonText, panelName, describeBlocker };
})(window);
