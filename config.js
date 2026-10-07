window.__DATAQ_CONFIG__ = { auth: { mode: 'bypass' } };
(function () {
  var route = new URLSearchParams(location.search).get('__route');
  if (route && route.charAt(0) === '/' && route.charAt(1) !== '/') {
    history.replaceState(null, '', "/DataQ/demo" + route);
  }
})();
