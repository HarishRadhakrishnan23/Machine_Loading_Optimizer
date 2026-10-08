import { useEffect, useState } from 'react'

import PageHeader from '../components/PageHeader'
import { LoadingPanel, ErrorPanel, EmptyPanel } from '../components/LoadingState'
import SearchableSelect from '../components/SearchableSelect'
import { useToast } from '../hooks/useToast'
import { getArchivedRuns, exportArchivedRunExcel } from '../api/client'

function fmt(ts) {
  return new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function DownloadArchive() {
  const toast = useToast()
  const [runs, setRuns] = useState(null)
  const [runId, setRunId] = useState('')
  const [error, setError] = useState(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    getArchivedRuns()
      .then((res) => {
        setRuns(res.runs)
        if (res.runs.length > 0) setRunId(res.runs[0].run_id)
      })
      .catch((e) => setError(e.message))
  }, [])

  const download = async () => {
    setDownloading(true)
    try {
      const filename = await exportArchivedRunExcel(runId)
      toast.success(`Downloaded ${filename}`)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setDownloading(false)
    }
  }

  if (runs && runs.length === 0) {
    return (
      <div>
        <PageHeader title="Download Archive" subtitle="Export every row of one frozen RUN_ID from MCH_SCHEDULE_OUTPUT_ARCHIVE as an .xlsx workbook" />
        <div className="p-6">
          <EmptyPanel
            title="No archived runs yet"
            hint="Go to Overview and click 'Freeze Schedule' to create a run you can later download here."
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Download Archive" subtitle="Export every row of one frozen Archive data as an .xlsx workbook" />
      <div className="p-6 max-w-2xl">
        {error && <ErrorPanel message={error} />}
        {!runs && !error && <LoadingPanel label="Loading archived runs…" />}

        {runs && (
          <div className="card p-4 flex items-end gap-3 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <label className="label">Run ID</label>
              <SearchableSelect
                options={runs.map((r) => ({
                  value: r.run_id,
                  label: `${fmt(r.generated_at)} (${r.row_count} rows) — ${r.run_id}`,
                }))}
                value={runId}
                onChange={setRunId}
                placeholder="Type to search…"
              />
            </div>
            <button className="btn-primary" onClick={download} disabled={downloading}>
              {downloading ? 'Downloading…' : 'Download Excel'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
