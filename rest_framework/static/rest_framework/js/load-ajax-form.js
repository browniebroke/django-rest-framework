document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('form').forEach(function(form) {
    initAjaxForm(form);
  });
});
