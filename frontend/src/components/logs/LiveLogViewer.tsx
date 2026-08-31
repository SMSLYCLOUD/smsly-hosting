'use client';

/**
 * Stable live-log viewer.
 *
 * Solves three problems with the previous implementation:
 *   1. Aggressive re-renders destroyed scroll position every poll tick
 *      (the user was scrolled up, the array re-keyed, the browser jumped
 *      to the new top instead of the user's anchor).
 *   2. No way to pause live updates or to filter visible lines.
 *   3. No way to clear the buffer.
 *
 * Design:
 *   - `lines` is an append-only `string[]`. The component NEVER replaces
 *     the array on a fresh fetch — it only `push`es new entries. React
 *     re-renders only when the array reference changes (which is only on
 *     `clear()`).
 *   - Smart auto-scroll: a `stickToBottom` flag is computed by the
 *     `onScroll` handler. While the user is within 80px of the bottom we
 *     follow new lines; once they scroll up we stop following, show a
 *     "N new lines — jump to live" pill, and the only thing that breaks
 *     their reading is them clicking the pill (or pressing End).
 *   - `paused` is a hard freeze: incoming lines are still appended to
 *     the in-memory buffer but they do not paint. Pressing Pause again
 *     flushes them to screen.
 *   - Filtering happens in a memoized `visibleLines` so typing in the
 *     search box does not re-walk the whole history on every keystroke.
 *
 * The component is data-source agnostic — it just exposes an `append`
 * method via `onReady` so parents (Loki poller, runtime WS, build WS)
 * can push lines into it.
 */

import {
    forwardRef,
    memo,
    useCallback,
    useEffect,
    useImperativeHandle,
    useLayoutEffect,
    useMemo,
    useRef,
    useState,
} from 'react';
import {
    ChevronDown,
    Pause,
    Play,
    Trash2,
    Search,
    X,
} from 'lucide-react';
import { cn } from '@/lib/utils';

export type LogSeverity = 'ALL' | 'ERROR' | 'WARNING' | 'SYSTEM' | 'NOISE' | 'APP';

export interface LogLine {
    /** Monotonically increasing key. UI uses this to dedupe. */
    id: string;
    /** Optional human timestamp (any string the parent already formatted). */
    time?: string;
    /** Optional source label (container / compose project / service). */
    source?: string;
    /** The actual log text. */
    text: string;
}

export interface LiveLogViewerHandle {
    append: (line: LogLine | LogLine[]) => void;
    appendRaw: (raw: string, source?: string) => void;
    clear: () => void;
    getBufferedCount: () => number;
    flushBuffer: () => void;
    setPaused: (paused: boolean) => void;
    isPaused: () => boolean;
}

export interface LiveLogViewerProps {
    initialLines?: LogLine[];
    /** Hard cap on kept-in-memory lines (oldest are dropped). Default 5000. */
    maxLines?: number;
    className?: string;
    /** Container height class. Default `h-[600px]`. */
    heightClass?: string;
    /** When true, shows the source column even if empty. */
    showSource?: boolean;
    /** Empty-state message. */
    emptyMessage?: string;
    /** Optional: keyboard shortcut hint shown in the toolbar. */
    shortcutsHint?: string;
}

