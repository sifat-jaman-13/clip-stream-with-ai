import { useCallback, useEffect, useState } from 'react';
import { getRecentLogs } from '../../api/client';
import { Icon, Btn, Badge, Panel } from '../../components/primitives';
import { Hero } from '../../components/headers';

const LEVEL_TONE = { ERROR: 'danger', CRITICAL: 'danger', WARNING: 'amber', WARN: 'amber', INFO: 'out', DEBUG: 'out' };

export function LogsView() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getRecentLogs(500);
      setRows(Array.isArray(data?.logs) ? data.logs : []);
      setError('');
    } catch (err) {
      setError(err?.message || 'Could not load backend logs.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <main className="container narrow fade-in">
      <Hero eyebrow="Diagnostics" line1="Backend logs." sub="Recent diagnostic messages from the local ClippyMe service." />
      <div className="results-head" style={{ marginBottom: 16 }}>
        <Badge tone="out">{rows.length} entries</Badge>
        <div className="rh-right">
          <Btn variant="secondary" size="sm" icon="refresh-cw" onClick={refresh} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </Btn>
        </div>
      </div>
      {error && (
        <Panel>
          <p role="alert" style={{ margin: 0, color: 'var(--danger)' }}>{error}</p>
        </Panel>
      )}
      {!error && !loading && rows.length === 0 && (
        <Panel>
          <div className="empty">
            <div className="ei"><Icon n="terminal" /></div>
            <h3>No backend logs yet</h3>
            <p>Messages will appear here when the backend performs work.</p>
          </div>
        </Panel>
      )}
      {rows.length > 0 && (
        <Panel pad={false}>
          <div role="log" aria-label="Recent backend logs" aria-live="polite"
            style={{ maxHeight: '65vh', overflowY: 'auto', fontFamily: 'var(--font-mono, monospace)' }}>
            {rows.map((row, index) => (
              <div key={index} style={{ display: 'grid', gridTemplateColumns: 'minmax(130px, auto) 74px minmax(0, 1fr)', gap: 10, padding: '9px 12px', borderBottom: '1px solid var(--line, #333)', fontSize: 12 }}>
                <span style={{ color: 'var(--fg-3)', whiteSpace: 'nowrap' }}>{row.time || '—'}</span>
                <span><Badge tone={LEVEL_TONE[String(row.level || 'INFO').toUpperCase()] || 'out'}>{row.level || 'INFO'}</Badge></span>
                <span style={{ minWidth: 0, overflowWrap: 'anywhere', color: 'var(--fg-1)' }}>
                  {row.logger && <strong style={{ color: 'var(--fg-3)' }}>{row.logger}: </strong>}
                  {row.message || ''}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </main>
  );
}
