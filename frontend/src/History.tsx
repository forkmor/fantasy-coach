import { useEffect, useState } from 'react'
import { errorMessage, request } from './api'
import type { ActionIntent, ActivityEvent, Run, RunDetail } from './types'
import { asRecord, normalizePlayers, useRoster } from './players'
import AdviceText, { friendlyExcerpt, reviewStatus } from './AdviceText'
import Modal from './Modal'
import SuggestionReview, { suggestionSummary, suggestionTitle } from './SuggestionReview'
import { Badge, dateTime, Empty, JsonDetails, Notice, pretty, SectionHeading, statusTone } from './ui'

export function Usage({ usage }: { usage: unknown }) {
  if (usage === undefined || usage === null || (typeof usage === 'object' && Object.keys(usage).length === 0)) {
    return <p className="muted">No provider usage reported.</p>
  }
  if (typeof usage !== 'object' || Array.isArray(usage)) return <JsonDetails data={usage} label="Provider-reported usage" />
  const scalars = Object.entries(usage).filter(([, value]) => typeof value === 'string' || typeof value === 'number')
  return <><dl className="usage-grid">{scalars.map(([key, value]) => <div key={key}><dt>{pretty(key)}</dt><dd>{String(value)}</dd></div>)}</dl>
    <JsonDetails data={usage} label="All provider usage fields" /><p className="field-hint">Provider-reported usage only. No estimated costs. Failed or cancelled requests may still be billable.</p></>
}

