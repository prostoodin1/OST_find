// Read only visible DOM content. Coordinates use Qt logical pixels after zoom.
(() => {
    const zoom = __ZOOM__;
    const search = __SEARCH__;
    const nodes = search
        ? document.querySelectorAll('a[data-testid="result-title-a"],a.result__a:not(.related-searches__link),article h2 a,li.b_algo h2 a')
        : document.querySelectorAll('a[href],main p,article p,body > p,h1,h2,h3');
    const targets = [];
    for (const node of nodes) {
        if (targets.length >= 65) break;
        if (node.closest('nav,header,footer,[role="navigation"]')) continue;
        const rects = Array.from(node.getClientRects());
        const rect = rects.find(r => r.width > 5 && r.height > 5 && r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0);
        if (!rect || getComputedStyle(node).visibility === 'hidden') continue;
        const text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 350);
        if (text.length < 3) continue;
        const link = node.closest('a[href]');
        const url = link ? link.href : location.href;
        if (!/^https?:\/\//.test(url)) continue;
        if (search && new URL(url).hostname.endsWith('duckduckgo.com')) continue;
        targets.push({text, url, kind: link ? 'link' : 'text', rect: [rect.x * zoom, rect.y * zoom, rect.width * zoom, Math.min(rect.height, 65) * zoom]});
    }
    return JSON.stringify({targets, scroll: scrollY, height: document.documentElement.scrollHeight, viewport: innerHeight});
})()
