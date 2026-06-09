/* DigitalTwinEngine — floating tooltip for any [data-tip] element.
   position:fixed escapes sidebar overflow:auto and any clipping parent.
   Loaded automatically by Dash from the assets/ directory. */
(function () {
    'use strict';

    var tip = null;

    function ensureTip() {
        if (tip) return;
        tip = document.createElement('div');
        tip.id = 'dt-floating-tip';
        tip.style.cssText = [
            'position:fixed',
            'display:none',
            'max-width:280px',
            'padding:9px 13px',
            'background:#0e1117',
            'border:1px solid #4d9eff',
            'border-radius:5px',
            'color:#e0e0e0',
            'font-size:11px',
            'font-family:"Inter","Helvetica Neue",Arial,sans-serif',
            'line-height:1.55',
            'white-space:normal',
            'box-shadow:0 4px 14px rgba(0,0,0,.65)',
            'z-index:99999',
            'pointer-events:none',
            'word-break:break-word',
        ].join(';');
        document.body.appendChild(tip);
    }

    function show(text, x, y) {
        ensureTip();
        tip.textContent = text;
        tip.style.display = 'block';
        reposition(x, y);
    }

    function hide() {
        if (tip) tip.style.display = 'none';
    }

    function reposition(x, y) {
        if (!tip || tip.style.display === 'none') return;
        /* offset so it doesn't sit under the cursor */
        var left = x + 14;
        var top  = y - 10;
        /* keep within viewport */
        var w = tip.offsetWidth  || 280;
        var h = tip.offsetHeight || 60;
        if (left + w > window.innerWidth  - 8) left = x - w - 10;
        if (top  + h > window.innerHeight - 8) top  = y - h - 4;
        if (top < 4) top = y + 18;
        tip.style.left = left + 'px';
        tip.style.top  = top  + 'px';
    }

    document.addEventListener('mouseover', function (e) {
        var el = e.target.closest('[data-tip]');
        if (!el) { hide(); return; }
        show(el.getAttribute('data-tip'), e.clientX, e.clientY);
    });

    document.addEventListener('mouseout', function (e) {
        var still = e.relatedTarget && e.relatedTarget.closest('[data-tip]');
        if (!still) hide();
    });

    document.addEventListener('mousemove', function (e) {
        reposition(e.clientX, e.clientY);
    });

    /* Re-attach after Dash hot-updates the DOM */
    if (window.MutationObserver) {
        new MutationObserver(function () { ensureTip(); })
            .observe(document.body, { childList: true, subtree: false });
    }
}());
