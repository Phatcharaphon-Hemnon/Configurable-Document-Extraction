import { useState, useEffect } from 'react';
import { ExtractionTab } from './ExtractionTab';
import { Disclaimer } from './Disclaimer';
import { downloadOriginal, extractFiles, pollJobStatus } from '../api/client';
import type { DocumentGroup, ExtractionResult } from '../types/extraction';

type Job = {
  id: string;
  filename: string;
  status: string;
  doc_type: string | null;
  language: string | null;
  extraction_source: string | null;
  completeness_score: number;
  needs_review: boolean;
  created_at: string;
  error: string | null;
};

type Stats = {
  total_jobs: number;
  by_status: Record<string, number>;
  by_doc_type: Record<string, number>;
  avg_completeness: number;
  needs_review: number;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

// If the backend is down or wedged, fail into the error UI (with its Retry
// button) instead of spinning forever.
const FETCH_TIMEOUT_MS = 15000;

async function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('The server is taking too long to respond — is the backend running?');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function HistoryTab() {
  const [selected, setSelected] = useState<DocumentGroup | null>(null);
  const [selectedPage, setSelectedPage] = useState(0);
  const [opening, setOpening] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const limit = 20;

  useEffect(() => {
    fetchHistory();
    fetchStats();
  }, [page]);

  const fetchHistory = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetchWithTimeout(`${API_BASE}/history?limit=${limit}&offset=${page * limit}`);
      if (!res.ok) throw new Error('Failed to fetch history');
      const data = await res.json();
      setJobs(data.jobs);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await fetchWithTimeout(`${API_BASE}/history/stats`);
      if (!res.ok) return;
      const data = await res.json();
      setStats(data);
    } catch {
      // Stats not available
    }
  };

  const openJob = async (job: Job) => {
    setOpening(true);
    try {
      const res = await fetchWithTimeout(`${API_BASE}/history/${job.id}`);
      if (!res.ok) throw new Error('Could not load saved result');
      const detail = await res.json();
      setSelected({id: job.id, label: job.filename, files: [], jobId: job.id, readOnly: true,
        status: detail.result ? 'done' : job.status === 'failed' ? 'error' : 'processing',
        response: detail.result, error: detail.error, progress: detail.progress});
      setSelectedPage(0);
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not open history'); }
    finally { setOpening(false); }
  };

  // True History retry: download the stored original(s) and resubmit through
  // POST /extract with the selected cache flags, then poll the new job.
  // Retrying a multipage PDF processes the entire original: all of its pages
  // share one source_id, so grouping by download_url downloads it once and
  // resubmitting it reprocesses every page. Shows an actionable error when
  // the original is unavailable (cleared history / pruned sources).
  const retrySelected = async (opts?: { forceRefresh?: boolean; disableCaches?: boolean }) => {
    if (!selected || retrying) return;
    setRetrying(true);
    setRetryError(null);
    try {
      const docs: ExtractionResult[] = selected.response?.documents ?? [];
      const byOriginal = new Map<string, { filename: string; downloadUrl: string }>();
      for (const d of docs) {
        const src = d.source;
        if (src?.download_url && src?.filename && !byOriginal.has(src.download_url)) {
          byOriginal.set(src.download_url, { filename: src.filename, downloadUrl: src.download_url });
        }
      }
      if (byOriginal.size === 0) {
        throw new Error(
          'No stored original is available for this job (it may predate source storage or its sources were cleared). Re-upload the file to retry extraction.',
        );
      }
      const files: File[] = [];
      for (const { filename, downloadUrl } of byOriginal.values()) {
        files.push(await downloadOriginal(downloadUrl, filename));
      }
      const label = files.length === 1 ? files[0].name : `${files.length} files (${files[0].name}, ...)`;
      const accepted = await extractFiles(files, label, undefined, opts);
      const job = await pollJobStatus(accepted.job_id, {});
      if (job.status === 'failed' || !job.result) {
        throw new Error(job.error || 'Retry extraction failed.');
      }
      setSelected({
        id: crypto.randomUUID(),
        label: `${selected.label} (retry)`,
        files,
        jobId: accepted.job_id,
        readOnly: true,
        status: 'done',
        response: job.result,
        progress: job.progress,
      });
      setSelectedPage(0);
      fetchHistory();
      fetchStats();
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : 'Could not retry from stored original');
    } finally {
      setRetrying(false);
    }
  };

  const deleteJob = async (jobId: string) => {
    if (!confirm('Are you sure you want to delete this job?')) return;
    try {
      const res = await fetch(`${API_BASE}/history/${jobId}`, { method: 'DELETE' });
      if (res.ok) {
        fetchHistory();
        fetchStats();
      }
    } catch {
      // Ignore error
    }
  };

  const clearHistory = async () => {
    if (!confirm('Delete ALL history jobs and their stored originals and previews? This cannot be undone.')) return;
    setClearing(true);
    setClearError(null);
    try {
      const res = await fetchWithTimeout(`${API_BASE}/history`, { method: 'DELETE' });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `Clear failed (${res.status})`);
      }
      setSelected(null);
      setSelectedPage(0);
      setPage(0);
      fetchHistory();
      fetchStats();
    } catch (err) {
      setClearError(err instanceof Error ? err.message : 'Could not clear history');
    } finally {
      setClearing(false);
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString();
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return '#174D38';
      case 'failed': return '#4D1717';
      case 'queued': return '#CBCBCB';
      case 'processing': return '#174D38';
      default: return '#CBCBCB';
    }
  };

  if (loading && jobs.length === 0) {
    return <div className="history-loading">Loading history...</div>;
  }

  if (error) {
    return (
      <div className="history-error">
        <p>Error: {error}</p>
        <button onClick={fetchHistory}>Retry</button>
      </div>
    );
  }

  if (selected) {
    const doc = selected.response?.documents[selectedPage] ?? null;
    return <div className="history-tab">
      <button className="button-secondary" onClick={() => setSelected(null)}>Back to history</button>
      {retryError && (
        <div className="history-error" style={{ marginTop: '8px' }}>
          <p>Retry failed: {retryError}</p>
          <button onClick={() => setRetryError(null)}>Dismiss</button>
        </div>
      )}
      {retrying && <p className="box-text">Retrying from stored original… downloading, resubmitting, and polling the new job.</p>}
      <ExtractionTab group={selected} doc={doc} docIndex={selectedPage} onSelectDoc={setSelectedPage}
        onRetry={(_id, opts) => void retrySelected(opts)} combinedFields={doc?.fields.map(field => [field.name, field]) ?? []} />
    </div>;
  }

  return (
    <div className="history-tab">
      <Disclaimer variant="history" />

      {/* Stats Cards */}
      {stats && (
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-value">{stats.total_jobs}</div>
            <div className="stat-label">Total Jobs</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{(stats.avg_completeness * 100).toFixed(1)}%</div>
            <div className="stat-label">Avg Completeness</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats.needs_review}</div>
            <div className="stat-label">Needs Review</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats.by_status?.completed || 0}</div>
            <div className="stat-label">Completed</div>
          </div>
        </div>
      )}

      {/* Jobs Table */}
      <div className="history-toolbar">
        <button
          className="button-secondary"
          disabled={clearing || (jobs.length === 0 && total === 0)}
          onClick={() => void clearHistory()}
          title="Delete all history jobs and stored sources"
        >
          {clearing ? 'Clearing…' : 'Clear history'}
        </button>
      </div>
      {clearError && (
        <div className="history-error">
          <p>Error: {clearError}</p>
          <button onClick={() => { setClearError(null); fetchHistory(); fetchStats(); }}>Retry</button>
        </div>
      )}
      <div className="history-table-container">
        <table className="history-table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Type</th>
              <th>Source</th>
              <th>Status</th>
              <th>Completeness</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-state">No extraction jobs found</td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id}>
                  <td className="filename-cell" title={job.filename}>
                    <button className="button-secondary" disabled={opening} onClick={() => void openJob(job)}>
                      {job.filename.length > 30 ? job.filename.slice(0, 30) + '...' : job.filename}
                    </button>
                  </td>
                  <td>
                    {/* API enum is unchanged; jobs without a routed type (OCR/Router
                        failures) display as Unclassified instead of '-' or a
                        misleading placeholder. */}
                    <span className="doc-type-badge">{job.doc_type ? job.doc_type.replace(/_/g, ' ') : 'Unclassified'}</span>
                  </td>
                  <td>
                    <span className="source-badge">{job.extraction_source || '-'}</span>
                  </td>
                  <td>
                    <span
                      className="status-badge"
                      style={{ backgroundColor: getStatusColor(job.status) }}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td>{(job.completeness_score * 100).toFixed(0)}%</td>
                  <td>{formatDate(job.created_at)}</td>
                  <td>
                    <button
                      className="delete-btn"
                      onClick={() => deleteJob(job.id)}
                      title="Delete job"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="pagination">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            Previous
          </button>
          <span>
            Page {page + 1} of {Math.ceil(total / limit)}
          </span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={(page + 1) * limit >= total}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
