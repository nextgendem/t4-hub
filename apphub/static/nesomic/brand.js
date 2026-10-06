/*
 * Mounts the animated Nesomic wordmark (wordmark.js, a copy of bcs-gui's src/assets/nesomic/wordmark.js)
 * into every .nesomic-wordmark element, with the same settings as the main app's header:
 * finished mark, white, slow idle motion. Loaded right after the header, so the elements exist.
 */
(function () {
  "use strict";
  if (typeof NesomicWordmark === "undefined") return;
  document.querySelectorAll(".nesomic-wordmark").forEach(function (el) {
    NesomicWordmark.mount(el, {intro: false, mono: true, speed: 0.3, fps: 20});
  });
})();
