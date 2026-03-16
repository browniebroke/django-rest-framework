document.addEventListener('DOMContentLoaded', function() {
  // JSON highlighting.
  prettyPrint();

  // Bootstrap tooltips.
  document.querySelectorAll('.js-tooltip').forEach(function(el) {
    new bootstrap.Tooltip(el, {
      delay: 1000,
      container: 'body'
    });
  });

  // Deal with rounded tab styling after tab clicks.
  document.querySelectorAll('a[data-bs-toggle="tab"]').forEach(function(el, index) {
    el.addEventListener('shown.bs.tab', function(e) {
      var tabbable = e.target.closest('.tabbable');
      if (tabbable) {
        // Check if this is the first tab
        var allTabs = tabbable.querySelectorAll('a[data-bs-toggle="tab"]');
        if (e.target === allTabs[0]) {
          tabbable.classList.add('first-tab-active');
        } else {
          tabbable.classList.remove('first-tab-active');
        }
      }
    });

    el.addEventListener('click', function() {
      document.cookie = "tabstyle=" + this.name + "; path=/";
    });
  });

  // Store tab preference in cookies & display appropriate tab on load.
  var selectedTab = null;
  var selectedTabName = getCookie('tabstyle');

  if (selectedTabName) {
    selectedTabName = selectedTabName.replace(/[^a-z-]/g, '');
  }

  if (selectedTabName) {
    selectedTab = document.querySelector('.form-switcher a[name=' + selectedTabName + ']');
  }

  if (selectedTab) {
    // Display whichever tab is selected.
    bootstrap.Tab.getOrCreateInstance(selectedTab).show();
  } else {
    // If no tab selected, display rightmost tab.
    var firstTab = document.querySelector('.form-switcher a');
    if (firstTab) {
      bootstrap.Tab.getOrCreateInstance(firstTab).show();
    }
  }

  window.addEventListener('load', function() {
    var errorModal = document.getElementById('errorModal');
    if (errorModal) {
      bootstrap.Modal.getOrCreateInstance(errorModal).show();
    }
  });
});
