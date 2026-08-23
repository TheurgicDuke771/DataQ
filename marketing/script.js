// DataQ marketing — "Watch Demo" modal. Progressive enhancement only: the
// button and video asset both work without this (the button just won't
// open the overlay), so a JS-disabled visitor still gets full content.
(function () {
  var openBtn = document.getElementById('watch-demo');
  var overlay = document.getElementById('demo-modal');
  var closeBtn = document.getElementById('demo-modal-close');
  var video = document.getElementById('demo-video');
  if (!openBtn || !overlay || !closeBtn || !video) return;

  function open() {
    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
    video.currentTime = 0;
    video.play();
    document.addEventListener('keydown', onKey);
  }

  function close() {
    overlay.hidden = true;
    document.body.style.overflow = '';
    video.pause();
    document.removeEventListener('keydown', onKey);
  }

  function onKey(e) {
    if (e.key === 'Escape') close();
  }

  openBtn.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) close();
  });
})();
