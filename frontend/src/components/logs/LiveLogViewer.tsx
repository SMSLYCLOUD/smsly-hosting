'use client';

/**
 * Stable, append-only live log viewer.
 *
 * Design goals (after the previous version's bug report):
 *
 *  1. **Stable scroll.** The viewer NEVER auto-scrolls when new lines
 *     arrive. New lines are appended to the bottom of the buffer; the
 *     user's scroll position is the source of truth. If they want to
 *     follow the stream they scroll down. The only time we ever move
 *     the scroller is on the first mount (when there are pre-existing
 *     initial lines, we scroll to bottom ONCE so they see the tail).
 *
 *  2. **No destructive re-renders.** `lines` is append-only. `clear()` is
 *     only called from the explicit Clear button or the `c` keyboard
 *     shortcut. Re-fetches (Loki poll, WS reconnect) MERGE new lines by
 *     id, they never wipe existing lines.
 *
 *  3. **Dedupe by id.** Every line carries a stable `id` (set by the
 *     caller — typically a timestamp + sequence). When the same id is
 *     appended twice, the second append is dropped silently. This makes
 *     Loki re-polls and WS reconnects idempotent.
 *
 *  4. **Pause is a real freeze.** Lines arriving while paused go into a
 *     `bufferRef` (not React state) and are NOT rendered. The pause pill
 *     shows the buffer count. Pressing Resume (or Space) flushes the
 *     buffer into the visible list. Empty state shows a "PAUSED, N
 *     lines buffered" message rather than the generic "no logs".
 *
 *  5. **No "new lines" pill.** A previous version popped a button onto
 *     the scroller every time a new line arrived while the user was
 *     scrolled up. That felt like the page was reloading. The new design
 *     just lets the lines flow below the user's reading position — they
 *     scroll down when they want to see them.
 */

import {
    forwardRef,
    useCallback,
    useEffect,
    useImperativeHandle,
    useLayoutEffect,
    useMemo,
    useRef,
    useState,
} from 'react';
import {
    Pause,
    Play,
    Trash2,
    Search,
    X,
} from 'lucide-react';
import { cn } from '@/lib/utils';

export type LogSeverity = 'ALL' | 'ERROR' | 'WARNING' | 'SYSTEM' | 'NOISE' | 'APP';

export interface LogLine {
    /** Stable id — caller-provided, used for dedupe. */
    id: string;
    /** Optional human timestamp (any pre-formatted string). */
    time?: string;
    /** Optional source label (container / compose project / service). */
    source?: string;
    /** The actual log text. */
    text: string;
}

export interface LiveLogViewerHandle {
    append: (line: LogLine | LogLine[]) => void;
    appendRaw: (raw: string, source?: string, idPrefix?: string) => void;
    /**
     * Append lines, dropping any whose id already exists in the buffer.
     * Use this when reconnecting to a stream (WS reconnect, REST poll) to
     * avoid double-painting lines we already have.
     */
    merge: (lines: LogLine[]) => void;
    mergeRaw: (raw: string, source?: string, idPrefix?: string) => void;
    clear: () => void;
    setPaused: (paused: boolean) => void;
    isPaused: () => boolean;
    getLineCount: () => number;
    getBufferedCount: () => number;
}

export interface LiveLogViewerProps {
    initialLines?: LogLine[];
    /** Hard cap on kept-in-memory lines (oldest are dropped). Default 5000. */
    maxLines?: number;
    className?: string;
    /** Container height class. Default `h-[600px]`. */
    heightClass?: string;
    /** Empty-state message. */
    emptyMessage?: string;
    /** Optional: keyboard shortcut hint shown in the toolbar. */
    shortcutsHint?: string;
    /**
     * If true (default), the viewer scrolls to the bottom ONCE on first
     * mount when `initialLines` is non-empty. Subsequent appends never
     * auto-scroll.
     */
    scrollToBottomOnMount?: boolean;
}

