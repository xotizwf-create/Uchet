(function () {
  if (!window.google) {
    window.google = {};
  }
  if (!window.google.script) {
    window.google.script = {};
  }

  function createRunner() {
    var successHandler = null;
    var failureHandler = null;

    return {
      withSuccessHandler: function (fn) {
        successHandler = fn;
        return this;
      },
      withFailureHandler: function (fn) {
        failureHandler = fn;
        return this;
      },
      appBackend: function (payload) {
        var onSuccess = successHandler;
        var onFailure = failureHandler;

        fetch('/api/appBackend', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(payload || {})
        })
          .then(function (response) { return response.json(); })
          .then(function (data) {
            if (onSuccess) {
              onSuccess(data);
            }
          })
          .catch(function (err) {
            if (onFailure) {
              onFailure(err && err.message ? err.message : err);
            }
          });

        return this;
      }
    };
  }

  window.google.script.run = {
    withSuccessHandler: function (fn) {
      return createRunner().withSuccessHandler(fn);
    },
    withFailureHandler: function (fn) {
      return createRunner().withFailureHandler(fn);
    },
    appBackend: function (payload) {
      return createRunner().appBackend(payload);
    }
  };
})();