function EventContent({ data }: { data: unknown }) {
  if (typeof data === 'string') return <p className="pre-wrap">{data}</p>
  const fields = asRecord(data)
  const summary = fields.summary ?? fields.message ?? fields.detail ?? fields.decision
  return <>{typeof summary === 'string' && <p className="pre-wrap">{summary}</p>}<JsonDetails data={data} label="Event details" /></>
}
export default function History({ active, initialRunId, onSelectRun, connected = false }: {
  active: boolean; initialRunId: string | null; onSelectRun: (id: string | null) => void; connected?: boolean
}) {
  const [runs, setRuns] = useState<Run[]>([])
  const [actions, setActions] = useState<ActionIntent[]>([])
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [activityError, setActivityError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [version, setVersion] = useState(0)
  const [suggestionId, setSuggestionId] = useState<string | null>(null)
  const roster = useRoster(connected)
  useEffect(() => {
    let current = true
    let inFlight = false
    async function load() {
      if (inFlight) return
      inFlight = true
      const [runResult, actionResult, activityResult] = await Promise.allSettled([
        request<Run[]>('/runs'), request<ActionIntent[]>('/actions'), request<ActivityEvent[]>('/activity'),
      ])
      if (current) {
        if (runResult.status === 'fulfilled') { setRuns(runResult.value); setError('') } else setError(errorMessage(runResult.reason))
        if (actionResult.status === 'fulfilled') { setActions(actionResult.value); setActionError('') } else setActionError(errorMessage(actionResult.reason))
        if (activityResult.status === 'fulfilled') { setActivity(activityResult.value); setActivityError('') } else setActivityError(errorMessage(activityResult.reason))
        setLoading(false)
      }
      inFlight = false
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 5000)
    return () => { current = false; window.clearInterval(timer) }
  }, [active, version])
  useEffect(() => {
    if (!initialRunId) { setDetail(null); return }
    let current = true
    let inFlight = false
    setDetail(null)
    setDetailError('')
    setDetailLoading(true)
    async function load() {
      if (inFlight) return
      inFlight = true
      try { const value = await request<RunDetail>(`/runs/${encodeURIComponent(initialRunId!)}`); if (current) { setDetail(value); setDetailError('') } }
      catch (err) { if (current) setDetailError(errorMessage(err)) }
      finally { inFlight = false; if (current) setDetailLoading(false) }
    }
    void load()
    const timer = active ? window.setInterval(() => { void load() }, 3000) : undefined
    return () => { current = false; window.clearInterval(timer) }
  }, [initialRunId, active, version])
  const historicalPlayers = detail?.events?.flatMap(event => normalizePlayers(asRecord(event.data).result)) ?? []
  const players = [...new Map([...roster.players, ...historicalPlayers].map(player => [player.player_id, player])).values()]
  const relatedActions = initialRunId ? actions.filter(intent => intent.run_id === initialRunId) : actions
  const selectedSuggestion = actions.find(intent => intent.id === suggestionId)
  const nextIndex = selectedSuggestion ? relatedActions.findIndex(intent => intent.id === selectedSuggestion.id) + 1 : -1
  return <div className="page-stack">
    <SectionHeading eyebrow="Advice you can use" title="Your coach's game plans"
      action={<button className="button secondary" onClick={() => setVersion(value => value + 1)}>Refresh reviews</button>}>
      Read the advice, look through suggested moves, and decide what's right for your team.
    </SectionHeading>
    {error && <Notice message={error} />}
    {roster.error && <Notice tone="info" message={`Player names could not be refreshed: ${roster.error}`} />}
    {loading ? <p role="status">Loading your reviews...</p> : runs.length === 0 ? <Empty title="Your first run starts here">Ask your coach to review your team. The advice will be saved here.</Empty>
      : <div className="review-card-grid">{runs.map(run => <button key={run.id} className="coach-review-card" onClick={() => onSelectRun(run.id)}
        aria-label={`Read review from ${dateTime(run.started_at)}`}>
        <div className="card-title"><span className="review-calendar">{new Date(run.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>
          <Badge tone={statusTone(run.status)}>{reviewStatus(run.status)}</Badge></div>
        <h3>{run.provider === 'grok' ? 'Grok' : 'Gemini'}'s team review</h3>
        <p className="review-excerpt">{friendlyExcerpt(run.summary || run.error || 'Open for progress and suggestions.', players)}</p>
        <span className="text-link">Read the game plan →</span>
      </button>)}</div>}
    <section className="card">
      <SectionHeading title="Suggested moves">Ideas only. Your ESPN team has not changed.</SectionHeading>
      {actionError && <Notice message={actionError} />}
      {!actions.length ? <Empty title="No suggested moves yet">Your coach may recommend keeping your team as it is. New ideas appear here after a review.</Empty>
        : <div className="suggestion-list">{actions.map(intent => <button className="suggestion-row" key={intent.id} onClick={() => setSuggestionId(intent.id)}>
          <span><strong>{suggestionTitle(intent)}</strong><small>{suggestionSummary(intent, players)}</small></span>
          <Badge tone={statusTone(intent.status ?? '')}>{intent.status === 'blocked' ? 'Outside your rules'
            : intent.status === 'completed' ? 'Completed on ESPN'
              : intent.status === 'unknown' ? 'Check ESPN'
                : intent.status === 'failed' ? 'Failed'
                  : intent.status === 'submitting' ? 'Submitting' : 'Review idea'}</Badge>
        </button>)}</div>}
    </section>
    {activityError && <Notice message={`Could not load workspace activity: ${activityError}`} />}
    <details className="technical-details"><summary>Behind the scenes: activity and troubleshooting</summary>
      <section className="card" aria-label="Workspace and scheduler activity">
        <SectionHeading title="Workspace & scheduler activity">Saved activity for troubleshooting. No credentials are included.</SectionHeading>
        {!activity.length ? !activityError && <Empty title="No workspace events yet">Schedule activity and settings changes appear here.</Empty>
          : <ol className="timeline">{activity.map(event => <li key={event.id}>
            <div className="event-heading"><Badge tone={/error|fail/i.test(event.kind) ? 'red' : 'neutral'}>{pretty(event.kind)}</Badge>
              <time dateTime={event.created_at}>{dateTime(event.created_at)}</time></div><EventContent data={event.data} />
          </li>)}</ol>}
      </section>
    </details>
    {initialRunId && !selectedSuggestion && <Modal title="Your coach's advice" onClose={() => onSelectRun(null)} footer={<>
      <button className="button secondary" onClick={() => onSelectRun(null)}>Done reading</button>
      {relatedActions.length > 0 && <button className="button primary" onClick={() => setSuggestionId(relatedActions[0].id)}>Walk through suggested moves</button>}
    </>}>
      {detailLoading && <p role="status">Loading your advice...</p>}
      {detailError && <Notice message={detailError} />}
      {detail && <>
        <div className="button-row"><Badge tone={statusTone(detail.status)}>{reviewStatus(detail.status)}</Badge><span className="muted">{dateTime(detail.started_at)}</span></div>
        <div className="run-summary"><h3>Your game plan</h3><p className="field-hint">Your coach's advice, not confirmation of an ESPN transaction.</p>
          <AdviceText players={players} text={detail.summary || (['running', 'queued'].includes(detail.status) ? 'Your coach is still checking the team. This view will update automatically.' : 'No final advice was returned. Check the message below.')} /></div>
        {detail.error && <Notice message={detail.error} />}
        <details className="technical-details"><summary>Review details and API usage</summary>
          <Usage usage={detail.usage} /><ol className="timeline">{detail.events?.map(event =>
            <li key={event.id}><strong>{pretty(event.kind)}</strong><EventContent data={event.data} /></li>)}</ol>
        </details>
      </>}
    </Modal>}
    {selectedSuggestion && <SuggestionReview intent={selectedSuggestion} players={players} onClose={() => setSuggestionId(null)}
      onNext={nextIndex > 0 && nextIndex < relatedActions.length ? () => setSuggestionId(relatedActions[nextIndex].id) : undefined} />}
  </div>
}