const SEVERITY_PATTERNS: Array<{ sev: Exclude<LogSeverity, 'ALL'>; re: RegExp }> = [
    { sev: 'ERROR', re: /\berror\b|\bfatal\b|\bpanic\b|\btraceback\b|\bexception\b|\bcrash\b/i },
    { sev: 'WARNING', re: /\bwarn(ing)?\b|\bdeprecated\b|\bslow\b|\bretry(ing)?\b|\bbackoff\b/i },
    { sev: 'NOISE', re: /\bhealth(check)?\b|\bheartbeat\b|\bping\b|\bpong\b|\bkeepalive\b|\bmetrics?\b/i },
    { sev: 'SYSTEM', re: /\bsystemd\b|\bkernel\b|\bdocker\b|\bcontainerd\b|\btraefik\b|\bnginx\b|\bpostgres\b|\bredis\b|\bcaddy\b/i },
];

function classifyLogLine(text: string): Exclude<LogSeverity, 'ALL'> {
    for (const { sev, re } of SEVERITY_PATTERNS) {
        if (re.test(text)) return sev;
    }
    return 'APP';
}

function severityColor(sev: Exclude<LogSeverity, 'ALL'>): string {
    switch (sev) {
        case 'ERROR': return 'text-red-400';
        case 'WARNING': return 'text-amber-300';
        case 'NOISE': return 'text-zinc-500';
        case 'SYSTEM': return 'text-cyan-300';
        case 'APP':
        default:
            return 'text-zinc-300';
    }
}

function severityDot(sev: Exclude<LogSeverity, 'ALL'>): string {
    switch (sev) {
        case 'ERROR': return 'bg-red-500';
        case 'WARNING': return 'bg-amber-400';
        case 'NOISE': return 'bg-zinc-600';
        case 'SYSTEM': return 'bg-cyan-500';
        case 'APP':
        default:
            return 'bg-emerald-500';
    }
}

