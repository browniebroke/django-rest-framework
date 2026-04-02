function replaceDocument(docString) {
  var doc = document.open("text/html");

  doc.write(docString);
  doc.close();

  if (window.djdt) {
    // If Django Debug Toolbar is available, reinitialize it so that
    // it can show updated panels from new `docString`.
    window.addEventListener("load", djdt.init);
  }
}

function doAjaxSubmit(e) {
  var form = e.currentTarget;
  var btn = form.clk;
  var method = (
    (btn && btn.dataset.method) ||
    form.dataset.method ||
    form.getAttribute('method') || 'GET'
  ).toUpperCase();

  if (method === 'GET') {
    // GET requests can always use standard form submits.
    return;
  }

  var contentTypeOverride =
    form.querySelector('input[data-override="content-type"]');
  var contentTypeSelect =
    form.querySelector('select[data-override="content-type"]');
  var contentType = '';

  if (contentTypeOverride) {
    contentType = contentTypeOverride.value;
  } else if (contentTypeSelect) {
    contentType = contentTypeSelect.options[contentTypeSelect.selectedIndex].text;
  }

  if (method === 'POST' && !contentType) {
    // POST requests can use standard form submits, unless we have
    // overridden the content type.
    return;
  }

  // At this point we need to make an AJAX form submission.
  e.preventDefault();

  var url = form.getAttribute('action');
  var data;
  var headers = {
    'Accept': 'text/html; q=1.0, */*'
  };

  if (contentType) {
    var contentEl = form.querySelector('[data-override="content"]');
    data = contentEl ? contentEl.value : '';

    if (contentType === 'multipart/form-data') {
      // We need to add a boundary parameter to the header
      // We assume the first valid-looking boundary line in the body is correct
      // regex is from RFC 2046 appendix A
      var boundaryCharNoSpace = "0-9A-Z'()+_,-./:=?";
      var boundaryChar = boundaryCharNoSpace + ' ';
      var re = new RegExp('^--([' + boundaryChar + ']{0,69}[' + boundaryCharNoSpace + '])[\\s]*?$', 'im');
      var boundary = data.match(re);
      if (boundary !== null) {
        contentType += '; boundary="' + boundary[1] + '"';
      }
      // Fix textarea.value EOL normalisation (multipart/form-data should use CR+NL, not NL)
      data = data.replace(/\n/g, '\r\n');
    }
    headers['Content-Type'] = contentType;
  } else {
    var enctype = form.getAttribute('enctype') || form.getAttribute('encoding');

    if (enctype === 'multipart/form-data') {
      // Use the FormData API and allow the content type to be set automatically,
      // so it includes the boundary string.
      // See https://developer.mozilla.org/en-US/docs/Web/API/FormData/Using_FormData_Objects
      data = new FormData(form);
    } else {
      headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
      data = new URLSearchParams(new FormData(form)).toString();
    }
  }

  var xhr = new XMLHttpRequest();
  xhr.open(method, url);

  for (var headerName in headers) {
    if (headers.hasOwnProperty(headerName)) {
      xhr.setRequestHeader(headerName, headers[headerName]);
    }
  }

  // Add CSRF headers
  addCsrfHeaders(xhr, method, url);

  xhr.onloadend = function() {
    var responseContentType = xhr.getResponseHeader("content-type") || "";

    if (responseContentType.toLowerCase().indexOf('text/html') === 0) {
      replaceDocument(xhr.responseText);

      try {
        // Modify the location and scroll to top, as if after page load.
        history.replaceState({}, '', url);
        scroll(0, 0);
      } catch (err) {
        // History API not supported, so redirect.
        window.location = url;
      }
    } else {
      // Not HTML content. We can't open this directly, so redirect.
      window.location = url;
    }
  };

  xhr.send(data);
}

function captureSubmittingElement(e) {
  var target = e.target;
  var form = target.closest('form');
  if (form) {
    form.clk = target;
  }
}

function initAjaxForm(form) {
  form.addEventListener('submit', doAjaxSubmit);
  form.addEventListener('click', captureSubmittingElement);
}
