import { t } from '../i18n.js';
import { ChevronLeft, ChevronRight, Download, ZoomIn, ZoomOut } from './icons.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { PdfTarget } from '../workspace.js';

type PdfModule = typeof import('pdfjs-dist');
type Zoom = {mode: 'width' | 'page'} | {mode: 'scale'; scale: number};
interface PageSize { width: number; height: number }

let pdfModule: Promise<PdfModule> | undefined;
/** Loaded on first use so editing never pays for the PDF engine; the worker comes from this origin under the CSP. */
function loadPdfModule() {
  pdfModule ??= import('pdfjs-dist').then(module => { module.GlobalWorkerOptions.workerSrc = '/pdf.worker.js'; return module; });
  return pdfModule;
}
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

function PdfPage({pdf, doc, number, size, scale, visible, target, onPoint}: {pdf: PdfModule; doc: PDFDocumentProxy; number: number; size: PageSize; scale: number; visible: boolean; target: PdfTarget | null; onPoint(page: number, x: number, y: number): void}) {
  const canvasHost = useRef<HTMLDivElement>(null), textHost = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!visible) return;
    let cancelled = false, renderTask: {cancel(): void} | undefined, textLayer: {cancel(): void} | undefined;
    void (async () => {
      const page = await doc.getPage(number);
      if (cancelled) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 3), viewport = page.getViewport({scale});
      // Draw off-screen and swap, so zooming never shows a blank page.
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
      const task = page.render({canvas, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0]});
      renderTask = task;
      await task.promise;
      if (cancelled || !canvasHost.current || !textHost.current) return;
      canvasHost.current.replaceChildren(canvas);
      textHost.current.replaceChildren();
      const layer = new pdf.TextLayer({textContentSource: page.streamTextContent(), container: textHost.current, viewport});
      textLayer = layer;
      await layer.render();
    })().catch((error: unknown) => {
      if (!cancelled && (error as {name?: string})?.name !== 'RenderingCancelledException') console.warn(`PDF page ${number} failed to render`, error);
    });
    return () => { cancelled = true; renderTask?.cancel(); textLayer?.cancel(); };
  }, [doc, number, scale, visible, pdf]);
  const boxes = target?.boxes.filter(box => box.page === number) || [];
  return <div className="pdf-page" data-page={number} role="img" aria-label={t('PDF 第 {0} 页', number)} style={{width: size.width * scale, height: size.height * scale, ['--total-scale-factor' as string]: String(scale)}}
    onDoubleClick={event => { const rect = event.currentTarget.getBoundingClientRect(); onPoint(number, (event.clientX - rect.left) / scale, (event.clientY - rect.top) / scale); }}>
    <div className="pdf-canvas" ref={canvasHost}/>
    <div className="textLayer" ref={textHost}/>
    {boxes.map((box, index) => <div key={`${target?.nonce}-${index}`} className="pdf-target" style={{left: box.x * scale, top: box.y * scale, width: box.width * scale, height: box.height * scale}}/>)}
  </div>;
}