export const LiveLogViewer = forwardRef<LiveLogViewerHandle, LiveLogViewerProps>(
    function LiveLogViewer(props, ref) {
        const {
            initialLines = [],
            maxLines = 5000,
            className,
            heightClass = 'h-[600px]',
            emptyMessage = 'No log lines yet.',
            shortcutsHint,
            scrollToBottomOnMount = true,
        } = props;

        // Append-only buffer. Replaced ONLY on clear(). The `lines` state
        // is what the user actually sees.
        const [lines, setLines] = useState<LogLine[]>(initialLines);
        const idSetRef = useRef<Set<string>>(new Set(initialLines.map((l) => l.id)));
        const bufferRef = useRef<LogLine[]>([]);
        const [bufferCount, setBufferCount] = useState(0);
        const [paused, setPaused] = useState(false);
        const [textFilter, setTextFilter] = useState('');
        const [severityFilter, setSeverityFilter] = useState<LogSeverity>('ALL');
        const [severityCounts, setSeverityCounts] = useState<Record<Exclude<LogSeverity, 'ALL'>, number>>({
            APP: 0, SYSTEM: 0, WARNING: 0, ERROR: 0, NOISE: 0,
        });
        const [totalRendered, setTotalRendered] = useState(initialLines.length);

        const scrollerRef = useRef<HTMLDivElement>(null);
        const counterSeqRef = useRef(0);
        const didInitialScrollRef = useRef(false);

        const makeId = useCallback((prefix: string) => {
            counterSeqRef.current += 1;
            return `${prefix}-${Date.now().toString(36)}-${counterSeqRef.current.toString(36)}`;
        }, []);

        // ---- dedupe-aware append ----
        const appendInternal = useCallback((incoming: LogLine[]) => {
            if (incoming.length === 0) return;

            // Filter out duplicates
            const fresh: LogLine[] = [];
            const newSevCounts: Record<Exclude<LogSeverity, 'ALL'>, number> = {
                APP: 0, SYSTEM: 0, WARNING: 0, ERROR: 0, NOISE: 0,
            };
            for (const l of incoming) {
                if (idSetRef.current.has(l.id)) continue;
                idSetRef.current.add(l.id);
                fresh.push(l);
                const sev = classifyLogLine(l.text);
                newSevCounts[sev] += 1;
            }
            if (fresh.length === 0) return;

            if (paused) {
                bufferRef.current.push(...fresh);
                if (bufferRef.current.length > maxLines) {
                    const drop = bufferRef.current.length - maxLines;
                    const dropped = bufferRef.current.splice(0, drop);
                    for (const d of dropped) idSetRef.current.delete(d.id);
                }
                setBufferCount(bufferRef.current.length);
                return;
            }

            setLines((prev) => {
                const next = prev.concat(fresh);
                if (next.length > maxLines) {
                    const drop = next.length - maxLines;
                    const dropped = next.splice(0, drop);
                    for (const d of dropped) idSetRef.current.delete(d.id);
                }
                return next;
            });
            setTotalRendered((t) => t + fresh.length);
            setSeverityCounts((prev) => {
                const next = { ...prev };
                for (const k of Object.keys(newSevCounts) as Array<keyof typeof newSevCounts>) {
                    if (newSevCounts[k]) next[k] = (next[k] || 0) + newSevCounts[k];
                }
                return next;
            });
        }, [paused, maxLines]);

        const append = useCallback((incoming: LogLine | LogLine[]) => {
            appendInternal(Array.isArray(incoming) ? incoming : [incoming]);
        }, [appendInternal]);

        const appendRaw = useCallback((raw: string, source?: string, idPrefix = 'raw') => {
            if (!raw) return;
            const chunks = raw.split(/\r?\n/).filter((c) => c.length > 0);
            if (chunks.length === 0) return;
            appendInternal(chunks.map((text) => ({
                id: makeId(idPrefix),
                source,
                text,
            })));
        }, [appendInternal, makeId]);

        // ---- merge (deduping) helpers ----
        const merge = useCallback((incoming: LogLine[]) => {
            appendInternal(incoming);
        }, [appendInternal]);

        const mergeRaw = useCallback((raw: string, source?: string, idPrefix = 'raw') => {
            if (!raw) return;
            const chunks = raw.split(/\r?\n/).filter((c) => c.length > 0);
            if (chunks.length === 0) return;
            // For mergeRaw, use a hash of the line content as the id so
            // re-polls of identical tail output don't double-paint.
            const lines: LogLine[] = chunks.map((text, i) => ({
                id: `${idPrefix}-${text.length}-${i}-${text.slice(0, 40)}`,
                source,
                text,
            }));
            appendInternal(lines);
        }, [appendInternal]);

        const clear = useCallback(() => {
            bufferRef.current = [];
            setBufferCount(0);
            setLines([]);
            idSetRef.current.clear();
            setTotalRendered(0);
            setSeverityCounts({ APP: 0, SYSTEM: 0, WARNING: 0, ERROR: 0, NOISE: 0 });
        }, []);

        const flushBuffer = useCallback(() => {
            if (bufferRef.current.length === 0) return;
            const drained = bufferRef.current.slice();
            bufferRef.current = [];
            setBufferCount(0);
            setLines((prev) => {
                const next = prev.concat(drained);
                if (next.length > maxLines) {
                    const drop = next.length - maxLines;
                    const dropped = next.splice(0, drop);
                    for (const d of dropped) idSetRef.current.delete(d.id);
                }
                return next;
            });
            setTotalRendered((t) => t + drained.length);
        }, [maxLines]);

        const togglePaused = useCallback(() => {
            setPaused((prev) => {
                const next = !prev;
                if (!next) flushBuffer();
                return next;
            });
        }, [flushBuffer]);

        useImperativeHandle(ref, () => ({
            append,
            appendRaw,
            merge,
            mergeRaw,
            clear,
            flushBuffer,
            getBufferedCount: () => bufferRef.current.length,
            setPaused: (p: boolean) => {
                setPaused(p);
                if (!p) flushBuffer();
            },
            isPaused: () => paused,
            getLineCount: () => lines.length,
        }), [append, appendRaw, merge, mergeRaw, clear, flushBuffer, paused, lines.length]);

        // ---- scroll: ONLY on first mount with initial lines ----
        useLayoutEffect(() => {
            if (didInitialScrollRef.current) return;
            if (!scrollToBottomOnMount) return;
            if (initialLines.length === 0) return;
            const el = scrollerRef.current;
            if (!el) return;
            // Defer to the next paint so the lines are mounted.
            requestAnimationFrame(() => {
                if (scrollerRef.current) {
                    scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
                    didInitialScrollRef.current = true;
                }
            });
        }, [initialLines.length, scrollToBottomOnMount]);

        // ---- keyboard shortcuts ----
        useEffect(() => {
            const handler = (e: KeyboardEvent) => {
                const target = e.target as HTMLElement | null;
                if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
                    return;
                }
                if (e.key === ' ' || e.code === 'Space') {
                    e.preventDefault();
                    togglePaused();
                } else if (e.key === 'End') {
                    e.preventDefault();
                    const el = scrollerRef.current;
                    if (el) el.scrollTop = el.scrollHeight;
                } else if (e.key === 'Home') {
                    e.preventDefault();
                    const el = scrollerRef.current;
                    if (el) el.scrollTop = 0;
                } else if ((e.key === 'c' || e.key === 'C') && !e.metaKey && !e.ctrlKey) {
                    e.preventDefault();
                    clear();
                }
            };
            window.addEventListener('keydown', handler);
            return () => window.removeEventListener('keydown', handler);
        }, [togglePaused, clear]);

        // ---- filtering ----
        const visibleLines = useMemo(() => {
            const txt = textFilter.trim().toLowerCase();
            if (!txt && severityFilter === 'ALL') return lines;
            return lines.filter((l) => {
                if (severityFilter !== 'ALL') {
                    const sev = classifyLogLine(l.text);
                    if (sev !== severityFilter) return false;
                }
                if (txt) {
                    return (l.text || '').toLowerCase().includes(txt)
                        || (l.source || '').toLowerCase().includes(txt);
                }
                return true;
            });
        }, [lines, textFilter, severityFilter]);

        // ---- rendering ----
        const isEmpty = visibleLines.length === 0;
        const totalAvailable = lines.length + bufferCount;

        return (
            <div className={cn(
                'flex flex-col rounded-md border border-border bg-zinc-950 text-zinc-200 overflow-hidden',
                className,
            )}>
                {/* Toolbar */}
                <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-3 py-2 text-xs">
                    <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-zinc-900 border border-zinc-800 flex-1 min-w-[200px] max-w-md">
                        <Search className="h-3.5 w-3.5 text-zinc-500" />
                        <input
                            value={textFilter}
                            onChange={(e) => setTextFilter(e.target.value)}
                            placeholder="Filter…"
                            className="flex-1 bg-transparent outline-none text-xs placeholder:text-zinc-600"
                            spellCheck={false}
                        />
                        {textFilter && (
                            <button
                                onClick={() => setTextFilter('')}
                                className="text-zinc-500 hover:text-zinc-300"
                                aria-label="Clear filter"
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        )}
                    </div>

                    <select
                        value={severityFilter}
                        onChange={(e) => setSeverityFilter(e.target.value as LogSeverity)}
                        className="bg-zinc-900 border border-zinc-800 rounded-md px-2 py-1 text-xs"
                        title="Filter by severity"
                    >
                        <option value="ALL">All ({totalRendered})</option>
                        <option value="APP">App ({severityCounts.APP})</option>
                        <option value="SYSTEM">System ({severityCounts.SYSTEM})</option>
                        <option value="WARNING">Warnings ({severityCounts.WARNING})</option>
                        <option value="ERROR">Errors ({severityCounts.ERROR})</option>
                        <option value="NOISE">Noise ({severityCounts.NOISE})</option>
                    </select>

                    <div className="flex items-center gap-1">
                        <button
                            onClick={togglePaused}
                            className={cn(
                                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs',
                                paused
                                    ? 'border-amber-500/60 bg-amber-500/10 text-amber-300'
                                    : 'border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800',
                            )}
                            title={paused ? 'Resume (Space)' : 'Pause (Space)'}
                        >
                            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                            {paused ? `Resume${bufferCount ? ` (${bufferCount})` : ''}` : 'Pause'}
                        </button>
                        <button
                            onClick={clear}
                            className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                            title="Clear (c)"
                        >
                            <Trash2 className="h-3.5 w-3.5" /> Clear
                        </button>
                    </div>

                    <div className="ml-auto flex items-center gap-2 text-[11px] text-zinc-500 font-mono">
                        {visibleLines.length !== totalRendered && (
                            <span>showing {visibleLines.length} / {totalRendered}</span>
                        )}
                        {paused && (
                            <span
                                className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold"
                                title="New lines are buffered; click Resume to view them"
                            >
                                PAUSED{bufferCount > 0 ? ` (${bufferCount} buffered)` : ''}
                            </span>
                        )}
                        {shortcutsHint && <span className="hidden md:inline">{shortcutsHint}</span>}
                    </div>
                </div>

                {/* Scroller — completely passive. No auto-scroll, no "new lines" pill. */}
                <div
                    ref={scrollerRef}
                    data-log-scroller
                    className={cn('relative overflow-y-auto font-mono text-[12px] leading-5', heightClass)}
                >
                    {isEmpty ? (
                        <div className="p-6 text-center text-zinc-500 text-sm space-y-2">
                            {paused && bufferCount > 0 ? (
                                <>
                                    <p className="text-amber-300 font-medium">
                                        {bufferCount} line{bufferCount === 1 ? '' : 's'} buffered (paused)
                                    </p>
                                    <p className="text-xs">Click Resume to view them, or press <kbd className="px-1 rounded bg-zinc-800">Space</kbd></p>
                                </>
                            ) : (
                                <p>{emptyMessage}</p>
                            )}
                        </div>
                    ) : (
                        <ul className="divide-y divide-zinc-900/60">
                            {visibleLines.map((line) => {
                                const sev = classifyLogLine(line.text);
                                return (
                                    <li
                                        key={line.id}
                                        className="px-3 py-1 hover:bg-zinc-900/40 flex items-start gap-2"
                                    >
                                        <span
                                            className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', severityDot(sev))}
                                            title={sev}
                                        />
                                        {line.time && (
                                            <span className="shrink-0 text-zinc-600 select-none tabular-nums">
                                                {line.time}
                                            </span>
                                        )}
                                        {line.source && (
                                            <span className="shrink-0 text-zinc-500 truncate max-w-[160px]" title={line.source}>
                                                {line.source}
                                            </span>
                                        )}
                                        <pre className={cn(
                                            'whitespace-pre-wrap break-all flex-1 min-w-0',
                                            severityColor(sev),
                                        )}>
                                            {line.text}
                                        </pre>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>

                {/* Tiny footer with total so user always knows how many lines are in the buffer */}
                {totalAvailable > 0 && !isEmpty && (
                    <div className="px-3 py-1 text-[10px] text-zinc-600 font-mono border-t border-zinc-900">
                        {totalAvailable} line{totalAvailable === 1 ? '' : 's'} • scroll to see more
                    </div>
                )}
            </div>
        );
    },
);

export { classifyLogLine, severityColor, severityDot };