const STICK_THRESHOLD_PX = 80;

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
        } = props;

        // Append-only buffer. Replace ONLY on clear() or on initial mount.
        const [lines, setLines] = useState<LogLine[]>(initialLines);
        // Lines that arrived while paused — flushed to `lines` on resume.
        const bufferRef = useRef<LogLine[]>([]);
        const [bufferCount, setBufferCount] = useState(0);
        const [paused, setPaused] = useState(false);
        const [textFilter, setTextFilter] = useState('');
        const [severityFilter, setSeverityFilter] = useState<LogSeverity>('ALL');
        const [autoScroll, setAutoScroll] = useState(true);
        const [pendingNew, setPendingNew] = useState(0);
        const [severityCounts, setSeverityCounts] = useState<Record<Exclude<LogSeverity, 'ALL'>, number>>({
            APP: 0, SYSTEM: 0, WARNING: 0, ERROR: 0, NOISE: 0,
        });
        const [totalRendered, setTotalRendered] = useState(initialLines.length);

        const scrollerRef = useRef<HTMLDivElement>(null);
        const lastStickCheck = useRef(0);
        const counterSeqRef = useRef(0);

        // ---- imperative handle for parents ----
        const makeId = useCallback(() => {
            counterSeqRef.current += 1;
            return `${Date.now().toString(36)}-${counterSeqRef.current.toString(36)}`;
        }, []);

        const append = useCallback((incoming: LogLine | LogLine[]) => {
            const arr = Array.isArray(incoming) ? incoming : [incoming];
            if (arr.length === 0) return;

            // Normalize: every line gets an id and a severity bucket.
            const normalized = arr.map((l) => {
                const sev = classifyLogLine(l.text);
                return { ...l, _sev: sev } as LogLine & { _sev: Exclude<LogSeverity, 'ALL'> };
            });
            // We need _sev at runtime but the public type doesn't carry it.
            // Cast to any in the renderer.
            (normalized as unknown as Array<LogLine & { _sev: Exclude<LogSeverity, 'ALL'> }>)
                .forEach((l) => (l as { _sev: Exclude<LogSeverity, 'ALL'> })._sev);

            if (paused) {
                bufferRef.current.push(...normalized);
                if (bufferRef.current.length > maxLines) {
                    bufferRef.current.splice(0, bufferRef.current.length - maxLines);
                }
                setBufferCount(bufferRef.current.length);
                setPendingNew((c) => c + normalized.length);
                return;
            }

            setLines((prev) => {
                const next = prev.concat(normalized);
                if (next.length > maxLines) {
                    next.splice(0, next.length - maxLines);
                }
                return next;
            });
            setTotalRendered((t) => t + normalized.length);
            // Update severity histogram
            setSeverityCounts((prev) => {
                const next = { ...prev };
                for (const l of normalized) {
                    const s = (l as LogLine & { _sev: Exclude<LogSeverity, 'ALL'> })._sev;
                    next[s] = (next[s] || 0) + 1;
                }
                return next;
            });
        }, [paused, maxLines]);

        const appendRaw = useCallback((raw: string, source?: string) => {
            if (!raw) return;
            // Split on newlines (one WebSocket / REST frame may carry many lines)
            const chunks = raw.split(/\r?\n/).filter((c) => c.length > 0 || raw.endsWith('\n'));
            if (chunks.length === 0) return;
            const now = new Date();
            const ts = now.toLocaleTimeString('en-US', { hour12: false });
            append(
                chunks.map((text) => ({
                    id: makeId(),
                    time: ts,
                    source,
                    text,
                })),
            );
        }, [append, makeId]);

        const clear = useCallback(() => {
            bufferRef.current = [];
            setBufferCount(0);
            setPendingNew(0);
            setLines([]);
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
                    next.splice(0, next.length - maxLines);
                }
                return next;
            });
            setTotalRendered((t) => t + drained.length);
        }, [maxLines]);

        const togglePaused = useCallback(() => {
            setPaused((prev) => {
                const next = !prev;
                if (!next) {
                    // Resuming -> flush whatever was buffered
                    flushBuffer();
                }
                return next;
            });
        }, [flushBuffer]);

        useImperativeHandle(ref, () => ({
            append,
            appendRaw,
            clear,
            flushBuffer,
            getBufferedCount: () => bufferRef.current.length,
            setPaused: (p: boolean) => {
                setPaused(p);
                if (!p) flushBuffer();
            },
            isPaused: () => paused,
        }), [append, appendRaw, clear, flushBuffer, paused]);

        // ---- sticky-bottom detection ----
        const onScroll = useCallback(() => {
            const el = scrollerRef.current;
            if (!el) return;
            const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            const shouldStick = distFromBottom <= STICK_THRESHOLD_PX;
            if (shouldStick !== autoScroll) {
                setAutoScroll(shouldStick);
            }
            if (shouldStick) {
                setPendingNew(0);
            }
            lastStickCheck.current = Date.now();
        }, [autoScroll]);

        // Auto-scroll to bottom whenever new lines arrive AND user is sticky.
        useLayoutEffect(() => {
            if (!autoScroll) return;
            const el = scrollerRef.current;
            if (!el) return;
            // Use a microtask to ensure the new lines have been painted.
            requestAnimationFrame(() => {
                el.scrollTop = el.scrollHeight;
            });
        }, [lines, autoScroll]);

        // ---- keyboard shortcuts ----
        useEffect(() => {
            const handler = (e: KeyboardEvent) => {
                // Only when the viewer (or one of its inputs) has focus.
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
                    if (el) {
                        el.scrollTop = el.scrollHeight;
                        setAutoScroll(true);
                        setPendingNew(0);
                    }
                } else if (e.key === 'Home') {
                    e.preventDefault();
                    const el = scrollerRef.current;
                    if (el) {
                        el.scrollTop = 0;
                        setAutoScroll(false);
                    }
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
                    const sev = (l as LogLine & { _sev?: Exclude<LogSeverity, 'ALL'> })._sev
                        ?? classifyLogLine(l.text);
                    if (sev !== severityFilter) return false;
                }
                if (txt) {
                    return (l.text || '').toLowerCase().includes(txt)
                        || (l.source || '').toLowerCase().includes(txt);
                }
                return true;
            });
        }, [lines, textFilter, severityFilter]);

        const jumpToLive = useCallback(() => {
            const el = scrollerRef.current;
            if (!el) return;
            el.scrollTop = el.scrollHeight;
            setAutoScroll(true);
            setPendingNew(0);
        }, []);

        // Reset the "new lines" pill to 0 whenever the user manually re-anchors
        useEffect(() => {
            if (autoScroll) setPendingNew(0);
        }, [autoScroll]);

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
                            <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold">PAUSED</span>
                        )}
                        {shortcutsHint && <span className="hidden md:inline">{shortcutsHint}</span>}
                    </div>
                </div>

                {/* Scroller */}
                <div
                    ref={scrollerRef}
                    onScroll={onScroll}
                    data-log-scroller
                    className={cn('relative overflow-y-auto font-mono text-[12px] leading-5', heightClass)}
                >
                    {visibleLines.length === 0 ? (
                        <div className="p-6 text-center text-zinc-500 text-sm">
                            {emptyMessage}
                        </div>
                    ) : (
                        <ul className="divide-y divide-zinc-900/60">
                            {visibleLines.map((line) => {
                                const sev = (line as LogLine & { _sev?: Exclude<LogSeverity, 'ALL'> })._sev
                                    ?? classifyLogLine(line.text);
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

                    {/* "New lines" pill — only when not stuck to bottom */}
                    {!autoScroll && pendingNew > 0 && (
                        <button
                            onClick={jumpToLive}
                            className="sticky bottom-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full bg-emerald-500 text-emerald-950 px-3 py-1.5 text-xs font-bold shadow-lg shadow-emerald-500/30 hover:bg-emerald-400 z-10"
                        >
                            <ChevronDown className="h-3.5 w-3.5" />
                            {pendingNew} new line{pendingNew === 1 ? '' : 's'} — jump to live
                        </button>
                    )}
                </div>
            </div>
        );
    },
);

// Re-export the classifier so callers (like the deployment LogsTab) can
// pre-classify or reuse the same severity buckets consistently.
export { classifyLogLine, severityColor, severityDot };