/** PDF.js viewer bound to one selected, successful build. Changing url replaces the whole document, so no old PDF can remain on screen. */
export function PdfViewer({url, documentKey, target, syncHint, onInverse, downloadName}: {url: string; documentKey: string; target: PdfTarget | null; syncHint: string; onInverse(page: number, x: number, y: number): void; downloadName: string}) {
  const [pdf, setPdf] = useState<PdfModule | null>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<PageSize[]>([]);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState<Zoom>({mode: 'width'});
  const [area, setArea] = useState({width: 0, height: 0});
  const [visible, setVisible] = useState<Set<number>>(new Set());
  const [current, setCurrent] = useState(1);
  const [pageInput, setPageInput] = useState('1');
  const scroller = useRef<HTMLDivElement>(null);
  // Keep the reader's place when a recompile replaces the document.
  const restorePage = useRef(1);
  const supported = typeof window !== 'undefined' && 'DOMMatrix' in window;

  useEffect(() => {
    if (!supported) { setError(t('此环境不支持 PDF 渲染')); return; }
    let cancelled = false, loading: ReturnType<PdfModule['getDocument']> | undefined;
    setError(''); setDoc(null); setSizes([]); setVisible(new Set());
    void (async () => {
      const module = await loadPdfModule();
      if (cancelled) return;
      setPdf(module);
      loading = module.getDocument({url, withCredentials: true, enableXfa: false, useWasm: false});
      const opened = await loading.promise;
      if (cancelled) return;
      const first = (await opened.getPage(1)).getViewport({scale: 1});
      if (cancelled) return;
      setSizes(Array.from({length: opened.numPages}, () => ({width: first.width, height: first.height})));
      setDoc(opened);
      const page = clamp(restorePage.current, 1, opened.numPages);
      setCurrent(page); setPageInput(String(page));
      // Page sizes are refined in the background; most papers use one size throughout.
      for (let number = 2; number <= opened.numPages && !cancelled; number++) {
        const viewport = (await opened.getPage(number)).getViewport({scale: 1});
        if (cancelled) return;
        if (viewport.width !== first.width || viewport.height !== first.height) setSizes(values => values.map((value, index) => index === number - 1 ? {width: viewport.width, height: viewport.height} : value));
      }
    })().catch((failure: unknown) => { if (!cancelled) setError(t('PDF 无法打开：{0}', String((failure as Error)?.message || failure))); });
    return () => { cancelled = true; void loading?.destroy(); };
  }, [url, supported]);

  useEffect(() => {
    const element = scroller.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => setArea({width: element.clientWidth, height: element.clientHeight}));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const scale = useMemo(() => {
    const page = sizes[current - 1] || sizes[0];
    if (zoom.mode === 'scale') return zoom.scale;
    if (!page || !area.width) return 1;
    const fit = (area.width - 32) / page.width;
    return clamp(zoom.mode === 'page' ? Math.min(fit, (area.height - 32) / page.height) : fit, .25, 4);
  }, [zoom, sizes, area, current]);

  const scrollToPage = useCallback((page: number, offset = 0) => {
    const element = scroller.current?.querySelector<HTMLElement>(`[data-page="${page}"]`);
    if (element && scroller.current) scroller.current.scrollTop = element.offsetTop - 12 + offset;
  }, []);
  const goToPage = (page: number) => {
    if (!doc) return;
    const next = clamp(page, 1, doc.numPages);
    setCurrent(next); setPageInput(String(next)); scrollToPage(next);
  };

  useEffect(() => {
    const root = scroller.current;
    if (!root || !doc || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(entries => setVisible(previous => {
      const next = new Set(previous);
      for (const entry of entries) {
        const page = Number((entry.target as HTMLElement).dataset.page);
        if (entry.isIntersecting) next.add(page); else next.delete(page);
      }
      return next;
    }), {root, rootMargin: '700px 0px'});
    root.querySelectorAll('.pdf-page').forEach(page => observer.observe(page));
    const restore = restorePage.current;
    requestAnimationFrame(() => scrollToPage(clamp(restore, 1, doc.numPages)));
    return () => observer.disconnect();
  }, [doc, sizes.length, scrollToPage]);

  const trackPage = () => {
    const root = scroller.current;
    if (!root) return;
    const middle = root.scrollTop + root.clientHeight / 2;
    let page = 1;
    for (const element of root.querySelectorAll<HTMLElement>('.pdf-page')) { if (element.offsetTop <= middle) page = Number(element.dataset.page); else break; }
    setCurrent(page); setPageInput(String(page)); restorePage.current = page;
  };

  // A forward SyncTeX result scrolls the box into the middle of the viewport.
  useEffect(() => {
    if (!target || !doc || !target.boxes.length || !scroller.current) return;
    const box = target.boxes[0];
    scrollToPage(box.page, box.y * scale - scroller.current.clientHeight / 2);
  }, [target?.nonce, doc]);

  const stepZoom = (factor: number) => setZoom({mode: 'scale', scale: clamp(Math.round(scale * factor * 100) / 100, .25, 4)});
  const onKeyDown = (event: React.KeyboardEvent) => {
    if ((event.target as HTMLElement).tagName === 'INPUT' || (event.target as HTMLElement).tagName === 'SELECT') return;
    if (event.key === 'PageDown') { event.preventDefault(); goToPage(current + 1); }
    else if (event.key === 'PageUp') { event.preventDefault(); goToPage(current - 1); }
    else if ((event.ctrlKey || event.metaKey) && (event.key === '=' || event.key === '+')) { event.preventDefault(); stepZoom(1.1); }
    else if ((event.ctrlKey || event.metaKey) && event.key === '-') { event.preventDefault(); stepZoom(1 / 1.1); }
  };
  const onWheel = (event: React.WheelEvent) => { if (event.ctrlKey || event.metaKey) stepZoom(event.deltaY < 0 ? 1.1 : 1 / 1.1); };
  const zoomValue = zoom.mode === 'scale' ? 'custom' : zoom.mode;

  return <div className="pdf-viewer" onKeyDown={onKeyDown}>
    <div className="toolbar pdf-toolbar" role="toolbar" aria-label={t('PDF 工具')}>
      <button id="pdf-prev" disabled={!doc || current <= 1} onClick={() => goToPage(current - 1)} className="icon-button" aria-label={t('上一页')} data-tip={t('上一页')}><ChevronLeft className="icon" aria-hidden="true"/></button>
      <input id="pdf-page" className="num" aria-label={t('页码')} inputMode="numeric" value={pageInput} disabled={!doc} onChange={event => setPageInput(event.currentTarget.value)} onKeyDown={event => { if (event.key === 'Enter') goToPage(Number(pageInput) || current); }} onBlur={() => setPageInput(String(current))}/>
      <span id="pdf-pages" className="muted">/ {doc?.numPages ?? '—'}</span>
      <button id="pdf-next" disabled={!doc || current >= (doc?.numPages ?? 1)} onClick={() => goToPage(current + 1)} className="icon-button" aria-label={t('下一页')} data-tip={t('下一页')}><ChevronRight className="icon" aria-hidden="true"/></button>
      <span className="toolbar-gap"/>
      <button id="pdf-zoom-out" disabled={!doc} onClick={() => stepZoom(1 / 1.1)} className="icon-button" aria-label={t('缩小')} data-tip={t('缩小')}><ZoomOut className="icon" aria-hidden="true"/></button>
      <select id="pdf-zoom" aria-label={t('缩放')} disabled={!doc} value={zoomValue} onChange={event => { const value = event.currentTarget.value; if (value === 'width' || value === 'page') setZoom({mode: value}); else if (value !== 'custom') setZoom({mode: 'scale', scale: Number(value)}); }}>
        <option value="width">{t('适合宽度')}</option><option value="page">{t('适合整页')}</option><option value="1">100%</option><option value="1.5">150%</option>
        {zoom.mode === 'scale' && <option value="custom">{Math.round(zoom.scale * 100)}%</option>}
      </select>
      <button id="pdf-zoom-in" disabled={!doc} onClick={() => stepZoom(1.1)} className="icon-button" aria-label={t('放大')} data-tip={t('放大')}><ZoomIn className="icon" aria-hidden="true"/></button>
      <span className="toolbar-gap"/>
      <a className="tool-link icon-button tip-end" href={url} download={downloadName} aria-label={t('下载 PDF')} data-tip={t('下载 PDF')}><Download className="icon" aria-hidden="true"/></a>
    </div>
    <div id="pdf-scroll" className="pdf-scroll" ref={scroller} tabIndex={0} role="region" aria-label={t('PDF 页面')} title={syncHint} onScroll={trackPage} onWheel={onWheel}>
      {error && <p className="pane-note" role="alert">{error}</p>}
      {!error && !doc && <p className="pane-note">{t('正在打开 PDF…')}</p>}
      {pdf && doc && <div key={documentKey} className="pdf-pages">{sizes.map((size, index) => <PdfPage key={index} pdf={pdf} doc={doc} number={index + 1} size={size} scale={scale} visible={visible.has(index + 1)} target={target} onPoint={onInverse}/>)}</div>}
    </div>
  </div>;
}
