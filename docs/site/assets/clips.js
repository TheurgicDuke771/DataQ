// navigation.instant swaps page content parsed from an inert document. WebKit
// never runs resource selection for a <video autoplay> adopted that way, so the
// clip sits at readyState 0 with no controls to start it. Kick it explicitly on
// every page load and fall back to visible controls if playback is refused.
document$.subscribe(function () {
  document.querySelectorAll("video[autoplay]").forEach(function (video) {
    if (video.readyState === 0) video.load();
    var attempt = video.play();
    if (attempt && attempt.catch) {
      attempt.catch(function () {
        video.controls = true;
      });
    }
  });
});
